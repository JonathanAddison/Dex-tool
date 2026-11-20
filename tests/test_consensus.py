import hashlib, json, os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(script, env):
    e=os.environ.copy(); e.update(env)
    return subprocess.check_output([sys.executable,str(ROOT/'scripts'/script)],env=e,text=True)

def test_select_candidate_is_deterministic():
    patch='diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n'
    q={"issue_id":"bug-a","patch":patch,"severity":"high","confidence":80,"evidence":[{"file":"x"}]}
    g={"issue_id":"bug-b","patch":"diff --git a/y b/y\n","severity":"medium","confidence":99,"evidence":[]}
    env={"QWEN_PREV":json.dumps(q),"GLM_PREV":json.dumps(g)}
    a=json.loads(run('select_candidate.py',env)); b=json.loads(run('select_candidate.py',env))
    assert a==b and a['issue_id']=='bug-a'
    assert a['patch_sha256']==hashlib.sha256(patch.encode()).hexdigest()

def test_consensus_requires_both_exact_approvals():
    patch='diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n'; digest=hashlib.sha256(patch.encode()).hexdigest(); snapshot='same-snapshot'
    candidate={"issue_id":"bug-a","patch":patch,"patch_sha256":digest,"source_snapshot_sha256":snapshot,"verified_evidence":[{"file":"x","lines":"1"}],"confidence":90,"objections":[],"needed_files":[]}
    base={"issue_id":"bug-a","decision":"approve","approved_candidate_patch_sha256":digest,"source_snapshot_sha256":snapshot,"verified_evidence":[{"file":"x","lines":"1"}],"confidence":90,"objections":[],"needed_files":[]}
    env={"SHARED_CANDIDATE":json.dumps(candidate),"QWEN_RESULT":json.dumps(base),"GLM_RESULT":json.dumps(base)}
    assert json.loads(run('validate_consensus.py',env))['consensus'] is True
    env['GLM_RESULT']=json.dumps({**base,"approved_candidate_patch_sha256":"wrong"})
    assert json.loads(run('validate_consensus.py',env))['consensus'] is False

def test_chunker_preserves_all_lines():
    import importlib.util
    spec=importlib.util.spec_from_file_location('gather',ROOT/'scripts'/'gather_code.py')
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
    text='\n'.join(f'line-{i}' for i in range(1,151))
    chunks=m.make_chunks('x.py',text,100)
    covered=set()
    for c in chunks: covered.update(range(c['start_line'],c['end_line']+1))
    assert covered==set(range(1,151))


def test_chunker_never_follows_symlinks(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('gather_symlink',ROOT/'scripts'/'gather_code.py')
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
    repo=tmp_path/'repo'; repo.mkdir()
    outside=tmp_path/'private.txt'; outside.write_text('PRIVATE OUTSIDE DATA')
    (repo/'safe.txt').write_text('safe source')
    try: (repo/'leak.txt').symlink_to(outside)
    except OSError: return
    m.ROOT=repo
    names=[p.name for p in m.iter_files()]
    assert 'safe.txt' in names
    assert 'leak.txt' not in names
