import json, os, subprocess, requests
from flask import Flask, request, Response, stream_with_context

app = Flask(__name__)
LLAMA_URL = "http://localhost:8080/v1/chat/completions"

# --- تعریف ابزارهایی که مدل می‌تونه استفاده کنه ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "خواندن محتوای یک فایل از ریپو. مسیر فایل را نسبت به ریشه ریپو وارد کن.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "مسیر فایل، مثلا src/menu.cpp"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "جستجوی یک عبارت یا کلمه کلیدی در تمام فایل‌های ریپو.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "عبارت مورد جستجو"}},
                "required": ["query"]
            }
        }
    }
]

def execute_tool(name, args):
    """اجرای واقعی ابزار روی فایل‌سیستم"""
    try:
        if name == "read_file":
            path = args.get("path", "")
            # جلوگیری از path traversal
            if ".." in path or path.startswith("/"):
                return "خطا: دسترسی به این مسیر مجاز نیست."
            if not os.path.exists(path):
                return f"خطا: فایل '{path}' پیدا نشد."
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(15000) # محدودیت حجم برای context
            return content
        elif name == "search_code":
            query = args.get("query", "")
            if not query:
                return "خطا: عبارت جستجو خالی است."
            # استفاده از grep برای جستجوی سریع
            result = subprocess.run(["grep", "-rIl", "--include=*.{cpp,h,js,py,yml,yaml,md}", query, "."], 
                                    capture_output=True, text=True, timeout=10)
            files = result.stdout.strip().split("\n")
            files = [f for f in files if f] # حذف خطوط خالی
            if not files:
                return f"هیچ فایلی حاوی '{query}' پیدا نشد."
            return "فایل‌های یافت شده:\n" + "\n".join(files[:10]) # حداکثر ۱۰ فایل
    except Exception as e:
        return f"خطا در اجرای ابزار: {str(e)}"
    return "ابزار ناشناخته."

@app.route('/v1/chat/completions', methods=['POST'])
def proxy_chat():
    data = request.json
    messages = data.get("messages", [])
    reasoning_effort = data.get("reasoning_effort", "medium") # پارامتر جدید از فرانت
    
    # ۱. ارسال درخواست اولیه به llama-server همراه با ابزارها
    payload = {
        "model": "ministral",
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": False, # برای سادگی، اول غیر استریم می‌گیریم
        "reasoning_effort": reasoning_effort, # کنترل عمق فکر
        "max_tokens": 4096
    }
    
    try:
        resp = requests.post(LLAMA_URL, json=payload, timeout=300)
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        return Response(f"data: {json.dumps({'error': str(e)})}\n\n", mimetype='text/event-stream')

    choice = result["choices"][0]
    message = choice["message"]
    
    # ۲. اگر مدل درخواست ابزار داد، اجرا کن و دوباره بهش بفرست
    if message.get("tool_calls"):
        messages.append(message) # پیام مدل با tool_calls
        for tool_call in message["tool_calls"]:
            func = tool_call["function"]
            tool_result = execute_tool(func["name"], json.loads(func["arguments"]))
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": tool_result
            })
        
        # ۳. ارسال نتیجه ابزار به مدل برای گرفتن جواب نهایی
        payload["messages"] = messages
        payload["tools"] = TOOLS # هنوز ابزارها رو نگه می‌داریم در صورت نیاز به فراخوانی چندگانه
        resp2 = requests.post(LLAMA_URL, json=payload, timeout=300)
        final_result = resp2.json()
        final_message = final_result["choices"][0]["message"]
    else:
        final_message = message

    # ۴. تبدیل خروجی نهایی به فرمت استریم SSE برای فرانت‌اند
    def generate():
        # ارسال reasoning_content (اگر وجود داشته باشه)
        reasoning = final_message.get("reasoning_content", "")
        if reasoning:
            yield f"data: {json.dumps({'choices':[{'delta':{'reasoning_content': reasoning}}]})}\n\n"
        # ارسال محتوای اصلی
        content = final_message.get("content", "")
        if content:
            yield f"data: {json.dumps({'choices':[{'delta':{'content': content}}]})}\n\n"
        # ارسال سیگنال پایان
        yield f"data: {json.dumps({'choices':[{'delta':{}, 'finish_reason':'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
