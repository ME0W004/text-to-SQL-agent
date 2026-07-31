# src/core/database/models.py

ORDER_MODEL = {
    "table_name": "Sale.Orders",
    "columns": [
        "Id", "supplierUser", "entityDate", "serialNo", "status",
        "jalaliYearNo", "operationMonth", "buyer", "buyerId",
        "currencyRate", "curency", "destination", "destinationId",
        "orderTypeId", "orderType", "bourseContractNo"
    ],
    "primary_key": "Id"
}

ORDER_ITEM_MODEL = {
    "table_name": "Sale.OrderItems",
    "columns": [
        "Id", "orderId", "itemNo", "orderNo", "mesc",
        "unit", "description", "miscInfo", "productGroup", "partNo",
        "quantity", "unitPrice", "packCount", "palletCount",
        "discount", "vatPercent"
    ],
    "primary_key": "Id",
    "foreign_key": {"orderId": "Sale.Orders.Id"}
}

FULL_SCHEMA = f"""
Table: {ORDER_MODEL['table_name']}
Columns: {', '.join(ORDER_MODEL['columns'])}
PK: {ORDER_MODEL['primary_key']}

Table: {ORDER_ITEM_MODEL['table_name']}
Columns: {', '.join(ORDER_ITEM_MODEL['columns'])}
PK: {ORDER_ITEM_MODEL['primary_key']}
FK: {ORDER_ITEM_MODEL['foreign_key']}

Relationship: Orders.Id = OrderItems.orderId
"""