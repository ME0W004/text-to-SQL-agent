# Current Problems & Failure Modes

> Evidence-based register. Severity: **Critical** (data loss/security/wrong answers shipped silently) · High (feature-breaking, frequent user impact) · Medium (degraded quality/reliability) · Low (hygiene). "Repro" notes whether observed in logs/live data (✅) or derived from code reading (code).

---

## A. Correctness

| ID | Problem | Evidence | Root cause | Impact | Severity |
|---|---|---|---|---|---|
| C1 | **Silent wrong answers from valid SQL** — semantic errors undetectable: currency-mixed SUMs, unit-mixed SUMs, cancelled orders included | DB facts: 5 currencies, 4 units, «ابطال» status; no semantic validation code | no semantic layer/validation stage | business numbers wrong while system reports success | **Critical** |
| C2 | Session ordering corruption within same second | live DB: 48 msgs / 24 distinct timestamps; `ORDER BY created_at` only (`manager.py:69`) | second-granularity timestamp + tie-blind query | history pairs can invert → confused follow-up answers | High |
| C3 | Unknown session_id silently replaced | `text_to_sql_agent.py:69-70` | lenient fallback | users silently lose conversation continuity; bugs masked | Medium |
| C4 | CLI never reuses sessions | `run.py:16` passes no session_id | wiring omission | memory feature dead on CLI | Low |
| C5 | Duplicate Shipments Ids make Shipment.Id-keyed logic unsafe | 795 dup groups in live data | data quality + no validator awareness | double counting in joins on Id | Medium |

## B. SQL generation & pipeline

| ID | Problem | Evidence | Root cause | Impact | Severity |
|---|---|---|---|---|---|
| S1 | **Multi-statement injection bypass**: trailing statements with non-blocked keywords pass validation and execute | empirical: sqlglot parses `SELECT 1; TRUNCATE TABLE Sale.Orders`; blocklist lacks TRUNCATE/GRANT/BACKUP/SHUTDOWN/OPENROWSET… (`validator.py:27-31`) | textual blocklist + parse_one ignoring trailing statements | destructive SQL can reach the server if login permits | **Critical** |
| S2 | Execution errors never repaired | pymssql 147 aggregate-in-WHERE reached user unrepaired (log ✅); repair loop only wraps validator calls (`extractor.py`) | repair loop scoped to validation only | hard failures for common LLM mistakes | High |
| S3 | Repair prompt lacks schema/rules/few-shots | `prompts.py:91-108` | context starvation | low repair success probability | High |
| S4 | `_clean_sql` can mutate statement type (INSERT…SELECT → SELECT) | regex prefix cut `extractor.py:34-37` (code) | lossy heuristic | masks model misbehavior; unpredictable outputs | Medium |
| S5 | Unbounded result sets executed & rendered | log ✅ `Generated SQL: SELECT *`; `fetchall()` no cap (`connection.py:51`) | no LIMIT policy / row cap | memory blowups, 60 s UI timeouts | High |
| S6 | Fixed ~5 s query timeout kills legitimate aggregations | `pymssql.connect(timeout=5)` (`connection.py:28`); all tables are unindexed heaps | single knob misused | intermittent failures on heavier questions | Medium |
| S7 | Few-shot teaches impossible year '1403' | `few_shots.py` vs data years 1394–1399 ✅ | stale example | empty-result pattern reinforced | Medium |

## C. Semantic understanding

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| N1 | Shipments invisible to generator: FULL_SCHEMA omits its columns | `models.py:81-125` (code); real user questions about حواله exist ✅ | delivery questions answered by guessing column names | High |
| N2 | No business-metric definitions (revenue net/gross/VAT/currency normalization) | prompts assert raw `quantity*unitPrice` only | inconsistent analytics across phrasings | High |
| N3 | Follow-up/coreference handled by dumping raw history; observed follow-up failure («داده های که بیشتر از 50 درصد» → "No data found") | memory DB ✅ | multi-turn UX broken in practice | High |

## D. Database understanding

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| D1 | Three drifting schema sources (DB, FULL_SCHEMA text, COLUMN_MAP) | mesc translation wrong; Buyers table phantom (`FULL_SCHEMA` buyerId gloss) | hallucination + wrong labels | High |
| D2 | Schema-change detection ineffective end-to-end | updater cwd bug writes to `scripts/src/core/column_mapper.py` (stray file tracked in git ✅); caller ignores False return | map staleness silent | Medium |
| D3 | No constraints/indexes acknowledged anywhere in prompts (none exist in DB either) | sys.key_constraints/sys.foreign_keys empty ✅ | correctness relies purely on LLM discipline | Medium |

## E. Security

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| X1 | Validator bypass class (see S1) | empirical | destructive SQL possible | **Critical** |
| X2 | XSS/prompt-injection surface: assistant markdown rendered `unsafe_allow_html=True`; DB-sourced strings flow into it | `app.py:208-211,278-281` (code) | stored-content attack via crafted rows/questions | High |
| X3 | CORS wildcard + credentials | `api/main.py:17-23` | cross-site API abuse posture | Medium |
| X4 | `.env.example` committed with concrete infra identifiers | git ls-files ✅ | infrastructure disclosure hygiene | Medium |
| X5 | Single privileged-style DB login; no read-only role separation at driver level | connection.py | blast radius of any bypass | High (with S1 ⇒ Critical chain) |

## F. Memory

See memory-audit register M1–M10. Top items: missing index (M1), tie-ordering (C2), orphaned sessions forever (M3), non-atomic pair insert (M6), history token bloat (M7).

## G. Prompts

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| P1 | Answer format contract self-contradicts (English headings vs Persian demand) → mixed-language output | `prompts.py:77-85`; stored answers ✅ | unpolished UX, brittle parsing assumptions | Medium |
| P2 | >5-row truncation invites fabricated tables in answers | `prompts.py:64-71` | hallucinated numbers | High |
| P3 | 500-token ceiling shared across tasks | `client.py:21` | truncated answers/SQL edge cases | Medium |
| P4 | Prompt knowledge triplicated (rules/schema-footer/few-shots) | prompts.py/models.py/few_shots.py | drift risk (already: '1403') | Medium |
| P5 | Translation prompt lacks table/type/sample context → proven wrong label (`mesc`=«مقدار») | script lines 47–60; live map ✅ | misleading Persian headers | Medium |

## H. LLM

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| L1 | Documented fallback does not exist | client.py single branch; dead provider files | outage = total failure (observed ✅ getaddrinfo) | High |
| L2 | Internet pre-check pings api.openai.com even when proxy base_url configured; bare except | `client.py:26-32` | false outages + latency | Medium |
| L3 | Historical broken Ollama init shows fallback was attempted and abandoned mid-way | log ✅ TypeError max_tokens | archaeology trap for maintainers | Low |
| L4 | No timeouts/retries config; SDK defaults (~minutes) possible | openai_client.py | hung requests under UI 60 s timeout | Medium |

## I. Result presentation & translation

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| R1 | Alias columns untranslated → English headers in Persian tables; 2,510 warnings/day | logs ✅; static-map design | presentation quality; warning spam | High |
| R2 | Wrong translation `mesc`→«مقدار» shipped in active map | column_mapper.py ✅ | actively misleading labels | Medium |
| R3 | Double rendering of results (answer Markdown table + dataframe) may disagree | app.py render path; answer truncation | inconsistent numbers on screen | Medium |
| R4 | Empty-result dead-end in English, no diagnosis | agent:85-86 | UX gap; also pollutes history | Medium |
| R5 | `*Id` blanket removal before answering hides ids from the answer step | agent:50-51 | limits legit id-referencing answers | Low |

## J. UI

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| U1 | Server-side sessions orphaned on "New Session" | app.py sidebar; clear_session uncalled ✅ | unbounded growth | Medium |
| U2 | Raw technical error strings shown | routes/agent envelopes; st.error | trust erosion | Medium |
| U3 | First-request-after-schema-change latency spike (subprocess+LLM inline) | updater flow; UI 60 s timeout | perceived hang/failure | Medium |
| U4 | English chrome around Persian product | app.py texts | polish | Low |

## K. Performance

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| F1 | Blocking sync LLM+DB inside async handlers serializes all requests | routes.py async def + sync agent | throughput collapse under concurrency | High |
| F2 | Shared cursor/connection across requests | connection.py singleton usage | race conditions possible | High (latent until concurrency) |
| F3 | Per-call internet ping adds latency to every LLM call | client.py | cumulative delay | Low |
| F4 | Unindexed heaps + unbounded queries | DB profile ✅ | timeout risk scaling with data | Medium |

## L. Observability

O1–O8 as listed in observability-audit §5: no request ids, no stage events/timings, no prompt capture, no token accounting, DEBUG-only execution details, date-pinned log files, 97% warning noise. Severity: **High collectively** — incidents like C2/N3 were discoverable only through manual DB forensics.

## M. Maintainability

| ID | Problem | Evidence | Impact | Severity |
|---|---|---|---|---|
| T1 | God-object agent (wiring+orchestration+presentation+persistence) | module-responsibility §1 | every future change touches one file | High |
| T2 | Dead code mass: 3 providers, exception hierarchy, execute_non_query, compact schema, schemas pkg, stray generated file | grep results ✅ | confusion, false docs | Medium |
| T3 | Zero tests despite test scaffolding dirs | tests/ empty ✅ | refactors unverifiable | High |
| T4 | Runtime code-generation as startup side effect | updater+generator | surprising behavior, VCS pollution (stray file committed) | Medium |
| T5 | Unused dependencies (~9 packages incl. 3 DB drivers) | configuration-audit §3 | supply-chain surface | Low-Medium |
