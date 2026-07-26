# config.py

import os
from dotenv import load_dotenv

load_dotenv()  # loads variables from .env file

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 1433)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

# Optional: check if all keys are present
missing = [k for k, v in DB_CONFIG.items() if v is None]
if missing:
    raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")