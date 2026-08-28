# Dependency and Coupling Audit

| Relationship | Current coupling | Evidence / consequence |
|---|---|---|
| Agent → SQLExtractor | Strong concrete import | Agent constructs extractor and owns its lifecycle. |
| Agent → Memory | Strong concrete import | Agent directly creates SQLite manager and injects raw history. |
| Agent → Database | Strong concrete import | Agent owns one connection/cursor for all requests. |
| Agent → LLM | Strong concrete facade | Answer and extraction share one provider/configuration. |
| SQLExtractor → Prompts/Validator | Strong concrete imports | Generation, cleaning and validation are inseparable in one class. |
| Validator → sqlglot | Direct library dependency | Parser output is discarded after pass/fail. |
| API → Agent | Global singleton hidden state | `agent_instance` is module global; async endpoint performs blocking work. |
| UI → API | Weak network boundary | JSON contract is simple, but UI assumes success fields and 200 error envelopes. |
| Schema hash → Column mapper | Strong startup coupling | Hash miss launches subprocess and source regeneration. |
| Column mapper → LLM | Indirect strong coupling | Generator calls LLM and writes Python source. |

No circular import dependency was found in the inspected modules. Hidden/global state includes the API singleton, shared DB cursor, relative files (`logs/`, `data/`, `.schema_cache.json`) and runtime-generated source. Hard-coded policy includes table names, prompt rules, model `gpt-4o-mini`, token/temperature values and 5-message history. The target architecture replaces these with interfaces, request-scoped state and explicit policy objects.
