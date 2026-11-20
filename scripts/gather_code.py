#!/usr/bin/env python3
"""Deterministic repository snapshot + context-safe source chunker.

Includes every eligible text file (including project scripts), records exclusions,
file hashes and line ranges, and writes a manifest plus JSONL chunks.
No third-party dependencies.
"""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, sys
from dataclasses import asdict, dataclass
from typing import Iterable

ROOT = pathlib.Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
DEFAULT_EXCLUDED_DIRS = {
    ".git", ".github/cache", "llama.cpp", "models", "node_modules", "vendor/cache",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", "out", "target", "results", "coverage", ".gradle", ".agent-work",
}
SENSITIVE_EXTS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".asc", ".gpg"}
SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "secrets.json", "credentials.json", "service-account.json", "id_rsa", "id_ed25519"}
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".7z",
    ".gz", ".tar", ".rar", ".so", ".dll", ".dylib", ".exe", ".bin", ".gguf",
    ".mp4", ".mov", ".mp3", ".wav", ".ttf", ".woff", ".woff2", ".pyc", ".class",
    ".dex", ".apk", ".aab", ".jar", ".keystore", ".jks", ".p12", ".pfx",
}
TEXT_NAMES = {"Dockerfile", "Makefile", " justfile", ".gitignore", ".gitattributes", ".editorconfig"}
MAX_FILE_BYTES = int(os.environ.get("MAX_SOURCE_FILE_BYTES", str(25 * 1024 * 1024)))
CHUNK_CHARS = int(os.environ.get("SOURCE_CHUNK_CHARS", "26000"))
OVERLAP_LINES = int(os.environ.get("SOURCE_OVERLAP_LINES", "12"))

@dataclass
class FileRecord:
    path: str
    bytes: int
    sha256: str
    lines: int
    language: str


def language_for(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py":"python", ".js":"javascript", ".jsx":"javascript", ".ts":"typescript",
        ".tsx":"typescript", ".cpp":"cpp", ".cc":"cpp", ".cxx":"cpp", ".c":"c",
        ".h":"c-header", ".hpp":"cpp-header", ".java":"java", ".kt":"kotlin",
        ".kts":"kotlin", ".smali":"smali", ".xml":"xml", ".json":"json",
        ".yml":"yaml", ".yaml":"yaml", ".md":"markdown", ".sh":"shell",
        ".toml":"toml", ".ini":"ini", ".gradle":"gradle", ".cmake":"cmake",
        ".txt":"text", ".rs":"rust", ".go":"go", ".html":"html", ".css":"css",
    }.get(ext, "text")


def is_excluded(path: pathlib.Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = set(rel.parts)
    if parts & {".git", "llama.cpp", "models", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "out", "target", "results", "coverage", ".gradle", ".agent-work"}:
        return True
    if path.suffix.lower() in BINARY_EXTS or path.suffix.lower() in SENSITIVE_EXTS or path.name.lower() in SENSITIVE_NAMES or path.name.lower().startswith(".env."):
        return True
    if path.name.lower() in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}:
        # Lockfiles are metadata, not useful source; manifest records exclusion.
        return True
    return False


def iter_files() -> Iterable[pathlib.Path]:
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "llama.cpp", "models", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "out", "target", "results", "coverage", ".gradle", ".agent-work"})
        for name in sorted(names):
            p = pathlib.Path(base, name)
            if p.is_symlink():
                continue
            if not is_excluded(p):
                try:
                    if p.stat().st_size <= MAX_FILE_BYTES:
                        yield p
                except OSError:
                    continue


def make_chunks(path: str, text: str, chunk_chars: int) -> list[dict]:
    lines = text.splitlines()
    chunks, start = [], 0
    while start < len(lines):
        end, chars = start, 0
        while end < len(lines):
            n = len(lines[end]) + 1
            if end > start and chars + n > chunk_chars:
                break
            chars += n
            end += 1
        body = "\n".join(lines[start:end])
        chunks.append({"path": path, "start_line": start + 1, "end_line": end,
                       "text": body, "sha256": hashlib.sha256(body.encode()).hexdigest()})
        if end >= len(lines):
            break
        start = max(start + 1, end - OVERLAP_LINES)
    if not lines:
        chunks.append({"path":path,"start_line":1,"end_line":1,"text":"","sha256":hashlib.sha256(b"").hexdigest()})
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".agent-work/source")
    ap.add_argument("--chunk-chars", type=int, default=CHUNK_CHARS)
    args = ap.parse_args()
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    records, chunks, skipped = [], [], []
    # Record eligible files above the configured per-file safety ceiling.
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "llama.cpp", "models", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "out", "target", "results", "coverage", ".gradle", ".agent-work"})
        for name in names:
            p = pathlib.Path(base, name)
            if p.is_symlink():
                skipped.append({"path":p.relative_to(ROOT).as_posix(),"reason":"symlink-not-followed"}); continue
            if is_excluded(p): continue
            try:
                size = p.stat().st_size
                if size > MAX_FILE_BYTES:
                    skipped.append({"path":p.relative_to(ROOT).as_posix(),"reason":f"file-exceeds-{MAX_FILE_BYTES}-byte-safety-limit","bytes":size})
            except OSError: pass
    for p in iter_files():
        rel = p.relative_to(ROOT).as_posix()
        try:
            raw = p.read_bytes()
            if b"\x00" in raw[:8192]:
                skipped.append({"path":rel,"reason":"binary-content"}); continue
            content = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            skipped.append({"path":rel,"reason":type(e).__name__}); continue
        records.append(FileRecord(rel, len(raw), hashlib.sha256(raw).hexdigest(), content.count("\n") + (1 if content else 0), language_for(p)))
        chunks.extend(make_chunks(rel, content, args.chunk_chars))
    # Stable IDs are based on path, line range and source digest.
    for i, c in enumerate(chunks, 1):
        c["chunk_id"] = f"C{i:05d}-{c['sha256'][:10]}"
        c["language"] = language_for(pathlib.Path(c["path"]))
    manifest = {
        "schema_version": 2, "root": ".", "files": [asdict(x) for x in records],
        "file_count": len(records), "chunk_count": len(chunks),
        "total_source_bytes": sum(x.bytes for x in records), "skipped": skipped,
        "chunk_chars": args.chunk_chars,
        "snapshot_sha256": hashlib.sha256("\n".join(f"{x.path}:{x.sha256}" for x in records).encode()).hexdigest(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (out / "file-index.txt").write_text("\n".join(f"{x.path}\t{x.lines} lines\t{x.bytes} bytes\t{x.sha256}" for x in records), encoding="utf-8")
    print(json.dumps({"files":len(records),"chunks":len(chunks),"bytes":manifest["total_source_bytes"],"snapshot":manifest["snapshot_sha256"],"manifest":str(out/"manifest.json")}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
