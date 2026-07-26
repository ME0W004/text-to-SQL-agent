# queries.py

from db_connection import DatabaseConnection
from config import DB_CONFIG

class SalesQueries:
    def __init__(self):
        self.db = DatabaseConnection(**DB_CONFIG)
        self.db.connect()

    def get_top_buyers(self, limit=10):
        """بازگرداندن بهترین خریداران بر اساس مبلغ کل"""
        query = """
            SELECT 
                o.buyer,
                COUNT(DISTINCT o.Id) AS order_count,
                SUM(oi.quantity * oi.unitPrice) AS total_spent
            FROM Sale.Orders o
            JOIN Sale.OrderItems oi ON o.Id = oi.orderId
            GROUP BY o.buyer
            ORDER BY total_spent DESC
        """
        if limit:
            query += f" OFFSET 0 ROWS FETCH NEXT {limit} ROWS ONLY"
        return self.db.execute_query(query)

    def get_sales_by_product_group(self):
        """فروش بر اساس گروه محصول"""
        query = """
            SELECT 
                productGroup,
                SUM(quantity * unitPrice) AS revenue,
                COUNT(*) AS item_count
            FROM Sale.OrderItems
            GROUP BY productGroup
            ORDER BY revenue DESC
        """
        return self.db.execute_query(query)

    def get_monthly_sales(self, year=None):
        """فروش ماهانه (بر اساس سال شمسی)"""
        query = """
            SELECT 
                o.jalaliYearNo,
                o.operationMonth,
                COUNT(DISTINCT o.Id) AS order_count,
                SUM(oi.quantity * oi.unitPrice) AS revenue
            FROM Sale.Orders o
            JOIN Sale.OrderItems oi ON o.Id = oi.orderId
        """
        if year:
            query += f" WHERE o.jalaliYearNo = '{year}'"
        query += """
            GROUP BY o.jalaliYearNo, o.operationMonth
            ORDER BY o.jalaliYearNo, o.operationMonth
        """
        return self.db.execute_query(query)

    def get_shipping_performance(self):
        """میانگین زمان بین سفارش و حمل"""
        query = """
            SELECT 
                AVG(DATEDIFF(day, o.entityDate, s.entityDate)) AS avg_days_to_ship,
                MIN(DATEDIFF(day, o.entityDate, s.entityDate)) AS min_days,
                MAX(DATEDIFF(day, o.entityDate, s.entityDate)) AS max_days
            FROM Sale.Orders o
            JOIN Sale.OrderItems oi ON o.Id = oi.orderId
            JOIN Sale.Shipments s ON oi.Id = s.orderItemId
            WHERE s.entityDate IS NOT NULL AND o.entityDate IS NOT NULL
        """
        return self.db.execute_query(query)

    def get_orders_by_status(self):
        """تعداد سفارش‌ها بر اساس وضعیت"""
        query = """
            SELECT 
                status,
                COUNT(*) AS order_count
            FROM Sale.Orders
            GROUP BY status
            ORDER BY order_count DESC
        """
        return self.db.execute_query(query)

    def get_product_details(self, product_group=None):
        """جزئیات محصولات با بیشترین فروش"""
        query = """
            SELECT 
                productGroup,
                partNo,
                description,
                SUM(quantity) AS total_quantity,
                AVG(unitPrice) AS avg_price,
                COUNT(*) AS order_count
            FROM Sale.OrderItems
            WHERE productGroup IS NOT NULL
        """
        if product_group:
            query += f" AND productGroup = '{product_group}'"
        query += """
            GROUP BY productGroup, partNo, description
            ORDER BY total_quantity DESC
        """
        return self.db.execute_query(query)

    def close(self):
        self.db.close()