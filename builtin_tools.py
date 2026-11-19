import os, subprocess, json, hashlib, base64, re, glob, shutil
from tool_registry import ToolRegistry

def sandboxed(cmd, timeout=60, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return (r.stdout + r.stderr).strip()[:6000] or "(بدون خروجی)"
    except subprocess.TimeoutExpired:
        return f"⏱️ timeout بعد از {timeout}s"
    except Exception as e:
        return f"خطا: {e}"

def safe_path(root, p):
    if not p: return root
    full = os.path.realpath(os.path.join(root, p))
    return full if full.startswith(root) else None

def register_builtin(reg: ToolRegistry, repo_root: str):
    R = repo_root

    # ========== 1) File System (10) ==========
    def list_files(a):
        full = safe_path(R, a.get("path", ""))
        if not full or not os.path.isdir(full): return "مسیر نامعتبر."
        items = [e + ("/" if os.path.isdir(os.path.join(full,e)) else "")
                 for e in sorted(os.listdir(full)) if not e.startswith(".git")]
        return "\n".join(items[:150]) or "(خالی)"
    def read_file(a):
        full = safe_path(R, a.get("path"))
        if not full or not os.path.isfile(full): return "فایل یافت نشد."
        lines = open(full, encoding="utf-8", errors="ignore").read().splitlines()
        off = max(0, a.get("offset", 1) - 1)
        lim = a.get("limit") or 400
        return "\n".join(f"{off+i+1:>5}| {l}" for i,l in enumerate(lines[off:off+lim]))[:15000]
    def write_file(a):
        full = safe_path(R, a.get("path"))
        if not full: return "مسیر نامعتبر."
        d = os.path.dirname(full)
        if d: os.makedirs(d, exist_ok=True)
        open(full, "w", encoding="utf-8").write(a.get("content",""))
        return f"✅ نوشته شد: {a.get('path')}"
    def edit_file(a):
        full = safe_path(R, a.get("path"))
        if not full or not os.path.isfile(full): return "فایل یافت نشد."
        old, new = a.get("old_string",""), a.get("new_string","")
        c = open(full, encoding="utf-8", errors="ignore").read()
        if c.count(old) != 1: return f"old_string باید یکتا باشد (count={c.count(old)})"
        open(full, "w", encoding="utf-8").write(c.replace(old, new, 1))
        return f"✅ ویرایش شد: {a.get('path')}"
    def append_file(a):
        full = safe_path(R, a.get("path"))
        if not full: return "مسیر نامعتبر."
        open(full, "a", encoding="utf-8").write(a.get("content",""))
        return "✅ الحاق شد."
    def delete_file(a):
        full = safe_path(R, a.get("path"))
        if not full or not os.path.isfile(full): return "فایل یافت نشد."
        os.remove(full); return "✅ حذف شد."
    def move_file(a):
        src, dst = safe_path(R, a.get("src")), safe_path(R, a.get("dst"))
        if not src or not dst: return "مسیر نامعتبر."
        shutil.move(src, dst); return "✅ منتقل شد."
    def copy_file(a):
        src, dst = safe_path(R, a.get("src")), safe_path(R, a.get("dst"))
        if not src or not dst: return "مسیر نامعتبر."
        shutil.copy2(src, dst); return "✅ کپی شد."
    def file_info(a):
        full = safe_path(R, a.get("path"))
        if not full or not os.path.exists(full): return "یافت نشد."
        st = os.stat(full)
        return json.dumps({"size": st.st_size, "mtime": st.st_mtime,
                           "is_dir": os.path.isdir(full)}, ensure_ascii=False)
    def create_directory(a):
        full = safe_path(R, a.get("path"))
        if not full: return "مسیر نامعتبر."
        os.makedirs(full, exist_ok=True); return "✅ ساخته شد."

    # ========== 2) Search (6) ==========
    def glob_search(a):
        base = safe_path(R, a.get("path", "")) or R
        files = glob.glob(os.path.join(base, a.get("pattern","")), recursive=True)
        return "\n".join(os.path.relpath(f, R) for f in files if os.path.isfile(f))[:6000] or "یافت نشد."
    def search_code(a):
        return sandboxed(["grep","-rIn","-E",a.get("query",""),
                          a.get("path","."),f"--include={a.get('include','*')}"], 20, R)[:6000]
    def search_regex(a): return search_code(a)
    def find_symbol(a):
        return sandboxed(["grep","-rIn","-E",rf"\b{re.escape(a.get('symbol',''))}\b",
                          a.get("path",".")], 20, R)[:6000]
    def find_references(a): return find_symbol(a)
    def grep_ast(a): return "grep_ast نیاز به tree-sitter دارد؛ فعلاً از search_code استفاده کن."

    # ========== 3) Git (8) ==========
    def git_log(a): return sandboxed(["git","log",f"-{a.get('limit',10)}","--oneline"], 10, R)
    def git_diff(a): return sandboxed(["git","diff","HEAD~1","--",a.get("path","")], 10, R)[:5000]
    def git_show(a): return sandboxed(["git","show","--stat",a.get("commit","")], 10, R)[:5000]
    def git_blame(a): return sandboxed(["git","blame",a.get("path","")], 15, R)[:5000]
    def git_status(a): return sandboxed(["git","status"], 10, R)
    def git_branch(a): return sandboxed(["git","branch","-a"], 10, R)
    def git_checkout(a): return sandboxed(["git","checkout",a.get("ref","")], 10, R)
    def git_stash(a): return sandboxed(["git","stash"], 10, R)

    # ========== 4) Execution (6) ==========
    def bash_execute(a): return sandboxed(["sh","-c",a.get("command","")], a.get("timeout",60), R)
    def python_execute(a): return sandboxed(["python3","-c",a.get("code","")], a.get("timeout",60), R)
    def node_execute(a): return sandboxed(["node","-e",a.get("code","")], a.get("timeout",60), R)
    def compile_code(a): return sandboxed(["sh","-c",a.get("command","")], 60, R)
    def run_tests(a): return sandboxed(["sh","-c",a.get("command","pytest")], 120, R)
    def install_package(a): return sandboxed(["pip","install",a.get("package","")], 120, R)

    # ========== 5) Binary Analysis (8) ==========
    def binary_strings(a): return sandboxed(["strings","-n",str(a.get("min_len",4)),a.get("path","")], 20, R)[:6000]
    def binary_symbols(a): return sandboxed(["nm","-C","--defined-only",a.get("path","")], 20, R)[:6000]
    def binary_headers(a): return sandboxed(["readelf","-h",a.get("path","")], 20, R)[:6000]
    def binary_sections(a): return sandboxed(["readelf","-S",a.get("path","")], 20, R)[:6000]
    def binary_disassemble(a): return sandboxed(["objdump","-d",a.get("path","")], 30, R)[:6000]
    def binary_hexdump(a): return sandboxed(["xxd","-l",str(a.get("length",256)),a.get("path","")], 10, R)
    def binary_entropy(a): return sandboxed(["sh","-c",f"ent {a.get('path','')}"], 20, R)[:6000]
    def binary_yara(a): return sandboxed(["yara",a.get("rule",""),a.get("path","")], 30, R)[:6000]

    # ========== 6) Code Analysis (8) ==========
    def parse_ast(a): return "parse_ast نیاز به tree-sitter دارد."
    def list_functions(a): return sandboxed(["sh","-c",rf"grep -rEn 'def |function |void |int ' {a.get('path','.')}"], 15, R)[:6000]
    def list_classes(a): return sandboxed(["sh","-c",rf"grep -rEn 'class ' {a.get('path','.')}"], 15, R)[:6000]
    def get_call_graph(a): return "get_call_graph نیاز به ctags دارد."
    def find_cyclomatic_complexity(a): return "find_cyclomatic_complexity نیاز به ابزار تحلیل دارد."
    def find_dead_code(a): return "find_dead_code نیاز به تحلیلگر دارد."
    def find_todos(a): return sandboxed(["grep","-rIn","TODO\\|FIXME",a.get("path",".")], 15, R)[:6000]
    def code_metrics(a): return sandboxed(["sh","-c",f"wc -l $(find {a.get('path','.')} -name '*.py' -o -name '*.js' -o -name '*.cpp')"], 15, R)[:6000]

    # ========== 7) Crypto/Hash (4) ==========
    def hash_file(a):
        full = safe_path(R, a.get("path"))
        if not full: return "مسیر نامعتبر."
        h = hashlib.new(a.get("algo","sha256")); h.update(open(full,"rb").read())
        return h.hexdigest()
    def hash_string(a):
        h = hashlib.new(a.get("algo","sha256")); h.update(a.get("text","").encode())
        return h.hexdigest()
    def base64_encode(a): return base64.b64encode(a.get("text","").encode()).decode()
    def base64_decode(a): return base64.b64decode(a.get("text","")).decode(errors="ignore")

    # ========== 8) Archive (4) ==========
    def list_archive(a): return sandboxed(["tar","-tf",a.get("path","")], 20, R)[:6000]
    def extract_archive(a): return sandboxed(["tar","-xf",a.get("path",""),"-C",a.get("dest",".")], 30, R)
    def create_archive(a): return sandboxed(["tar","-czf",a.get("path","archive.tar.gz")]+a.get("files",[]), 30, R)
    def compress_file(a): return sandboxed(["gzip",a.get("path","")], 20, R)

    # ========== 9) System (4) ==========
    def system_info(a): return sandboxed(["uname","-a"], 5, R)
    def process_list(a): return sandboxed(["ps","aux"], 5, R)[:6000]
    def disk_usage(a): return sandboxed(["df","-h"], 5, R)
    def env_vars(a): return sandboxed(["env"], 5, R)[:6000]

    # ========== 10) Database (2) ==========
    def sqlite_query(a):
        full = safe_path(R, a.get("path"))
        if not full: return "مسیر نامعتبر."
        return sandboxed(["sqlite3",full,a.get("query","")], 20, R)[:6000]
    def sqlite_schema(a):
        full = safe_path(R, a.get("path"))
        if not full: return "مسیر نامعتبر."
        return sandboxed(["sqlite3",full,".schema"], 20, R)[:6000]

    # ========== 11) Meta / Skills / Plugins (5) ==========
    def list_skills(a):
        return "\n".join(f"- {k}: {v['description']}" for k,v in reg.skills.items()) or "هیچ skill یافت نشد."
    def load_skill(a):
        sk = reg.activate_skill(a.get("name",""))
        return f"✅ Skill فعال شد: {a.get('name')}" if sk else "Skill یافت نشد."
    def unload_skill(a):
        reg.deactivate_skill(); return "✅ Skill غیرفعال شد."
    def list_plugins(a):
        return "\n".join(f"- {k}: {v['meta'].get('description','')}" for k,v in reg.plugins.items()) or "هیچ plugin یافت نشد."
    def load_plugin(a):
        reg.load_plugin(a.get("path","")); return "✅ Plugin بارگذاری شد."

    # ========== ثبت همه ==========
    defs = [
        ("list_files","لیست محتویات دایرکتوری.",{"path":{"type":"string"}},[],list_files,"file",True),
        ("read_file","خواندن فایل با شماره خط.",{"path":{"type":"string"},"offset":{"type":"integer"},"limit":{"type":"integer"}},["path"],read_file,"file",True),
        ("write_file","نوشتن یا بازنویسی کامل فایل.",{"path":{"type":"string"},"content":{"type":"string"}},["path","content"],write_file,"file",False),
        ("edit_file","جایگزینی دقیق یک رشته یکتا.",{"path":{"type":"string"},"old_string":{"type":"string"},"new_string":{"type":"string"}},["path","old_string","new_string"],edit_file,"file",True),
        ("append_file","الحاق به انتهای فایل.",{"path":{"type":"string"},"content":{"type":"string"}},["path","content"],append_file,"file",False),
        ("delete_file","حذف فایل.",{"path":{"type":"string"}},["path"],delete_file,"file",False),
        ("move_file","انتقال فایل.",{"src":{"type":"string"},"dst":{"type":"string"}},["src","dst"],move_file,"file",False),
        ("copy_file","کپی فایل.",{"src":{"type":"string"},"dst":{"type":"string"}},["src","dst"],copy_file,"file",False),
        ("file_info","اطلاعات فایل.",{"path":{"type":"string"}},["path"],file_info,"file",False),
        ("create_directory","ساخت دایرکتوری.",{"path":{"type":"string"}},["path"],create_directory,"file",False),
        ("glob_search","پیدا کردن فایل با glob.",{"pattern":{"type":"string"},"path":{"type":"string"}},["pattern"],glob_search,"search",True),
        ("search_code","جستجوی regex در فایل‌ها.",{"query":{"type":"string"},"path":{"type":"string"},"include":{"type":"string"}},["query"],search_code,"search",True),
        ("search_regex","جستجوی regex پیشرفته.",{"query":{"type":"string"},"path":{"type":"string"}},["query"],search_regex,"search",False),
        ("find_symbol","پیدا کردن نماد.",{"symbol":{"type":"string"},"path":{"type":"string"}},["symbol"],find_symbol,"search",False),
        ("find_references","پیدا کردن ارجاعات.",{"symbol":{"type":"string"},"path":{"type":"string"}},["symbol"],find_references,"search",False),
        ("grep_ast","جستجوی ساختاری.",{"query":{"type":"string"}},["query"],grep_ast,"search",False),
        ("git_log","تاریخچه کامیت‌ها.",{"limit":{"type":"integer"}},["limit"],git_log,"git",True),
        ("git_diff","تغییرات نسبت به HEAD~1.",{"path":{"type":"string"}},["path"],git_diff,"git",False),
        ("git_show","جزئیات کامیت.",{"commit":{"type":"string"}},["commit"],git_show,"git",False),
        ("git_blame","blame فایل.",{"path":{"type":"string"}},["path"],git_blame,"git",False),
        ("git_status","وضعیت ریپو.",{},[],git_status,"git",False),
        ("git_branch","لیست برنچ‌ها.",{},[],git_branch,"git",False),
        ("git_checkout","checkout به ref.",{"ref":{"type":"string"}},["ref"],git_checkout,"git",False),
        ("git_stash","stash تغییرات.",{},[],git_stash,"git",False),
        ("bash_execute","اجرای shell.",{"command":{"type":"string"},"timeout":{"type":"integer"}},["command"],bash_execute,"exec",True),
        ("python_execute","اجرای Python.",{"code":{"type":"string"},"timeout":{"type":"integer"}},["code"],python_execute,"exec",True),
        ("node_execute","اجرای Node.js.",{"code":{"type":"string"},"timeout":{"type":"integer"}},["code"],node_execute,"exec",False),
        ("compile_code","کامپایل کد.",{"command":{"type":"string"}},["command"],compile_code,"exec",False),
        ("run_tests","اجرای تست‌ها.",{"command":{"type":"string"}},["command"],run_tests,"exec",False),
        ("install_package","نصب پکیج pip.",{"package":{"type":"string"}},["package"],install_package,"exec",False),
        ("binary_strings","استخراج رشته‌ها.",{"path":{"type":"string"},"min_len":{"type":"integer"}},["path"],binary_strings,"binary",False),
        ("binary_symbols","لیست نمادها.",{"path":{"type":"string"}},["path"],binary_symbols,"binary",False),
        ("binary_headers","ELF headers.",{"path":{"type":"string"}},["path"],binary_headers,"binary",False),
        ("binary_sections","ELF sections.",{"path":{"type":"string"}},["path"],binary_sections,"binary",False),
        ("binary_disassemble","دیس‌اسمبل.",{"path":{"type":"string"}},["path"],binary_disassemble,"binary",False),
        ("binary_hexdump","هکس‌دامپ.",{"path":{"type":"string"},"length":{"type":"integer"}},["path"],binary_hexdump,"binary",False),
        ("binary_entropy","محاسبه آنتروپی.",{"path":{"type":"string"}},["path"],binary_entropy,"binary",False),
        ("binary_yara","اسکن با YARA.",{"rule":{"type":"string"},"path":{"type":"string"}},["rule","path"],binary_yara,"binary",False),
        ("parse_ast","پارس AST.",{"path":{"type":"string"}},["path"],parse_ast,"code",False),
        ("list_functions","لیست توابع.",{"path":{"type":"string"}},["path"],list_functions,"code",False),
        ("list_classes","لیست کلاس‌ها.",{"path":{"type":"string"}},["path"],list_classes,"code",False),
        ("get_call_graph","گراف فراخوانی.",{"path":{"type":"string"}},["path"],get_call_graph,"code",False),
        ("find_cyclomatic_complexity","پیچیدگی سایکلوماتیک.",{"path":{"type":"string"}},["path"],find_cyclomatic_complexity,"code",False),
        ("find_dead_code","کد مرده.",{"path":{"type":"string"}},["path"],find_dead_code,"code",False),
        ("find_todos","یافتن TODO/FIXME.",{"path":{"type":"string"}},["path"],find_todos,"code",False),
        ("code_metrics","متریک‌های کد.",{"path":{"type":"string"}},["path"],code_metrics,"code",False),
        ("hash_file","هش فایل.",{"path":{"type":"string"},"algo":{"type":"string"}},["path"],hash_file,"crypto",False),
        ("hash_string","هش رشته.",{"text":{"type":"string"},"algo":{"type":"string"}},["text"],hash_string,"crypto",False),
        ("base64_encode","Base64 encode.",{"text":{"type":"string"}},["text"],base64_encode,"crypto",False),
        ("base64_decode","Base64 decode.",{"text":{"type":"string"}},["text"],base64_decode,"crypto",False),
        ("list_archive","لیست محتویات آرشیو.",{"path":{"type":"string"}},["path"],list_archive,"archive",False),
        ("extract_archive","استخراج آرشیو.",{"path":{"type":"string"},"dest":{"type":"string"}},["path"],extract_archive,"archive",False),
        ("create_archive","ساخت آرشیو.",{"path":{"type":"string"},"files":{"type":"array","items":{"type":"string"}}},["path","files"],create_archive,"archive",False),
        ("compress_file","فشرده‌سازی.",{"path":{"type":"string"}},["path"],compress_file,"archive",False),
        ("system_info","اطلاعات سیستم.",{},[],system_info,"system",False),
        ("process_list","لیست پروسه‌ها.",{},[],process_list,"system",False),
        ("disk_usage","فضای دیسک.",{},[],disk_usage,"system",False),
        ("env_vars","متغیرهای محیطی.",{},[],env_vars,"system",False),
        ("sqlite_query","کوئری SQLite.",{"path":{"type":"string"},"query":{"type":"string"}},["path","query"],sqlite_query,"db",False),
        ("sqlite_schema","ساختار SQLite.",{"path":{"type":"string"}},["path"],sqlite_schema,"db",False),
        ("list_skills","لیست Skills.",{},[],list_skills,"meta",True),
        ("load_skill","فعال‌سازی Skill.",{"name":{"type":"string"}},["name"],load_skill,"meta",True),
        ("unload_skill","غیرفعال‌سازی Skill.",{},[],unload_skill,"meta",False),
        ("list_plugins","لیست Plugins.",{},[],list_plugins,"meta",False),
        ("load_plugin","بارگذاری Plugin.",{"path":{"type":"string"}},["path"],load_plugin,"meta",False),
    ]

    for name, desc, props, required, func, cat, base in defs:
        schema = {"type":"function","function":{"name":name,"description":desc,
                  "parameters":{"type":"object","properties":props,"required":required}}}
        reg.register_tool(schema, func, category=cat, base=base)
