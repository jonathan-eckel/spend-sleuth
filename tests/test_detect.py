import pytest
from datetime import date
from spend_sleuth.detect import normalize_merchant, find_duplicate_candidates
from tests.conftest import insert_transaction


# --- normalize_merchant ---

def test_normalize_strips_store_number():
    assert normalize_merchant("TRADER JOE'S #123") == "TRADER JOE'S"

def test_normalize_strips_sq_prefix():
    assert normalize_merchant("SQ *BLUE BOTTLE") == "BLUE BOTTLE"

def test_normalize_strips_tst_prefix():
    assert normalize_merchant("TST*MOMOFUKU") == "MOMOFUKU"

def test_normalize_strips_pp_prefix():
    assert normalize_merchant("PP*VENMO") == "VENMO"

def test_normalize_uppercases():
    assert normalize_merchant("whole foods") == "WHOLE FOODS"

def test_normalize_collapses_whitespace():
    assert normalize_merchant("  TARGET   STORE  ") == "TARGET STORE"

def test_normalize_unknown_prefix_unchanged():
    assert normalize_merchant("AMZN*SOMETHING") == "AMZN*SOMETHING"


# --- find_duplicate_candidates ---

def test_finds_exact_duplicate(conn):
    insert_transaction(conn, description="TRADER JOE'S #1", debit=47.82,
                       transaction_date="2025-03-01", file_row=0)
    insert_transaction(conn, description="TRADER JOE'S #2", debit=47.82,
                       transaction_date="2025-03-03", file_row=1)

    results = find_duplicate_candidates(conn)
    assert len(results) == 1
    assert results[0]["normalized_merchant"] == "TRADER JOE'S"
    assert results[0]["days_apart"] == 2
    assert results[0]["amount_diff"] == 0.0
    assert results[0]["is_omny"] is False


def test_ignores_different_merchants(conn):
    insert_transaction(conn, description="TRADER JOE'S", debit=20.00,
                       transaction_date="2025-03-01", file_row=0)
    insert_transaction(conn, description="WHOLE FOODS", debit=20.00,
                       transaction_date="2025-03-02", file_row=1)

    results = find_duplicate_candidates(conn)
    assert len(results) == 0


def test_ignores_pair_outside_window(conn):
    insert_transaction(conn, description="STARBUCKS", debit=6.50,
                       transaction_date="2025-03-01", file_row=0)
    insert_transaction(conn, description="STARBUCKS", debit=6.50,
                       transaction_date="2025-03-15", file_row=1)

    results = find_duplicate_candidates(conn, window_days=7)
    assert len(results) == 0


def test_amount_tolerance(conn):
    insert_transaction(conn, description="NETFLIX", debit=15.49,
                       transaction_date="2025-03-01", file_row=0)
    insert_transaction(conn, description="NETFLIX", debit=15.50,
                       transaction_date="2025-03-02", file_row=1)

    assert len(find_duplicate_candidates(conn, amount_tolerance=0.01)) == 1
    assert len(find_duplicate_candidates(conn, amount_tolerance=0.00)) == 0


def test_flags_omny_pair(conn):
    insert_transaction(conn, description="OMNY * MTA", debit=2.90,
                       transaction_date="2025-03-01", file_row=0)
    insert_transaction(conn, description="OMNY * MTA", debit=2.90,
                       transaction_date="2025-03-01", file_row=1)

    results = find_duplicate_candidates(conn)
    assert len(results) == 1
    assert results[0]["is_omny"] is True


def test_date_range_filter(conn):
    insert_transaction(conn, description="AMAZON", debit=29.99,
                       transaction_date="2025-01-05", file_row=0)
    insert_transaction(conn, description="AMAZON", debit=29.99,
                       transaction_date="2025-01-06", file_row=1)
    # pair outside range
    insert_transaction(conn, description="AMAZON", debit=29.99,
                       transaction_date="2025-03-01", file_row=2)
    insert_transaction(conn, description="AMAZON", debit=29.99,
                       transaction_date="2025-03-02", file_row=3)

    results = find_duplicate_candidates(
        conn, start_date=date(2025, 1, 1), end_date=date(2025, 1, 31)
    )
    assert len(results) == 1
    dates = {
        results[0]["txn_a"]["transaction_date"].date(),
        results[0]["txn_b"]["transaction_date"].date(),
    }
    assert dates == {date(2025, 1, 5), date(2025, 1, 6)}


def test_no_self_pairs(conn):
    insert_transaction(conn, description="DUANE READE", debit=12.00,
                       transaction_date="2025-03-01", file_row=0)

    results = find_duplicate_candidates(conn)
    assert len(results) == 0
