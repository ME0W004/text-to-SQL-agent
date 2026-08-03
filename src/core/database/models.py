# src/core/database/models.py

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

def build_detailed_schema() -> str:
    lines = []
    
    for model in [ORDERS_MODEL, ORDER_ITEMS_MODEL, SHIPMENTS_MODEL]:
        lines.append(f"Table: {model['table_name']}")
        lines.append(f"Columns: {', '.join(model['columns'])}")
        lines.append(f"PK: {model['primary_key']}")
        for fk in model.get('foreign_keys', []):
            lines.append(f"FK: {fk['column']} -> {fk['referred_table']}.{fk['referred_column']}")
        lines.append("")
    
    lines.append("Relationships:")
    lines.append("  - Sale.Orders.Id = Sale.OrderItems.orderId (One-to-Many)")
    lines.append("  - Sale.OrderItems.Id = Sale.Shipments.orderItemId (One-to-Many)")
    
    return "\n".join(lines)

FULL_SCHEMA = build_compact_schema()
DETAILED_SCHEMA = build_detailed_schema() 


ALL_MODELS = [ORDERS_MODEL, ORDER_ITEMS_MODEL, SHIPMENTS_MODEL]


__all__ = [
    "ORDERS_MODEL",
    "ORDER_ITEMS_MODEL",
    "SHIPMENTS_MODEL",
    "FULL_SCHEMA",
    "DETAILED_SCHEMA",
    "ALL_MODELS",
    "build_compact_schema",
    "build_detailed_schema"
]