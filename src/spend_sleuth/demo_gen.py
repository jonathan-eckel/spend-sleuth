"""Demo-data generator: turn real transactions into anonymized demo alerts.

Pure, UI-free logic used by the Streamlit "Demo Data Builder" page. Takes one
or more real (base) transactions the user picked, anonymizes the card number
and merchant, and shapes them into a series engineered to trip one of the three
detectors in `detect.py`. Results are written to a dedicated, accumulating demo
DuckDB (separate from `demo_data/demo.db`, which `seed.py` overwrites).

Anonymization is stateless and deterministic so the same real identity always
maps to the same fake one — required so every row in a generated series shares
one merchant string and clusters together in the merchant-keyed detectors.
"""
import os
import hashlib
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .db import get_connection, init_schema
from .detect import normalize_merchant, interval_to_pattern
from .load import compute_row_hash

_DEFAULT_DEMO_DB = Path(__file__).parent.parent.parent / "demo_data" / "builder.db"
DEMO_DB_PATH = (
    Path(os.environ["SPEND_SLEUTH_DEMO_DB"])
    if "SPEND_SLEUTH_DEMO_DB" in os.environ
    else _DEFAULT_DEMO_DB
)

# All generated rows carry this source_file; row_hash uses it + file_row, so
# re-saving the same preset is idempotent (ON CONFLICT DO NOTHING dedupes).
DEMO_SOURCE_FILE = "demo_builder.csv"

# Fake brand parts — combined deterministically into a readable two-word name.
# ~20×24 = 480 combinations keeps cross-merchant collisions rare.
_BRAND_PREFIXES = [
    "BLUE", "GOLDEN", "NORTH", "SILVER", "RED", "GREEN", "IRON", "AMBER",
    "CEDAR", "CORAL", "EMBER", "FROST", "MAPLE", "ONYX", "PINE", "RIVER",
    "SLATE", "STONE", "URBAN", "WILLOW",
]
_BRAND_NOUNS = [
    "HARBOR", "TRADERS", "MARKET", "GROCERS", "CAFE", "ROASTERS", "GOODS",
    "SUPPLY", "DINER", "KITCHEN", "PANTRY", "BISTRO", "DEPOT", "OUTFITTERS",
    "MERCANTILE", "BREWERY", "CREAMERY", "WORKS", "EXCHANGE", "PROVISIONS",
    "GRILL", "BAKERY", "FARMS", "EMPORIUM",
]

# Days between charges for each subscription cadence.
PATTERN_INTERVAL_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 91,
    "semi-annual": 182,
    "annual": 365,
}


def _seed_int(text: str) -> int:
    """Stable integer seed derived from a SHA-256 digest of `text`."""
    return int(hashlib.sha256(text.encode()).hexdigest(), 16)


def anonymize_card(card_no: str) -> str:
    """Deterministic demo placeholder for a card number, e.g. ``DEMO-0427``."""
    return f"DEMO-{_seed_int('card:' + str(card_no)) % 10000:04d}"


def anonymize_merchant(description: str) -> str:
    """Deterministic fake-but-consistent brand name for a real merchant.

    Normalizes first (dropping store numbers / known prefixes), so merchant
    variants like ``TRADER JOE'S #142`` and ``TRADER JOE'S #99`` collapse to the
    same fake brand.
    """
    norm = normalize_merchant(description)
    seed = _seed_int("merchant:" + norm)
    prefix = _BRAND_PREFIXES[seed % len(_BRAND_PREFIXES)]
    noun = _BRAND_NOUNS[(seed // len(_BRAND_PREFIXES)) % len(_BRAND_NOUNS)]
    return f"{prefix} {noun}"


def _to_date(value) -> date:
    """Coerce a date / datetime / ISO string / pandas Timestamp to a date."""
    if isinstance(value, date):
        return value
    return pd.to_datetime(value).date()


def make_row(
    *,
    transaction_date: date,
    card_no: str,
    description: str,
    category: str,
    debit: float,
    file_row: int,
    posted_date: date | None = None,
    credit: float | None = None,
) -> dict:
    """Assemble one fully-formed transaction row (10 columns incl. row_hash).

    `description` and `card_no` are expected to already be anonymized by the
    caller. `posted_date` defaults to `transaction_date`.
    """
    row = {
        "transaction_date": transaction_date,
        "posted_date": posted_date or transaction_date,
        "card_no": card_no,
        "description": description,
        "category": category,
        "debit": round(float(debit), 2),
        "credit": credit,
        "source_file": DEMO_SOURCE_FILE,
        "file_row": file_row,
    }
    row["row_hash"] = compute_row_hash(row)
    return row


def _base_fields(base: Mapping, *, anonymize: bool = True) -> dict:
    """Pull the fields we need from a picked base transaction.

    With ``anonymize=True`` (default) the card and merchant are scrubbed here.
    Pass ``anonymize=False`` when the caller has already chosen the final card /
    merchant values (e.g. the user edited them in the UI) so they're used as-is.
    """
    return {
        "transaction_date": _to_date(base["transaction_date"]),
        "card_no": anonymize_card(base["card_no"]) if anonymize else str(base["card_no"]),
        "description": anonymize_merchant(base["description"]) if anonymize else str(base["description"]),
        "category": base.get("category") or "Uncategorized",
        "debit": float(base["debit"]),
    }


def summarize_selection(bases: list[Mapping]) -> dict:
    """Infer synthetic-series parameters from a set of picked transactions.

    Returns ``count``, ``mean_amount``, ``max_amount``, ``first_date`` and
    ``last_date`` always; adds ``mean_interval_days`` and ``pattern`` (via the
    detector's own :func:`interval_to_pattern`) when ≥2 transactions are given.
    Used by the "combine" workflow to infer a subscription's cadence/amount.
    """
    amounts = [float(b["debit"]) for b in bases]
    dates = sorted(_to_date(b["transaction_date"]) for b in bases)
    out = {
        "count": len(bases),
        "mean_amount": round(sum(amounts) / len(amounts), 2),
        "max_amount": round(max(amounts), 2),
        "first_date": dates[0],
        "last_date": dates[-1],
    }
    if len(dates) >= 2:
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        mean_interval = sum(intervals) / len(intervals)
        out["mean_interval_days"] = round(mean_interval, 1)
        out["pattern"] = interval_to_pattern(mean_interval)
    return out


def infer_unusual_baseline_spike(bases: list[Mapping]) -> tuple[float, float]:
    """Infer an unusual-amount (baseline, spike) pair from picked transactions.

    The largest selected charge is treated as the anomaly (spike); the baseline
    is the mean of the remaining charges. With a single transaction there's no
    "rest", so the baseline falls back to ``spike / 3``.
    """
    amounts = sorted(float(b["debit"]) for b in bases)
    spike = amounts[-1]
    rest = amounts[:-1]
    baseline = sum(rest) / len(rest) if rest else spike / 3
    return round(baseline, 2), round(spike, 2)


def gen_passthrough(base: Mapping, *, anonymize: bool = True) -> list[dict]:
    """Return the base transaction as a single anonymized row, otherwise unchanged.

    Preserves the real date, posted date, amount, category and credit — only the
    card number and merchant are anonymized. Used to seed realistic *background*
    transactions into the demo dataset without generating any alert pattern.
    """
    b = _base_fields(base, anonymize=anonymize)
    posted = base.get("posted_date")
    posted_date = _to_date(posted) if posted is not None and not pd.isna(posted) else None
    credit = base.get("credit")
    credit_val = None if credit is None or pd.isna(credit) else float(credit)
    return [make_row(
        transaction_date=b["transaction_date"],
        posted_date=posted_date,
        card_no=b["card_no"],
        description=b["description"],
        category=b["category"],
        debit=b["debit"],
        credit=credit_val,
        file_row=0,
    )]


def gen_duplicate(
    base: Mapping,
    *,
    copies: int = 2,
    days_apart: int = 0,
    amount_diff: float = 0.0,
    anonymize: bool = True,
) -> list[dict]:
    """Clone a base transaction into near-identical charges (duplicate alert).

    Defaults (``days_apart=0``, ``amount_diff=0``) produce the strongest signal:
    same merchant, same amount, same day. `file_row` differs per copy so each
    row hashes distinctly.
    """
    b = _base_fields(base, anonymize=anonymize)
    rows = []
    for i in range(max(2, copies)):
        rows.append(make_row(
            transaction_date=b["transaction_date"] + timedelta(days=days_apart * i),
            card_no=b["card_no"],
            description=b["description"],
            category=b["category"],
            debit=b["debit"] + amount_diff * i,
            file_row=i,
        ))
    return rows


def gen_unusual_amount(
    base: Mapping,
    *,
    history: int = 8,
    baseline: float | None = None,
    multiplier: float = 3.0,
    spike_amount: float | None = None,
    spread: float = 0.05,
    anonymize: bool = True,
) -> list[dict]:
    """Build a tight baseline history plus one spike charge (unusual-amount alert).

    `history` normal charges sit at ``baseline`` (defaults to the base amount)
    with a tiny deterministic spread, spaced at *irregular* intervals leading up
    to the base date. The final (anomaly) charge is ``spike_amount`` when given
    (e.g. the largest real charge from a combined selection), otherwise
    ``baseline * multiplier``.

    The irregular spacing is deliberate: evenly-spaced history would also look
    like a subscription and co-fire that detector, muddying a single-alert demo.
    """
    b = _base_fields(base, anonymize=anonymize)
    base_amt = float(baseline) if baseline is not None else b["debit"]
    spike_amt = float(spike_amount) if spike_amount is not None else base_amt * multiplier
    # Irregular gaps (days) break any clock-like cadence so the subscription
    # detector's interval CV stays above its 0.3 threshold.
    _GAPS = [5, 11, 4, 9, 6, 12, 3, 10]
    offset, offsets = 7, []
    for i in range(history):
        offset += _GAPS[i % len(_GAPS)]
        offsets.append(offset)

    rows = []
    file_row = 0
    # Oldest first (largest offset before the spike date).
    for off in sorted(offsets, reverse=True):
        # Cycle amounts baseline*(1-spread), baseline, baseline*(1+spread) for
        # a small but non-zero stddev.
        factor = 1 + spread * ((file_row % 3) - 1)
        rows.append(make_row(
            transaction_date=b["transaction_date"] - timedelta(days=off),
            card_no=b["card_no"],
            description=b["description"],
            category=b["category"],
            debit=base_amt * factor,
            file_row=file_row,
        ))
        file_row += 1
    # The spike, on the base date.
    rows.append(make_row(
        transaction_date=b["transaction_date"],
        card_no=b["card_no"],
        description=b["description"],
        category=b["category"],
        debit=spike_amt,
        file_row=file_row,
    ))
    return rows


def gen_subscription(
    base: Mapping,
    *,
    pattern: str = "monthly",
    count: int = 6,
    jitter_days: int = 1,
    anonymize: bool = True,
) -> list[dict]:
    """Generate a clock-like recurring series (forgotten-subscription alert).

    `count` charges at a fixed amount and cadence, with ±`jitter_days` of
    deterministic wobble so the interval CV stays well under 0.30 and the span
    exceeds two full cycles.
    """
    interval = PATTERN_INTERVAL_DAYS.get(pattern, 30)
    b = _base_fields(base, anonymize=anonymize)
    rows = []
    # Small alternating jitter pattern keeps stddev > 0 but CV tiny.
    jitter_cycle = [0, jitter_days, -jitter_days]
    for i in range(max(3, count)):
        # Most recent charge (i=0) on the base date, older charges going back.
        offset = interval * i + jitter_cycle[i % len(jitter_cycle)]
        rows.append(make_row(
            transaction_date=b["transaction_date"] - timedelta(days=offset),
            card_no=b["card_no"],
            description=b["description"],
            category=b["category"],
            debit=b["debit"],
            file_row=i,
        ))
    # Oldest first for a tidy file_row order.
    rows.reverse()
    for i, row in enumerate(rows):
        row["file_row"] = i
        row["row_hash"] = compute_row_hash(row)
    return rows


_TABLE_COLUMNS = [
    "transaction_date", "posted_date", "card_no", "description",
    "category", "debit", "credit", "source_file", "file_row", "row_hash",
]


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a staging DataFrame with columns in table order."""
    return pd.DataFrame(rows)[_TABLE_COLUMNS]


def write_demo_rows(rows: list[dict], db_path: Path = DEMO_DB_PATH) -> int:
    """Insert generated rows into the demo DB. Returns the number inserted.

    Idempotent: rows already present (same row_hash) are skipped.
    """
    if not rows:
        return 0
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path, read_only=False)
    try:
        init_schema(conn)
        before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.register("_demo_staging", _rows_to_frame(rows))
        conn.execute("""
            INSERT INTO transactions
            SELECT * FROM _demo_staging
            ON CONFLICT (row_hash) DO NOTHING
        """)
        after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        return after - before
    finally:
        conn.close()


def clear_demo_db(db_path: Path = DEMO_DB_PATH) -> None:
    """Empty the demo dataset (drop + recreate the transactions table)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path, read_only=False)
    try:
        conn.execute("DROP TABLE IF EXISTS transactions")
        init_schema(conn)
    finally:
        conn.close()


def demo_db_count(db_path: Path = DEMO_DB_PATH) -> int:
    """Row count in the demo DB, or 0 if it doesn't exist yet."""
    if not Path(db_path).exists():
        return 0
    conn = get_connection(db_path, read_only=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


DEMO_PREVIEW_COLUMNS = [
    "transaction_date", "card_no", "description", "category", "debit", "credit",
]


def read_demo_rows(db_path: Path = DEMO_DB_PATH) -> pd.DataFrame:
    """Return the demo dataset's transactions as a DataFrame (newest first).

    Empty DataFrame (with the expected columns) if the DB doesn't exist yet.
    """
    empty = pd.DataFrame(columns=DEMO_PREVIEW_COLUMNS)
    if not Path(db_path).exists():
        return empty
    conn = get_connection(db_path, read_only=True)
    try:
        return conn.execute(f"""
            SELECT {', '.join(DEMO_PREVIEW_COLUMNS)}
            FROM transactions
            ORDER BY transaction_date DESC
        """).df()
    except Exception:
        return empty
    finally:
        conn.close()
