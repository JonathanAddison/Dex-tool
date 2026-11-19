# scripts/peer_consensus.py
import subprocess
import os
import json
import sys
import re
import tempfile

MODEL_PATH = os.environ.get("MODEL_PATH")
AGENT_NAME = os.environ.get("AGENT_NAME")
ROUND = int(os.environ.get("ROUND", "1"))
TASK_PROMPT = os.environ.get("TASK_PROMPT")
MY_LAST_OUTPUT = os.environ.get("MY_LAST_OUTPUT", "")
PEER_LAST_OUTPUT = os.environ.get("PEER_LAST_OUTPUT", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE")
CODE_CONTEXT_FILE = os.environ.get("CODE_CONTEXT_FILE", "/tmp/code_context.txt")

# حداکثر context قابل استفاده (بر اساس مدل)
MAX_CTX = {
    "qwen": 131072,  # Qwen2.5-Coder-7B -> 128K
    "glm": 131072,   # GLM-4-9B -> 128K
}


def load_code_context():
    try:
        with open(CODE_CONTEXT_FILE, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        return "(No code context available)"


def extract_file_requests(text):
    """مدل می‌تونه [NEED_FILE: path] بنویسه تا فایل کامل رو دریافت کنه"""
    return re.findall(r'\[NEED_FILE:\s*([^\]]+)\]', text)


def fetch_requested_files(requests):
    """فایل‌های درخواستی مدل رو کامل می‌خونه"""
    chunks = []
    for path in requests:
        path = path.strip()
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                chunks.append(f"--- FILE: {path} ---\n{f.read()}\n")
        except Exception as e:
            chunks.append(f"--- FILE: {path} (ERROR: {e}) ---\n")
    return "\n".join(chunks)


def clean_output(text):
    text = re.sub(r'\n*>?\s*EOF\s+by\s+(the\s+)?user.*$', '', text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<\|im_end\|>.*$', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|endoftext\|>.*$', '', text, flags=re.DOTALL)
    text = re.sub(r'\n*(Human|User|Assistant)\s*:.*$', '', text,
                  flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def run_llm(prompt, model_path, agent):
    ctx = MAX_CTX.get(agent, 32768)

    # نوشتن prompt در یک فایل موقت برای دور زدن محدودیت طول آرگومان
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                     encoding='utf-8') as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        cmd = [
            "./llama.cpp/build/bin/llama-completion",
            "-m", model_path,
            "-f", prompt_file,             # به جای -p از -f استفاده می‌کنیم
            "-n", "4096",
            "--temp", "0.2",
            "--top-p", "0.9",
            "-c", str(ctx),
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "--flash-attn", "on",                # بهینه‌سازی KV cache و پشتیبانی q8_0
            "--no-display-prompt",
            "-t", "4",
            "--log-disable",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                check=True, timeout=10800)
        return clean_output(result.stdout)
    except subprocess.TimeoutExpired:
        return "[ERROR: Model timed out]"
    except subprocess.CalledProcessError as e:
        return f"[ERROR: {e.stderr[:500]}]"
    finally:
        try:
            os.unlink(prompt_file)
        except OSError:
            pass


def check_consensus(text, rnd):
    if rnd < 2:
        return False
    return "[CONSENSUS]" in text.upper()


def build_prompt(agent, task, my_prev, peer_prev, rnd, code):
    base = f"""You are {agent.upper()}, one of two expert AI agents collaborating as EQUALS on the Dex-tool project.
Dex-tool is a runtime Android app inspector using Frida (JavaScript) and ImGui (C++).

YOUR TASK: {task}

=== FULL PROJECT SOURCE CODE (all text files) ===
{code}
=== END OF SOURCE CODE ===

This is ROUND {rnd} of a peer-to-peer consensus loop."""

    if rnd == 1:
        return f"""{base}

This is the FIRST round. Study the source code above carefully.

CRITICAL RULES:
- Base your analysis ONLY on the actual code above. Do NOT invent bugs or APIs.
- If you cannot find a real bug, say so explicitly.
- Do NOT output [CONSENSUS] in this round.

Provide:
### 1. Concrete Bugs Found (file, line/function, severity)
### 2. Most Critical Bug Chosen (why, real impact)
### 3. Proposed Fix (real code only, no hallucinated APIs)
"""

    return f"""{base}

--- YOUR PREVIOUS OUTPUT (Round {rnd-1}) ---
{my_prev}

--- YOUR PEER'S PREVIOUS OUTPUT (Round {rnd-1}) ---
{peer_prev}

AS A PEER, DO THIS:
1. Validate your peer's claimed bug against the ACTUAL source code above. Does it really exist?
2. Check your peer's code: are the APIs real? Any memory/correctness issues?
3. Re-examine your own answer with the same scrutiny.
4. Produce a REVISED answer: keep what's correct, remove hallucinated APIs/invented bugs, fix the REAL critical bug with REAL working code.
5. ONLY if ALL hallucinated content is removed and the fix is genuinely correct, end with [CONSENSUS] on its own line. Otherwise, do NOT use that tag.
"""


def main():
    if not MODEL_PATH or not AGENT_NAME:
        print("Missing MODEL_PATH or AGENT_NAME")
        sys.exit(1)

    code = load_code_context()
    prompt = build_prompt(AGENT_NAME, TASK_PROMPT, MY_LAST_OUTPUT,
                          PEER_LAST_OUTPUT, ROUND, code)
    print(f"🚀 {AGENT_NAME.upper()} Round {ROUND} | prompt={len(prompt)} chars | "
          f"code={len(code)} chars")

    output = run_llm(prompt, MODEL_PATH, AGENT_NAME)
    consensus = check_consensus(output, ROUND)

    # درخواست فایل: اگه مدل فایلی خواست، توی دور بعد تزریق می‌شود
    requested = extract_file_requests(output)
    if requested:
        print(f"📎 Model requested files: {requested}")

    result = {
        "agent": AGENT_NAME,
        "round": ROUND,
        "output": output,
        "consensus": consensus,
        "requested_files": requested,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ {AGENT_NAME.upper()} finished. Consensus={consensus}")

    if "[ERROR" in output:
        sys.exit(1)


if __name__ == "__main__":
    main()
