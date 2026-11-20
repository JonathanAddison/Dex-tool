#!/usr/bin/env python3
"""Deterministically choose one candidate for both agents to review next."""
import hashlib, json, os, sys

def parse(name):
    raw=os.environ.get(name,"")
    if not raw: return {}
    try: return json.loads(raw)
    except json.JSONDecodeError: return {"raw":raw}

def score(x):
    sev={"critical":5,"high":4,"medium":3,"low":2,"info":1}.get(str(x.get("severity","medium")).lower(),0)
    evidence=len(x.get("evidence",[])) if isinstance(x.get("evidence"),list) else 0
    patch= x.get("revised_patch") or x.get("patch") or ""
    confidence=int(x.get("confidence",0) or 0)
    return (sev, bool(patch.strip()), evidence, confidence)

def main():
    q=parse("QWEN_PREV"); g=parse("GLM_PREV")
    candidates=[]
    for agent,x in (("qwen",q),("glm",g)):
        if not isinstance(x,dict): continue
        patch=x.get("revised_patch") or x.get("patch") or ""
        if not patch.strip(): continue
        digest=hashlib.sha256(patch.encode()).hexdigest()
        candidates.append({"origin":agent,"issue_id":x.get("issue_id","unknown"),"title":x.get("title",""),"severity":x.get("severity","medium"),"confidence":x.get("confidence",0),"diagnosis":x.get("diagnosis",x.get("root_cause","")),"evidence":x.get("verified_evidence") or x.get("evidence") or [],"patch":patch,"patch_sha256":digest,"tests_to_run":x.get("tests_to_run",[]),"needed_files":x.get("needed_files",[]),"source_snapshot_sha256":x.get("source_snapshot_sha256","")})
    if not candidates:
        result={"status":"no_patch_candidate","issue_id":"","patch":"","patch_sha256":"","evidence":[]}
    else:
        # Deterministic tie-break: score, then stable SHA; same result in both jobs.
        candidates.sort(key=lambda x:(score(x),x["patch_sha256"]),reverse=True)
        result=candidates[0]
    print(json.dumps(result,ensure_ascii=False,separators=(",",":")))
if __name__=="__main__": main()
