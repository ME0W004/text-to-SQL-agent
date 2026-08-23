# run.py

from src.agents.text_to_sql_agent import TextToSQLAgent

def main():
    agent = TextToSQLAgent()
    print("Text-to-SQL Agent is ready. Type 'exit' to quit.\n")

    while True:
        question = input("Ask a question about sales data: ")
        if question.lower() in ("exit", "quit", "q"):
            break
        if not question.strip():
            continue

        result = agent.ask(question)

        if not result.get("success", False):
            print(f"\nError: {result.get('error', 'Unknown error')}")
            if result.get("sql"):
                print(f"Generated SQL (rejected):\n{result['sql']}")
            print("Please rephrase your question or try a different query.\n")
            continue

        print(f"\nGenerated SQL:\n{result['sql']}\n")
        print(f"Results ({result['row_count']} rows):")
        for row in result['rows'][:5]:
            print(row)
        if result['row_count'] > 5:
            print(f"... and {result['row_count']-5} more rows.")

        print(f"\nAnswer: {result['answer']}\n")

    agent.close()

if __name__ == "__main__":
    main()