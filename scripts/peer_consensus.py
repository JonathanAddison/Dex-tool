
import os
import sys
import re
import json
import time
import queue
import codecs
import threading
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_PATH = os.environ.get("MODEL_PATH")
AGENT_NAME = os.environ.get("AGENT_NAME", "unknown").lower()
ROUND = int(os.environ.get("ROUND", "1"))

TASK_PROMPT = os.environ.get(
    "TASK_PROMPT",
    "Find and fix the most critical bug in the project."
)

MY_LAST_OUTPUT = os.environ.get("MY_LAST_OUTPUT", "")
PEER_LAST_OUTPUT = os.environ.get("PEER_LAST_OUTPUT", "")

OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "results/output.json")
CODE_CONTEXT_FILE = os.environ.get(
    "CODE_CONTEXT_FILE",
    "/tmp/code_context.txt"
)

MAX_CTX = {
    "qwen": 131072,
    "glm": 131072,
}

CONTEXT_SIZE = int(
    os.environ.get("CONTEXT_SIZE", MAX_CTX.get(AGENT_NAME, 32768))
)

MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
THREADS = int(os.environ.get("LLM_THREADS", "4"))
TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "10800"))

CHECKPOINT_INTERVAL = 10

LLAMA_BINARY = os.environ.get(
    "LLAMA_BINARY",
    "./llama.cpp/build/bin/llama-completion"
)


# ============================================================
# PATHS
# ============================================================

OUTPUT_PATH = Path(OUTPUT_FILE)
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

BASE_PATH = OUTPUT_PATH.with_suffix("")

TEXT_FILE = Path(str(BASE_PATH) + ".txt")
LOG_FILE = Path(str(BASE_PATH) + ".log")
PARTIAL_FILE = Path(str(BASE_PATH) + ".partial.json")


# ============================================================
# LOGGING
# ============================================================

START_TIME = time.monotonic()
START_DATETIME = datetime.now(timezone.utc).isoformat()

def timestamp():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(message):
    print(
        f"[{timestamp()}] [{AGENT_NAME.upper()}] {message}",
        flush=True
    )


def github_group(title):
    print(f"::group::{title}", flush=True)


def github_endgroup():
    print("::endgroup::", flush=True)


def load_code_context():
    try:
        with open(
            CODE_CONTEXT_FILE,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as f:
            return f.read()

    except FileNotFoundError:
        return "(No code context available)"


# ============================================================
# OUTPUT PROCESSING
# ============================================================

def extract_file_requests(text):
    return re.findall(
        r"\[NEED_FILE:\s*([^\]]+)\]",
        text
    )


def clean_output(text):
    text = re.sub(
        r"\n*>?\s*EOF\s+by\s+(the\s+)?user.*$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    text = re.sub(
        r"<\|im_end\|>.*$",
        "",
        text,
        flags=re.DOTALL
    )

    text = re.sub(
        r"<\|endoftext\|>.*$",
        "",
        text,
        flags=re.DOTALL
    )

    text = re.sub(
        r"\n*(Human|User|Assistant)\s*:.*$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    return text.strip()


def check_consensus(text, rnd):
    if rnd < 2:
        return False

    return "[CONSENSUS]" in text.upper()


# ============================================================
# ATOMIC JSON WRITER
# ============================================================

def save_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent)
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)

    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


# ============================================================
# PARTIAL RESULT CHECKPOINT
# ============================================================

def save_checkpoint(output, status, error=None):
    data = {
        "agent": AGENT_NAME,
        "round": ROUND,
        "status": status,
        "output": output,
        "consensus": check_consensus(output, ROUND),
        "requested_files": extract_file_requests(output),
        "error": error,
        "started_at": START_DATETIME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - START_TIME, 2),
        "partial": True
    }

    save_json_atomic(PARTIAL_FILE, data)


# ============================================================
# PROMPT BUILDER
# ============================================================

def build_prompt(agent, task, my_prev, peer_prev, rnd, code):

    base = f"""You are {agent.upper()}, one of two expert AI agents collaborating as EQUALS on the Dex-tool project.

Dex-tool is a runtime Android app inspector using Frida (JavaScript) and ImGui (C++).

YOUR TASK: {task}

=== FULL PROJECT SOURCE CODE ===
{code}
=== END OF SOURCE CODE ===

This is ROUND {rnd} of a peer-to-peer consensus loop.
"""

    if rnd == 1:
        return f"""{base}

This is the FIRST round. Study the source code carefully.

CRITICAL RULES:
- Base your analysis ONLY on the actual code above.
- Do NOT invent bugs or APIs.
- If you cannot find a real bug, say so explicitly.
- Do NOT output [CONSENSUS] in this round.

Provide:

### 1. Concrete Bugs Found
(file, line/function, severity)

### 2. Most Critical Bug Chosen
(why, real impact)

### 3. Proposed Fix
(real code only, no hallucinated APIs)
"""

    return f"""{base}

--- YOUR PREVIOUS OUTPUT (Round {rnd-1}) ---
{my_prev}

--- YOUR PEER'S PREVIOUS OUTPUT (Round {rnd-1}) ---
{peer_prev}

AS A PEER, DO THIS:

1. Validate your peer's claimed bug against the actual source code.
2. Check whether your peer's proposed APIs really exist.
3. Re-examine your own answer critically.
4. Produce a revised answer with a correct, working fix.
5. ONLY if all hallucinated content is removed and the fix is genuinely correct, end with [CONSENSUS] on its own line.
6. Otherwise, do NOT use that tag.
"""


# ============================================================
# LIVE STREAM READER
# ============================================================

def stream_reader(pipe, stream_name, event_queue):

    try:
        while True:
            chunk = pipe.read(4096)

            if not chunk:
                break

            event_queue.put((stream_name, chunk))

    except Exception as exc:
        event_queue.put((
            "reader_error",
            f"{stream_name}: {exc}".encode("utf-8")
        ))

    finally:
        event_queue.put((stream_name + "_done", b""))


# ============================================================
# LIVE MODEL EXECUTION
# ============================================================

def run_llm(prompt, model_path, agent):

    log("Preparing model execution...")

    if not os.path.isfile(model_path):
        raise FileNotFoundError(
            f"Model file not found: {model_path}"
        )

    if not os.path.isfile(LLAMA_BINARY):
        raise FileNotFoundError(
            f"llama.cpp binary not found: {LLAMA_BINARY}"
        )

    # Save prompt outside command-line arguments.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        encoding="utf-8"
    ) as f:
        f.write(prompt)
        prompt_file = f.name

    cmd = [
        LLAMA_BINARY,
        "-m", model_path,
        "-f", prompt_file,
        "-n", str(MAX_TOKENS),
        "--temp", "0.2",
        "--top-p", "0.9",
        "-c", str(CONTEXT_SIZE),
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--flash-attn", "on",
        "--no-display-prompt",
        "-t", str(THREADS),
    ]

    output_parts = []
    event_queue = queue.Queue()

    process = None
    readers = []
    timed_out = False
    exit_code = None
    error_message = None

    stdout_decoder = codecs.getincrementaldecoder("utf-8")(
        errors="replace"
    )
    stderr_decoder = codecs.getincrementaldecoder("utf-8")(
        errors="replace"
    )

    started = time.monotonic()
    last_checkpoint = started

    log("Starting llama.cpp...")
    log(f"Model: {model_path}")
    log(f"Context: {CONTEXT_SIZE}")
    log(f"Max output tokens: {MAX_TOKENS}")
    log(f"Threads: {THREADS}")
    log(f"Timeout: {TIMEOUT} seconds")

    github_group(f"{agent.upper()} Round {ROUND} - Live Model Output")

    try:
        with open(TEXT_FILE, "w", encoding="utf-8") as text_file, \
             open(LOG_FILE, "w", encoding="utf-8") as log_file:

            log_file.write(
                f"Agent: {agent}\n"
                f"Round: {ROUND}\n"
                f"Started: {START_DATETIME}\n"
                f"Command: {' '.join(cmd[:4])} ...\n\n"
            )
            log_file.flush()

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )

            for pipe, name in (
                (process.stdout, "stdout"),
                (process.stderr, "stderr")
            ):
                thread = threading.Thread(
                    target=stream_reader,
                    args=(pipe, name, event_queue),
                    daemon=True
                )
                thread.start()
                readers.append(thread)

            done_streams = set()

            while len(done_streams) < 2:

                elapsed = time.monotonic() - started

                if elapsed > TIMEOUT and not timed_out:
                    timed_out = True
                    error_message = (
                        f"Model timed out after {TIMEOUT} seconds"
                    )

                    log(f"ERROR: {error_message}")

                    process.kill()

                try:
                    stream_name, chunk = event_queue.get(
                        timeout=0.25
                    )

                except queue.Empty:
                    if process.poll() is not None:
                        continue
                    continue

                if stream_name.endswith("_done"):
                    done_streams.add(
                        stream_name.replace("_done", "")
                    )
                    continue

                if stream_name == "reader_error":
                    message = chunk.decode(
                        "utf-8",
                        errors="replace"
                    )
                    log_file.write(
                        f"\n[READER ERROR] {message}\n"
                    )
                    log_file.flush()
                    continue

                decoder = (
                    stdout_decoder
                    if stream_name == "stdout"
                    else stderr_decoder
                )

                decoded = decoder.decode(chunk)

                if not decoded:
                    continue

                log_file.write(
                    f"[{stream_name.upper()}] {decoded}"
                )
                log_file.flush()

                if stream_name == "stdout":
                    text_file.write(decoded)
                    text_file.flush()

                    output_parts.append(decoded)

                    # Live model output in GitHub Actions.
                    sys.stdout.write(decoded)
                    sys.stdout.flush()

                else:
                    # llama.cpp diagnostic messages.
                    sys.stderr.write(decoded)
                    sys.stderr.flush()

                now = time.monotonic()

                if now - last_checkpoint >= CHECKPOINT_INTERVAL:
                    current_output = "".join(output_parts)

                    save_checkpoint(
                        current_output,
                        status="running"
                    )

                    last_checkpoint = now

            # Flush remaining incomplete UTF-8 sequences.
            remaining_stdout = stdout_decoder.decode(
                b"",
                final=True
            )

            remaining_stderr = stderr_decoder.decode(
                b"",
                final=True
            )

            if remaining_stdout:
                text_file.write(remaining_stdout)
                text_file.flush()
                output_parts.append(remaining_stdout)
                sys.stdout.write(remaining_stdout)
                sys.stdout.flush()

            if remaining_stderr:
                sys.stderr.write(remaining_stderr)
                sys.stderr.flush()

            exit_code = process.wait()

            text_file.flush()
            os.fsync(text_file.fileno())

    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

        for thread in readers:
            thread.join(timeout=2)

        try:
            os.unlink(prompt_file)
        except OSError:
            pass

        github_endgroup()

    output = "".join(output_parts)

    elapsed = round(time.monotonic() - started, 2)

    if timed_out:
        return output, "timeout", error_message, elapsed

    if exit_code != 0:
        error_message = (
            f"llama.cpp exited with code {exit_code}"
        )
        return output, "failed", error_message, elapsed

    if not output.strip():
        error_message = "Model returned empty output"
        return output, "failed", error_message, elapsed

    return output, "completed", None, elapsed


# ============================================================
# MAIN
# ============================================================

def main():

    log("=" * 60)
    log("PEER CONSENSUS ENGINE STARTED")
    log("=" * 60)

    log(f"Agent: {AGENT_NAME}")
    log(f"Round: {ROUND}")
    log(f"Task: {TASK_PROMPT}")

    if not MODEL_PATH:
        raise RuntimeError("MODEL_PATH is missing")

    if not AGENT_NAME:
        raise RuntimeError("AGENT_NAME is missing")

    log("Loading project source code...")

    code = load_code_context()

    log(f"Source size: {len(code):,} characters")

    prompt = build_prompt(
        AGENT_NAME,
        TASK_PROMPT,
        MY_LAST_OUTPUT,
        PEER_LAST_OUTPUT,
        ROUND,
        code
    )

    log(f"Prompt size: {len(prompt):,} characters")
    log("Prompt prepared successfully.")

    output = ""
    status = "failed"
    error = None
    elapsed = 0

    try:
        output, status, error, elapsed = run_llm(
            prompt,
            MODEL_PATH,
            AGENT_NAME
        )

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = "failed"

        log(f"EXECUTION ERROR: {error}")

    cleaned_output = clean_output(output)

    consensus = check_consensus(
        cleaned_output,
        ROUND
    )

    requested_files = extract_file_requests(
        cleaned_output
    )

    result = {
        "agent": AGENT_NAME,
        "round": ROUND,
        "status": status,
        "output": cleaned_output,
        "consensus": consensus,
        "requested_files": requested_files,
        "error": error,
        "started_at": START_DATETIME,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "partial": status != "completed"
    }

    # Always preserve the final result, even after a failure.
    save_json_atomic(OUTPUT_FILE, result)

    # Remove the temporary checkpoint after final JSON is saved.
    try:
        PARTIAL_FILE.unlink()
    except FileNotFoundError:
        pass

    log("=" * 60)
    log(f"STATUS: {status}")
    log(f"CONSENSUS: {consensus}")
    log(f"ELAPSED: {elapsed} seconds")
    log(f"OUTPUT: {OUTPUT_FILE}")
    log(f"TEXT: {TEXT_FILE}")
    log(f"LOG: {LOG_FILE}")
    log("=" * 60)

    if requested_files:
        log(f"Requested files: {requested_files}")

    if error:
        log(f"ERROR: {error}")
        sys.exit(1)

    log("Agent finished successfully.")


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        log("Interrupted by user.")
        sys.exit(130)

    except Exception as exc:
        log(f"FATAL ERROR: {type(exc).__name__}: {exc}")
        sys.exit(1)
