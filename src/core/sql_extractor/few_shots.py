# src/core/sql_extractor/few_shots.py

FEW_SHOT_EXAMPLES = [
    {
        "question": "۱۰ مشتری برتر سال ۱۴۰۳ چه کسانی بودند؟",
        "sql": """
            SELECT TOP 10 buyer, SUM(oi.quantity * oi.unitPrice) AS total_spent
            FROM Sale.Orders o
            JOIN Sale.OrderItems oi ON o.Id = oi.orderId
            WHERE o.jalaliYearNo = '1403'
            GROUP BY buyer
            ORDER BY total_spent DESC
        """
    },
    {
        "question": "مجموع فروش هر گروه محصول در سال ۱۳۹۹ چقدر بوده است؟",
        "sql": """
            SELECT oi.productGroup, SUM(oi.quantity * oi.unitPrice) AS total_sales
            FROM Sale.OrderItems oi
            JOIN Sale.Orders o ON oi.orderId = o.Id
            WHERE o.jalaliYearNo = '1399'
            GROUP BY oi.productGroup
            ORDER BY total_sales DESC
        """
    },
    {
        "question": "میانگین ارزش هر سفارش در سال ۱۳۹۹ را محاسبه کن.",
        "sql": """
            SELECT AVG(order_value) AS avg_order_value
            FROM (
                SELECT SUM(oi.quantity * oi.unitPrice) AS order_value
                FROM Sale.Orders o
                JOIN Sale.OrderItems oi ON o.Id = oi.orderId
                WHERE o.jalaliYearNo = '1399'
                GROUP BY o.Id
            ) AS order_totals
        """
    },
    {
        "question": "کدام گروه‌های محصول بیشترین تعداد سفارش را داشته‌اند؟",
        "sql": """
            SELECT oi.productGroup, COUNT(DISTINCT o.Id) AS order_count
            FROM Sale.Orders o
            JOIN Sale.OrderItems oi ON o.Id = oi.orderId
            GROUP BY oi.productGroup
            ORDER BY order_count DESC
        """
    },
    {
        "question": "تعداد کل سفارش‌ها و مجموع ارزش فروش را برای هر ماه از سال ۱۳۹۹ نمایش بده.",
        "sql": """
            SELECT 
                o.operationMonth,
                COUNT(DISTINCT o.Id) AS total_orders,
                SUM(oi.quantity * oi.unitPrice) AS total_revenue
            FROM Sale.Orders o
            JOIN Sale.OrderItems oi ON o.Id = oi.orderId
            WHERE o.jalaliYearNo = '1399'
            GROUP BY o.operationMonth
            ORDER BY o.operationMonth
        """
    }
]