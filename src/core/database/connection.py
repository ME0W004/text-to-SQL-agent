# src/core/database/connection.py

import pymssql
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseConnection:
    def __init__(self, host, port, user, password, database=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection = None
        self.cursor = None

    def connect(self, database=None):
        db = database or self.database
        try:
            self.connection = pymssql.connect(
                server=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=db,
                timeout=5,
                charset='UTF-8'
            )
            self.cursor = self.connection.cursor(as_dict=True)
            logger.info(f"Connected to database: {db}")
            return self.connection
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

    def close(self):
        if self.connection:
            self.connection.close()
            logger.info("Connection closed.")

    def execute_query(self, query, params=None):
        if not self.connection:
            self.connect()
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            rows = self.cursor.fetchall()
            logger.debug(f"Query executed: {query[:100]}... ({len(rows)} rows)")
            return rows
        except Exception as e:
            logger.error(f"Query execution error: {e}")
            raise

    def execute_non_query(self, query, params=None):
        if not self.connection:
            self.connect()
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            self.connection.commit()
            logger.info("Non-query executed successfully.")
            return True
        except Exception as e:
            self.connection.rollback()
            logger.error(f"Non-query failed: {e}")
            raise