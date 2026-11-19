# scripts/peer_consensus.py
import subprocess
import os
import json
import sys
import re

MODEL_PATH = os.environ.get("MODEL_PATH")
AGENT_NAME = os.environ.get("AGENT_NAME")
ROUND = int(os.environ.get("ROUND", "1"))
TASK_PROMPT = os.environ.get("TASK_PROMPT")
MY_LAST_OUTPUT = os.environ.get("MY_LAST_OUTPUT", "")
PEER_LAST_OUTPUT = os.environ.get("PEER_LAST_OUTPUT", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE")
CODE_CONTEXT_FILE = os.environ.get("CODE_CONTEXT_FILE", "/tmp/code_context.txt")

def load_code_context():
    try:
        with open(CODE_CONTEXT_FILE, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        return "(No code context available)"

def clean_output(text):
    text = re.sub(r'\n*>?\s*EOF\s+by\s+(the\s+)?user.*$', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<\|im_end\|>.*$', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|endoftext\|>.*$', '', text, flags=re.DOTALL)
    text = re.sub(r'\n*(Human|User|Assistant)\s*:.*$', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()

def run_llm(prompt, model_path):
    cmd = [
        "./llama.cpp/build/bin/llama-completion",
        "-m", model_path,
        "-p", prompt,
        "-n", "4096",
        "--temp", "0.2",
        "--top-p", "0.9",
        "-c", "32768",          # حداکثر context برای کد
        "--no-display-prompt",
        "-t", "4",              # استفاده از هر ۴ هسته
        "--log-disable",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10800)  # 3 ساعت
        return clean_output(result.stdout)
    except subprocess.TimeoutExpired:
        return "[ERROR: Model timed out]"
    except subprocess.CalledProcessError as e:
        return f"[ERROR: {e.stderr[:500]}]"

def check_consensus(text, rnd):
    if rnd < 2:
        return False            # دور ۱ هرگز اجماع نداره
    return "[CONSENSUS]" in text.upper()

def build_prompt(agent, task, my_prev, peer_prev, rnd, code):
    base = f"""You are {agent.upper()}, one of two expert AI agents collaborating as EQUALS on the Dex-tool project.
Dex-tool is a runtime Android app inspector using Frida (JavaScript) and ImGui (C++).

YOUR TASK: {task}

=== ACTUAL PROJECT SOURCE CODE ===
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
-------------------------------------------------------

--- YOUR PEER'S PREVIOUS OUTPUT (Round {rnd-1}) ---
{peer_prev}
-------------------------------------------------------

AS A PEER, DO THIS:
1. Validate your peer's claimed bug against the ACTUAL source code. Does it really exist?
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
    prompt = build_prompt(AGENT_NAME, TASK_PROMPT, MY_LAST_OUTPUT, PEER_LAST_OUTPUT, ROUND, code)
    print(f"🚀 {AGENT_NAME.upper()} Round {ROUND} | prompt={len(prompt)} chars | code={len(code)} chars")

    output = run_llm(prompt, MODEL_PATH)
    consensus = check_consensus(output, ROUND)

    result = {
        "agent": AGENT_NAME,
        "round": ROUND,
        "output": output,
        "consensus": consensus,
    }
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ {AGENT_NAME.upper()} finished. Consensus={consensus}")
    if "[ERROR" in output:
        print(f"⚠️ Error in output: {output[:200]}")
        sys.exit(1)

if __name__ == "__main__":
    main()
