# LLM System Audit

> Scope: everything under `src/core/llm/` plus every call site that invokes an LLM. Facts from code at commit `e6b53d6`; runtime evidence from `logs/`.

---

## 1. Providers: documented vs implemented vs wired

| Provider | Client file | Default model | Implements `BaseLLMClient`? | Instantiated anywhere? | Verdict |
|---|---|---|---|---|---|
| OpenAI(-compatible) | `openai_client.py` (`OpenAIClient`) | `gpt-4o-mini` (line 12) | No | Yes — by `LLMClient._init_client` (`client.py:15-24`) | **Active** |
| Gemini | `gemini_client.py` (`GeminiClient`) | `gemini-2.5-flash` (line 10) | No | **No** | Dead code |
| Groq | `groq_client.py` (`GroqClient`, via `langchain_groq.ChatGroq`) | `llama-3.3-70b-versatile` (line 12) | No | **No** | Dead code |
| Ollama | `ollama_client.py` (`OllamaClient`) | `llama3.1:8b` (line 10) | Yes (the only one) | **No** (current code) | Dead code |

README claims «پشتیبانی از چندین مدل زبانی (OpenAI، Groq، Gemini) با قابلیت Fallback خودکار» — multi-provider automatic fallback. **No fallback exists in code**: `LLMClient.generate` has a single branch; failure raises immediately (`client.py:34-43`). Recorded in `discrepancies.md`.

## 2. Active provider configuration (as actually run)

| Parameter | Value | Set where |
|---|---|---|
| Model | `gpt-4o-mini` | `openai_client.py:12` |
| Temperature | `0.1` (SQL + answers both) — client default is 0.2, overridden by `LLMClient` | `client.py:20-22` |
| max_tokens | `500` for **every** task | `client.py:21` |
| base_url | `OPENAI_BASE_URL` env or `https://api.openai.com/v1`. The deployed `.env.example` sets it to a third-party proxy (`https://api.gapgpt.app/v1`), so the "OpenAI" provider is in practice proxied through an external relay | `openai_client.py:19` |
| API key | `OPENAI_API_KEY`; constructor raises ValueError if absent | `openai_client.py:20-22` |
| Timeout / retries | Not configured → OpenAI SDK defaults (~600 s total retry window with 2 retries) apply per call | absent from code |
| Message shape | Single `"user"` message containing the entire prompt; no system role usage anywhere | `openai_client.py:34-38` |
| Structured output | None (plain text completion; JSON only hoped-for in generator script) | — |

## 3. Provider selection & availability logic

- Selection: hard-coded. `_init_client` constructs `OpenAIClient` iff `OPENAI_API_KEY` present; otherwise `self.openai_client=None`.
- Availability: `LLMClient.is_available()` = client constructed AND `is_internet_available()`.
- `is_internet_available()` performs `requests.get("https://api.openai.com", timeout=3)` before **every** generation (`client.py:26-32`):
  - adds up to ~3 s latency per LLM call,
  - fails when the direct OpenAI domain is unreachable even though the configured proxy works (base_url decoupling ignored),
  - bare `except:` swallows all errors.
- `/health` triggers this same network ping through the agent singleton.

## 4. Retry / fallback behavior

| Layer | Behavior |
|---|---|
| OpenAI SDK | default retries (2) with exponential backoff inside SDK; no app-level control |
| SQL repair loop | 1 corrective regeneration on validation failure (`extractor.py:50-69`) — this is prompt-level retry, not provider-level |
| Provider fallback | **None**. On any OpenAI/proxy error: RuntimeError propagates → agent returns generic failure envelope |
| Log evidence | `logs/app_20260823.log`: `RuntimeError: OpenAI API error: Connection error.` and `getaddrinfo failed` chains — single point of failure confirmed in practice |

Historical note: `logs/app_20260822.log` contains `WARNING ... Ollama client init failed: OllamaClient.__init__() got an unexpected keyword argument 'max_tokens'` — proof an earlier facade attempted Ollama initialization with incompatible kwargs; current code removed the attempt entirely rather than fixing it.

## 5. Per-call-site parameter matrix

| Call site | Purpose | Model | Temp | max_tokens | Notes |
|---|---|---|---|---|---|
| `SQLExtractor.extract` initial | NL→SQL | gpt-4o-mini (via proxy) | 0.1 | 500 | Prompt includes schema+rules+few-shots+history; output expected to be pure SQL |
| `SQLExtractor.extract` repair | Fix rejected SQL | same | 0.1 | 500 | Short context; no schema/rules/few-shots re-supplied |
| `TextToSQLAgent.ask` answer | Persian Markdown answer | same | 0.1 | 500 | Input includes first 5 rows as dict reprs; 500 tokens frequently truncates longer tables/insights |
| `generate_column_map.translate_chunk` ×N | Column-name translation JSON | same | 0.1 | 500 | Chunks of 15 columns; regex-extracts `{...}`; identity fallback on failure |

Observations:
- One temperature/model/token budget for two very different tasks (code generation vs free-text explanation).
- 500-token ceiling interacts badly with the demanded 4-section Markdown answer including a full data table.
- No per-task model routing (e.g., stronger model for SQL, cheaper for answers).

## 6. Error handling & timeout behavior (current)

- Every client wraps provider exceptions into `RuntimeError("... API error: {e}")` losing exception type info.
- `LLMClient.generate`: if internet check fails → `RuntimeError("OpenAI client is not available or internet is down.")` even when a working proxy base_url is configured.
- No circuit breaker, no jittered retries, no degradation path (e.g., answer without LLM), no request cancellation handling.
- Agent catches the resulting generic `Exception` and returns `success=false` with the raw error string surfaced to end users (`text_to_sql_agent.py:130-139`).

## 7. Potential provider inconsistencies (if dead clients were ever wired)

- Only `OllamaClient` implements `BaseLLMClient`; the other three would fail polymorphic use of `is_available()`.
- Defaults disagree: OpenAI/Groq/Gemini temp 0.2 vs facade-enforced 0.1; Ollama has no `max_tokens` param (the exact mismatch that crashed historically); Groq has its own retry counter; Gemini lacks availability checking entirely.
- Three different SDK stacks coexist in dependencies (raw `openai`, `google-genai`, `langchain-*`) — heavy duplication for zero active benefit.

## 8. Summary judgment

The LLM layer is effectively **single-provider (proxied OpenAI-compatible), fixed-model, fixed-temperature(0.1), fixed-500-token**, with a pre-call connectivity check that can itself be a false negative, no real fallback despite documentation, and no structured-output discipline. All multi-provider machinery is inert scaffolding.
