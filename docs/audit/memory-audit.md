# Memory Audit

SQLite data/conversations.db stores sessions(id, created_at, updated_at) and messages(id, session_id, role, content, created_at). UUID sessions are created, checked and deletable; UI reset only clears local state and leaves server sessions orphaned. History is the latest five messages by created_at descending, reversed in Python; same-second ordering is nondeterministic. There is no explicit index, expiry, cleanup, token budget, summary, provenance or transactional user+assistant turn. Only role/content is injected into SQL prompting. Long conversations lose context and raw history can pollute prompts; CLI creates a fresh session per question.

