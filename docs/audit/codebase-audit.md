# Codebase Audit

Inspected all Python files under src, launchers, configuration (pyproject.toml, uv.lock, .env.example), scripts, tests, README/report and existing docs/evaluation files. Tests contain only empty __init__.py packages; no automated behavior tests were found. pyproject includes unused PostgreSQL/MySQL/SQLAlchemy dependencies alongside SQL Server drivers. .env.example contains concrete infrastructure/provider values rather than safe placeholders. src/schemas/__init__.py is empty. Existing current-system audits are detailed and were used as evidence.

Unknown: actual DB permissions of the configured principal and deployment worker topology require external inspection. Live profile facts are retained from existing database-profile docs, not newly guessed.

