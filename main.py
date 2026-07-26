# main.py

from queries import SalesQueries
from prettytable import PrettyTable  

def print_table(data, title=None):
    if not data:
        print("No data found.")
        return
    if title:
        print(f"\n{title}")
        print("-" * 80)
    columns = data[0].keys()
    table = PrettyTable(columns)
    for row in data:
        table.add_row([row.get(col, '') for col in columns])
    print(table)

def main():
    sales = SalesQueries()

    # 1. best buyers
    top_buyers = sales.get_top_buyers(10)
    print_table(top_buyers, "TOP 10 BUYERS BY SPENT AMOUNT")

    # 2. sales by product
    product_sales = sales.get_sales_by_product_group()
    print_table(product_sales, "SALES BY PRODUCT GROUP")

    # 3. mounthly sales(year:1399)
    monthly = sales.get_monthly_sales(year="1399")
    print_table(monthly, "MONTHLY SALES - 1399")

    # 4. shipping 
    shipping = sales.get_shipping_performance()
    print_table(shipping, "SHIPPING PERFORMANCE (days)")

    # 5. order status
    order_status = sales.get_orders_by_status()
    print_table(order_status, "ORDERS BY STATUS")

    # 6. product_details
    products = sales.get_product_details(product_group="HDPE")
    print_table(products, "HDPE PRODUCT DETAILS")

    sales.close()

if __name__ == "__main__":
    main()