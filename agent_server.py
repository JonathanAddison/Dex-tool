import json, os, subprocess
import requests
from flask import Flask, request, Response, stream_with_context, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
REPO_ROOT = os.getcwd()

# ---------------- TOOLS ----------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "لیست فایل‌های یک مسیر در ریپو را برمی‌گرداند. اگر مسیر خالی باشد، ریشه ریپو را لیست می‌کند.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "مسیر نسبی، مثلا src/ یا خالی برای ریشه"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "خواندن محتوای یک فایل از ریپو. مسیر را نسبت به ریشه ریپو وارد کن.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "مسیر فایل، مثلا src/menu.cpp"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "جستجوی یک عبارت یا کلمه کلیدی در تمام فایل‌های ریپو (به‌جز باینری‌ها و مدل‌ها).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "عبارت مورد جستجو"}
                },
                "required": ["query"]
            }
        }
    }
]

def safe_path(path):
    """جلوگیری از path traversal"""
    if not path:
        return REPO_ROOT
    full = os.path.realpath(os.path.join(REPO_ROOT, path))
    if not full.startswith(REPO_ROOT):
        return None
    return full

def execute_tool(name, args):
    try:
        if name == "list_files":
            path = args.get("path", "")
            full = safe_path(path)
            if full is None:
                return "خطا: مسیر غیرمجاز."
            if not os.path.exists(full):
                return f"خطا: مسیر '{path}' وجود ندارد."
            items = []
            for entry in sorted(os.listdir(full)):
                if entry.startswith(".git") or entry in ("model", "llama-bin"):
                    continue
                p = os.path.join(full, entry)
                items.append(entry + ("/" if os.path.isdir(p) else ""))
            return "\n".join(items[:100]) or "(خالی)"

        if name == "read_file":
            path = args.get("path", "")
            full = safe_path(path)
            if full is None:
                return "خطا: مسیر غیرمجاز."
            if not os.path.isfile(full):
                return f"خطا: فایل '{path}' پیدا نشد."
            if os.path.getsize(full) > 200_000:
                return f"خطا: فایل بزرگ‌تر از ۲۰۰KB است ({os.path.getsize(full)} bytes)."
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(15000) or "(فایل خالی)"

        if name == "search_code":
            query = args.get("query", "").strip()
            if not query:
                return "خطا: عبارت جستجو خالی است."
            result = subprocess.run(
                ["grep", "-rIl", "-E",
                 "--include=*.cpp", "--include=*.h", "--include=*.hpp",
                 "--include=*.js", "--include=*.py", "--include=*.md",
                 "--include=*.yml", "--include=*.yaml", "--include=*.json",
                 "--include=*.txt", "--include=*.gradle", "--include=*.xml",
                 query, "."],
                capture_output=True, text=True, timeout=15, cwd=REPO_ROOT
            )
            files = [f.lstrip("./") for f in result.stdout.strip().split("\n") if f]
            if not files:
                return f"هیچ فایلی حاوی '{query}' پیدا نشد."
            return "فایل‌های یافت‌شده:\n" + "\n".join(files[:15])
    except subprocess.TimeoutExpired:
        return "خطا: جستجو زمان‌بر شد."
    except Exception as e:
        return f"خطا در اجرای ابزار: {str(e)}"
    return f"ابزار ناشناخته: {name}"

# ---------------- ROUTES ----------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/v1/chat/completions", methods=["POST"])
def proxy_chat():
    data = request.json or {}
    messages = data.get("messages", [])
    effort = data.get("reasoning_effort", "medium")

    # اضافه کردن دستور سیستمی برای استفاده از ابزارها
    if not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {
            "role": "system",
            "content": (
                "You are an expert reverse engineer and code analyst for the Dex-tool repository. "
                "You have tools: list_files, read_file, search_code. "
                "ALWAYS use tools to inspect the real code before answering. "
                "When the user asks about a file or bug, first search or list files, then read relevant ones. "
                "Base every claim on actual file contents you have read."
            )
        })

    def run_llama(msgs):
        payload = {
            "model": "ministral",
            "messages": msgs,
            "tools": TOOLS,
            "tool_choice": "auto",
            "stream": False,
            "reasoning_effort": effort,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        r = requests.post(LLAMA_URL, json=payload, timeout=600)
        r.raise_for_status()
        return r.json()

    def sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def generate():
        try:
            max_rounds = 6
            for _ in range(max_rounds):
                result = run_llama(messages)
                choice = result["choices"][0]
                msg = choice["message"]

                # ارسال افکار (reasoning_content)
                reasoning = msg.get("reasoning_content", "")
                if reasoning:
                    yield sse({"choices": [{"delta": {"reasoning_content": reasoning}}]})

                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    # جواب نهایی
                    content = msg.get("content", "") or ""
                    if content:
                        yield sse({"choices": [{"delta": {"content": content}}]})
                    yield sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                    yield "data: [DONE]\n\n"
                    return

                # پیام مدل با tool_calls را اضافه کن
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content", "") or "",
                    "tool_calls": tool_calls,
                })

                # هر ابزار را اجرا کن و نتیجه را بفرست
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments", "{}"))
                    except Exception:
                        args = {}
                    # به کاربر نشون بده که چه ابزاری صدا زده شد
                    yield sse({"choices": [{"delta": {"tool_call": {"name": fn, "arguments": args}}}]})
                    output = execute_tool(fn, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": output,
                    })
            # اگر از max_rounds گذشت
            yield sse({"choices": [{"delta": {"content": "⚠️ حداکثر تعداد مراحل ابزار reached."}}]})
            yield sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]\n\n"

        except requests.exceptions.RequestException as e:
            yield sse({"choices": [{"delta": {"content": f"❌ خطا در ارتباط با llama-server: {e}"}}]})
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield sse({"choices": [{"delta": {"content": f"❌ خطای سرور: {e}"}}]})
            yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
