#!/usr/bin/env python3
"""Run bounded, read-only analyzers already present on the runner.
No tools are silently installed; every invocation and exit status is recorded.
"""
from __future__ import annotations
import datetime, json, os, pathlib, shutil, subprocess, time
ROOT=pathlib.Path(os.environ.get("GITHUB_WORKSPACE",".")).resolve()
BASE=ROOT/".agent-work"; BASE.mkdir(exist_ok=True)
MANIFEST=BASE/"source"/"manifest.json"
OUT=BASE/"static_checks.json"

def run(name,cmd,timeout=180):
    started=time.monotonic(); print(f"[STATIC] START {name}: {cmd}",flush=True)
    try:
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,errors="replace",timeout=timeout)
        result={"name":name,"command":cmd,"status":"passed" if p.returncode==0 else "nonzero_exit","returncode":p.returncode,"stdout":p.stdout[-30000:],"stderr":p.stderr[-30000:],"seconds":round(time.monotonic()-started,2)}
    except subprocess.TimeoutExpired as e:
        result={"name":name,"command":cmd,"status":"timeout","returncode":None,"stdout":str(e.stdout or "")[-30000:],"stderr":str(e.stderr or "")[-30000:],"seconds":round(time.monotonic()-started,2)}
    except Exception as e:
        result={"name":name,"command":cmd,"status":"error","error":f"{type(e).__name__}: {e}","seconds":round(time.monotonic()-started,2)}
    print(f"[STATIC] END {name}: {result['status']} ({result['seconds']}s)",flush=True)
    if result.get("stdout"): print(result["stdout"],flush=True)
    if result.get("stderr"): print(result["stderr"],flush=True)
    return result

def main():
    os.environ.setdefault("ZIZMOR_OFFLINE", "1")
    manifest=json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths=[x["path"] for x in manifest["files"]]
    py=[x for x in paths if x.endswith(".py")]
    sh=[x for x in paths if x.endswith(".sh")]
    cpp=[x for x in paths if pathlib.Path(x).suffix.lower() in {".c",".cc",".cpp",".cxx",".h",".hpp"}]
    js=[x for x in paths if pathlib.Path(x).suffix.lower() in {".js",".mjs"}]
    workflows=[x for x in paths if x.startswith(".github/workflows/") and x.endswith((".yml",".yaml"))]
    results=[]
    if py:
        # py_compile is stdlib and does not execute project modules.
        results.append(run("python-syntax",[shutil.which("python3") or "python3","-m","py_compile",*py],180))
    if py and shutil.which("ruff"):
        results.append(run("ruff",["ruff","check","--output-format","json",*py],180))
    if py and shutil.which("bandit"):
        results.append(run("bandit",["bandit","-q","-r",*sorted({str(pathlib.Path(x).parent) for x in py})],240))
    if sh and shutil.which("shellcheck"):
        results.append(run("shellcheck",["shellcheck","-f","json",*sh],180))
    if cpp and shutil.which("cppcheck"):
        results.append(run("cppcheck",["cppcheck","--enable=warning,performance,portability","--inline-suppr","--error-exitcode=0",*cpp],300))
    if js and shutil.which("node"):
        for source in js:
            results.append(run("node-syntax:"+source,["node","--check",source],60))
    if workflows and shutil.which("actionlint"):
        results.append(run("actionlint",["actionlint",*workflows],120))
    if workflows and shutil.which("zizmor"):
        results.append(run("zizmor",["zizmor","--format","json",*workflows],180))
    if shutil.which("clang-tidy") and (ROOT/"compile_commands.json").exists():
        results.append(run("clang-tidy",["clang-tidy","-p",str(ROOT),*cpp],300))
    if shutil.which("clang-format") and any((ROOT/x).exists() for x in [".clang-format","_clang-format"]):
        results.append(run("clang-format",["clang-format","--dry-run","--Werror",*cpp],180))
    # Project tests are optional and run only when explicitly enabled; arbitrary build/test
    # scripts are not executed by default on a hosted runner.
    if os.environ.get("RUN_PROJECT_TESTS","0")=="1" and shutil.which("pytest") and any("test" in p.lower() for p in paths):
        results.append(run("pytest",["pytest","-q"],600))
    payload={"generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"checks":results,"executed":sum(1 for x in results if x["status"]!="skipped"),"note":"Nonzero/timeout statuses are evidence for review, not automatic proof of a product bug."}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"checks":len(results),"passed":sum(x["status"]=="passed" for x in results),"report":str(OUT)},ensure_ascii=False))
if __name__=="__main__":main()
