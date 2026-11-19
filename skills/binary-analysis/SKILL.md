---
name: binary-analysis
description: تحلیل فایل‌های باینری ELF/PE شامل استخراج رشته‌ها، نمادها، سکشن‌ها و دیس‌اسمبل
tools: binary_strings, binary_symbols, binary_headers, binary_sections, binary_disassemble, binary_hexdump, binary_entropy, binary_yara
---

# دستورالعمل تحلیل باینری

وقتی این skill فعال شد، مراحل زیر را دنبال کن:

1. اول با `binary_headers` معماری و نوع فایل را تشخیص بده.
2. با `binary_sections` سکشن‌های مهم (`.text`, `.data`, `.rodata`) را پیدا کن.
3. با `binary_strings` رشته‌های مشکوک (URL, IP, کلید) را استخراج کن.
4. با `binary_symbols` توابع exported/imported را لیست کن.
5. اگر نیاز به تحلیل عمیق‌تر بود، از `binary_disassemble` روی سکشن `.text` استفاده کن.
6. در پاسخ نهایی، آدرس‌ها و آفست‌های دقیق را ذکر کن.
