# src/core/schema_hash.py

import hashlib
import json
from src.core.database.connection import DatabaseConnection


def get_schema_hash(db_conn) -> str:
    """
    Calculate a hash of the current schema for Orders, OrderItems, and Shipments.
    The hash changes if any column is added, removed, or renamed.
    """
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT 
            TABLE_NAME,
            COLUMN_NAME,
            DATA_TYPE,
            IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'Sale'
          AND TABLE_NAME IN ('Orders', 'OrderItems', 'Shipments')
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)
    rows = cursor.fetchall()

    # Create a stable string representation of the schema
    schema_str = ""
    for row in rows:
        schema_str += f"{row[0]}.{row[1]}.{row[2]}.{row[3]}|"

    # Return MD5 hash of the schema string
    return hashlib.md5(schema_str.encode("utf-8")).hexdigest()