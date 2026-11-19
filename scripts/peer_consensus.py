# scripts/peer_consensus.py
import subprocess
import os
import json
import sys
import re

# --- پیکربندی ---
MODEL_PATH = os.environ.get("MODEL_PATH")
AGENT_NAME = os.environ.get("AGENT_NAME")
ROUND = int(os.environ.get("ROUND", "1"))
TASK_PROMPT = os.environ.get("TASK_PROMPT")
MY_LAST_OUTPUT = os.environ.get("MY_LAST_OUTPUT", "")
PEER_LAST_OUTPUT = os.environ.get("PEER_LAST_OUTPUT", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE")
CODE_CONTEXT_FILE = os.environ.get("CODE_CONTEXT_FILE", "/tmp/code_context.txt")
MAX_CODE_BYTES = 18000  # ~4.5k tokens، جا برای prompt و output هم می‌مونه


def load_code_context():
    """کد واقعی پروژه رو برای مدل بارگذاری می‌کنه"""
    try:
        with open(CODE_CONTEXT_FILE, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read(MAX_CODE_BYTES)
        if not data.strip():
            return "(No source files found in repository)"
        return data
    except FileNotFoundError:
        return "(No code context provided — you must work from the task description only)"


def clean_output(text):
    """حذف artifactهای قالب چت و متا-کامنت‌ها"""
    # حذف "EOF by user" و مشابه
    text = re.sub(r'\n*>?\s*EOF\s+by\s+(the\s+)?user.*$', '', text, flags=re.IGNORECASE)
    # حذف تگ‌های im_end
    text = re.sub(r'<\|im_end\|>.*$', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|endoftext\|>.*$', '', text, flags=re.DOTALL)
    # حذف "Human:" یا "User:" انتهایی
    text = re.sub(r'\n*(Human|User|Assistant)\s*:.*$', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def run_llm(prompt, model_path):
    """اجرای مدل با llama-completion"""
    cmd = [
        "./llama.cpp/build/bin/llama-completion",
        "-m", model_path,
        "-p", prompt,
        "-n", "3072",
        "--temp", "0.2",
        "--top-p", "0.9",
        "-c", "16384",       # فضای بیشتر برای کد context
        "--no-display-prompt",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=1800)
        return clean_output(result.stdout)
    except subprocess.TimeoutExpired:
        return "[ERROR: Model timed out after 30 minutes]"
    except subprocess.CalledProcessError as e:
        return f"[ERROR: {e.stderr}]"


def check_consensus(text, rnd):
    """دور ۱ هرگز اجماع نمی‌تونه داشته باشه — باید اول بازبینی همتا انجام بشه"""
    if rnd < 2:
        return False
    return "[CONSENSUS]" in text.upper()


def build_peer_prompt(agent, task, my_prev, peer_prev, rnd, code_context):
    base = f"""You are {agent.upper()}, one of two expert AI agents collaborating as EQUALS on the Dex-tool project.
Dex-tool is a runtime Android app inspector using Frida (JavaScript) and ImGui (C++).

YOUR TASK: {task}

=== ACTUAL PROJECT SOURCE CODE (from the repository) ===
{code_context}
=== END OF SOURCE CODE ===

This is ROUND {rnd} of a peer-to-peer consensus loop."""

    if rnd == 1:
        return f"""{base}

This is the FIRST round. Study the source code above carefully.

CRITICAL RULES for Round 1:
- You MUST base your analysis on the actual code above, NOT on hypothetical scenarios.
- If you cannot find a real bug in the provided code, say so explicitly — do NOT invent one.
- Do NOT output the tag [CONSENSUS] in this round. Your peer has not reviewed your work yet.

Provide your analysis in this exact structure:

### 1. Concrete Bugs Found (in the real code)
For each bug: file path, line/function, what's wrong, severity (Critical/High/Medium).

### 2. Most Critical Bug Chosen
Why this one? What is its real impact on the actual Dex-tool codebase?

### 3. Proposed Fix
Provide the ACTUAL corrected code as a diff or complete file. Use only real APIs (real Frida API, real ImGui API, real C++ std). Do NOT hallucinate function names.
"""

    return f"""{base}

--- YOUR PREVIOUS OUTPUT (from round {rnd-1}) ---
{my_prev}
-------------------------------------------------------

--- YOUR PEER'S PREVIOUS OUTPUT (from round {rnd-1}) ---
{peer_prev}
-------------------------------------------------------

NOW, AS A PEER, DO THIS:
1. Validate your peer's claimed bug against the ACTUAL source code above. Does that bug really exist? If not, say so.
2. Check your peer's proposed code: are the APIs real? Are there memory/correctness issues?
3. Re-examine your own previous answer with the same scrutiny.
4. Produce a REVISED answer that:
   - Keeps what is actually correct in both answers
   - Removes any hallucinated APIs or invented bugs
   - Fixes the REAL most critical bug with REAL working code
5. ONLY if you are confident that ALL hallucinated content has been removed and the fix is genuinely correct, end with [CONSENSUS] on its own line. Otherwise do NOT use that tag.
"""


def main():
    if not MODEL_PATH or not AGENT_NAME:
        print("❌ Missing MODEL_PATH or AGENT_NAME")
        sys.exit(1)

    code_context = load_code_context()
    print(f"📄 Code context loaded: {len(code_context)} chars")

    prompt = build_peer_prompt(AGENT_NAME, TASK_PROMPT, MY_LAST_OUTPUT, PEER_LAST_OUTPUT, ROUND, code_context)
    print(f"🚀 {AGENT_NAME.upper()} running round {ROUND}... (prompt {len(prompt)} chars)")
    output = run_llm(prompt, MODEL_PATH)
    consensus = check_consensus(output, ROUND)

    result = {
        "agent": AGENT_NAME,
        "round": ROUND,
        "output": output,
        "consensus": consensus
    }
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ {AGENT_NAME.upper()} finished round {ROUND}. Consensus: {consensus}")
    if "[ERROR" in output:
        print(f"⚠️ Output contains error: {output[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
