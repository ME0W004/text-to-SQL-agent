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
9. Always include an `ORDER BY` clause with a relevant column for sorted results (e.g., `ORDER BY total_sales DESC`).
10. For calculating revenue, use `quantity * unitPrice`.
11. Only use the tables: Sale.Orders, Sale.OrderItems, Sale.Shipments.
12. For joining tables, use the following foreign key relationships:
    - Sale.Orders.Id = Sale.OrderItems.orderId
    - Sale.OrderItems.Id = Sale.Shipments.orderItemId
13. For filtering on aggregated values (e.g., SUM, AVG, COUNT), ALWAYS use the HAVING clause, never WHERE.
14. For conditional counting, use `SUM(CASE WHEN condition THEN 1 ELSE 0 END)`.
15. When you need to include rows that may not have matching records in the joined table, use LEFT JOIN instead of INNER JOIN.
16. Distinguish between "count" (تعداد) and "total amount" (مجموع مبلغ):
   - For "تعداد پرداختی" or "تعداد سفارش", use COUNT.
   - For "مجموع مبلغ" or "ارزش کل", use SUM(quantity * unitPrice).

**IMPORTANT about conversation history:**
- The conversation history is provided ONLY for context to understand references like "them", "it", or "those" from previous questions.
- You MUST generate the SQL query based SOLELY on the current user question, not on the history content.
- Do NOT let the history influence the structure or logic of the SQL query beyond resolving ambiguous references.

**Examples:**
"""
        for ex in FEW_SHOT_EXAMPLES:
            prompt += f"\nQuestion: {ex['question']}\nSQL: {ex['sql']}\n"

        if history:
            prompt += "\n**Conversation history (for reference only):**\n"
            for msg in history[-5:]:
                prompt += f"{msg['role']}: {msg['content']}\n"
            prompt += "\n**Focus ONLY on the current question when generating the SQL query.**\n"

        prompt += f"\n**Current user question:** {question}\n**SQL query:**"
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

**IMPORTANT:** You MUST respond in **pure Markdown format** without any extra text, code blocks, or backticks.
Your response must start directly with a Markdown heading (e.g., `# خلاصه`) and contain only the following sections:

1. **# Summary**: A brief overview of the results (e.g., total rows, key metrics).
2. **## Key insights**: Bullet points with the most important findings (e.g., highest/lowest values, trends). If the data contains numeric values, include totals, averages, or percentages where relevant.
3. **## Data table**: A Markdown table with the results. Only show the most relevant columns.
4. **## Conclusion**: A short closing statement with actionable insights or recommendations.

Do NOT include any text before the first heading. Do NOT use code blocks. Do NOT use backticks. Only use Markdown syntax.

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