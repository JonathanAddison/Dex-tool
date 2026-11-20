#!/usr/bin/env python3
"""Discover optional local analysis tools; presence is not execution."""
import json, shutil, subprocess, pathlib, os, datetime, importlib.util
TOOLS={
 "python":"python3", "pytest":"pytest", "ruff":"ruff", "mypy":"mypy", "bandit":"bandit",
 "semgrep":"semgrep", "cppcheck":"cppcheck", "clang_tidy":"clang-tidy", "clang_format":"clang-format", "clang":"clang",
 "clangxx":"clang++", "cmake":"cmake", "ninja":"ninja",
 "make":"make", "shellcheck":"shellcheck", "shfmt":"shfmt", "node":"node", "npm":"npm",
 "java":"java", "gradle":"gradle", "adb":"adb", "frida":"frida", "jadx":"jadx",
 "apktool":"apktool", "git":"git", "jq":"jq", "docker":"docker", "actionlint":"actionlint", "zizmor":"zizmor", "go":"go", "rustc":"rustc",
 "cargo":"cargo", "g++":"g++", "gcc":"gcc"
}
def main():
    result={"generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"tools":{},"python_modules":{}}
    for key,cmd in TOOLS.items():
        path=shutil.which(cmd)
        item={"available":bool(path),"path":path}
        if path and key in {"python","pytest","ruff","clang","clangxx","cmake","node","java","git","go","rustc","cargo"}:
            try:
                p=subprocess.run([cmd,"--version"],capture_output=True,text=True,timeout=5)
                item["version"]=(p.stdout or p.stderr).splitlines()[0][:240]
            except Exception as e:item["version_error"]=type(e).__name__
        result["tools"][key]=item
    for module in ["ruff","bandit","pytest","androguard","lief","zizmor"]:
        try: available=importlib.util.find_spec(module) is not None
        except (ImportError,ValueError): available=False
        result["python_modules"][module]={"available":available}
    out=pathlib.Path(os.environ.get("TOOL_INVENTORY_FILE",".agent-work/tool_inventory.json"));out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
