#!/usr/bin/env python3
"""Project-specific, offline audit for Dex-tool's Android NDK + Frida + ImGui stack.
This emits evidence and tool results; heuristic matches are not automatically bugs.
"""
from __future__ import annotations
import json, pathlib, re, shutil, subprocess, time, os, collections
ROOT=pathlib.Path(os.environ.get("GITHUB_WORKSPACE",pathlib.Path(__file__).resolve().parents[1])).resolve()
OUT=ROOT/".agent-work"/"android_native_audit.json"
SKIP={".git",".agent-work","build","deps","imgui","node_modules",".gradle","models","llama.cpp"}
TEXT={".cpp",".cc",".cxx",".c",".h",".hpp",".js",".mjs",".java",".kt",".gradle",".kts",".cmake",".txt",".yml",".yaml"}
PATTERNS={
 "jni_lifecycle":r"\bJNI_OnLoad\b|\bJNI_OnUnload\b",
 "jni_thread_attachment":r"AttachCurrentThread|DetachCurrentThread|GetEnv\s*\(",
 "native_threading":r"std::thread|std::mutex|std::recursive_mutex|std::atomic",
 "frida_interceptor":r"Interceptor\.(attach|replace|revert)|NativeFunction|NativeCallback",
 "frida_java_bridge":r"Java\.(perform|use|choose|enumerateLoadedClasses)",
 "frida_memory_api":r"Memory\.(patchCode|scan|alloc|protect)|Memory\.write",
 "imgui_render":r"ImGui::NewFrame|ImGui::Render|ImGui_Impl.*_NewFrame",
 "android_gl_context":r"eglMakeCurrent|eglDestroyContext|EGL_CONTEXT_LOST|glDelete",
 "native_crash_handling":r"sigaction|SIGSEGV|SA_SIGINFO|write\s*\(",
}

def walk():
 for p in ROOT.rglob("*"):
  if not p.is_file() or p.is_symlink() or p.suffix.lower() not in TEXT: continue
  if any(part in SKIP for part in p.relative_to(ROOT).parts): continue
  yield p

def main():
 started=time.monotonic(); matches=[]; files=[]; counts=collections.Counter()
 compiledb=(ROOT/"compile_commands.json").exists() or (ROOT/"build/compile_commands.json").exists()
 for p in walk():
  rel=p.relative_to(ROOT).as_posix(); files.append(rel)
  try: lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
  except OSError: continue
  for n,line in enumerate(lines,1):
   for tag,pattern in PATTERNS.items():
    if re.search(pattern,line):
     counts[tag]+=1
     if counts[tag] <= 30 and len(matches) < 240: matches.append({"category":tag,"file":rel,"line":n,"text":line.strip()[:280]})
 tool_results=[]
 js=ROOT/"agent/agent.js"
 if js.exists() and shutil.which("node"):
  p=subprocess.run(["node","--check",str(js)],cwd=ROOT,capture_output=True,text=True,timeout=45)
  tool_results.append({"tool":"node --check agent/agent.js","status":"passed" if p.returncode==0 else "failed","returncode":p.returncode,"stdout":p.stdout[-3000:],"stderr":p.stderr[-5000:]})
 for name,cmd in [("cppcheck",["cppcheck","--enable=warning,performance,portability","--inline-suppr","--error-exitcode=0","--quiet"] )]:
  if shutil.which(name):
   native=[str(p.relative_to(ROOT)) for p in walk() if p.suffix.lower() in {".c",".cc",".cpp",".cxx",".h",".hpp"}]
   if native:
    try:
     p=subprocess.run(cmd+native,cwd=ROOT,capture_output=True,text=True,timeout=300)
     tool_results.append({"tool":name,"status":"completed" if p.returncode==0 else "error","returncode":p.returncode,"stdout":p.stdout[-16000:],"stderr":p.stderr[-16000:]})
    except subprocess.TimeoutExpired: tool_results.append({"tool":name,"status":"timeout"})
 clang_tidy=shutil.which("clang-tidy")
 db=next((x for x in [ROOT/"compile_commands.json",ROOT/"build/compile_commands.json"] if x.exists()),None)
 if clang_tidy and db:
  try:
   entries=json.loads(db.read_text()); sources=[e.get("file") for e in entries if e.get("file")]
   p=subprocess.run([clang_tidy,"-p",str(db.parent),*sources[:120]],cwd=ROOT,capture_output=True,text=True,timeout=300)
   tool_results.append({"tool":"clang-tidy","status":"completed" if p.returncode==0 else "findings_or_error","returncode":p.returncode,"stdout":p.stdout[-16000:],"stderr":p.stderr[-16000:]})
  except Exception as e: tool_results.append({"tool":"clang-tidy","status":"error","error":str(e)})
 elif clang_tidy:
  tool_results.append({"tool":"clang-tidy","status":"skipped","reason":"No compile_commands.json; compile flags for Android/Frida dependencies are not available in this job."})
 report={"project_profile":"Android NDK native library + Frida GumJS + ImGui overlay","files_scanned":len(files),"files":files,"compile_database_available":compiledb,"source_markers":matches,"source_marker_counts":dict(counts),"tool_results":tool_results,"elapsed_seconds":round(time.monotonic()-started,2),"interpretation":"Marker matches are navigation evidence, not bug findings. cppcheck/clang-tidy output requires human/model interpretation."}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"files_scanned":len(files),"marker_count":len(matches),"tools":len(tool_results),"report":str(OUT)},ensure_ascii=False))
 return 0
if __name__=="__main__": raise SystemExit(main())
