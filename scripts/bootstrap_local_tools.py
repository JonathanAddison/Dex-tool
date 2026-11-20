#!/usr/bin/env python3
"""Install/discover a reproducible local analysis toolchain; no AI/API services."""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
APT_PACKAGES=["cppcheck","clang-tidy","clang-format","shellcheck","cmake","ninja-build"]
PIP_REQUIREMENTS=ROOT/"requirements-analysis.txt"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--install",action="store_true",help="install missing system and Python analysis tools")
    ap.add_argument("--skip-python",action="store_true")
    args=ap.parse_args()
    report={"install_requested":args.install,"system":{},"python":{},"warnings":[]}
    commands={"cppcheck":"cppcheck","clang-tidy":"clang-tidy","clang-format":"clang-format","shellcheck":"shellcheck","cmake":"cmake","ninja":"ninja","node":"node","npm":"npm","java":"java","go":"go","actionlint":"actionlint","zizmor":"zizmor"}
    missing=[]
    for name,cmd in commands.items():
        path=shutil.which(cmd); report["system"][name]={"available":bool(path),"path":path}
        if name in {"cppcheck","clang-tidy","clang-format","shellcheck","cmake","ninja"} and not path: missing.append(name)
    if args.install and missing:
        if not shutil.which("apt-get"):
            report["warnings"].append("apt-get unavailable; system tools were not installed")
        else:
            update=subprocess.run(["sudo","apt-get","update","-qq"],capture_output=True,text=True)
            if update.returncode:
                report["warnings"].append("apt-get update failed: "+update.stderr[-1500:])
            else:
                install=subprocess.run(["sudo","apt-get","install","-y","--no-install-recommends",*APT_PACKAGES],capture_output=True,text=True)
                if install.returncode: report["warnings"].append("apt install failed: "+install.stderr[-2500:])
                else: report["system_install"]="success"
    if args.install and not args.skip_python:
        p=subprocess.run([sys.executable,"-m","pip","install","--disable-pip-version-check","-r",str(PIP_REQUIREMENTS)],capture_output=True,text=True)
        report["python_install"]="success" if p.returncode==0 else "failed"
        if p.returncode: report["warnings"].append(p.stderr[-3000:])
    # actionlint is installed from its pinned Go module release, with no GitHub API token.
    if args.install and not shutil.which("actionlint") and shutil.which("go"):
        gobin=pathlib.Path.home()/".local"/"bin"; gobin.mkdir(parents=True,exist_ok=True)
        env=os.environ.copy(); env["GOBIN"]=str(gobin)
        p=subprocess.run(["go","install","github.com/rhysd/actionlint/cmd/actionlint@v1.7.7"],env=env,capture_output=True,text=True,timeout=600)
        if p.returncode==0:
            report["actionlint_install"]="success"
            gh_path=os.environ.get("GITHUB_PATH")
            if gh_path:
                with open(gh_path,"a",encoding="utf-8") as f: f.write(str(gobin)+"\n")
            else:
                os.environ["PATH"]=str(gobin)+os.pathsep+os.environ.get("PATH","")
        else: report["warnings"].append("actionlint install failed: "+p.stderr[-2000:])
    for module in ["ruff","bandit","pytest","androguard","lief","zizmor"]:
        p=subprocess.run([sys.executable,"-c",f"import {module}; print(getattr({module}, '__version__', 'available'))"],capture_output=True,text=True)
        report["python"][module]={"available":p.returncode==0,"version":p.stdout.strip() if p.returncode==0 else ""}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0
if __name__=="__main__": raise SystemExit(main())
