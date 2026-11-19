# scripts/gather_code.py
import os
import sys

# سقف نداریم — فقط از باینری‌ها و پوشه‌های آشغال صرف‌نظر می‌کنیم
EXCLUDE_DIRS = {"llama.cpp", "models", ".git", "results", "scripts", "__pycache__", "node_modules", "venv", ".venv"}
EXCLUDE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".zip", ".tar", ".gz",
                ".so", ".dll", ".dylib", ".exe", ".bin", ".gguf", ".mp4", ".pdf", ".pyc"}

def should_skip(path):
    parts = path.split(os.sep)
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    _, ext = os.path.splitext(path)
    return ext.lower() in EXCLUDE_EXTS

def main():
    files = []
    for root, dirs, filenames in os.walk("."):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in filenames:
            path = os.path.join(root, fn)
            if should_skip(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            files.append((path, size))

    files.sort()  # ترتیب الفبایی برای پایداری

    output = []
    output.append("=== REPOSITORY FILE LIST ===")
    for path, size in files:
        output.append(f"{path} ({size} bytes)")
    output.append("\n=== FILE CONTENTS ===\n")

    # هیچ سقف بایتی اعمال نمی‌شود — همه فایل‌های متنی کامل ارسال می‌شوند
    for path, _ in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        output.append(f"--- FILE: {path} ---")
        output.append(content)
        output.append("")

    full = "\n".join(output)
    print(full)
    print(f"\n[TOTAL GATHERED SIZE: {len(full)} bytes / {len(full)//4} estimated tokens]", file=sys.stderr)

if __name__ == "__main__":
    main()
