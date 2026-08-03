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
        "question": "فروش هر محصول در سه ماه گذشته چقدر بوده است؟",
        "sql": """
            SELECT oi.productGroup, SUM(oi.quantity * oi.unitPrice) AS revenue
            FROM Sale.OrderItems oi
            JOIN Sale.Orders o ON oi.orderId = o.Id
            WHERE o.entityDate >= DATEADD(month, -3, GETDATE())
            GROUP BY oi.productGroup
            ORDER BY revenue DESC
        """
    },
    {
        "question": "مجموع فروش هر گروه محصول در سال 1399 چقدر بوده است؟",
        "sql": """
            SELECT oi.productGroup, SUM(oi.quantity * oi.unitPrice) AS total_sales
            FROM Sale.OrderItems oi
            JOIN Sale.Orders o ON oi.orderId = o.Id
            WHERE o.jalaliYearNo = '1399'
            GROUP BY oi.productGroup
            ORDER BY total_sales DESC
        """
    }
]