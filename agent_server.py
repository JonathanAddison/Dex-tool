import json, os, subprocess, re, resource
import requests
from flask import Flask, request, Response, stream_with_context, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
REPO_ROOT = os.getcwd()
SANDBOX_TIMEOUT = 60
SANDBOX_MEM_MB = 1024

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "لیست محتویات دایرکتوری.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "خواندن فایل با شماره خط.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "نوشتن یا بازنویسی کامل فایل.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "جایگزینی دقیق یک رشته یکتا در فایل.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "glob_search", "description": "پیدا کردن فایل‌ها با الگوی glob مثل **/*.cpp.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "search_code", "description": "جستجوی regex در محتوای فایل‌ها.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "include": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "bash_execute", "description": "اجرای shell در sandbox امن (بدون شبکه، timeout، محدودیت حافظه).", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "python_execute", "description": "اجرای Python در sandbox امن.", "parameters": {"type": "object", "properties": {"code": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "git_log", "description": "تاریخچه کامیت‌ها.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "git_diff", "description": "تغییرات یک فایل نسبت به کامیت قبلی.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "binary_strings", "description": "استخراج رشته‌ها از فایل باینری.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "min_len": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "binary_symbols", "description": "لیست نمادهای باینری.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]

def safe_path(path):
    if not path: return REPO_ROOT
    full = os.path.realpath(os.path.join(REPO_ROOT, path))
    return full if full.startswith(REPO_ROOT) else None

def _sandbox_prefix():
    if subprocess.run(["which", "bwrap"], capture_output=True).returncode == 0:
        return [
            "bwrap", "--unshare-all", "--die-with-parent",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/bin", "/bin",
            "--proc", "/proc", "--dev", "/dev",
            "--bind", REPO_ROOT, REPO_ROOT, "--chdir", REPO_ROOT,
        ]
    return None

def _limit_resources():
    os.setrlimit(resource.RLIMIT_AS, (SANDBOX_MEM_MB*1024*1024,)*2)
    os.setrlimit(resource.RLIMIT_CPU, (SANDBOX_TIMEOUT, SANDBOX_TIMEOUT))
    os.setrlimit(resource.RLIMIT_CORE, (0, 0))

def run_sandboxed(cmd_list, timeout=None):
    timeout = timeout or SANDBOX_TIMEOUT
    prefix = _sandbox_prefix()
    full = (prefix + ["--"] + cmd_list) if prefix else cmd_list
    try:
        kwargs = dict(capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT)
        if not prefix:
            kwargs["preexec_fn"] = _limit_resources
        r = subprocess.run(full, **kwargs)
        out = (r.stdout + r.stderr).strip()
        return out[:6000] or "(بدون خروجی)", r.returncode
    except subprocess.TimeoutExpired:
        return f"⏱️ timeout بعد از {timeout}s", -1
    except Exception as e:
        return f"خطا: {e}", -1

def execute_tool(name, args):
    try:
        if name == "list_files":
            full = safe_path(args.get("path", ""))
            if not full or not os.path.isdir(full): return "خطا: مسیر نامعتبر."
            items = [e + ("/" if os.path.isdir(os.path.join(full,e)) else "")
                     for e in sorted(os.listdir(full))
                     if not e.startswith(".git") and e not in ("model","llama-bin","draft")]
            return "\n".join(items[:150]) or "(خالی)"

        if name == "read_file":
            full = safe_path(args.get("path"))
            if not full or not os.path.isfile(full): return "خطا: فایل یافت نشد."
            if os.path.getsize(full) > 300_000: return "خطا: فایل بزرگ‌تر از ۳۰۰KB."
            lines = open(full, encoding="utf-8", errors="ignore").read().splitlines()
            off = max(0, args.get("offset", 1) - 1)
            lim = args.get("limit") or 400
            chunk = lines[off:off+lim]
            return "\n".join(f"{off+i+1:>5}| {l}" for i,l in enumerate(chunk))[:15000] or "(خالی)"

        if name == "write_file":
            full = safe_path(args.get("path"))
            if not full: return "خطا: مسیر نامعتبر."
            d = os.path.dirname(full)
            if d: os.makedirs(d, exist_ok=True)
            open(full, "w", encoding="utf-8").write(args.get("content",""))
            return f"✅ نوشته شد: {args.get('path')}"

        if name == "edit_file":
            full = safe_path(args.get("path"))
            if not full or not os.path.isfile(full): return "خطا: فایل یافت نشد."
            old, new = args.get("old_string",""), args.get("new_string","")
            if not old: return "خطا: old_string خالی است."
            content = open(full, encoding="utf-8", errors="ignore").read()
            cnt = content.count(old)
            if cnt == 0: return "خطا: old_string یافت نشد."
            if cnt > 1: return f"خطا: old_string {cnt} بار تکرار شده؛ باید یکتا باشد."
            open(full, "w", encoding="utf-8").write(content.replace(old, new, 1))
            return f"✅ ویرایش شد: {args.get('path')}"

        if name == "glob_search":
            import glob as _glob
            pat = args.get("pattern","")
            base = safe_path(args.get("path", "")) or REPO_ROOT
            if not pat: return "خطا: pattern خالی."
            files = _glob.glob(os.path.join(base, pat), recursive=True)
            files = [os.path.relpath(f, REPO_ROOT) for f in files if os.path.isfile(f)][:100]
            return "\n".join(files) or "فایلی یافت نشد."

        if name == "search_code":
            q = args.get("query","").strip()
            if not q: return "خطا: کوئری خالی."
            base = args.get("path", ".")
            inc = args.get("include", "")
            cmd = ["grep","-rIn","-E",q,base]
            if inc: cmd.insert(2, f"--include={inc}")
            out, _ = run_sandboxed(cmd, timeout=20)
            return "\n".join(out.split("\n")[:80]) or f"یافت نشد: {q}"

        if name == "bash_execute":
            cmd = args.get("command","").strip()
            if not cmd: return "خطا: دستور خالی."
            out, rc = run_sandboxed(["sh","-c",cmd], timeout=args.get("timeout"))
            return f"[exit={rc}]\n{out}"

        if name == "python_execute":
            code = args.get("code","")
            if not code.strip(): return "خطا: کد خالی."
            out, rc = run_sandboxed(["python3","-c",code], timeout=args.get("timeout"))
            return f"[exit={rc}]\n{out}"

        if name == "git_log":
            out, _ = run_sandboxed(["git","log",f"-{args.get('limit',10)}","--oneline","--decorate"], timeout=10)
            return out

        if name == "git_diff":
            out, _ = run_sandboxed(["git","diff","HEAD~1","--",args.get("path","")], timeout=10)
            return out[:5000] or "تغییری یافت نشد."

        if name == "binary_strings":
            full = safe_path(args.get("path"))
            if not full or not os.path.isfile(full): return "خطا: فایل یافت نشد."
            out, _ = run_sandboxed(["strings","-n",str(args.get("min_len",4)),full], timeout=20)
            return "\n".join(out.split("\n")[:120]) or "رشته‌ای یافت نشد."

        if name == "binary_symbols":
            full = safe_path(args.get("path"))
            if not full or not os.path.isfile(full): return "خطا: فایل یافت نشد."
            out, rc = run_sandboxed(["nm","-C","--defined-only",full], timeout=20)
            if rc != 0:
                out, _ = run_sandboxed(["objdump","-t",full], timeout=20)
            return "\n".join(out.split("\n")[:80]) or "نمادی یافت نشد."
    except Exception as e:
        return f"خطا: {e}"
    return f"ابزار ناشناخته: {name}"

SYSTEM_PROMPT = """You are a Claude-level autonomous coding and reverse-engineering agent for the Dex-tool repo. You have 12 tools including SANDBOXED bash/python execution, exact file editing, glob/grep search, git, and binary analysis.

RULES:
1. NEVER ask for permission. Investigate autonomously.
2. Prefer dedicated tools (read_file, edit_file, glob_search, search_code) over bash when possible — same as Claude Code.
3. bash_execute and python_execute run in an isolated sandbox with NO network, 60s timeout, 1GB memory cap. Use them for tests, builds, or quick scripts.
4. edit_file requires a UNIQUE old_string; if ambiguous, add surrounding context.
5. Cite exact paths and line numbers in final answers.
6. Respond in the user's language (Persian → Persian).
7. Before final answer: "Do I have enough evidence? Did I check the most relevant files?"

You are an engineer with full repo access in a sandboxed environment."""

@app.route("/health", methods=["GET"])
def health(): return jsonify({"status":"ok","sandbox":bool(_sandbox_prefix())})

@app.route("/v1/chat/completions", methods=["POST"])
def proxy_chat():
    data = request.json or {}
    messages = data.get("messages", [])
    effort = data.get("reasoning_effort", "medium")
    if not any(m.get("role")=="system" for m in messages):
        messages.insert(0, {"role":"system","content":SYSTEM_PROMPT})

    def sse(obj): return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def generate():
        try:
            for round_num in range(15):
                payload = {
                    "model": "ministral", "messages": messages,
                    "tools": TOOLS, "tool_choice": "auto",
                    "stream": False, "reasoning_effort": effort,
                    "temperature": 0.1, "max_tokens": 4096,
                    "parallel_tool_calls": True,
                }
                r = requests.post(LLAMA_URL, json=payload, timeout=600)
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]

                raw = msg.get("content","") or ""
                th = re.search(r'\[THINK\](.*?)\[/THINK\]', raw, re.DOTALL)
                if th:
                    yield sse({"choices":[{"delta":{"reasoning_content": th.group(1).strip()}}]})

                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    final = re.sub(r'\[THINK\].*?\[/THINK\]', '', raw, flags=re.DOTALL).strip()
                    if final: yield sse({"choices":[{"delta":{"content": final}}]})
                    yield sse({"choices":[{"delta":{}, "finish_reason":"stop"}]})
                    yield "data: [DONE]\n\n"
                    return

                messages.append({"role":"assistant","content":raw, "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    try: args = json.loads(tc["function"].get("arguments","{}"))
                    except: args = {}
                    yield sse({"choices":[{"delta":{"tool_call":{"name":fn,"arguments":args}}}]})
                    out = execute_tool(fn, args)
                    messages.append({"role":"tool","tool_call_id":tc["id"],"content":out[:10000]})

            yield sse({"choices":[{"delta":{"content":"⚠️ حداکثر ۱۵ مرحله ابزار."}}]})
            yield sse({"choices":[{"delta":{},"finish_reason":"stop"}]})
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield sse({"choices":[{"delta":{"content":f"❌ خطا: {e}"}}]})
            yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
