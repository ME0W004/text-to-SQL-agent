# Database Profile — Actual Inspection Results

> Live, read-only inspection performed during Sprint 0 against the configured database (connection parameters from `.env`; **no credentials are reproduced in this document**).

---

## 1. Server & connection

| Item | Value |
|---|---|
| DBMS | Microsoft SQL Server 2022 (RTM), 16.0.1000.6, Enterprise Edition (64-bit), Windows Server 2025 |
| Database name | `AgentiveAi` |
| Application driver | `pymssql` 2.3.13 (`pyproject.toml`, installed venv) |
| Connection code | `src/core/database/connection.py:19-36` — `pymssql.connect(server, port, user, password, database, timeout=5, charset='UTF-8')`, cursor `as_dict=True` |
| Lifecycle | One connection + one shared cursor per `TextToSQLAgent`; agent is a process-wide singleton under the API; no pooling, no reconnect, no health pings besides `/health` truthiness |
| Timeout semantics | pymssql `timeout=5` ⇒ ~5 s query wait timeout for every statement; login timeout left at default |
| Charset | UTF-8 requested; Persian values round-trip correctly (verified via sample reads) |

## 2. Schemas and tables

Single user schema: **`Sale`**. Exactly three base tables exist:

| Table | Rows (COUNT(*)) | Storage |
|---|---|---|
| `Sale.Orders` | 16,948 | HEAP — no indexes at all |
| `Sale.OrderItems` | 17,416 | HEAP — no indexes |
| `Sale.Shipments` | 37,872 | HEAP — no indexes |

**Constraints: none declared.**
- `sys.key_constraints` → empty ⇒ **no primary keys** despite every table having an `Id int NOT NULL` column.
- `sys.foreign_keys` → empty ⇒ **no FK constraints**; relationships exist only logically (orderId→Orders.Id; orderItemId→OrderItems.Id).
- Referential quality observed: orphan Shipments = 0 (checked); duplicate `Id` groups in Shipments = **795** (Ids are *not* unique in practice); Orders/OrderItems Ids unique.

## 3. Columns

### Sale.Orders
| Column | Type | Nullable | Notes / observed content |
|---|---|---|---|
| Id | int | NO | unique |
| supplierUser | nvarchar(100) | NO | sales-rep usernames: 'J.Sadeghi', 'F.Pazand', 'System', … |
| entityDate | date | YES | 0 nulls; range **2016-01-11 → 2021-02-25** |
| serialNo | bigint | YES | separate sequential business number (≠ Id) |
| status | nvarchar(300) | NO | Persian lifecycle: «اتمام سفارش» 16,809 · «تایید باربری» 136 · «تایید انبار» 1 · «تایید انبار محصول» 1 · «ابطال» (cancelled) 1 |
| jalaliYearNo | varchar(4) | YES | '1394'(1) · '1395'(3) · '1396'(3) · '1397'(10) · '1398'(9,227) · '1399'(7,704); 0 nulls |
| operationMonth | int | YES | Jalali YYYYMM as integer, e.g., 139901 |
| buyer | nvarchar(200) | NO | company names, Persian + foreign ('Arcplast Plastik Ambalaj') |
| buyerId | int | YES | dangling reference — **no Buyers table exists** |
| currencyRate | decimal(18,2) | NO | e.g., 1.00, 4365.00–4408.00 band seen |
| curency | nvarchar(10) | YES | misspelled column name; RLS 15,467 · USD 501 · RUB 487 · EUR 352 · AED 141 orders |
| destination | nvarchar(500) | NO | mixed granularity: provinces/cities («اصفهان»,«البرز») AND countries («ترکیه»,«ارمنستان»,'Gebze') |
| destinationId | int | NO | dangling ref (no lookup table) |
| orderTypeId | int | YES | dangling ref |
| orderType | nvarchar(100) | YES | «فروش کارگزاری بورس», «فروش داخلی», «فروش نمونه», «فروش صادراتی», «فروش اعتباری بورسی», «فروش اعتباری داخلی» |
| bourseContractNo | nvarchar(100) | YES | exchange contract numbers like '1403168797' |

### Sale.OrderItems
| Column | Type | Nullable | Notes |
|---|---|---|---|
| Id | int | NO | unique |
| orderId | int | NO | logical FK → Orders.Id |
| itemNo | int | NO | line number within order |
| orderNo | nvarchar(50) | YES | business document number like 'AP02833/03' — does NOT match Orders.Id (verified: 0 matches on cast-join) |
| mesc | nvarchar(12) | NO | zero-padded material codes: '0000007194', … |
| unit | nvarchar(2) | YES | KG · TN · LT · M3 |
| description | nvarchar(max) | YES | product description |
| miscInfo | nvarchar(50) | YES | 1,494/17,416 nulls; values like 'ON', 'OFF1', 'OFF2' |
| productGroup | nvarchar(10) | NO | LIQUID · GPPS · HIPS · HDPE · EPS · ABS |
| partNo | nvarchar(50) | YES | polymer grades: '3840 UA', '1540 OP', '221 HS', … |
| quantity | float | YES | 0 … 3,045,649 · avg ≈ 41,228 (KG-dominant interpretation per docs) |
| unitPrice | decimal(18,5) | YES | 0 … 3,516,463,671 · avg ≈ 4.63e6; 1 null |
| packCount / palletCount | int | YES | packaging counts |
| discount | decimal(18,2) | NO | always 0.00 across all rows |
| vatPercent | decimal(5,2) | NO | 0 … 10.00 · avg ≈ 8.96 |

### Sale.Shipments
| Column | Type | Nullable | Notes |
|---|---|---|---|
| Id | int | NO | **not unique** — 795 duplicated values |
| orderItemId | int | YES | logical FK → OrderItems.Id |
| serialNo | bigint | YES | own sequence |
| fullEntityNo | nvarchar(200) | YES | shipment document number |
| entityDate | date | YES | shipment date |
| quantity | float | YES | delivered quantity; 15 items have Σshipped > ordered (over-delivery) |
| packCount / palletCount | int | YES | |
| packingWeight / netWeight / grossWeight | decimal(18,3) | YES | weights |
| vehicleIdentificationNo | nvarchar(100) | YES | truck plate/id |
| driverId | int | YES | dangling ref |
| driver | nvarchar(200) | YES | driver name |
| typeId | int | NO | dangling ref |
| typeName | nvarchar(500) | YES | «بارگیری» 37,851 · «برگشت از فروش» 16 · «عودت» 5 |

## 4. Indexes

None. `sys.indexes` shows only heap entries (`type_desc='HEAP'`, `is_primary_key=0`). Every query is a full scan; with ≤38k rows this is tolerable but explains why any future growth or heavy joins will hit the 5 s query timeout quickly.

## 5. Representative sample values (safe excerpts)

- Buyers: «فرآورده های نسوز آذر شهاب تبریز», «کارخانجات تولیدی پلاستی ران», 'Arcplast Plastik Ambalaj'
- Status: «اتمام سفارش», «تایید باربری», «ابطال»
- Product groups: HDPE, GPPS, HIPS, EPS, ABS, LIQUID
- Units: KG, TN, LT, M3
- Currencies: RLS, USD, RUB, AED, EUR
- Shipment types: «بارگیری», «برگشت از فروش», «عودت»

## 6. Value-share fact relevant to semantics (verified)

`SUM(quantity*unitPrice)` grouped by `curency`: RLS ≈ 3.63e14 dominates; USD ≈ 8.53e7, RUB ≈ 3.67e9, AED ≈ 4.71e7, EUR ≈ 2.59e7. Naive cross-currency totals are dominated by RLS but still mathematically meaningless (mixing units). `currencyRate` exists to normalize but nothing uses it.

## 7. Relationship to code-level schema knowledge

| Source | Coverage | Accuracy issues found |
|---|---|---|
| Actual DB (this inspection) | ground truth | — |
| `database/models.py` ORDERS/ORDER_ITEMS/SHIPMENTS dicts | full column lists match DB | `foreign_keys` arrays describe logical links only (DB has none); ORDERS_MODEL.foreign_keys=[] while others list links |
| `FULL_SCHEMA` (prompt schema) | Orders + OrderItems only | Shipments columns missing entirely; claims buyerId→Buyers table which doesn't exist |
| `column_mapper.py` | 41 names incl. stale `delivered_quantity` | wrong translation for `mesc`; regenerated output varies per run |
| `schema_hash.py` | the same 3 tables | consistent with DB scope today |

## 8. Security notes (documentation-safe)

- The application connects with a single SQL login whose permission scope was not audited beyond read success; destructive-statement protection currently relies solely on the weak validator (see sql-pipeline-audit).
- `.env.example` committed to git contains concrete-looking infrastructure identifiers rather than placeholders (host IP, username, DB name); secrets themselves masked. Flagged in discrepancies/configuration audit.
