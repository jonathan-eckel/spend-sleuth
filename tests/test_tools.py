import json
from datetime import date, timedelta

import pytest

from spend_sleuth.agent.tools import make_tools
from tests.conftest import insert_transaction


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def _insert_charges(conn, description, intervals_days, amount=15.99, start_date=None):
    """Insert len(intervals)+1 charges spaced by the given intervals."""
    if start_date is None:
        start_date = date(2024, 1, 1)
    current = start_date
    insert_transaction(conn, description=description, debit=amount,
                       transaction_date=str(current), file_row=0)
    for i, gap in enumerate(intervals_days):
        current += timedelta(days=gap)
        insert_transaction(conn, description=description, debit=amount,
                           transaction_date=str(current), file_row=i + 1)


# ── query_transaction_history ─────────────────────────────────────────────────

def test_query_returns_matching_rows(conn):
    today = date.today()
    insert_transaction(conn, description="NETFLIX", debit=15.99,
                       transaction_date=str(today - timedelta(days=5)), file_row=1)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "query_transaction_history").invoke({"merchant": "NETFLIX"}))
    assert len(result) == 1
    assert result[0]["description"] == "NETFLIX"
    assert result[0]["debit"] == 15.99


def test_query_returns_empty_when_no_match(conn):
    today = date.today()
    insert_transaction(conn, description="NETFLIX", debit=15.99,
                       transaction_date=str(today - timedelta(days=5)))
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "query_transaction_history").invoke({"merchant": "HULU"}))
    assert result == []


def test_query_respects_days_back(conn):
    today = date.today()
    insert_transaction(conn, description="NETFLIX", debit=15.99,
                       transaction_date=str(today - timedelta(days=10)), file_row=1)
    insert_transaction(conn, description="NETFLIX", debit=15.99,
                       transaction_date=str(today - timedelta(days=200)), file_row=2)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "query_transaction_history").invoke(
        {"merchant": "NETFLIX", "days_back": 30}
    ))
    assert len(result) == 1
    assert result[0]["transaction_date"] == str(today - timedelta(days=10))


def test_query_min_amount_filter(conn):
    today = date.today()
    insert_transaction(conn, description="AMAZON", debit=5.00,
                       transaction_date=str(today - timedelta(days=1)), file_row=1)
    insert_transaction(conn, description="AMAZON", debit=50.00,
                       transaction_date=str(today - timedelta(days=2)), file_row=2)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "query_transaction_history").invoke(
        {"merchant": "AMAZON", "min_amount": 20.0}
    ))
    assert len(result) == 1
    assert result[0]["debit"] == 50.0


def test_query_max_amount_filter(conn):
    today = date.today()
    insert_transaction(conn, description="AMAZON", debit=5.00,
                       transaction_date=str(today - timedelta(days=1)), file_row=1)
    insert_transaction(conn, description="AMAZON", debit=50.00,
                       transaction_date=str(today - timedelta(days=2)), file_row=2)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "query_transaction_history").invoke(
        {"merchant": "AMAZON", "max_amount": 20.0}
    ))
    assert len(result) == 1
    assert result[0]["debit"] == 5.0


# ── get_recurring_pattern ─────────────────────────────────────────────────────

def test_recurring_monthly_pattern(conn):
    _insert_charges(conn, "NETFLIX", [30, 30, 30, 30])
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_recurring_pattern").invoke({"merchant": "NETFLIX"}))
    assert result["pattern"] == "monthly"
    assert result["charge_count"] == 5
    assert "mean_interval_days" in result
    assert "typical_amount" in result


def test_recurring_weekly_pattern(conn):
    _insert_charges(conn, "GYM FEE", [7, 7, 7])
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_recurring_pattern").invoke({"merchant": "GYM"}))
    assert result["pattern"] == "weekly"


def test_recurring_quarterly_pattern(conn):
    _insert_charges(conn, "ICLOUD STORAGE", [90, 90, 90])
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_recurring_pattern").invoke({"merchant": "ICLOUD"}))
    assert result["pattern"] == "quarterly"


def test_recurring_fewer_than_3_charges_returns_none(conn):
    insert_transaction(conn, description="NETFLIX", debit=15.99,
                       transaction_date="2024-01-01", file_row=1)
    insert_transaction(conn, description="NETFLIX", debit=15.99,
                       transaction_date="2024-02-01", file_row=2)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_recurring_pattern").invoke({"merchant": "NETFLIX"}))
    assert result["pattern"] is None


def test_recurring_irregular_intervals_returns_none(conn):
    # Intervals vary widely: cv ≈ 0.76 (well above 0.3 threshold)
    _insert_charges(conn, "IRREGULAR CO", [10, 80, 15, 70, 20])
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_recurring_pattern").invoke({"merchant": "IRREGULAR"}))
    assert result["pattern"] is None


def test_recurring_no_charges_returns_none(conn):
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_recurring_pattern").invoke({"merchant": "UNKNOWN"}))
    assert result["pattern"] is None


# ── get_user_context ──────────────────────────────────────────────────────────

def test_user_context_empty_db(conn):
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_user_context").invoke({}))
    assert result["top_categories"] == []
    assert result["top_merchants"] == []
    assert result["avg_monthly_spend"] == 0
    assert result["months_of_data"] == 0


def test_user_context_categories_sorted_by_total(conn):
    insert_transaction(conn, category="Dining", debit=100.00,
                       transaction_date="2025-01-15", file_row=1)
    insert_transaction(conn, category="Shopping", debit=200.00,
                       transaction_date="2025-01-16", file_row=2)
    insert_transaction(conn, category="Shopping", debit=50.00,
                       transaction_date="2025-01-17", file_row=3)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_user_context").invoke({}))
    cats = result["top_categories"]
    assert cats[0]["category"] == "Shopping"
    assert cats[0]["total"] == 250.0
    assert cats[1]["category"] == "Dining"


def test_user_context_merchants_sorted_by_total(conn):
    insert_transaction(conn, description="WHOLE FOODS", debit=80.00,
                       transaction_date="2025-01-01", file_row=1)
    insert_transaction(conn, description="WHOLE FOODS", debit=60.00,
                       transaction_date="2025-01-15", file_row=2)
    insert_transaction(conn, description="COSTCO", debit=200.00,
                       transaction_date="2025-01-20", file_row=3)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_user_context").invoke({}))
    totals = [m["total"] for m in result["top_merchants"]]
    assert totals == sorted(totals, reverse=True)


def test_user_context_avg_monthly_spend(conn):
    insert_transaction(conn, debit=100.00, transaction_date="2025-01-15", file_row=1)
    insert_transaction(conn, debit=200.00, transaction_date="2025-02-15", file_row=2)
    tools = make_tools(conn)
    result = json.loads(_tool(tools, "get_user_context").invoke({}))
    assert result["avg_monthly_spend"] == 150.0
    assert result["months_of_data"] == 2
