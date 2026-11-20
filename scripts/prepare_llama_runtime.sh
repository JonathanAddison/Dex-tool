#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="${1:?usage: prepare_llama_runtime.sh <runtime-directory>}"
RUNTIME_DIR="$(realpath "$RUNTIME_DIR")"
if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "ERROR: llama.cpp runtime directory does not exist: $RUNTIME_DIR" >&2
  exit 2
fi

BIN="$(find "$RUNTIME_DIR" -type f -name llama-completion -print -quit)"
if [[ -z "$BIN" ]]; then
  echo "ERROR: llama-completion not found under $RUNTIME_DIR" >&2
  find "$RUNTIME_DIR" -maxdepth 4 -type f -print | head -200 >&2 || true
  exit 3
fi
chmod u+x "$BIN"

# Release archives place shared libraries in one or more sibling/subdirectories.
mapfile -t LIB_DIRS < <(find "$RUNTIME_DIR" -type f \( -name '*.so' -o -name '*.so.*' \) -printf '%h\n' | sort -u)
LIB_PATH=""
if ((${#LIB_DIRS[@]})); then
  LIB_PATH="$(IFS=:; echo "${LIB_DIRS[*]}")"
fi
if [[ -n "$LIB_PATH" ]]; then
  export LD_LIBRARY_PATH="$LIB_PATH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export LLAMA_BIN="$BIN"

echo "Resolved llama.cpp executable: $LLAMA_BIN"
echo "Resolved shared-library directories: ${LIB_PATH:-<none>}"
echo "Dynamic dependency check:"
LDD_OUTPUT="$(ldd "$LLAMA_BIN" 2>&1 || true)"
printf '%s\n' "$LDD_OUTPUT"
if grep -q 'not found' <<<"$LDD_OUTPUT"; then
  echo "ERROR: unresolved llama.cpp shared-library dependency" >&2
  exit 4
fi

# This must succeed; do not mask loader or executable failures.
"$LLAMA_BIN" --version

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    printf 'LLAMA_BIN=%s\n' "$LLAMA_BIN"
    printf 'LD_LIBRARY_PATH=%s\n' "${LD_LIBRARY_PATH:-}"
  } >> "$GITHUB_ENV"
fi
