# بستهٔ بازطراحی موتور اجماع دو عامله Dex-tool

این بسته برای جایگزینی فایل‌های مربوط به موتور تحلیل و workflow فعلی طراحی شده است. هیچ patchی را خودکار روی سورس اعمال نمی‌کند.

## فایل‌ها
- `scripts/gather_code.py`: snapshot قطعی، manifest با SHA-256 و chunking بر اساس فایل/خط؛ پوشهٔ scripts دیگر حذف نمی‌شود.
- `scripts/peer_consensus.py`: اجرای streamشدهٔ llama.cpp، تحلیل مستقل همهٔ chunkها در دور اول، نقد متقابل در دورهای بعد، ثبت stdout/stderr و transcript کامل.
- `scripts/select_candidate.py`: انتخاب قطعی یک patch مشترک برای نقد هر دو عامل.
- `scripts/validate_consensus.py`: اجماع فقط وقتی پذیرفته می‌شود که هر دو مدل همان issue و همان SHA-256 patch را صریحاً تأیید کرده باشند و snapshot یکسان باشد.
- `.github/workflows/peer-round.yml`: اجرای موازی Qwen و GLM و داوری مرکزی.
- `.github/workflows/consensus-loop.yml`: حداکثر ۷ دور، توقف زودهنگام فقط با اجماع معتبر.
- `skills/REVIEW_SKILLS.md`: مهارت‌های تخصصی متناسب با معماری واقعی Dex-tool: C++ concurrency/lifetime، JNI، Android NDK/ABI، Frida GumJS، ImGui/EGL و build.
- `scripts/bootstrap_local_tools.py` و `requirements-analysis.txt`: نصب ابزارهای محلی بدون API هوش مصنوعی.
- `scripts/android_native_audit.py`: ممیزی آفلاین مسیرهای native، JNI، Frida، ImGui، EGL و syntax عامل JavaScript.
- `scripts/binary_artifact_audit.py`: استخراج metadata از APK/DEX/ELF با Androguard و LIEF؛ هیچ باینری اجرا نمی‌شود.
- `scripts/run_static_checks.py`: اجرای cppcheck، clang-tidy در صورت وجود compile database، clang-format در صورت وجود config، ShellCheck، Ruff، Bandit و actionlint/zizmor در صورت نصب.

## نصب روی main
1. ابتدا از فایل‌های فعلی و workflowها یک branch پشتیبان بساز.
2. فایل‌های این بسته را با همان مسیرها در ریشهٔ مخزن کپی کن.
3. تغییرات را commit کن و workflow با نام `Dex-tool - Dual Agent Evidence-Based Consensus (7 Rounds)` را اجرا کن.

## تنظیمات اختیاری Repository Variables
- `QWEN_CONTEXT`: پیش‌فرض 32768؛ برای Qwen مقدار بزرگ‌تر نگذار مگر اینکه metadata همان GGUF و محدودیت واقعی مدل تأیید شده باشد.
- `GLM_CONTEXT`: پیش‌فرض 32768؛ فقط پس از بررسی metadata همان فایل GGUF افزایش بده.

## نکتهٔ ظرفیت
تحلیل chunk به chunk باعث می‌شود کل سورس بررسی شود بدون اینکه یک prompt عظیم 123K توکنی به context محدود مدل تزریق شود. دور اول عمداً می‌تواند طولانی باشد چون هر دو مدل همهٔ chunkها را جداگانه بررسی می‌کنند. دورهای ۲ تا ۷ روی candidate مشترک و source evidence مرتبط تمرکز می‌کنند.

## ابزارهای محلی اضافه‌شده
- ابزارهای Python: Ruff، Bandit، Pytest، Androguard، LIEF و zizmor.
- ابزارهای native: Cppcheck، Clang-Tidy، Clang-Format، CMake، Ninja و ShellCheck.
- ابزار JavaScript: Node.js syntax check برای `agent/agent.js`.
- ابزارهای workflow: actionlint و zizmor در صورت موجود بودن روی runner.
- ابزارهای تحلیل Android/DEX/ELF: Androguard و LIEF به‌صورت کتابخانه‌های محلی؛ این‌ها نیازمند API یا کلید سرویس نیستند.
- ابزارهای تست ثابت داخل Docker: cppcheck و Node syntax check؛ suite کامل Android build فقط وقتی dependencyهای از پیش ساخته‌شده موجود باشند اجرا می‌شود. در غیر این صورت صریحاً `skipped` گزارش می‌شود، نه موفقیت جعلی.

## ابزارها و پلاگین‌ها
این دو مدل محلی GGUF به‌صورت پیش‌فرض به ChatGPT Plugins، APIهای خصوصی یا MCP serverها وصل نیستند. این بسته مهارت‌های تحلیلی را به شکل prompt/checklist تعریف می‌کند، یک لایهٔ tool-calling فقط‌خواندنی و محدود برای عامل‌ها دارد و از analyzerهای محلی موجود در runner استفاده می‌کند. برای ابزارهای اضافی واقعی باید آن‌ها را نصب و در workflow اجرا کرد؛ برای سرویس‌های بیرونی هم باید Secret امن و مجوز مناسب تعریف شود. لایهٔ ابزار فعلی عمداً فقط‌خواندنی است؛ مدل اجازهٔ اجرای shell دلخواه، دسترسی شبکه، نوشتن/حذف فایل یا خواندن فایل‌های Secret را ندارد. هیچ‌وقت Secret را داخل prompt یا artifact قرار نده.

## محدودیت‌ها و وضعیت اعتبارسنجی
این بسته هنوز روی runner واقعی GitHub Actions و با فایل‌های GGUF واقعی اجرا نشده است. نصب ابزارهای اضافی در هر job زمان و پهنای‌باند مصرف می‌کند؛ برای کاهش زمان، pip cache فعال است. ابزارهای تحلیل آفلاین‌اند و هیچ API یا کلید هوش مصنوعی لازم ندارند. قبل از ادغام نهایی، workflow را روی branch آزمایشی اجرا کن و لاگ‌های دانلود مدل، `llama-completion --help`, مصرف RAM، زمان هر chunk و حجم artifact را بررسی کن. اجماع به معنی تأیید دو عامل است، نه اثبات ریاضی درستی patch؛ patch باید جداگانه در محیط تست اعمال و build/test شود.


## سندباکس اجرای واقعی کد برای عامل‌ها
- هر دو عامل ابزار `sandbox_run` دارند و می‌توانند یک unified diff را در یک کپی موقت از مخزن آزمایش کنند.
- اجرای تست داخل Docker انجام می‌شود؛ کپی موقت سورس به‌صورت writable mount می‌شود تا build/test بتواند فایل تولید کند، اما ریشهٔ فایل‌سیستم کانتینر read-only است.
- کانتینر با `--network none`، بدون Docker socket، بدون Secret، با حذف Linux capabilities، `no-new-privileges` و محدودیت CPU/RAM/PID/زمان اجرا می‌شود.
- مدل فقط نام یک suite ثابت را انتخاب می‌کند (`auto`, `native`, `python`, `cmake`, `node`, `go`, `rust`, `shell`) و اجازهٔ فرستادن دستور shell دلخواه ندارد.
- patch ابتدا با `git apply --check` بررسی و فقط روی کپی موقت اعمال می‌شود. فایل‌های اصلی checkout هرگز توسط sandbox تغییر نمی‌کنند.
- suiteهای auto بر اساس manifestهای موجود اجرا می‌شوند. نبودن manifest یا تست به معنی موفقیت تست نیست؛ نتیجهٔ `skipped` جداگانه گزارش می‌شود.
- تست‌های پروژه عمداً از میزبان runner خارج شده‌اند؛ `run_static_checks.py` فقط تحلیل‌گرهای ایستا و syntax checks را اجرا می‌کند. اجرای test/build واقعی از ابزار sandbox انجام می‌شود.
- تصویر Docker با Buildx و cache جداگانه برای Qwen و GLM ساخته می‌شود؛ لایه‌های apt/pip بین دورها cache می‌شوند.
- گزارش تحلیل سورس با کلید commit cache می‌شود تا تحلیل ایستا در هر یک از هفت دور بی‌دلیل تکرار نشود.

**محدودیت مهم:** این sandbox لایهٔ ایزوله‌سازی عملی برای اجرای کد غیرقابل‌اعتماد در GitHub-hosted runner است، نه تضمین امنیتی مطلق در برابر آسیب‌پذیری‌های kernel/Docker. از self-hosted runner حساس یا runner دارای credentialهای ارزشمند استفاده نکن. هیچ Secret را به کانتینر mount نکن. فایل‌های حساس رایج، `.env`ها و symlinkها از snapshot و کپی سندباکس حذف می‌شوند.

## اصلاح runtime مربوط به llama.cpp (نسخهٔ رفع خطای shared library)

در workflow، به‌جای کپی‌کردن تنها فایل `llama-completion`، کل آرشیو release در `llama.cpp/runtime/` نگهداری می‌شود تا کتابخانه‌های `.so` همراه آن باقی بمانند. پس از restore از cache یا دانلود تازه، اسکریپت `scripts/prepare_llama_runtime.sh` مسیر executable و پوشه‌های کتابخانه را پیدا می‌کند، `LD_LIBRARY_PATH` و `LLAMA_BIN` را تنظیم می‌کند، با `ldd` وابستگی‌های unresolved را بررسی می‌کند و اجرای `--version` را بدون مخفی‌کردن خطا اعتبارسنجی می‌کند. کلید cache به v3 تغییر کرده تا cache قدیمی که فقط executable را داشت استفاده نشود.

این اصلاح در محیط محلی بدون فایل runtime واقعی llama.cpp فقط از نظر ساختار workflow، syntax و تست‌های واحد بررسی شده است؛ تأیید نهایی وابسته به اجرای workflow روی GitHub Actions و دریافت آرشیو release است.
