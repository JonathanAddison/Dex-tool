#!/usr/bin/env python3
"""Create a disposable patched checkout and execute fixed test suites in Docker.
The model cannot supply a shell command. Network, host paths, secrets and Docker socket
are never mounted into the container.
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, subprocess, sys, tempfile
ROOT=pathlib.Path(os.environ.get('GITHUB_WORKSPACE','.')).resolve()
IMAGE=os.environ.get('DEX_SANDBOX_IMAGE','dex-agent-sandbox:latest')
MAX_PATCH=180_000
IGNORE=shutil.ignore_patterns('.git','.agent-work','models','llama.cpp','results','.venv','venv','node_modules','__pycache__','build','dist','target','.gradle','deps','imgui','android-sdk','frida-gum-src','dobby-src','.env','.env.*','*.pem','*.key','*.p12','*.pfx','*.jks','*.keystore','secrets.json','credentials.json','service-account.json','id_rsa','id_ed25519')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--suite',choices=['auto','native','python','cmake','node','go','rust','shell'],default='auto'); ap.add_argument('--patch-stdin',action='store_true'); a=ap.parse_args()
    patch=sys.stdin.read() if a.patch_stdin else ''
    if len(patch)>MAX_PATCH: print(json.dumps({'ok':False,'error':'patch exceeds size limit'})); return 2
    if not shutil.which('docker'): print(json.dumps({'ok':False,'error':'Docker is unavailable; sandbox execution refused'})); return 2
    with tempfile.TemporaryDirectory(prefix='dex-agent-sandbox-') as td:
        work=pathlib.Path(td)/'workspace'; shutil.copytree(ROOT,work,ignore=IGNORE,symlinks=True)
        if patch.strip():
            (work/'.git').mkdir(exist_ok=True)
            subprocess.run(['git','init','-q'],cwd=work,check=True,capture_output=True)
            check=subprocess.run(['git','apply','--check','--whitespace=nowarn','-'],cwd=work,input=patch,text=True,capture_output=True)
            if check.returncode:
                print(json.dumps({'ok':False,'stage':'patch-check','stderr':check.stderr[-8000:]})); return 1
            applied=subprocess.run(['git','apply','--whitespace=nowarn','-'],cwd=work,input=patch,text=True,capture_output=True)
            if applied.returncode:
                print(json.dumps({'ok':False,'stage':'patch-apply','stderr':applied.stderr[-8000:]})); return 1
        uid=os.getuid(); gid=os.getgid()
        cmd=['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL','--security-opt','no-new-privileges','--pids-limit','192','--ulimit','nofile=1024:1024','--ulimit','fsize=1073741824:1073741824','--memory','5g','--memory-swap','5g','--cpus','2','--user',f'{uid}:{gid}','--env','HOME=/tmp','--env','TMPDIR=/tmp','--env','npm_config_cache=/tmp/npm-cache','--env','CARGO_HOME=/tmp/cargo','--tmpfs','/tmp:rw,nosuid,nodev,size=1536m','--mount',f'type=bind,src={work},dst=/workspace,rw','--workdir','/workspace',IMAGE,'--suite',a.suite]
        try:
            p=subprocess.run(cmd,capture_output=True,text=True,errors='replace',timeout=1200)
        except subprocess.TimeoutExpired as e:
            print(json.dumps({'ok':False,'stage':'docker-timeout','timeout_seconds':1200,'stdout':str(e.stdout or '')[-12000:],'stderr':str(e.stderr or '')[-12000:]})); return 124
        result={'ok':p.returncode==0,'stage':'execution','returncode':p.returncode,'suite':a.suite,'patch_applied':bool(patch.strip()),'stdout':p.stdout[-24000:],'stderr':p.stderr[-12000:],'container_security':['network none','read-only root filesystem','capabilities dropped','no-new-privileges','no Docker socket','no secrets mounted','resource limits']}
        print(json.dumps(result,ensure_ascii=False)); return p.returncode
if __name__=='__main__': raise SystemExit(main())
