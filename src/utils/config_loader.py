# src/utils/config_loader.py

import os
from dotenv import load_dotenv

class ConfigurationError(Exception):
    pass

def load_config():
    load_dotenv()

    # مپینگ نام متغیرهای .env به نام پارامترهای کلاس
    mapping = {
        "DB_HOST": "host",
        "DB_PORT": "port",
        "DB_USER": "user",
        "DB_PASSWORD": "password",
        "DB_NAME": "database",
    }

    required_keys = list(mapping.keys())
    config = {}
    missing = []

    for env_key, param_key in mapping.items():
        value = os.getenv(env_key)
        if value is None:
            missing.append(env_key)
        else:
            if env_key == "DB_PORT":
                value = int(value)
            config[param_key] = value

    if missing:
        raise ConfigurationError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return config