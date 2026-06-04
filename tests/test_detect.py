import pytest
from datetime import date
from spend_sleuth.detect import normalize_merchant, find_duplicate_candidates, find_unusual_amount_candidates, find_subscription_candidates, build_canonical_merchant_map
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


# --- build_canonical_merchant_map ---

def test_canonical_map_clusters_similar_merchants(conn):
    # "ACME STORE" and "ACME STORES" are very similar (ratio ≈ 0.95)
    # Higher-count merchant should become canonical
    for i in range(10):  # 10 transactions for ACME STORE
        insert_transaction(conn, description="ACME STORE", debit=20.00, file_row=i)
    for i in range(3):   # 3 for ACME STORES → should map to ACME STORE
        insert_transaction(conn, description="ACME STORES", debit=20.00, file_row=100 + i)

    cmap = build_canonical_merchant_map(conn)
    assert cmap["ACME STORE"] == "ACME STORE"
    assert cmap["ACME STORES"] == "ACME STORE"


def test_canonical_map_leaves_dissimilar_merchants_alone(conn):
    insert_transaction(conn, description="AMAZON", debit=50.00, file_row=0)
    insert_transaction(conn, description="WALMART", debit=50.00, file_row=1)

    cmap = build_canonical_merchant_map(conn)
    assert cmap["AMAZON"] == "AMAZON"
    assert cmap["WALMART"] == "WALMART"


def test_canonical_map_transitivity(conn):
    # Three merchants that all pair-wise score ≥ 0.80 (each pair ≈ 0.95).
    # Union-find should merge all three into one cluster; canonical = highest count.
    # "ACME STORE" / "ACME STORES" / "ACME STORED" — each pair differs by 1 char.
    for i in range(10):
        insert_transaction(conn, description="ACME STORE", debit=10.00, file_row=i)
    for i in range(5):
        insert_transaction(conn, description="ACME STORES", debit=10.00, file_row=100 + i)
    for i in range(2):
        insert_transaction(conn, description="ACME STORED", debit=10.00, file_row=200 + i)

    cmap = build_canonical_merchant_map(conn)
    # All three should share the same canonical name (ACME STORE has highest count)
    assert cmap["ACME STORE"] == cmap["ACME STORES"] == cmap["ACME STORED"] == "ACME STORE"


# --- find_subscription_candidates ---

def _insert_recurring(conn, description, amount, interval_days, count, start_date="2025-01-01", file_row_offset=0):
    """Insert `count` charges spaced `interval_days` apart."""
    from datetime import date, timedelta
    base = date.fromisoformat(start_date)
    for i in range(count):
        insert_transaction(
            conn,
            description=description,
            debit=amount,
            transaction_date=str(base + timedelta(days=i * interval_days)),
            file_row=file_row_offset + i,
        )


def test_subscription_finds_monthly_recurring(conn):
    _insert_recurring(conn, "NETFLIX", 15.99, interval_days=30, count=6)

    results = find_subscription_candidates(conn)
    assert len(results) == 1
    r = results[0]
    assert r["alert_type"] == "subscription"
    assert r["normalized_merchant"] == "NETFLIX"
    assert r["pattern"] == "monthly"
    assert r["charge_count"] == 6
    assert r["typical_amount"] == pytest.approx(15.99)
    assert r["total_spent"] == pytest.approx(15.99 * 6)


def test_subscription_most_recent_charge_as_transaction(conn):
    _insert_recurring(conn, "SPOTIFY", 9.99, interval_days=30, count=4)

    results = find_subscription_candidates(conn)
    assert len(results) == 1
    # Most recent charge is the last inserted (index 3 → 90 days after start)
    from datetime import date, timedelta
    expected_date = str(date.fromisoformat("2025-01-01") + timedelta(days=3 * 30))
    assert results[0]["transaction"]["transaction_date"] == expected_date


def test_subscription_ignores_irregular_charges(conn):
    # Intervals: 5, 30, 60, 90, 10 days — high variance, cv >> 0.3
    from datetime import date
    dates = ["2025-01-01", "2025-01-06", "2025-02-05", "2025-04-06", "2025-07-04", "2025-07-14"]
    for i, d in enumerate(dates):
        insert_transaction(conn, description="IRREGULAR CO", debit=20.00,
                           transaction_date=d, file_row=i)

    results = find_subscription_candidates(conn)
    assert len(results) == 0


def test_subscription_requires_min_charges(conn):
    _insert_recurring(conn, "NEW SUB", 12.00, interval_days=30, count=2)

    results = find_subscription_candidates(conn, min_charges=3)
    assert len(results) == 0


def test_subscription_requires_min_span(conn):
    # 4 charges at 7-day intervals = 21-day span, below default min_span_days=60
    _insert_recurring(conn, "WEEKLY APP", 5.00, interval_days=7, count=4)

    results = find_subscription_candidates(conn, min_span_days=60)
    assert len(results) == 0


def test_subscription_detects_weekly_pattern(conn):
    _insert_recurring(conn, "WEEKLY APP", 5.00, interval_days=7, count=10)

    results = find_subscription_candidates(conn, min_span_days=60)
    assert len(results) == 1
    assert results[0]["pattern"] == "weekly"


def test_subscription_detects_quarterly_pattern(conn):
    _insert_recurring(conn, "QUARTERLY SVC", 50.00, interval_days=90, count=5)

    results = find_subscription_candidates(conn)
    assert len(results) == 1
    assert results[0]["pattern"] == "quarterly"


def test_subscription_sorted_by_total_spent(conn):
    _insert_recurring(conn, "CHEAP SUB", 5.00, interval_days=30, count=6, file_row_offset=0)
    _insert_recurring(conn, "EXPENSIVE SUB", 50.00, interval_days=30, count=6, file_row_offset=10)

    results = find_subscription_candidates(conn)
    assert len(results) == 2
    assert results[0]["normalized_merchant"] == "EXPENSIVE SUB"
    assert results[1]["normalized_merchant"] == "CHEAP SUB"


def test_canonical_map_threshold_one_no_clustering(conn):
    # At threshold=1.0 only identical strings cluster — each maps to itself
    insert_transaction(conn, description="ACME STORE", debit=20.00, file_row=0)
    insert_transaction(conn, description="ACME STORES", debit=20.00, file_row=1)

    cmap = build_canonical_merchant_map(conn, threshold=1.0)
    assert cmap["ACME STORE"] == "ACME STORE"
    assert cmap["ACME STORES"] == "ACME STORES"
