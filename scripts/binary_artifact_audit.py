#!/usr/bin/env python3
"""Offline metadata audit for APK/DEX/ELF artifacts found in the repository.
No execution of APKs/native libraries; only parsers are used. Missing optional
libraries are reported as unavailable, never treated as a clean scan.
"""
from __future__ import annotations
import json, os, pathlib, time
ROOT=pathlib.Path(os.environ.get("GITHUB_WORKSPACE",pathlib.Path(__file__).resolve().parents[1])).resolve()
OUT=ROOT/".agent-work"/"binary_artifact_audit.json"
SKIP={".git",".agent-work","models","llama.cpp","node_modules",".gradle","build","dist","out","target","deps","imgui"}
EXTS={".apk",".dex",".aab",".apks",".jar",".so",".elf"}
MAX_BYTES=250*1024*1024

def candidates():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.is_symlink() or p.suffix.lower() not in EXTS: continue
        if any(x in SKIP for x in p.relative_to(ROOT).parts): continue
        try:
            if p.stat().st_size <= MAX_BYTES: yield p
        except OSError: pass

def main():
    started=time.monotonic(); files=list(candidates()); results=[]; availability={}
    try:
        from androguard.core.apk import APK
        from androguard.core.dex import DEX
        availability["androguard"]=True
    except Exception as e:
        APK=DEX=None; availability["androguard"]=False; availability["androguard_error"]=type(e).__name__
    try:
        import lief
        availability["lief"]=True
    except Exception as e:
        lief=None; availability["lief"]=False; availability["lief_error"]=type(e).__name__
    for path in files:
        rel=path.relative_to(ROOT).as_posix(); item={"path":rel,"bytes":path.stat().st_size,"type":path.suffix.lower()}
        try:
            if path.suffix.lower()==".apk" and APK:
                obj=APK(str(path)); item.update({"package":obj.get_package(),"version_name":obj.get_androidversion_name(),"min_sdk":obj.get_min_sdk_version(),"target_sdk":obj.get_target_sdk_version(),"permissions":obj.get_permissions(),"activities":obj.get_activities(),"services":obj.get_services(),"receivers":obj.get_receivers(),"providers":obj.get_providers()})
                item["parser"]="androguard"
            elif path.suffix.lower()==".dex" and DEX:
                obj=DEX(path.read_bytes()); classes=obj.get_classes(); item.update({"class_count":len(classes),"class_names":[c.get_name() for c in classes[:120]],"string_count":len(obj.get_strings())}); item["parser"]="androguard"
            elif path.suffix.lower() in {".so",".elf"} and lief:
                obj=lief.parse(str(path))
                if obj is None: item["parse_status"]="unrecognized"
                else:
                    item.update({"format":str(obj.format),"architecture":str(obj.header.machine_type),"entrypoint":int(getattr(obj,"entrypoint",0)),"sections":[{"name":s.name,"size":int(s.size)} for s in list(obj.sections)[:100]],"imports":[x.name for x in list(getattr(obj,"imports",[]))[:160]],"exports":[x.name for x in list(getattr(obj,"exported_symbols",[]))[:160]]}); item["parser"]="lief"
            else:
                item["parse_status"]="parser_unavailable_or_format_not_supported"
        except Exception as e:
            item["parse_status"]="error"; item["error"]=f"{type(e).__name__}: {e}"[:1200]
        results.append(item)
    report={"files_found":len(files),"optional_parser_availability":availability,"artifacts":results,"elapsed_seconds":round(time.monotonic()-started,2),"safety":"No APK, DEX, JAR or native library was executed; metadata parsers only. Files over 250 MiB and generated dependency/build directories are skipped."}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"files_found":len(files),"parser_availability":availability,"report":str(OUT)},ensure_ascii=False))
    return 0
if __name__=="__main__": raise SystemExit(main())
