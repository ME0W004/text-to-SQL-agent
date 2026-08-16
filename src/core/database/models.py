# src/core/database/models.py

# ====================================================================
# TABLE: Sale.Orders (Full model for code usage)
# ====================================================================
ORDERS_MODEL = {
    "table_name": "Sale.Orders",
    "columns": [
        "Id", "supplierUser", "entityDate", "serialNo", "status",
        "jalaliYearNo", "operationMonth", "buyer", "buyerId",
        "currencyRate", "curency", "destination", "destinationId",
        "orderTypeId", "orderType", "bourseContractNo"
    ],
    "primary_key": "Id",
    "foreign_keys": []
}

# ====================================================================
# TABLE: Sale.OrderItems (Full model for code usage)
# ====================================================================
ORDER_ITEMS_MODEL = {
    "table_name": "Sale.OrderItems",
    "columns": [
        "Id", "orderId", "itemNo", "orderNo", "mesc",
        "unit", "description", "miscInfo", "productGroup", "partNo",
        "quantity", "unitPrice", "packCount", "palletCount",
        "discount", "vatPercent"
    ],
    "primary_key": "Id",
    "foreign_keys": [
        {"column": "orderId", "referred_table": "Sale.Orders", "referred_column": "Id"}
    ]
}

# ====================================================================
# TABLE: Sale.Shipments (Full model for code usage)
# ====================================================================
SHIPMENTS_MODEL = {
    "table_name": "Sale.Shipments",
    "columns": [
        "Id", "orderItemId", "serialNo", "fullEntityNo", "entityDate",
        "quantity", "packCount", "palletCount", "packingWeight",
        "netWeight", "grossWeight", "vehicleIdentificationNo",
        "driverId", "driver", "typeId", "typeName"
    ],
    "primary_key": "Id",
    "foreign_keys": [
        {"column": "orderItemId", "referred_table": "Sale.OrderItems", "referred_column": "Id"}
    ]
}

# ====================================================================
# Compact schema version (for prompt - without descriptions)
# ====================================================================
def build_compact_schema() -> str:
    return """
Database Schema (Microsoft SQL Server):

Table: Sale.Orders
Columns: Id, supplierUser, entityDate, serialNo, status, jalaliYearNo, operationMonth, buyer, buyerId, currencyRate, curency, destination, destinationId, orderTypeId, orderType, bourseContractNo
PK: Id

Table: Sale.OrderItems
Columns: Id, orderId, itemNo, orderNo, mesc, unit, description, miscInfo, productGroup, partNo, quantity, unitPrice, packCount, palletCount, discount, vatPercent
PK: Id
FK: orderId -> Sale.Orders.Id

Table: Sale.Shipments
Columns: Id, orderItemId, serialNo, fullEntityNo, entityDate, quantity, packCount, palletCount, packingWeight, netWeight, grossWeight, vehicleIdentificationNo, driverId, driver, typeId, typeName
PK: Id
FK: orderItemId -> Sale.OrderItems.Id

Relationships:
- Sale.Orders (1) ---< (N) Sale.OrderItems via orderId
- Sale.OrderItems (1) ---< (N) Sale.Shipments via orderItemId
"""

# ====================================================================
# Full schema with semantic descriptions (for prompt)
# ====================================================================
FULL_SCHEMA = """
**Table: Sale.Orders**
Columns:
  - Id: Unique order identifier (Primary Key)
  - supplierUser: Sales representative who created the order
  - entityDate: Order creation date (Miladi)
  - serialNo: Sequential order number
  - status: Order status ('اتمام سفارش' = completed, 'تایید باربری' = shipping confirmed, etc.)
  - jalaliYearNo: Persian calendar year (e.g., '1399')
  - operationMonth: Persian calendar month in YYYYMM format (e.g., 139901 for Farvardin 1399)
  - buyer: Customer/company name
  - buyerId: Customer ID (Foreign Key to Buyers table, not shown)
  - currencyRate: Exchange rate used for the order
  - curency: Currency type (e.g., 'RLS' for Rials)
  - destination: Destination country
  - destinationId: Destination ID
  - orderTypeId: Order type ID
  - orderType: Order type description ('فروش کارگزاری بورس' = brokerage sale)
  - bourseContractNo: Stock exchange contract number

**Table: Sale.OrderItems**
Columns:
  - Id: Unique item identifier (Primary Key)
  - orderId: Foreign Key to Sale.Orders.Id
  - itemNo: Line number within the order
  - orderNo: Order number (redundant, but useful for reference)
  - mesc: Material code
  - unit: Unit of measurement (e.g., 'KG')
  - description: Product description
  - miscInfo: Additional information (often NULL)
  - productGroup: Product category (e.g., 'HDPE', 'GPPS', 'LIQUID')
  - partNo: Product grade/part number (e.g., '3840 UA')
  - quantity: Ordered quantity (in KG)
  - unitPrice: Price per unit (in RLS)
  - packCount: Number of packages
  - palletCount: Number of pallets
  - discount: Discount amount (default 0.00)
  - vatPercent: VAT percentage (0%, 9%, 10%)

**Relationships:**
- Sale.Orders (1) ---< (N) Sale.OrderItems via orderId
- Sale.OrderItems (1) ---< (N) Sale.Shipments via orderItemId

**Important:** For calculating revenue, use `quantity * unitPrice`. For Persian date filtering, use `jalaliYearNo`. For grouping by month, use `operationMonth`.
"""

# ====================================================================
# Main variables for use in other files
# ====================================================================
ALL_MODELS = [ORDERS_MODEL, ORDER_ITEMS_MODEL, SHIPMENTS_MODEL]

__all__ = [
    "ORDERS_MODEL",
    "ORDER_ITEMS_MODEL",
    "SHIPMENTS_MODEL",
    "FULL_SCHEMA",
    "ALL_MODELS",
    "build_compact_schema",
]