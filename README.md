
# Text-to-SQL Agent

یک ایجنت هوشمند برای تبدیل سوالات طبیعی به کوئری SQL روی دیتابیس Microsoft SQL Server با قابلیت دسترسی از طریق API و رابط کاربری وب.

---

## ویژگی‌ها

- تبدیل زبان طبیعی به کوئری T-SQL
- پشتیبانی از چندین مدل زبانی (OpenAI، Groq، Gemini)
- اعتبارسنجی خودکار کوئری‌ها (فقط SELECT)
- خروجی پاسخ به صورت Markdown با جداول و نکات کلیدی
- حذف خودکار ستون‌های Id از خروجی جدول
- ترجمه نام ستون‌ها به فارسی
- API RESTful با FastAPI
- رابط کاربری وب با Streamlit
- تاریخچه مکالمه در جلسات کاربری

---

## پیش‌نیازها

- Python 3.11 یا بالاتر
- [uv](https://docs.astral.sh/uv/) برای مدیریت وابستگی‌ها
- دسترسی به دیتابیس SQL Server با اطلاعات اتصال
- کلید API برای حداقل یکی از سرویس‌های: OpenAI / Groq / Gemini

---

## مراحل اجرا

### ۱. کلون کردن پروژه

```bash
git clone https://github.com/ME0W004/text-to-SQL-agent.git
cd text-to-SQL-agent
```

### ۲. ایجاد محیط مجازی با uv

```bash
uv venv
```

### ۳. فعال‌سازی محیط مجازی

**ویندوز:**

```bash
.venv\Scripts\activate
```

**لینوکس / مک:**

```bash
source .venv/bin/activate
```

### ۴. نصب وابستگی‌ها

```bash
uv sync
```

### ۵. تنظیم فایل محیطی

```bash
cp .env.example .env
```

سپس فایل `.env` را ویرایش کرده و اطلاعات واقعی را وارد کنید:

```env
# اتصال به دیتابیس
DB_HOST=your_host
DB_PORT=1433
DB_USER=your_username
DB_PASSWORD=your_password
DB_NAME=your_database

# تنظیمات API (برای Streamlit)
API_URL=http://localhost:8000/api/v1

# کلیدهای سرویس‌های زبانی (حداقل یکی)
OPENAI_API_KEY=sk-...
GROQ_API_KEY=...
GOOGLE_API_KEY=...
```

### ۶. (اختیاری) تولید نگاشت کامل نام ستون‌ها به فارسی

برای اینکه تمام ستون‌های خروجی به فارسی ترجمه شوند، یک اسکریپت وجود دارد که با استفاده از LLM، نگاشت کامل را تولید می‌کند:

```bash
uv run python scripts/generate_column_map.py
```

این اسکریپت تمام ستون‌های جداول `Orders`، `OrderItems` و `Shipments` را از دیتابیس خوانده، با کمک مدل زبانی به فارسی ترجمه کرده و فایل `src/core/column_mapper.py` را تولید می‌کند.

> **نکته:** اگر این مرحله را انجام ندهید، ستون‌های ترجمه‌نشده به همان زبان انگلیسی در خروجی نمایش داده می‌شوند.

### ۷. اجرای پروژه

#### ۷-۱. اجرای سرور API (FastAPI)

```bash
uv run python run_api.py
```

سرور API روی آدرس `http://localhost:8000` در دسترس خواهد بود.

#### ۷-۲. اجرای رابط کاربری (Streamlit)

در یک ترمینال جدید:

```bash
uv run streamlit run src/web/app.py
```

رابط کاربری روی آدرس `http://localhost:8501` در دسترس خواهد بود.

> **نکته:** برای استفاده از رابط کاربری، سرور API باید در حال اجرا باشد.

---

## ساختار پروژه

```
text-to-sql-agent/
├── scripts/
│   └── generate_column_map.py   # اسکریپت تولید نگاشت فارسی ستون‌ها
├── src/
│   ├── agents/                  # منطق اصلی ایجنت
│   ├── api/                     # API با FastAPI
│   │   ├── main.py
│   │   ├── models.py
│   │   └── routes.py
│   ├── core/                    # هسته اصلی
│   │   ├── database/            # اتصال و مدل دیتابیس
│   │   ├── llm/                 # کلاینت‌های مدل زبانی
│   │   ├── sql_extractor/       # تولید و اعتبارسنجی SQL
│   │   ├── column_mapper.py     # نگاشت نام ستون‌ها (تولید خودکار)
│   │   └── prompts.py           # پرامپت‌های سیستم
│   └── web/                     # رابط کاربری Streamlit
│       └── app.py
├── run.py                       # اجرای خط فرمان (CLI)
├── run_api.py                   # اجرای سرور API
├── run_ui.py                    # اجرای رابط کاربری (جایگزین)
├── .env.example
├── pyproject.toml
└── uv.lock
```

---

## نحوه استفاده

1. سرور API را با `uv run python run_api.py` اجرا کنید.
2. در ترمینال جدید، Streamlit را با `uv run streamlit run src/web/app.py` اجرا کنید.
3. مرورگر را در آدرس `http://localhost:8501` باز کنید.
4. سوال خود را به زبان طبیعی مطرح کنید و پاسخ را دریافت کنید.

### خروجی نمونه

پاسخ دریافتی شامل موارد زیر است:
- خلاصه و نکات کلیدی به صورت Markdown
- جدول داده‌ها با ستون‌های ترجمه‌شده به فارسی (بدون ستون‌های Id)
- کوئری SQL تولیدشده (قابل مشاهده در بخش `View SQL Query`)

---

## عیب‌یابی

| خطا | راه‌حل |
|------|-------|
| `Connection to API failed` | مطمئن شوید سرور API در حال اجراست (`run_api.py`). |
| `Login failed for user` | اطلاعات `.env` را بررسی کنید. |
| `ModuleNotFoundError` | `uv sync` را اجرا کنید. |
| `Import "fastapi" could not be resolved` | محیط مجازی را در VS Code انتخاب کنید. |
| `Missing translation for column` | اسکریپت `generate_column_map.py` را اجرا کنید تا نگاشت کامل تولید شود. |

---

## مشارکت

1. مخزن را Fork کنید.
2. برنچ ویژگی خود را ایجاد کنید (`git checkout -b feature/amazing-feature`).
3. تغییرات را کامیت کنید (`git commit -m 'Add some amazing feature'`).
4. به برنچ خود پوش کنید (`git push origin feature/amazing-feature`).
5. یک Pull Request باز کنید.

---

## مجوز

این پروژه برای استفاده داخلی توسعه داده شده است.
```