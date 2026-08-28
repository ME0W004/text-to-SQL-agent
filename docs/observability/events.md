# Event Taxonomy

Implemented events include:

- Request/agent: `request_started`, `request_completed`, `request_failed`, `agent_started`, `agent_completed`, `agent_failed`
- SQL/security: `sql_generated`, `sql_validation_started`, `sql_validation_passed`, `sql_validation_failed`
- Database: `db_connection_opened`, `db_connection_failed`, `db_connection_closed`, `db_execution_started`, `db_execution_completed`, `db_execution_failed`, `db_timeout`, `db_cursor_close_failed`, `db_connection_close_failed`
- LLM: `llm_call_started`, `llm_call_completed`, `llm_call_failed`, `llm_provider_failed`, `llm_client_init_failed`
- Response: `response_generated`

Database and LLM events include duration where measurable. SQL events include only a SHA-256 fingerprint, never SQL text. Error events include stable error codes/types rather than raw vendor messages.
