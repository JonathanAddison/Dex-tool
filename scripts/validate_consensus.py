#!/usr/bin/env python3
"""Strict central consensus gate. A text marker can never establish consensus."""
import hashlib,json,os,sys

def load(k):
    try:return json.loads(os.environ.get(k,"{}"))
    except json.JSONDecodeError:return {}

def main():
    q=load("QWEN_RESULT"); g=load("GLM_RESULT")
    candidate=load("SHARED_CANDIDATE")
    expected=candidate.get("patch_sha256","")
    q_ok=q.get("decision")=="approve" and q.get("approved_candidate_patch_sha256")==expected
    g_ok=g.get("decision")=="approve" and g.get("approved_candidate_patch_sha256")==expected
    same_issue=bool(candidate.get("issue_id")) and q.get("issue_id")==candidate.get("issue_id") and g.get("issue_id")==candidate.get("issue_id")
    same_snapshot=bool(q.get("source_snapshot_sha256")) and q.get("source_snapshot_sha256")==g.get("source_snapshot_sha256")==candidate.get("source_snapshot_sha256")
    patch=candidate.get("patch", "") or ""
    valid_hash=bool(expected and patch.strip()) and hashlib.sha256(patch.encode()).hexdigest()==expected
    valid_patch_format=patch.startswith("diff --git ") or (patch.startswith("--- ") and "+++ " in patch)
    q_evidence=isinstance(q.get("verified_evidence"),list) and len(q["verified_evidence"])>0
    g_evidence=isinstance(g.get("verified_evidence"),list) and len(g["verified_evidence"])>0
    q_objections=q.get("objections"); g_objections=g.get("objections")
    q_requests=q.get("needed_files"); g_requests=g.get("needed_files")
    no_blockers=isinstance(q_objections,list) and not q_objections and isinstance(g_objections,list) and not g_objections and isinstance(q_requests,list) and not q_requests and isinstance(g_requests,list) and not g_requests
    try: confidence_ok=int(q.get("confidence",0) or 0)>=75 and int(g.get("confidence",0) or 0)>=75
    except (TypeError,ValueError): confidence_ok=False
    consensus=bool(q_ok and g_ok and same_issue and same_snapshot and valid_hash and valid_patch_format and q_evidence and g_evidence and no_blockers and confidence_ok)
    out={"consensus":consensus,"candidate":candidate,"checks":{"qwen_approved_exact_hash":q_ok,"glm_approved_exact_hash":g_ok,"same_issue":same_issue,"same_source_snapshot":same_snapshot,"patch_hash_valid":valid_hash,"patch_format_valid":valid_patch_format,"qwen_verified_evidence":q_evidence,"glm_verified_evidence":g_evidence,"no_blocking_objections_or_file_requests":no_blockers,"confidence_at_least_75_each":confidence_ok},"qwen_decision":q.get("decision"),"glm_decision":g.get("decision"),"qwen_objections":q.get("objections",[]),"glm_objections":g.get("objections",[])}
    print(json.dumps(out,ensure_ascii=False,separators=(",",":")))
if __name__=="__main__":main()
