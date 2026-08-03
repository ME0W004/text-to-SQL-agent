
### 1. کلون پروژه

```bash
git clone https://github.com/ME0W004/text-to-SQL-agent.git
```

### 2. آماده سازی محیط 

```bash
pip install uv
```
```bash
uv venv
```

### 3. فعال‌سازی محیط مجازی


```bash
.venv\Scripts\activate
```



### 4. نصب وابستگی‌ها

```bash
uv sync
```


### 5. تنظیم فایل محیطی

```bash
cp .env.example .env
```

سپس فایل `.env` را ویرایش کرده و اطلاعات واقعی را وارد کنید:

```env
DB_HOST=******
DB_PORT=1433
DB_USER=******
DB_PASSWORD=*******
DB_NAME=******
OPENAI_API_KEY=*******
OPENAI_BASE_URL=***********
GOOGLE_API_KEY=****************************
GROQ_API_KEY=****************************
```

### 6. اجرای پروژه

```bash
uv run python run.py
```
