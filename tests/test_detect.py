import pytest
from datetime import date
from spend_sleuth.detect import normalize_merchant, find_duplicate_candidates, find_unusual_amount_candidates
from tests.conftest import insert_transaction


# --- normalize_merchant ---

def test_normalize_strips_store_number():
    assert normalize_merchant("TRADER JOE'S #123") == "TRADER JOE'S"

def test_normalize_strips_sq_prefix():
    assert normalize_merchant("SQ *BLUE BOTTLE") == "BLUE BOTTLE"

def test_normalize_strips_tst_prefix():
    assert normalize_merchant("TST*MOMOFUKU") == "MOMOFUKU"

# def test_normalize_strips_pp_prefix():
#     assert normalize_merchant("PP*VENMO") == "VENMO"

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
    assert results[0]["alert_type"] == "duplicate"
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


def test_flags_mta_nyct_paygo_as_omny(conn):
    insert_transaction(conn, description="MTA*NYCT PAYGO", debit=2.90,
                       transaction_date="2025-03-01", file_row=0)
    insert_transaction(conn, description="MTA*NYCT PAYGO", debit=2.90,
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


# --- find_unusual_amount_candidates ---

def _insert_baseline(conn, description, amounts, start_date="2025-01-01"):
    """Insert a sequence of transactions for a merchant to establish a baseline."""
    from datetime import date, timedelta
    base = date.fromisoformat(start_date)
    for i, amount in enumerate(amounts):
        insert_transaction(
            conn,
            description=description,
            debit=amount,
            transaction_date=str(base + timedelta(days=i * 14)),
            file_row=i,
        )


def test_unusual_amount_flags_high_outlier(conn):
    # 10 charges at ~$50, one at $100 — well above threshold
    _insert_baseline(conn, "NETFLIX", [50.00] * 10)
    insert_transaction(conn, description="NETFLIX", debit=100.00,
                       transaction_date="2025-07-01", file_row=10)

    results = find_unusual_amount_candidates(conn)
    assert len(results) == 1
    assert results[0]["alert_type"] == "unusual_amount"
    assert results[0]["normalized_merchant"] == "NETFLIX"
    assert results[0]["transaction"]["debit"] == 100.00
    assert results[0]["z_score"] > 2.0
    assert results[0]["delta"] > 10.0


def test_unusual_amount_ignores_normal_charge(conn):
    # 10 charges at ~$50, one at $55 — within normal range
    _insert_baseline(conn, "NETFLIX", [50.00] * 10)
    insert_transaction(conn, description="NETFLIX", debit=55.00,
                       transaction_date="2025-07-01", file_row=10)

    results = find_unusual_amount_candidates(conn)
    assert len(results) == 0


def test_unusual_amount_requires_min_history(conn):
    # 3 baseline + 1 outlier = 4 total transactions, below min_history of 5
    _insert_baseline(conn, "SPOTIFY", [10.00] * 3)
    insert_transaction(conn, description="SPOTIFY", debit=100.00,
                       transaction_date="2025-07-01", file_row=3)

    results = find_unusual_amount_candidates(conn, min_history=5)
    assert len(results) == 0


def test_unusual_amount_respects_min_delta(conn):
    # High variance merchant: $1–$9 charges, one at $9.50 — z is high but delta < $10
    _insert_baseline(conn, "CORNER DELI", [1.00, 2.00, 3.00, 4.00, 5.00, 6.00, 7.00, 8.00, 9.00, 1.00])
    insert_transaction(conn, description="CORNER DELI", debit=9.50,
                       transaction_date="2025-07-01", file_row=10)

    results = find_unusual_amount_candidates(conn, min_delta=10.0)
    assert len(results) == 0


def test_unusual_amount_ignores_low_zscore(conn):
    # High variance merchant: $20–$80 charges, one at $95 — delta > $10 but z < 2.0
    amounts = [20.00, 80.00, 20.00, 80.00, 20.00, 80.00, 20.00, 80.00, 20.00, 80.00]
    _insert_baseline(conn, "VARIABLE SHOP", amounts)
    insert_transaction(conn, description="VARIABLE SHOP", debit=70.00,
                       transaction_date="2025-07-01", file_row=10)

    results = find_unusual_amount_candidates(conn, z_threshold=2.0)
    assert len(results) == 0


def test_unusual_amount_high_side_only(conn):
    # Charge well below the mean — should not flag
    _insert_baseline(conn, "GYM", [50.00] * 10)
    insert_transaction(conn, description="GYM", debit=5.00,
                       transaction_date="2025-07-01", file_row=10)

    results = find_unusual_amount_candidates(conn)
    assert len(results) == 0
