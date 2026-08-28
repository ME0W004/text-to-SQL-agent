# Problem Inventory

| ID | Problem | Category | Evidence | Impact | Severity | Direction | Sprint |
|---|---|---|---|---|---|---|---|
| P-001 | Multi-statement/security bypass | Security | Prefix+blocklist+parse; TRUNCATE bypass recorded | Write risk | Critical | AST single-statement and read-only boundary | S1 |
| P-002 | Unbounded fetchall/render | Execution | DatabaseConnection and Streamlit | Memory/latency outage | High | Caps, paging, timeout budget | S1 |
| P-003 | Static/incomplete schema | Schema | FULL_SCHEMA omits Shipments; duplicated sources | Wrong SQL | High | Live versioned catalog | S1 |
| P-004 | Broken map regeneration | Maintainability | Relative generator path; ignored failure | Stale labels | High | Explicit artifact path | S1 |
| P-005 | Semantic checks absent | Correctness | Units/currency/aggregate rules are prompt-only | Plausible wrong answers | High | Planner and semantic validator | S2 |
| P-006 | One provider | LLM reliability | Only OpenAI wired | Availability/cost risk | Medium | LLMRouter | S2 |
| P-007 | Shared singleton/cursor | Architecture | Global agent in async API | Concurrency/blocking | High | Request-scoped executor | S1 |
| P-008 | Memory retention/order gaps | Memory | Timestamp ties; no expiry/index | Context/storage issues | Medium | Layered memory | S2 |
| P-009 | Ungrounded answer/unsafe HTML | Results/security | Five-row prompt; unsafe_allow_html | Hallucination/XSS | High | Result/answer validators | S2 |
| P-010 | No regression harness | Testing | Empty tests; baseline JSON only | No safe refactor | High | Dataset runner and CI | S1 |
| P-011 | Docs/config drift | Maintainability | README overstates fallback/repair; env values concrete | Operator/security confusion | Medium | Docs and secret hygiene | S0 |
| P-012 | Unused exception taxonomy | Observability | Typed branches unreachable | Poor triage | Medium | Boundary error mapping | S1 |

