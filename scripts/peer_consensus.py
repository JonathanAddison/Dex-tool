# scripts/peer_consensus.py
import subprocess
import os
import json
import sys
from pathlib import Path

# --- پیکربندی از طریق متغیرهای محیطی ---
MODEL_PATH = os.environ.get("MODEL_PATH")
AGENT_NAME = os.environ.get("AGENT_NAME")  # "qwen" یا "glm"
ROUND = int(os.environ.get("ROUND", "1"))
TASK_PROMPT = os.environ.get("TASK_PROMPT")
MY_LAST_OUTPUT = os.environ.get("MY_LAST_OUTPUT", "")
PEER_LAST_OUTPUT = os.environ.get("PEER_LAST_OUTPUT", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE")

def run_llm(prompt, model_path):
    """اجرای مدل با llama.cpp و فعال‌سازی حداکثر سطح فکر"""
    cmd = [
        "./llama.cpp/llama-cli",
        "-m", model_path,
        "-p", prompt,
        "-n", "4096",
        "--temp", "0.2",
        "--top-p", "0.9",
        "--ctx-size", "8192",
        "--reasoning", "on",
        "--reasoning-effort", "max",
        "--reasoning-format", "deepseek",
        "--no-display-prompt"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=1800)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "[ERROR: Model timed out after 30 minutes]"
    except subprocess.CalledProcessError as e:
        return f"[ERROR: {e.stderr}]"

def check_consensus(text):
    """بررسی وجود سیگنال اجماع در خروجی"""
    return "[CONSENSUS]" in text.upper()

def build_peer_prompt(agent, task, my_prev, peer_prev, rnd):
    """ساخت پرامپت همتا برای هر دور"""
    base = f"""You are {agent.upper()}, one of two expert AI agents collaborating as EQUALS on the Dex-tool project.
The project is a runtime Android app inspector using Frida (JavaScript) and ImGui (C++).

YOUR TASK: {task}

This is ROUND {rnd} of a peer-to-peer consensus loop."""

    if rnd == 1:
        return f"""{base}

There is no previous output yet. This is the first round.
Perform the task independently and thoroughly. Provide your complete analysis and any code changes.
If you believe the task is fully complete and correct, end your response with the tag [CONSENSUS].
"""

    return f"""{base}

--- YOUR PREVIOUS OUTPUT (from round {rnd-1}) ---
{my_prev}
-------------------------------------------------------

--- YOUR PEER'S PREVIOUS OUTPUT (from round {rnd-1}) ---
{peer_prev}
-------------------------------------------------------

NOW, AS A PEER:
1. Critically review your peer's output. Find flaws, missing edge cases, or better approaches.
2. Re-examine your own previous output based on what your peer said or missed.
3. Produce a REVISED output that incorporates the best of both.
4. If you genuinely believe that ALL issues are resolved and both outputs are now equivalent in quality, end with [CONSENSUS]. Otherwise, do NOT use that tag.
"""

def main():
    if not MODEL_PATH or not AGENT_NAME:
        print("❌ Missing MODEL_PATH or AGENT_NAME")
        sys.exit(1)

    prompt = build_peer_prompt(AGENT_NAME, TASK_PROMPT, MY_LAST_OUTPUT, PEER_LAST_OUTPUT, ROUND)
    print(f"🚀 {AGENT_NAME.upper()} running round {ROUND}...")
    output = run_llm(prompt, MODEL_PATH)
    consensus = check_consensus(output)

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
