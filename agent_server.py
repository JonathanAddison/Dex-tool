import json, os, re, subprocess
import requests
from flask import Flask, request, Response, stream_with_context, jsonify
from flask_cors import CORS
from tool_registry import ToolRegistry
from builtin_tools import register_builtin

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
REPO_ROOT = os.getcwd()

reg = ToolRegistry(REPO_ROOT, max_native=16)
register_builtin(reg, REPO_ROOT)
reg.load_all_skills(os.path.join(REPO_ROOT, "skills"))
reg.load_all_plugins(os.path.join(REPO_ROOT, "plugins"))

SYSTEM_PROMPT = """You are a Claude-level autonomous agent with 69 tools organized into skills and plugins.
You have 16 native tool slots. Use list_skills() to see available skills, then load_skill(name) to activate its tools.

RULES:
1. NEVER ask for permission. Investigate autonomously.
2. Start broad: list_files(), git_log(), glob_search().
3. If you need specialized tools (binary, code analysis), first load the relevant skill.
4. Prefer dedicated tools over bash when possible.
5. Cite exact paths and line numbers.
6. Respond in the user's language (Persian → Persian)."""

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "total_tools": len(reg.tools),
        "skills": list(reg.skills.keys()),
        "plugins": list(reg.plugins.keys()),
        "active_skill": reg.active_skill,
        "native_slots": reg.max_native,
    })

@app.route("/v1/chat/completions", methods=["POST"])
def proxy_chat():
    data = request.json or {}
    messages = data.get("messages", [])
    if not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    def sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def generate():
        try:
            for _ in range(15):
                active_schemas = reg.get_active_schemas()
                skill_prompt = reg.get_skill_prompt()

                cur_msgs = list(messages)
                if skill_prompt and not any(m.get("content") == skill_prompt
                                            for m in cur_msgs if m.get("role")=="system"):
                    cur_msgs.insert(1, {"role":"system","content":skill_prompt})

                payload = {
                    "model": "ministral",
                    "messages": cur_msgs,
                    "tools": active_schemas,
                    "tool_choice": "auto",
                    "stream": False,
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "parallel_tool_calls": True,
                }
                r = requests.post(LLAMA_URL, json=payload, timeout=600)
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]

                raw = msg.get("content", "") or ""
                th = re.search(r'\[THINK\](.*?)\[/THINK\]', raw, re.DOTALL)
                if th:
                    yield sse({"choices":[{"delta":{"reasoning_content": th.group(1).strip()}}]})

                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    final = re.sub(r'\[THINK\].*?\[/THINK\]', '', raw, flags=re.DOTALL).strip()
                    if final:
                        yield sse({"choices":[{"delta":{"content": final}}]})
                    yield sse({"choices":[{"delta":{}, "finish_reason":"stop"}]})
                    yield "data: [DONE]\n\n"
                    return

                messages.append({"role":"assistant","content":raw,"tool_calls":tool_calls})
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    try: args = json.loads(tc["function"].get("arguments","{}"))
                    except: args = {}
                    yield sse({"choices":[{"delta":{"tool_call":{"name":fn,"arguments":args}}}]})
                    out = reg.execute(fn, args)
                    messages.append({"role":"tool","tool_call_id":tc["id"],"content":out[:10000]})

            yield sse({"choices":[{"delta":{"content":"⚠️ حداکثر ۱۵ مرحله."}}]})
            yield sse({"choices":[{"delta":{},"finish_reason":"stop"}]})
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield sse({"choices":[{"delta":{"content":f"❌ خطا: {e}"}}]})
            yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
