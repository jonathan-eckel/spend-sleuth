"""Tests for the demo-data generator.

Unit tests cover deterministic anonymization and the hash refactor. The
end-to-end tests are the real value: each preset is written to a temp DuckDB
and the matching detector is run to confirm the engineered alert actually
fires.
"""
from datetime import date

import duckdb
import pytest

from spend_sleuth.db import get_connection, init_schema
from spend_sleuth.detect import (
    find_duplicate_candidates,
    find_subscription_candidates,
    find_unusual_amount_candidates,
    interval_to_pattern,
    normalize_merchant,
)
from spend_sleuth.load import compute_row_hash
from spend_sleuth import demo_gen


# --- Fixtures ---------------------------------------------------------------

BASE = {
    "transaction_date": date(2025, 6, 1),
    "card_no": "1234",
    "description": "TRADER JOE'S #142",
    "category": "Groceries",
    "debit": 50.00,
}


@pytest.fixture
def demo_db(tmp_path):
    """Path to an isolated demo DB under tmp_path."""
    return tmp_path / "builder.db"


def _open(db_path) -> duckdb.DuckDBPyConnection:
    return get_connection(db_path, read_only=True)


# --- Anonymization ----------------------------------------------------------

def test_anonymize_card_is_deterministic_and_scrubs_original():
    out = demo_gen.anonymize_card("1234")
    assert out == demo_gen.anonymize_card("1234")
    assert out.startswith("DEMO-")
    assert "1234" not in out


def test_anonymize_card_distinguishes_cards():
    assert demo_gen.anonymize_card("1234") != demo_gen.anonymize_card("5678")


def test_anonymize_merchant_is_deterministic_and_scrubs_original():
    raw = "TRADER JOE'S #142"
    out = demo_gen.anonymize_merchant(raw)
    assert out == demo_gen.anonymize_merchant(raw)
    # The real, distinctive merchant string is gone (a generic brand word like
    # "TRADERS" may coincidentally appear, but the merchant isn't recoverable).
    assert out != normalize_merchant(raw)
    assert "JOE" not in out


def test_anonymize_merchant_collapses_store_number_variants():
    # Store-number variants normalize to the same merchant, so same fake brand.
    assert demo_gen.anonymize_merchant("TRADER JOE'S #142") == \
        demo_gen.anonymize_merchant("TRADER JOE'S #99")


def test_generators_anonymize_by_default():
    rows = demo_gen.gen_duplicate(BASE)
    assert all(r["card_no"] == demo_gen.anonymize_card(BASE["card_no"]) for r in rows)
    assert all(r["description"] == demo_gen.anonymize_merchant(BASE["description"]) for r in rows)


def test_generators_honor_anonymize_false_override():
    # When anonymize=False, the caller's card/merchant are used verbatim on every
    # row (the UI uses this so a user-edited merchant name propagates).
    base = {**BASE, "card_no": "DEMO-9999", "description": "MY CUSTOM SHOP"}
    for rows in (
        demo_gen.gen_duplicate(base, anonymize=False),
        demo_gen.gen_unusual_amount(base, anonymize=False),
        demo_gen.gen_subscription(base, anonymize=False),
    ):
        assert {r["card_no"] for r in rows} == {"DEMO-9999"}
        assert {r["description"] for r in rows} == {"MY CUSTOM SHOP"}


# --- Combine: inference helpers --------------------------------------------

@pytest.mark.parametrize("days, expected", [
    (7, "weekly"), (14, "biweekly"), (30, "monthly"),
    (91, "quarterly"), (182, "semi-annual"), (365, "annual"),
])
def test_interval_to_pattern(days, expected):
    assert interval_to_pattern(days) == expected


def test_summarize_selection_infers_monthly_cadence_and_amount():
    bases = [
        {"transaction_date": date(2025, 1, 5), "debit": 15.49},
        {"transaction_date": date(2025, 2, 4), "debit": 15.49},
        {"transaction_date": date(2025, 3, 6), "debit": 15.49},
        {"transaction_date": date(2025, 4, 5), "debit": 15.49},
    ]
    s = demo_gen.summarize_selection(bases)
    assert s["count"] == 4
    assert s["pattern"] == "monthly"
    assert 28 <= s["mean_interval_days"] <= 31
    assert s["mean_amount"] == pytest.approx(15.49)
    assert s["last_date"] == date(2025, 4, 5)


def test_infer_unusual_baseline_spike_uses_largest_as_spike():
    bases = [
        {"debit": 48.0}, {"debit": 50.0}, {"debit": 52.0}, {"debit": 210.0},
    ]
    baseline, spike = demo_gen.infer_unusual_baseline_spike(bases)
    assert spike == 210.0
    assert baseline == pytest.approx(50.0)  # mean of 48, 50, 52


def test_gen_unusual_amount_spike_override():
    rows = demo_gen.gen_unusual_amount(BASE, baseline=40.0, spike_amount=200.0)
    # The final (anomaly) row uses the explicit spike, not baseline × multiplier.
    assert rows[-1]["debit"] == pytest.approx(200.0)


# --- Combine: end-to-end ----------------------------------------------------

def test_combine_subscription_trips_detector_at_inferred_cadence(demo_db):
    bases = [
        {"transaction_date": date(2025, 1, 5), "card_no": "1234",
         "description": "NETFLIX.COM", "category": "Entertainment", "debit": 15.49},
        {"transaction_date": date(2025, 2, 4), "card_no": "1234",
         "description": "NETFLIX.COM", "category": "Entertainment", "debit": 15.49},
        {"transaction_date": date(2025, 3, 6), "card_no": "1234",
         "description": "NETFLIX.COM", "category": "Entertainment", "debit": 15.49},
    ]
    s = demo_gen.summarize_selection(bases)
    combined = {
        "transaction_date": s["last_date"], "card_no": "DEMO-1",
        "description": "WILLOW STREAM", "category": "Entertainment",
        "debit": s["mean_amount"],
    }
    rows = demo_gen.gen_subscription(combined, pattern=s["pattern"], count=6, anonymize=False)
    demo_gen.write_demo_rows(rows, demo_db)

    conn = _open(demo_db)
    cands = find_subscription_candidates(conn)
    conn.close()
    assert cands, "expected a subscription candidate"
    assert cands[0]["pattern"] == "monthly"
    assert cands[0]["cv"] < 0.3


def test_combine_unusual_trips_detector_with_largest_as_spike(demo_db):
    bases = [
        {"transaction_date": date(2025, 5, 2), "card_no": "1234",
         "description": "AMAZON.COM", "category": "Shopping", "debit": 48.0},
        {"transaction_date": date(2025, 5, 12), "card_no": "1234",
         "description": "AMAZON.COM", "category": "Shopping", "debit": 52.0},
        {"transaction_date": date(2025, 6, 1), "card_no": "1234",
         "description": "AMAZON.COM", "category": "Shopping", "debit": 200.0},
    ]
    s = demo_gen.summarize_selection(bases)
    baseline, spike = demo_gen.infer_unusual_baseline_spike(bases)
    combined = {
        "transaction_date": s["last_date"], "card_no": "DEMO-1",
        "description": "URBAN DEPOT", "category": "Shopping", "debit": baseline,
    }
    rows = demo_gen.gen_unusual_amount(
        combined, baseline=baseline, spike_amount=spike, anonymize=False
    )
    demo_gen.write_demo_rows(rows, demo_db)

    conn = _open(demo_db)
    cands = find_unusual_amount_candidates(conn)
    conn.close()
    assert cands, "expected an unusual-amount candidate"
    flagged = max(cands, key=lambda c: c["z_score"])
    assert flagged["z_score"] >= 2.0
    assert flagged["transaction"]["debit"] == pytest.approx(spike)


# --- Hash refactor ----------------------------------------------------------

def test_compute_row_hash_matches_known_value():
    fields = {
        "transaction_date": "2025-01-01",
        "posted_date": "2025-01-02",
        "card_no": "1234",
        "description": "TEST MERCHANT",
        "debit": 10.0,
        "credit": None,
        "source_file": "test.csv",
        "file_row": 0,
    }
    import hashlib
    key = "|".join(str(fields[c]) for c in [
        "transaction_date", "posted_date", "card_no",
        "description", "debit", "credit", "source_file", "file_row",
    ])
    expected = hashlib.sha256(key.encode()).hexdigest()
    assert compute_row_hash(fields) == expected


# --- Preset generators (end-to-end against detectors) -----------------------

def test_gen_duplicate_trips_duplicate_detector(demo_db):
    rows = demo_gen.gen_duplicate(BASE)
    inserted = demo_gen.write_demo_rows(rows, demo_db)
    assert inserted == len(rows)

    conn = _open(demo_db)
    cands = find_duplicate_candidates(conn)
    conn.close()

    assert cands, "expected a duplicate candidate"
    c = cands[0]
    assert c["amount_diff"] == 0.0
    assert c["days_apart"] == 0
    assert not c["is_omny"]
    assert c["normalized_merchant"] == normalize_merchant(rows[0]["description"])


def test_gen_unusual_amount_trips_unusual_detector(demo_db):
    rows = demo_gen.gen_unusual_amount(BASE, multiplier=3.0)
    demo_gen.write_demo_rows(rows, demo_db)

    conn = _open(demo_db)
    cands = find_unusual_amount_candidates(conn)
    conn.close()

    assert cands, "expected an unusual-amount candidate"
    spike = max(cands, key=lambda c: c["z_score"])
    assert spike["z_score"] >= 2.0
    # The flagged transaction should be the spike (3x baseline = $150).
    assert spike["transaction"]["debit"] == pytest.approx(150.0)


@pytest.mark.parametrize("pattern", ["weekly", "monthly", "quarterly"])
def test_gen_subscription_trips_subscription_detector(demo_db, pattern):
    rows = demo_gen.gen_subscription(BASE, pattern=pattern, count=6)
    demo_gen.write_demo_rows(rows, demo_db)

    conn = _open(demo_db)
    cands = find_subscription_candidates(conn)
    conn.close()

    assert cands, f"expected a subscription candidate for {pattern}"
    c = cands[0]
    assert c["cv"] < 0.3
    assert c["pattern"] == pattern
    # Quarterly×6 spans >400d (the detector's lookback), so a couple of the
    # oldest charges fall outside the window — just confirm enough remain.
    assert c["charge_count"] >= 3


# --- Persistence ------------------------------------------------------------

def test_seed_preset_from_multiple_bases(demo_db):
    # The UI seeds the series from every selected row: applying a preset to each
    # base and concatenating yields one alert per distinct merchant.
    bases = [
        {"transaction_date": date(2025, 6, 1), "card_no": "A",
         "description": "SHOP ONE", "category": "Dining", "debit": 10.0},
        {"transaction_date": date(2025, 6, 2), "card_no": "B",
         "description": "SHOP TWO", "category": "Dining", "debit": 20.0},
    ]
    rows = []
    for b in bases:
        rows += demo_gen.gen_duplicate(b, anonymize=False)
    assert demo_gen.write_demo_rows(rows, demo_db) == 4

    conn = _open(demo_db)
    cands = find_duplicate_candidates(conn)
    conn.close()
    assert {c["normalized_merchant"] for c in cands} == {"SHOP ONE", "SHOP TWO"}


def test_write_demo_rows_is_idempotent(demo_db):
    rows = demo_gen.gen_duplicate(BASE)
    assert demo_gen.write_demo_rows(rows, demo_db) == len(rows)
    # Re-writing identical rows inserts nothing.
    assert demo_gen.write_demo_rows(rows, demo_db) == 0


def test_write_demo_rows_accumulates_distinct_series(demo_db):
    demo_gen.write_demo_rows(demo_gen.gen_duplicate(BASE), demo_db)
    other = {**BASE, "description": "WHOLE FOODS MARKET", "card_no": "5678"}
    demo_gen.write_demo_rows(demo_gen.gen_duplicate(other), demo_db)
    assert demo_gen.demo_db_count(demo_db) == 4


def test_clear_demo_db_empties_dataset(demo_db):
    demo_gen.write_demo_rows(demo_gen.gen_duplicate(BASE), demo_db)
    assert demo_gen.demo_db_count(demo_db) > 0
    demo_gen.clear_demo_db(demo_db)
    assert demo_gen.demo_db_count(demo_db) == 0


def test_demo_db_count_missing_file_is_zero(tmp_path):
    assert demo_gen.demo_db_count(tmp_path / "nope.db") == 0
