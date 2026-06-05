import json
from datetime import timedelta

import duckdb
from langchain_core.tools import tool


def make_tools(conn: duckdb.DuckDBPyConnection) -> list:
    """Return tool list bound to the given DB connection."""

    @tool
    def query_transaction_history(
        merchant: str,
        days_back: int = 90,
        min_amount: float | None = None,
        max_amount: float | None = None,
    ) -> str:
        """Search transaction history by merchant name substring.

        Args:
            merchant: Merchant name or substring to search for.
            days_back: How many days of history to look at (default 90).
                For subscription alerts use mean_interval_days × 4 to cover
                multiple full billing cycles (e.g. 360 for quarterly).
            min_amount: Optional minimum debit amount filter.
            max_amount: Optional maximum debit amount filter.
        """
        params = [merchant, merchant, days_back]
        amount_clauses = ""
        if min_amount is not None:
            amount_clauses += " AND debit >= ?"
            params.append(min_amount)
        if max_amount is not None:
            amount_clauses += " AND debit <= ?"
            params.append(max_amount)

        sql = f"""
            SELECT description, transaction_date::VARCHAR AS transaction_date, debit
            FROM transactions
            WHERE (UPPER(description) LIKE UPPER('%' || ? || '%')
                   OR UPPER(description) LIKE UPPER(? || '%'))
              AND transaction_date >= CURRENT_DATE - INTERVAL (?) DAYS
              {amount_clauses}
            ORDER BY transaction_date DESC
            LIMIT 50
        """
        rows = conn.execute(sql, params).fetchall()
        return json.dumps([
            {"description": r[0], "transaction_date": r[1], "debit": float(r[2]) if r[2] is not None else None}
            for r in rows
        ])

    @tool
    def get_recurring_pattern(merchant: str) -> str:
        """Detect whether a merchant has a recurring charge pattern.

        Args:
            merchant: Merchant name or substring to search for.
        """
        rows = conn.execute("""
            SELECT transaction_date::VARCHAR AS transaction_date, debit
            FROM transactions
            WHERE UPPER(description) LIKE UPPER('%' || ? || '%')
              AND debit IS NOT NULL AND debit > 0
            ORDER BY transaction_date
        """, [merchant]).fetchall()

        if len(rows) < 3:
            return json.dumps({"pattern": None, "message": "fewer than 3 charges found, cannot detect pattern"})

        from datetime import date as date_type
        dates = [date_type.fromisoformat(r[0]) for r in rows]
        amounts = [float(r[1]) for r in rows]
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

        mean_interval = sum(intervals) / len(intervals)
        variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
        stddev = variance ** 0.5
        cv = stddev / mean_interval if mean_interval > 0 else 1.0

        if cv < 0.3:
            pattern = "weekly" if mean_interval < 10 else "monthly" if mean_interval < 45 else "quarterly"
            return json.dumps({
                "pattern": pattern,
                "mean_interval_days": round(mean_interval, 1),
                "charge_count": len(rows),
                "typical_amount": round(sum(amounts) / len(amounts), 2),
                "message": f"Regular {pattern} pattern detected ({len(rows)} charges, avg every {mean_interval:.0f} days)",
            })

        return json.dumps({
            "pattern": None,
            "charge_count": len(rows),
            "message": "no consistent recurring pattern detected",
        })

    @tool
    def get_user_context() -> str:
        """Return a synthesized spending profile: top categories, top merchants, monthly spend."""
        category_rows = conn.execute("""
            SELECT category, SUM(debit) AS total, COUNT(*) AS count
            FROM transactions
            WHERE debit IS NOT NULL
            GROUP BY category
            ORDER BY total DESC
            LIMIT 10
        """).fetchall()

        merchant_rows = conn.execute("""
            SELECT description, SUM(debit) AS total, COUNT(*) AS count
            FROM transactions
            WHERE debit IS NOT NULL
            GROUP BY description
            ORDER BY total DESC
            LIMIT 10
        """).fetchall()

        monthly_rows = conn.execute("""
            SELECT DATE_TRUNC('month', transaction_date)::VARCHAR AS month, SUM(debit) AS total
            FROM transactions
            WHERE debit IS NOT NULL
            GROUP BY month
            ORDER BY month
        """).fetchall()

        monthly_totals = [float(r[1]) for r in monthly_rows]
        avg_monthly = sum(monthly_totals) / len(monthly_totals) if monthly_totals else 0

        return json.dumps({
            "top_categories": [
                {"category": r[0], "total": float(r[1]), "count": r[2]}
                for r in category_rows
            ],
            "top_merchants": [
                {"description": r[0], "total": float(r[1]), "count": r[2]}
                for r in merchant_rows
            ],
            "avg_monthly_spend": round(avg_monthly, 2),
            "months_of_data": len(monthly_rows),
        })

    return [query_transaction_history, get_recurring_pattern, get_user_context]
