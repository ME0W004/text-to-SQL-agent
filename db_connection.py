# db_connection.py

import pymssql

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
        """Establish connection (uses self.database if not overridden)."""
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
            print(f"Connected to database: {db}")
            return self.connection
        except Exception as e:
            print(f"Connection failed: {e}")
            return None

    def close(self):
        if self.connection:
            self.connection.close()
            print("Connection closed.")

    def execute_query(self, query, params=None):
        """Execute a SELECT query and return results as list of dicts."""
        if not self.connection:
            self.connect()
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            return self.cursor.fetchall()
        except Exception as e:
            print(f"Query execution error: {e}")
            return None

    def execute_non_query(self, query, params=None):
        """Execute INSERT/UPDATE/DELETE and commit."""
        if not self.connection:
            self.connect()
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            self.connection.commit()
            return True
        except Exception as e:
            print(f"Non-query error: {e}")
            self.connection.rollback()
            return False