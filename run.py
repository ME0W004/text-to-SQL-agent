# run.py

from src.core.database.connection import DatabaseConnection
from src.core.llm.client import LLMClient
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.sql_extractor.validator import SQLValidationError
from src.utils.config_loader import load_config

def main():
    config = load_config()
    db = DatabaseConnection(**config)
    db.connect()

    llm = LLMClient()
    extractor = SQLExtractor(llm)

    print("Text-to-SQL Agent is ready. Type 'exit' to quit.\n")

    while True:
        question = input("Ask a question about sales data: ")
        if question.lower() in ("exit", "quit", "q"):
            break
        if not question.strip():
            continue

        try:
            # Step 1: Generate and validate SQL
            sql = extractor.extract(question)
            print(f"\nGenerated SQL:\n{sql}\n")

            # Step 2: Execute SQL
            rows = db.execute_query(sql)
            if not rows:
                print("No data found for this query.")
                continue

            print(f"Results ({len(rows)} rows):")
            for row in rows[:5]:
                print(row)
            if len(rows) > 5:
                print(f"... and {len(rows)-5} more rows.")

            # Step 3: Generate natural language answer
            summary = f"The query returned {len(rows)} rows. Sample: {rows[:3]}"
            prompt_answer = f"""User asked: {question}
Results: {summary}
Give a concise answer in Persian (فارسی). Be clear and informative.
Answer:"""
            answer = llm.generate(prompt_answer)
            print(f"\nAnswer: {answer}\n")

        except SQLValidationError as e:
            print(f"\nValidation Error: {e.message}")
            if e.sql:
                print(f"Generated SQL (rejected):\n{e.sql}")
            print("Please rephrase your question or try a different query.")
        except Exception as e:
            print(f"\nError: {e}")

    db.close()

if __name__ == "__main__":
    main()