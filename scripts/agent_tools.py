#!/usr/bin/env python3
"""Sandboxed, read-only tool API exposed to the two local model agents.
No arbitrary shell, network, write, delete, or secret-reading tool is available.
"""
from __future__ import annotations
import fnmatch, json, os, pathlib, re, shutil, subprocess
ROOT=pathlib.Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
MANIFEST=pathlib.Path(os.environ.get("MANIFEST_FILE", ".agent-work/source/manifest.json"))
MAX_RESULT=18000


def _files():
    try: return [x["path"] for x in json.loads(MANIFEST.read_text(encoding="utf-8")).get("files",[])]
    except Exception: return []

def _safe_path(raw: str) -> tuple[pathlib.Path,str]:
    rel=str(raw).replace("\\","/").strip()
    p=(ROOT/rel).resolve()
    if p != ROOT and ROOT not in p.parents: raise ValueError("path escapes repository root")
    if rel not in _files(): raise ValueError("path is not in the safe source manifest")
    return p,rel

def _clip(value):
    text=str(value)
    return text if len(text)<=MAX_RESULT else text[:MAX_RESULT]+"\n...[result clipped; request a narrower range]"

def execute(name: str,args: dict) -> dict:
    if not isinstance(args,dict): return {"ok":False,"error":"args must be an object"}
    try:
        files=_files()
        if name=="list_files":
            pattern=str(args.get("pattern","*"))
            found=[p for p in files if fnmatch.fnmatch(p,pattern)][:300]
            return {"ok":True,"count":len(found),"files":found}
        if name=="read_file":
            p,rel=_safe_path(args.get("path","")); lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
            start=max(1,int(args.get("start_line",1))); end=min(len(lines),int(args.get("end_line",start+180)))
            if end<start: raise ValueError("end_line must be >= start_line")
            body="\n".join(f"{i}: {lines[i-1]}" for i in range(start,end+1))
            return {"ok":True,"path":rel,"start_line":start,"end_line":end,"total_lines":len(lines),"content":_clip(body)}
        if name=="search_code":
            query=str(args.get("query",""))
            if not query: raise ValueError("query is required")
            pattern=str(args.get("pattern","*")); limit=min(100,max(1,int(args.get("limit",40))))
            hits=[]
            for rel in files:
                if not fnmatch.fnmatch(rel,pattern): continue
                try: lines=(ROOT/rel).read_text(encoding="utf-8",errors="replace").splitlines()
                except Exception: continue
                for n,line in enumerate(lines,1):
                    if query.casefold() in line.casefold():
                        hits.append({"file":rel,"line":n,"text":line[:500]})
                        if len(hits)>=limit: return {"ok":True,"query":query,"matches":hits,"truncated":True}
            return {"ok":True,"query":query,"matches":hits,"truncated":False}
        if name=="find_symbol":
            symbol=str(args.get("symbol",""))
            if not symbol: raise ValueError("symbol is required")
            rx=re.compile(r"(?:class|struct|enum|def|function|namespace|using|typedef)\s+"+re.escape(symbol)+r"\b")
            hits=[]
            for rel in files:
                if not fnmatch.fnmatch(rel,str(args.get("pattern","*"))): continue
                try: lines=(ROOT/rel).read_text(encoding="utf-8",errors="replace").splitlines()
                except Exception: continue
                for n,line in enumerate(lines,1):
                    if rx.search(line):
                        hits.append({"file":rel,"line":n,"text":line[:500]})
                        if len(hits)>=80:return {"ok":True,"symbol":symbol,"matches":hits,"truncated":True}
            return {"ok":True,"symbol":symbol,"matches":hits,"truncated":False}
        if name=="git_history":
            rel=str(args.get("path",""));
            if rel: _safe_path(rel)
            limit=min(20,max(1,int(args.get("limit",8))))
            cmd=["git","log",f"-{limit}","--date=short","--pretty=format:%h %ad %s"]
            if rel: cmd += ["--",rel]
            p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=15)
            return {"ok":p.returncode==0,"history":_clip(p.stdout),"error":p.stderr[:1000]}
        if name=="tool_inventory":
            p=ROOT/".agent-work"/"tool_inventory.json"
            return {"ok":True,"inventory":json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}}
        if name=="sandbox_run":
            # Deliberately fixed suite enum; never accept a shell command from the model.
            suite=str(args.get("suite","auto"))
            allowed={"auto","native","python","cmake","node","go","rust","shell"}
            if suite not in allowed: raise ValueError(f"suite must be one of {sorted(allowed)}")
            patch=args.get("patch","")
            if not isinstance(patch,str): raise ValueError("patch must be a unified-diff string")
            if len(patch)>180000: raise ValueError("patch exceeds 180000 characters")
            runner=ROOT/"scripts"/"sandbox_runner.py"
            proc=subprocess.run([os.environ.get("PYTHON","python3"),str(runner),"--suite",suite,"--patch-stdin"],cwd=ROOT,input=patch,text=True,capture_output=True,timeout=1250)
            try: payload=json.loads(proc.stdout.strip().splitlines()[-1])
            except Exception: payload={"ok":False,"raw_stdout":proc.stdout[-12000:],"stderr":proc.stderr[-8000:],"returncode":proc.returncode}
            payload.setdefault("returncode",proc.returncode)
            return payload
        return {"ok":False,"error":f"unknown tool: {name}","available_tools":["list_files","read_file","search_code","find_symbol","git_history","tool_inventory","sandbox_run"]}
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}: {e}"}
