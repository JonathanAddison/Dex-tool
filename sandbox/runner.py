#!/usr/bin/env python3
"""Fixed-command test runner executed inside a network-isolated Docker sandbox."""
from __future__ import annotations
import argparse, json, os, pathlib, subprocess, sys, time, shutil
ROOT = pathlib.Path('/workspace')
MAX_LOG = 24000

def run(name, cmd, timeout=300):
    started=time.monotonic()
    env=os.environ.copy()
    env.update({'CI':'1','GIT_TERMINAL_PROMPT':'0','PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1','GOPROXY':'off','CARGO_NET_OFFLINE':'true','npm_config_offline':'true'})
    print(f'[SANDBOX] START {name}: {cmd}', flush=True)
    try:
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,errors='replace',timeout=timeout,env=env)
        item={'suite':name,'status':'passed' if p.returncode==0 else 'failed','returncode':p.returncode,'seconds':round(time.monotonic()-started,2),'stdout':p.stdout[-MAX_LOG:],'stderr':p.stderr[-MAX_LOG:]}
    except subprocess.TimeoutExpired as e:
        item={'suite':name,'status':'timeout','returncode':None,'seconds':round(time.monotonic()-started,2),'stdout':str(e.stdout or '')[-MAX_LOG:],'stderr':str(e.stderr or '')[-MAX_LOG:]}
    print(f"[SANDBOX] END {name}: {item['status']} ({item['seconds']}s)",flush=True)
    if item['stdout']: print(item['stdout'],flush=True)
    if item['stderr']: print(item['stderr'],file=sys.stderr,flush=True)
    return item

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--suite',choices=['auto','native','python','cmake','node','go','rust','shell'],default='auto'); a=ap.parse_args()
    results=[]; suite=a.suite
    has=lambda p:(ROOT/p).exists()
    if suite in ('auto','native'):
        native=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and p.suffix.lower() in {'.c','.cc','.cpp','.cxx','.h','.hpp'} and not any(x in p.parts for x in ['.git','deps','imgui','build','out','target'])]
        if native and shutil.which('cppcheck'):
            results.append(run('cppcheck',['cppcheck','--enable=warning,performance,portability','--inline-suppr','--error-exitcode=0',*native],300))
        js=ROOT/'agent'/'agent.js'
        if js.exists() and shutil.which('node'):
            results.append(run('frida-agent-js-syntax',['node','--check',str(js.relative_to(ROOT))],60))
        if shutil.which('clang-tidy') and (ROOT/'compile_commands.json').exists():
            db=json.loads((ROOT/'compile_commands.json').read_text())
            files=[x.get('file') for x in db if x.get('file')]
            if files: results.append(run('clang-tidy',['clang-tidy','-p','.',*files[:100]],300))
        if not results: results.append({'suite':'native','status':'skipped','reason':'No C/C++ sources or local analyzers were available.'})
    if suite in ('auto','python') and (suite=='python' or any(has(x) for x in ['pyproject.toml','pytest.ini','setup.cfg','tox.ini'])):
        py=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*.py') if not any(x in p.parts for x in ['.git','.venv','venv','node_modules','build','dist','target'])]
        if py: results.append(run('python-compile',['python','-m','compileall','-q',*py],180))
        if any(has(x) for x in ['pyproject.toml','pytest.ini','setup.cfg','tox.ini']) or has('tests'):
            results.append(run('pytest',['pytest','-q','-p','no:cacheprovider'],600))
    if suite in ('auto','cmake') and (suite=='cmake' or has('CMakeLists.txt')):
        deps_ready=has('deps/frida-gum/arm64-v8a/libfrida-gumjs.a') and has('deps/dobby/arm64-v8a/libdobby.a') and has('imgui/imgui.cpp')
        if has('CMakeLists.txt') and deps_ready:
            results.append(run('cmake-configure',['cmake','-S','.','-B','/tmp/dex-build','-G','Ninja','-DCMAKE_BUILD_TYPE=Debug'],300))
            if results[-1]['status']=='passed':
                results.append(run('cmake-build',['cmake','--build','/tmp/dex-build','--parallel','2'],900))
                if results[-1]['status']=='passed': results.append(run('ctest',['ctest','--test-dir','/tmp/dex-build','--output-on-failure'],600))
    if suite in ('auto','cmake') and has('CMakeLists.txt') and not deps_ready:
        results.append({'suite':'cmake-android-build','status':'skipped','reason':'The full project build requires prebuilt Frida GumJS, Dobby, ImGui and Android NDK dependencies. This isolated offline sandbox intentionally does not download them; use the repository Build Dex-tool workflow for the real two-ABI integration build.'})
    if suite in ('auto','node') and (suite=='node' or has('package.json')):
        if has('package.json'):
            # npm scripts are untrusted code; this executes only inside this container.
            results.append(run('npm-test',['npm','test'],600))
    if suite in ('auto','go') and (suite=='go' or has('go.mod')):
        if has('go.mod'): results.append(run('go-test',['go','test','./...'],600))
    if suite in ('auto','rust') and (suite=='rust' or has('Cargo.toml')):
        if has('Cargo.toml'): results.append(run('cargo-test',['cargo','test','--offline','--locked'],600))
    if suite in ('auto','shell') and (suite=='shell' or any(ROOT.rglob('*.sh'))):
        sh=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*.sh') if '.git' not in p.parts]
        if sh: results.append(run('shellcheck',['shellcheck','-f','gcc',*sh],180))
    if not results: results.append({'suite':suite,'status':'skipped','reason':'No matching project/test manifest was found.'})
    summary={'sandbox':'docker-network-none-readonly-rootfs','suite':suite,'results':results,'passed':sum(x['status']=='passed' for x in results),'failed':sum(x['status']=='failed' for x in results),'timeouts':sum(x['status']=='timeout' for x in results)}
    print('DEX_SANDBOX_RESULT='+json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if all(x['status'] in ('passed','skipped') for x in results) else 1
if __name__=='__main__': raise SystemExit(main())
