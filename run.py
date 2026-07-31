# run.py

from src.core.database.connection import DatabaseConnection
from src.utils.config_loader import load_config

config = load_config()
db = DatabaseConnection(**config)

try:
    db.connect()
    rows = db.execute_query("SELECT TOP 5 * FROM Sale.Orders")
    for row in rows:
        print(row)
finally:
    db.close()