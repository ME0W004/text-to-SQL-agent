# src/core/sql_extractor/extractor.py

import re
from src.core.database.models import FULL_SCHEMA
from src.core.llm.client import LLMClient
from src.core.sql_extractor.validator import SQLValidator, SQLValidationError

class SQLExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.schema = FULL_SCHEMA
        self.validator = SQLValidator()
        self.few_shot_examples = [
            {
                "question": "۱۰ مشتری برتر سال ۱۴۰۳ چه کسانی بودند؟",
                "sql": """
                    SELECT TOP 10 buyer, SUM(oi.quantity * oi.unitPrice) AS total_spent
                    FROM Sale.Orders o
                    JOIN Sale.OrderItems oi ON o.Id = oi.orderId
                    WHERE o.jalaliYearNo = '1403'
                    GROUP BY buyer
                    ORDER BY total_spent DESC
                """
            },
            {
                "question": "فروش هر محصول در سه ماه گذشته چقدر بوده است؟",
                "sql": """
                    SELECT oi.productGroup, SUM(oi.quantity * oi.unitPrice) AS revenue
                    FROM Sale.OrderItems oi
                    JOIN Sale.Orders o ON oi.orderId = o.Id
                    WHERE o.entityDate >= DATEADD(month, -3, GETDATE())
                    GROUP BY oi.productGroup
                    ORDER BY revenue DESC
                """
            },
            {
                "question": "مجموع فروش هر گروه محصول در سال 1399 چقدر بوده است؟",
                "sql": """
                    SELECT oi.productGroup, SUM(oi.quantity * oi.unitPrice) AS total_sales
                    FROM Sale.OrderItems oi
                    JOIN Sale.Orders o ON oi.orderId = o.Id
                    WHERE o.jalaliYearNo = '1399'
                    GROUP BY oi.productGroup
                    ORDER BY total_sales DESC
                """
            }
        ]

    def _build_prompt(self, question: str) -> str:
        prompt = f"""You are an expert in T-SQL for Microsoft SQL Server.
        Convert the user's question into a valid SQL query.

        Database Schema:
        {self.schema}

        Examples:
        """
        for ex in self.few_shot_examples:
            prompt += f"\nQuestion: {ex['question']}\nSQL: {ex['sql']}\n"

        prompt += f"\nQuestion: {question}\nSQL:"
        return prompt

    def _clean_sql(self, raw_sql: str) -> str:
        sql = raw_sql.strip()
        # Remove markdown code blocks
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]

        # Remove any text before the first SELECT or WITH
        match = re.search(r'(WITH|SELECT)\s+', sql, re.IGNORECASE)
        if match:
            sql = sql[match.start():]

        sql = sql.rstrip(';').strip()
        return sql

    def extract(self, question: str) -> str:
        prompt = self._build_prompt(question)
        raw_sql = self.llm.generate(prompt)

        sql = self._clean_sql(raw_sql)

        # Validate
        try:
            self.validator.validate(sql)
        except SQLValidationError as e:
            # Re-raise with the generated SQL attached
            raise SQLValidationError(f"{e.message}", sql=sql)

        return sql