"""Architecture tests — dependency rules enforced mechanically.

Sprint 2 Prompt 2B introduces these tests as part of Step 2B.30. They
walk the import graph of every component module and assert that:

* No domain component module imports ``src.agents.state`` (the
  orchestrator is the sole writer of ``AgentState`` — ADR-013).
* No domain component module imports ``pymssql`` (the database boundary
  is single — ADR-006).
* No domain component module imports the LLM provider SDKs
  (``OpenAIClient``, ``GeminiClient``, ``GroqClient``, ``OllamaClient``).
* No domain component module imports ``MemoryManager`` directly.
* No domain component module imports the FastAPI ``src.api`` package.

The tests use static AST inspection so they work without import side
effects. They cover the modules introduced in Sprint 2 Prompt 2B and
the modules they introduce (``contracts.py``, ``orchestrator.py``,
``adapters.py``).

For modules that already exist and may legitimately import infra
(current implementations), the tests enforce the rule for the new
contract modules only. For example, ``adapters.py`` may import
``DatabaseConnection`` because the whole point of the adapter is to
expose it as ``QueryExecutor`` — but it must not import ``AgentState``
itself.
"""