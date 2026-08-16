# src/core/prompts.py

from src.core.database.models import FULL_SCHEMA
from src.core.sql_extractor.few_shots import FEW_SHOT_EXAMPLES

class SystemPrompts:
    """Centralized class for all system prompts."""

    @staticmethod
    def sql_generation_prompt(question: str, history: list = None) -> str:
        """
        Generate SQL from user question with clear rules and guidelines.
        """
        prompt = f"""You are an expert in T-SQL for Microsoft SQL Server.
Your task is to convert the user's natural language question into a valid T-SQL query.

**Database Schema:**
{FULL_SCHEMA}

**Rules (STRICT):**
1. Only use the tables and columns defined in the schema above.
2. Use `TOP` instead of `LIMIT` for limiting results.
3. Use `GETDATE()` for the current date.
4. For Persian (Shamsi) date filtering, ALWAYS use the `jalaliYearNo` column (e.g., `WHERE o.jalaliYearNo = '1399'`).
5. For grouping by month, use the `operationMonth` column (e.g., `GROUP BY o.operationMonth`).
6. For aggregations, use the correct column names: `quantity`, `unitPrice`, `productGroup`, etc.
7. Always use table aliases (e.g., `o` for Orders, `oi` for OrderItems).
8. Return ONLY the SQL query, no extra text or explanations.

**Examples:**
"""
        for ex in FEW_SHOT_EXAMPLES:
            prompt += f"\nQuestion: {ex['question']}\nSQL: {ex['sql']}\n"

        if history:
            prompt += "\n**Conversation history:**\n"
            for msg in history[-5:]:
                prompt += f"{msg['role']}: {msg['content']}\n"

        prompt += f"\n**User question:** {question}\n**SQL query:**"
        return prompt

    @staticmethod
    def natural_response_prompt(question: str, rows: list, row_count: int) -> str:
        """
        Generate natural language response in Persian with Markdown format.
        """
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
        Generate prompt for correcting SQL query errors (for auto-correction loop).
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