# Database Semantics Audit

> Goal: infer what the database *means* as a business domain, separating **facts** (supported by data/code) from **assumptions** (inferred). Sources: table/column names, types, live sample values, `database/models.py` glosses, prompts, few-shots, and the conversation history in `data/conversations.db`.

---

## 1. Inferred business domain

A polymer/petrochemical **sales & export operation of an Iranian company**, recording orders (contract-level), order line items, and physical shipments, spanning Jalali years 1394–1399 (Gregorian 2015-2021 window), sold both domestically and exported.

**Fact basis**: product groups HDPE/GPPS/HIPS/EPS/ABS/LIQUID; part numbers are polymer grades ('3840 UA', '1540 OP'); units KG/TN/LT/M3; Persian status vocabulary; bourse contract numbers + «فروش کارگزاری بورس» order type (Iranian Mercantile Exchange brokerage sales); destinations include provinces («اصفهان», «البرز») and countries («ترکیه», «ارمنستان»); currencies RLS/USD/RUB/AED/EUR.

## 2. Entity semantics

| Concept | Realized as | Fact / Assumption |
|---|---|---|
| Customer | `Orders.buyer` (free-text company name) + dangling `buyerId` | buyer text = fact; “customer master is missing” = fact (no Buyers table); buyerId semantics = assumption |
| Sales rep | `Orders.supplierUser` (username strings) | fact that it's a username; meaning "who created the order" = models.py gloss (assumption) |
| Order | One `Sale.Orders` row; lifecycle via `status` | fact; «اتمام سفارش» ≈ completed (gloss in models.py), «ابطال» = cancelled — interpretation of 1-row sample = weak-evidence assumption |
| Order line | `Sale.OrderItems`; `itemNo` orders lines within an order | fact |
| Product | `productGroup` + `partNo` + `description` (+ `mesc` material code) | productGroup= polymer family (fact by values); mesc = material code (models.py gloss + zero-padded code samples) |
| Quantity | `OrderItems.quantity` float | unit column says KG mostly; per-line unit may be TN/LT/M3 — mixing possible (assumption flagged below) |
| Price | `unitPrice` decimal(18,5) | price **per unit of `unit`, denominated in `Orders.curency`** (assumption — strongest consistent reading) |
| Revenue | `quantity * unitPrice` | prompt/few-shot convention = project fact; correctness across currencies/units unverified (see §4) |
| Shipment/delivery | `Sale.Shipments` rows per order item; typeName «بارگیری» (loading) dominant, plus returns «برگشت از فروش», «عودت» | fact; delivery-ratio analytics implied by user questions in memory DB («حواله های که کمتر از 50 درصداشان تحویل شده») |
| Time | Gregorian `entityDate` (date) ↔ Jalali `jalaliYearNo` (varchar yyyy) ↔ `operationMonth` (int YYYYMM) | fact; triple redundancy intentional for Jalali querying |

## 3. Implicit assumptions the current system already encodes

These exist in prompts/rules/few-shots today (`prompts.py:21-41`, `few_shots.py`) — the system behaves as if all are true:

1. Revenue = Σ `quantity × unitPrice` — ignores discount (always 0.00 today, so harmless *now*) and VAT (up to 10%) and never converts currency.
2. Jalali filtering always via `jalaliYearNo`; monthly grouping via `operationMonth`.
3. Join chain Orders.Id = OrderItems.orderId → OrderItems.Id = Shipments.orderItemId.
4. "Count of orders" = COUNT(DISTINCT Orders.Id) after join (few-shot pattern).
5. Table universe = exactly the three Sale tables.

## 4. Semantic hazards (facts that contradict naive assumptions)

| Hazard | Evidence | Impact on questions like “کل فروش” |
|---|---|---|
| Multi-currency orders | RLS 15,467 vs USD 501 / RUB 487 / EUR 352 / AED 141; value share verified | Unqualified SUMs mix units; result dominated by RLS but wrong |
| Mixed measurement units | unit ∈ {KG, TN, LT, M3} while quantity is a bare float | SUM(quantity) across units is meaningless unless filtered by unit |
| Cancelled orders included | status «ابطال» row exists; no prompt rule excludes it | Tiny today (1 row) but conceptually wrong |
| Over-delivery exists | 15 items with Σshipment.quantity > ordered quantity | Delivery-ratio caps (>100%) surprise users |
| Duplicate Shipment Ids | 795 duplicated Id groups | Any join/count keyed on Shipments.Id can double-count |
| `orderNo` ≠ order identifier | 'AP02833/03'-style doc numbers; 0 matches to Orders.Id on cast join | Questions about “order number” may hit the wrong column |
| Two serial numbers | Orders.serialNo vs Shipments.serialNo vs fullEntityNo | Ambiguity for “شماره سریال” questions |
| destination granularity mixed | provinces AND countries in one column | Country-level questions need filtering logic the system doesn't model |
| buyerId/destinationId/typeId/driverId dangling | no lookup tables exist | ID columns are unresolvable; agent drops *Id columns at presentation anyway |
| vatPercent ~9–10 typical | avg 8.96 | Net-vs-gross revenue ambiguity unaddressed |
| Year coverage skew | 1398+1399 = 99%+ of orders; few-shot year '1403' impossible | Default-year intuitions mislead; empty results likely for recent-year questions |

## 5. What users actually ask (evidence from memory DB)

Sample persisted questions show real intent:
- «حواله های که کمتر از 50 درصداشان تحویل شده» → shipment delivery-ratio analysis (requires Shipments aggregation + ratio math against OrderItems.quantity).
- Follow-up shorthand «داده های که بیشتر از 50 درصد» → coreference resolution; currently produced "No data found" (recorded assistant reply).
This demonstrates demand for Shipments-aware semantics that `FULL_SCHEMA` does not even describe.

## 6. Facts vs assumptions register (summary)

**Facts**: schema objects/types/nullability; row counts; value distributions listed in `database-profile.md`; join path popularity in few-shots/log SQL; existence of multi-currency/multi-unit data; absence of constraints/indexes/lookup tables; duplicate Shipments Ids; discount≡0; VAT≈9–10%.

**Assumptions** (reasonable but unproven): unitPrice denomination follows `curency`; quantity unit equals `unit` column per line; supplierUser = creator; «اتمام سفارش» = final success state; revenue convention should ignore VAT; buyer free-text is stable identity.
