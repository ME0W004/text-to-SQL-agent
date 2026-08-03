# src/core/prompts.py

from src.core.database.models import FULL_SCHEMA
from src.core.sql_extractor.few_shots import FEW_SHOT_EXAMPLES

class SystemPrompts:

    @staticmethod
    def sql_generation_prompt(question: str, history: list = None) -> str:
        
        prompt = f"""You are an expert in T-SQL for Microsoft SQL Server.
Convert the user's natural language question into a valid T-SQL query.

Database Schema (Microsoft SQL Server):
{FULL_SCHEMA}

Rules:
1. Only use the tables and columns defined in the schema above.
2. Use TOP instead of LIMIT.
3. Use GETDATE() for current date.
4. Always use proper JOIN syntax with ON clause.
5. For Persian dates, use the 'jalaliYearNo' column.
6. Return only the SQL query, no extra text or explanations.

Examples:
"""
        for ex in FEW_SHOT_EXAMPLES:
            prompt += f"\nQuestion: {ex['question']}\nSQL: {ex['sql']}\n"

        if history:
            prompt += "\nConversation history:\n"
            for msg in history[-5:]:
                prompt += f"{msg['role']}: {msg['content']}\n"

        prompt += f"\nQuestion: {question}\nSQL:"
        return prompt

    @staticmethod
    def natural_response_prompt(question: str, rows: list, row_count: int) -> str:
       
        sample = rows[:5] if rows else []
        summary = f"The query returned {row_count} rows."
        if sample:
            summary += f"\nSample data (first {len(sample)} rows):\n"
            for row in sample:
                summary += str(row) + "\n"
        else:
            summary += " No data found."

        prompt = f"""You are a helpful assistant that explains SQL query results in Persian (فارسی).
The user asked: "{question}"
Results: {summary}

Please provide a clear, concise answer in **Markdown format** with the following structure:

1. **Summary**: A brief overview of the results.
2. **Key insights**: Bullet points with the most important findings.
3. **Data table** (if applicable): Use a Markdown table to display the results.
4. **Conclusion**: A short closing statement.

Use proper Persian formatting. Be informative but not overly detailed.

Answer:"""
        return prompt

    @staticmethod
    def error_correction_prompt(question: str, error_message: str, invalid_sql: str) -> str:
        """
           for future
                """
        prompt = f"""The previous SQL query was invalid and produced this error:
Error: {error_message}

Invalid SQL:
{invalid_sql}

User's original question: "{question}"

Please generate a corrected SQL query that fixes the error.
Follow the schema and rules strictly.
Only return the SQL query, no extra text.

SQL:"""
        return prompt