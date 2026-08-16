# src/core/column_mapper_updater.py

import os
import json
import subprocess
import sys
from src.core.database.connection import DatabaseConnection
from src.core.schema_hash import get_schema_hash
from src.utils.logger import get_logger

logger = get_logger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".schema_cache.json")


def _load_cache() -> dict:
    """Load the schema hash cache from disk."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"schema_hash": None}


def _save_cache(schema_hash: str):
    """Save the schema hash cache to disk."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_hash": schema_hash}, f, indent=2)


def _run_column_map_generator():
    """Execute the generate_column_map.py script."""
    script_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "generate_column_map.py"
    )
    if not os.path.exists(script_path):
        logger.error(f"Generator script not found: {script_path}")
        return False

    try:
        # Run the script using the same Python interpreter
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(script_path)
        )
        if result.returncode == 0:
            logger.info("Column map generated successfully.")
            return True
        else:
            logger.error(f"Generator script failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error running generator script: {e}")
        return False


def ensure_column_map_is_updated(db_conn: DatabaseConnection) -> bool:
    """
    Check if the schema has changed. If so, regenerate column_mapper.py.
    Returns True if the map is up-to-date, False if an error occurred.
    """
    try:
        # Calculate current schema hash
        current_hash = get_schema_hash(db_conn.connection)

        # Load cached hash
        cache = _load_cache()
        cached_hash = cache.get("schema_hash")

        if current_hash == cached_hash:
            logger.info("Schema unchanged. Using existing column map.")
            return True

        # Schema has changed - regenerate the column map
        logger.info("Schema change detected. Regenerating column map...")
        success = _run_column_map_generator()

        if success:
            # Update cache with new hash
            _save_cache(current_hash)
            logger.info("Column map updated and cache saved.")
            return True
        else:
            logger.error("Failed to regenerate column map.")
            return False

    except Exception as e:
        logger.error(f"Error checking schema: {e}")
        return False