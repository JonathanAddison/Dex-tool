#!/usr/bin/env python3
"""Two-agent, evidence-first code review engine for llama.cpp GGUF models.

Round 1: each agent independently reviews every source chunk, then synthesizes.
Rounds 2-7: both receive both previous proposals and independently challenge the
shared candidate against exact source evidence. Raw stdout/stderr and JSONL events
are streamed to disk and Actions logs. This script never applies a patch.
"""
from __future__ import annotations
import concurrent.futures, datetime as dt, hashlib, json, os, pathlib, queue, re, subprocess, sys, tempfile, threading, time
from agent_tools import execute as execute_agent_tool
from typing import Any

ROOT = pathlib.Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
AGENT = os.environ.get("AGENT_NAME", "unknown").lower()
ROUND = int(os.environ.get("ROUND", "1"))
MODEL = os.environ.get("MODEL_PATH", "")
OUTPUT = pathlib.Path(os.environ.get("OUTPUT_FILE", f"results/{AGENT}_round{ROUND}.json"))
WORK = pathlib.Path(os.environ.get("AGENT_WORK_DIR", f".agent-work/{AGENT}/round{ROUND}"))
CHUNKS = pathlib.Path(os.environ.get("CHUNKS_FILE", ".agent-work/source/chunks.jsonl"))
MANIFEST = pathlib.Path(os.environ.get("MANIFEST_FILE", ".agent-work/source/manifest.json"))
TASK = os.environ.get("TASK_PROMPT", "Find the most critical real bug and propose a verified fix.")
MY_PREV = os.environ.get("MY_LAST_OUTPUT", "")
PEER_PREV = os.environ.get("PEER_LAST_OUTPUT", "")
MODEL_BIN = os.environ.get("LLAMA_BIN", "./llama.cpp/build/bin/llama-completion")
CTX = int(os.environ.get("MODEL_CONTEXT", "32768"))
N_PREDICT = int(os.environ.get("MAX_OUTPUT_TOKENS", "4096"))
THREADS = int(os.environ.get("LLAMA_THREADS", "4"))
TIMEOUT = int(os.environ.get("MODEL_TIMEOUT_SECONDS", "7200"))
TEMP = float(os.environ.get("MODEL_TEMPERATURE", "0.15"))
TOP_P = float(os.environ.get("MODEL_TOP_P", "0.9"))


def emit(event: str, **data: Any) -> None:
    row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "agent": AGENT, "round": ROUND, "event": event, **data}
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[{row['ts']}] [{AGENT.upper()} R{ROUND}] {event}: " + json.dumps(data, ensure_ascii=False), flush=True)


def read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run gather_code.py first")
    out=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip(): out.append(json.loads(line))
    return out


def stream_process(prompt: str, label: str, index: int = 0) -> tuple[str, str, int, float]:
    """Run a single non-interactive completion, stream both pipes without deadlock."""
    WORK.mkdir(parents=True, exist_ok=True)
    prompt_path = WORK / f"{label}_{index:05d}.prompt.txt"
    stdout_path = WORK / f"{label}_{index:05d}.stdout.txt"
    stderr_path = WORK / f"{label}_{index:05d}.stderr.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    n_predict = int(os.environ.get("CHUNK_OUTPUT_TOKENS", "1200")) if label == "scan" else N_PREDICT
    cmd = [MODEL_BIN, "-m", MODEL, "-f", str(prompt_path), "-n", str(n_predict),
           "--temp", str(TEMP), "--top-p", str(TOP_P), "-c", str(CTX),
           "-t", str(THREADS), "--no-display-prompt", "--simple-io", "--single-turn"]
    # Optional llama.cpp flags are opt-in because prebuilt release builds differ.
    if os.environ.get("LLAMA_FLASH_ATTN", "0") == "1": cmd += ["--flash-attn", "on"]
    if os.environ.get("LLAMA_KV_CACHE_TYPE", ""):
        kv=os.environ["LLAMA_KV_CACHE_TYPE"]; cmd += ["--cache-type-k", kv, "--cache-type-v", kv]
    emit("model_start", label=label, index=index, context=CTX, output_tokens=N_PREDICT, command=cmd)
    start=time.monotonic(); proc=subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    buffers={"out":bytearray(),"err":bytearray()}
    def pump(pipe, key, path, prefix):
        with path.open("wb") as log:
            while True:
                chunk=pipe.read(4096)
                if not chunk: break
                buffers[key].extend(chunk); log.write(chunk); log.flush()
                decoded=chunk.decode("utf-8", errors="replace")
                for line in decoded.splitlines():
                    if line.strip(): print(f"[{AGENT.upper()} {label} {prefix}] {line}", flush=True)
    t1=threading.Thread(target=pump,args=(proc.stdout,"out",stdout_path,"MODEL"),daemon=True)
    t2=threading.Thread(target=pump,args=(proc.stderr,"err",stderr_path,"DIAG"),daemon=True)
    t1.start(); t2.start()
    try:
        rc=proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill(); rc=proc.wait(); emit("model_timeout",label=label,seconds=TIMEOUT)
    t1.join(timeout=30); t2.join(timeout=30)
    elapsed=time.monotonic()-start
    out=bytes(buffers["out"]).decode("utf-8",errors="replace")
    err=bytes(buffers["err"]).decode("utf-8",errors="replace")
    emit("model_end",label=label,returncode=rc,seconds=round(elapsed,2),stdout_chars=len(out),stderr_chars=len(err))
    if rc != 0: raise RuntimeError(f"llama.cpp failed ({rc}) for {label}; see {stderr_path}")
    return out,err,rc,elapsed


def extract_json(text: str) -> dict:
    # Prefer explicit fenced JSON; fall back to the outermost object.
    candidates=re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S|re.I)
    if not candidates:
        start=text.find("{"); end=text.rfind("}")
        if start >= 0 and end > start: candidates=[text[start:end+1]]
    for candidate in reversed(candidates):
        try:
            value=json.loads(candidate)
            if isinstance(value,dict): return value
        except json.JSONDecodeError: pass
    return {"status":"invalid_output","raw_text":text,"consensus":False}


def call_model(prompt: str, label: str, index: int=0) -> tuple[dict,str]:
    """Run model, repair malformed JSON once, and service bounded read-only tool calls."""
    transcript=[]; tool_events=[]; elapsed_total=0.0; parsed={}; stdout=""
    for turn in range(4):
        raw,stderr,_,elapsed=stream_process(prompt,label if turn==0 else f"{label}_toolturn",index+turn)
        elapsed_total += elapsed; stdout += raw
        parsed=extract_json(raw)
        if parsed.get("status")=="invalid_output":
            repair_prompt=(prompt + "\n\nYour previous response was not valid JSON. Return ONLY one complete valid JSON object matching the exact requested schema. Do not use markdown fences. Previous response follows:\n" + raw[-12000:])
            repaired,repair_stderr,_,repair_elapsed=stream_process(repair_prompt,label+"_repair",index+turn)
            elapsed_total += repair_elapsed; stdout += "\n\n--- JSON REPAIR ATTEMPT ---\n" + repaired
            parsed=extract_json(repaired)
        requests=parsed.get("tool_calls",[]) if isinstance(parsed,dict) else []
        if not requests or turn==3:
            break
        results=[]
        for call in requests[:5]:
            if not isinstance(call,dict): continue
            name=str(call.get("name","")); args=call.get("args",{})
            result=execute_agent_tool(name,args)
            event={"turn":turn+1,"name":name,"args":args,"result":result}
            tool_events.append(event); results.append(event)
            emit("tool_call",tool=name,ok=result.get("ok",False))
        if not results: break
        tool_log=WORK/"tool_calls.jsonl"
        with tool_log.open("a",encoding="utf-8") as f:
            for event in results: f.write(json.dumps(event,ensure_ascii=False)+"\n")
        transcript.append({"turn":turn+1,"tool_results":results})
        prompt=prompt + "\n\nTOOL RESULTS (read-only repository tools; treat outputs as source evidence):\n" + json.dumps(results,ensure_ascii=False) + "\nNow continue your analysis. You may request more tools if necessary, otherwise return the required final JSON object with tool_calls: [].\n"
    (WORK / f"{label}_{index:05d}.combined.txt").write_text(stdout + "\n\n--- TOOL CALLS ---\n" + json.dumps(transcript,ensure_ascii=False,indent=2), encoding="utf-8")
    parsed["tool_calls_executed"]=tool_events
    parsed.setdefault("raw_output_sha256",hashlib.sha256(stdout.encode()).hexdigest())
    parsed["elapsed_seconds"]=round(elapsed_total,2)
    return parsed,stdout


def load_skill_pack() -> str:
    p = ROOT / "skills" / "REVIEW_SKILLS.md"
    try: return p.read_text(encoding="utf-8")
    except OSError: return "Use strict evidence-based engineering review; do not invent APIs or claim unrun tests."

def load_tool_inventory() -> str:
    p = ROOT / ".agent-work" / "tool_inventory.json"
    try:
        inventory=p.read_text(encoding="utf-8")
    except OSError:
        inventory="{}"
    checks=ROOT / ".agent-work" / "static_checks.json"
    android=ROOT / ".agent-work" / "android_native_audit.json"
    binaries=ROOT / ".agent-work" / "binary_artifact_audit.json"
    sections=["TOOL INVENTORY:\n"+inventory[:7000]]
    caps={str(checks):18000,str(android):16000,str(binaries):12000}
    for label,path in (("STATIC CHECK RESULTS",checks),("ANDROID / NATIVE AUDIT",android),("BINARY ARTIFACT AUDIT",binaries)):
        try:
            content=path.read_text(encoding="utf-8")
            cap=caps[str(path)]
            if len(content)>cap: content=content[:cap]+"\n...[report clipped for prompt; full report is preserved as workflow artifact]"
            sections.append(label+":\n"+content)
        except OSError: pass
    return "\n\n".join(sections)

def chunk_prompt(chunk: dict, manifest: dict) -> str:
    return f'''You are {AGENT.upper()}, an independent senior software engineer and security reviewer.
Task: {TASK}
Specialist review skills:\n{load_skill_pack()}\nCompact runner tool summary (full reports are retained as artifacts): {load_tool_inventory()[:3500]}\nRepository snapshot SHA256: {manifest['snapshot_sha256']}
Analyze this exact source segment. It is one part of a complete repository scan.
Do not assume code outside this segment exists. Identify only demonstrable defects or important architectural risks.
Return STRICT JSON only with keys: {{"chunk_id":"...","path":"...","range":"...","tool_calls":[{{"name":"read_file|search_code|list_files|find_symbol|git_history|tool_inventory|sandbox_run","args":{{}}}}],"findings":[{{"id":"stable-short-id","title":"...","severity":"critical|high|medium|low|info","confidence":0-100,"evidence":"exact short quote or symbol","why_bug":"...","impact":"...","fix_direction":"..."}}],"notable_symbols":[],"questions":[]}}.
If no supported finding, findings must be []. Never invent line numbers; cite only the provided range. Use repository-inspection tools when cross-file context is necessary. Tool calls must be valid JSON and use only the listed tool names. You may use sandbox_run with a complete patch and one fixed suite (auto, python, cmake, node, go, rust, shell) to test a candidate patch in an isolated disposable Docker container; no arbitrary commands are accepted. Report the actual result and never infer a pass.

CHUNK {chunk['chunk_id']} | {chunk['path']}:{chunk['start_line']}-{chunk['end_line']} | {chunk['language']}
```{chunk['language']}
{chunk['text']}
```
'''


def source_for_evidence(proposal: dict, manifest: dict, max_chars: int=30000) -> str:
    files={x["path"] for x in manifest.get("files",[])}
    paths=[]
    for requested in proposal.get("needed_files",[]) if isinstance(proposal.get("needed_files"),list) else []:
        rp=str(requested).replace("\\", "/").strip()
        if rp in files and rp not in paths: paths.append(rp)
    for ev in proposal.get("evidence",[]) if isinstance(proposal.get("evidence"),list) else []:
        p=str(ev.get("file",""))
        if p in files and p not in paths: paths.append(p)
    blocks=[]; used=0
    for rel in paths[:12]:
        p=(ROOT/rel).resolve()
        if ROOT not in p.parents and p != ROOT: continue
        try: content=p.read_text(encoding="utf-8",errors="replace")
        except OSError: continue
        # Keep the file context but bound review prompt; line numbering is explicit.
        lines=content.splitlines()
        relevant=set()
        for ev in proposal.get("evidence",[]):
            if ev.get("file")==rel:
                m=re.search(r"(\d+)",str(ev.get("lines", "")))
                if m:
                    n=int(m.group(1)); relevant.update(range(max(1,n-35),min(len(lines),n+35)+1))
        if relevant:
            content="\n".join(f"{i}: {lines[i-1]}" for i in sorted(relevant))
        else: content="\n".join(f"{i}: {line}" for i,line in enumerate(lines,1))
        block=f"\n--- {rel} ---\n{content}\n"
        if used+len(block)>max_chars: break
        blocks.append(block); used+=len(block)
    return "".join(blocks) or "No evidence-linked files could be loaded."


def round1(chunks: list[dict], manifest: dict) -> tuple[dict,list[dict]]:
    findings=[]; chunk_reports=[]
    for i,c in enumerate(chunks,1):
        emit("source_chunk_start",chunk_id=c["chunk_id"],path=c["path"],range=f"{c['start_line']}-{c['end_line']}",progress=f"{i}/{len(chunks)}")
        report,raw=call_model(chunk_prompt(c,manifest),"scan",i)
        chunk_reports.append(report)
        for f in report.get("findings",[]) if isinstance(report.get("findings"),list) else []:
            f.setdefault("file",c["path"]); f.setdefault("chunk_id",c["chunk_id"]); findings.append(f)
        emit("source_chunk_done",chunk_id=c["chunk_id"],findings=len(report.get("findings",[])))
    (WORK/"chunk_findings.json").write_text(json.dumps(chunk_reports,ensure_ascii=False,indent=2),encoding="utf-8")
    prompt=f'''You are {AGENT.upper()}, synthesizing your independent complete repository scan.
Task: {TASK}\nSpecialist review skills:\n{load_skill_pack()}\nAvailable runner tools (presence only; not yet executed): {load_tool_inventory()}\nYou reviewed {len(chunks)} source chunks from {manifest['file_count']} files. Snapshot {manifest['snapshot_sha256']}.
You may request repository tools using tool_calls: list_files, read_file, search_code, find_symbol, git_history, tool_inventory; isolated execution using sandbox_run. Tool calls must use {{"name":"...","args":{{...}}}} and will be executed through a path-restricted allowlist. You may call sandbox_run with {{"suite":"auto","patch":"complete unified diff"}} to apply the candidate patch to a disposable copy and run fixed tests in a network-disabled Docker sandbox. Do not request shell commands; report exact test outcomes.\nHere are your extracted findings (untrusted candidate data; verify consistency and deduplicate):
{json.dumps(findings,ensure_ascii=False)}
Return STRICT JSON with schema:
{{"schema_version":1,"agent":"{AGENT}","round":1,"status":"proposal","issue_id":"stable-kebab-case","title":"...","severity":"critical|high|medium|low","confidence":0-100,"diagnosis":"...","root_cause":"...","impact":"...","evidence":[{{"file":"repo-relative path","lines":"line or range","symbol":"...","quote":"exact code excerpt"}}],"patch":"complete unified diff or empty string","patch_sha256":"sha256 of exact patch text or empty","tests_to_run":["..."],"validation_gaps":["..."],"alternatives_considered":["..."],"tool_calls":[],"consensus":false}}.
If you produce a non-empty patch, you MUST call sandbox_run with suite auto and that exact complete patch before finalizing. If sandbox execution is unavailable or skipped, explicitly state that validation was not performed. Pick one highest-impact real issue, not a list of speculative issues. Evidence must be present in the findings above. Do not claim tests ran unless the sandbox tool returned an execution result. Patch must be minimal, syntactically plausible, and include correct diff headers. If no real bug is supported, state that and do not fabricate a patch.
'''
    proposal,_=call_model(prompt,"synthesis",0)
    proposal.setdefault("agent",AGENT); proposal.setdefault("round",1); proposal["consensus"]=False
    patch_text=proposal.get("patch", "") or ""
    proposal["patch_sha256"]=hashlib.sha256(patch_text.encode()).hexdigest() if patch_text else ""
    proposal["scan_stats"]={"files":manifest["file_count"],"chunks":len(chunks),"findings":len(findings),"snapshot_sha256":manifest["snapshot_sha256"]}
    return proposal,chunk_reports


def later_round(manifest: dict) -> dict:
    mine=json.loads(MY_PREV) if MY_PREV.strip().startswith("{") else {"raw":MY_PREV}
    peer=json.loads(PEER_PREV) if PEER_PREV.strip().startswith("{") else {"raw":PEER_PREV}
    # One canonical candidate is selected deterministically by aggregator and passed as env.
    candidate_raw=os.environ.get("SHARED_CANDIDATE","")
    try: candidate=json.loads(candidate_raw) if candidate_raw else peer
    except json.JSONDecodeError: candidate=peer
    source=source_for_evidence(candidate,manifest)
    prompt=f'''You are {AGENT.upper()}, acting as a skeptical independent reviewer. This is round {ROUND}/7.
Task: {TASK}\nSpecialist review skills:\n{load_skill_pack()}\nAvailable runner tools (presence only; not yet executed): {load_tool_inventory()}\nRepository snapshot SHA256: {manifest['snapshot_sha256']}.
Your previous proposal:
{json.dumps(mine,ensure_ascii=False)}
Peer's previous proposal:
{json.dumps(peer,ensure_ascii=False)}
Canonical candidate under review (the exact candidate both agents must evaluate):
{json.dumps(candidate,ensure_ascii=False)}
Relevant source reloaded from the repository (line-numbered):
{source}

You may request repository tools using tool_calls: list_files, read_file, search_code, find_symbol, git_history, tool_inventory; isolated execution using sandbox_run. Tool calls must use {{"name":"...","args":{{...}}}} and will be executed through a path-restricted allowlist. You may call sandbox_run with {{"suite":"auto","patch":"complete unified diff"}} to apply the candidate patch to a disposable copy and run fixed tests in a network-disabled Docker sandbox. Do not request shell commands; report exact test outcomes.\nRequired procedure:
1. Verify every cited file, symbol, line and quote against supplied source. Reject fabricated/mismatched evidence.
2. Independently challenge root cause, severity, exploitability/user impact, and whether the patch fixes it without regressions.
3. Identify missing call sites, platform/build variants, ownership/lifetime/threading/ABI/API issues where relevant.
4. Review patch as a code reviewer; do not approve based on confidence or politeness.
5. If changes are needed, provide a complete revised unified diff and exact hash. If source context is insufficient, say so and request exact files using a `needed_files` array.
6. You MUST call sandbox_run with suite auto and the exact candidate patch before approving it; if you revise the patch, test the revised patch too. If the sandbox is unavailable, do not claim tests passed and do not approve as validated.
7. Approval must be explicit and refer to candidate patch SHA256 exactly. Never output consensus merely because you agree with the general diagnosis.

Return STRICT JSON:
{{"schema_version":1,"agent":"{AGENT}","round":{ROUND},"status":"review","issue_id":"...","decision":"approve|request_changes|reject|insufficient_evidence","confidence":0-100,"verified_evidence":[{{"file":"...","lines":"...","quote":"..."}}],"objections":[{{"severity":"critical|high|medium|low","claim":"...","evidence":"...","required_change":"..."}}],"revised_patch":"complete unified diff or empty","revised_patch_sha256":"...","approved_candidate_patch_sha256":"exact candidate patch hash only if fully approved, otherwise empty","needed_files":[],"tests_to_run":[],"tool_calls":[],"consensus":false}}.
Consensus is always false at individual-agent level; central validator decides only when both agents approve the same canonical patch and issue.
'''
    result,_=call_model(prompt,"review",0)
    result.setdefault("agent",AGENT); result.setdefault("round",ROUND); result["consensus"]=False
    # Carry the exact canonical candidate forward unless a revised patch replaces it.
    result["patch"]=candidate.get("patch", "")
    result["patch_sha256"]=candidate.get("patch_sha256", "")
    result["issue_id"]=result.get("issue_id") or candidate.get("issue_id", "")
    result["evidence"]=result.get("verified_evidence") or candidate.get("evidence", [])
    result["needed_files"]=result.get("needed_files", [])
    revised=result.get("revised_patch", "") or ""
    result["revised_patch_sha256"]=hashlib.sha256(revised.encode()).hexdigest() if revised else ""
    result["candidate_patch_sha256"]=candidate.get("patch_sha256","")
    return result


def main() -> int:
    if AGENT not in {"qwen","glm"}: raise SystemExit("AGENT_NAME must be qwen or glm")
    if not MODEL or not pathlib.Path(MODEL).exists(): raise SystemExit(f"MODEL_PATH missing: {MODEL}")
    if not pathlib.Path(MODEL_BIN).exists(): raise SystemExit(f"llama binary missing: {MODEL_BIN}")
    manifest=json.loads(MANIFEST.read_text(encoding="utf-8"))
    emit("engine_start",model=MODEL,context=CTX,files=manifest["file_count"],snapshot=manifest["snapshot_sha256"])
    if ROUND==1:
        chunks=read_jsonl(CHUNKS)
        proposal,chunk_reports=round1(chunks,manifest)
    else:
        proposal=later_round(manifest)
    proposal["agent"]=AGENT; proposal["round"]=ROUND
    proposal["source_snapshot_sha256"]=manifest["snapshot_sha256"]
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding="utf-8")
    # Compact transport payload; complete logs and scan findings stay in artifacts.
    transport={k:proposal.get(k) for k in ("schema_version","agent","round","status","issue_id","title","severity","confidence","diagnosis","root_cause","impact","evidence","patch","patch_sha256","decision","objections","verified_evidence","revised_patch","revised_patch_sha256","approved_candidate_patch_sha256","candidate_patch_sha256","needed_files","tests_to_run","validation_gaps","consensus","source_snapshot_sha256") if k in proposal}
    transport_text=json.dumps(transport,ensure_ascii=False,separators=(",",":"))
    limit=int(os.environ.get("TRANSPORT_MAX_CHARS","45000"))
    if len(transport_text)>limit:
        # Preserve core review data; never truncate JSON mid-token.
        transport["diagnosis"]=str(transport.get("diagnosis",""))[:8000]
        transport["evidence"]=(transport.get("evidence") or [])[:12]
        transport["patch"]=str(transport.get("patch",""))[:18000]
        transport["objections"]=(transport.get("objections") or [])[:12]
        transport_text=json.dumps(transport,ensure_ascii=False,separators=(",",":"))
    (WORK/"transport.json").write_text(json.dumps(transport,ensure_ascii=False,indent=2),encoding="utf-8")
    emit("engine_complete",output=str(OUTPUT),transport_chars=len(transport_text),decision=proposal.get("decision",proposal.get("status")))
    print("::group::COMPACT_AGENT_RESULT")
    print("AGENT_RESULT="+transport_text,flush=True)
    print("::endgroup::")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}",file=sys.stderr,flush=True)
        raise
