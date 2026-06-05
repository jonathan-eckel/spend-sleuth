import re
from datetime import date, timedelta
import duckdb
import pandas as pd

_PREFIX_PATTERNS = [
    r"^SQ \*",
    r"^TST\*",
    # r"^AUT \*",
    # r"^PP\*",
    # r"^AMZN MKTP",
]
_STORE_NUMBER = r"\s*#\w+$"

_CANDIDATES_SQL = """
WITH scoped AS (
    SELECT * FROM transactions
    WHERE transaction_date BETWEEN ? AND ?
)
SELECT
    a.row_hash        AS a_hash,
    a.transaction_date AS a_transaction_date,
    a.posted_date     AS a_posted_date,
    a.card_no         AS a_card_no,
    a.description     AS a_description,
    a.category        AS a_category,
    a.debit           AS a_debit,
    a.credit          AS a_credit,
    a.source_file     AS a_source_file,
    a.file_row        AS a_file_row,

    b.row_hash        AS b_hash,
    b.transaction_date AS b_transaction_date,
    b.posted_date     AS b_posted_date,
    b.card_no         AS b_card_no,
    b.description     AS b_description,
    b.category        AS b_category,
    b.debit           AS b_debit,
    b.credit          AS b_credit,
    b.source_file     AS b_source_file,
    b.file_row        AS b_file_row,

    ABS(DATEDIFF('day', a.transaction_date, b.transaction_date)) AS days_apart,
    ABS(a.debit - b.debit) AS amount_diff

FROM scoped a
JOIN scoped b
  ON a.row_hash < b.row_hash
 AND a.debit IS NOT NULL
 AND b.debit IS NOT NULL
 AND a.debit > 0
 AND b.debit > 0
 AND ABS(a.debit - b.debit) <= ?
 AND ABS(DATEDIFF('day', a.transaction_date, b.transaction_date)) <= ?
"""


def normalize_merchant(description: str) -> str:
    s = description.strip().upper()
    for pat in _PREFIX_PATTERNS:
        s = re.sub(pat, "", s)
    s = re.sub(_STORE_NUMBER, "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_MIN_UNUSUAL_DELTA: float = 10.0   # minimum absolute $ above historical mean to flag; tune after review
_CANONICAL_THRESHOLD: float = 0.80  # fuzzy similarity cutoff for merchant clustering

_OMNY_PATTERNS = ("OMNY", "MTA*NYCT PAYGO")


def build_canonical_merchant_map(
    conn: duckdb.DuckDBPyConnection,
    threshold: float = _CANONICAL_THRESHOLD,
) -> dict[str, str]:
    """Return {normalized_key → canonical_name} via fuzzy clustering.

    Canonical name = highest-transaction-count normalized key in each cluster.
    Merchants with no similar peers map to themselves (identity mapping).
    """
    from collections import defaultdict
    from difflib import SequenceMatcher

    rows = conn.execute("""
        SELECT description, COUNT(*) AS n
        FROM transactions
        WHERE debit IS NOT NULL AND debit > 0
        GROUP BY description
    """).fetchall()

    key_counts: dict[str, int] = {}
    for desc, n in rows:
        key = normalize_merchant(desc)
        key_counts[key] = key_counts.get(key, 0) + n

    keys = list(key_counts.keys())
    parent = {k: k for k in keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[py] = px  # root is arbitrary; canonical chosen after clustering

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if SequenceMatcher(None, keys[i], keys[j]).ratio() >= threshold:
                union(keys[i], keys[j])

    # Group by cluster root, then pick the highest-count key as canonical
    clusters: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        clusters[find(k)].append(k)

    canonical_map: dict[str, str] = {}
    for members in clusters.values():
        canonical = max(members, key=lambda k: key_counts[k])
        for k in members:
            canonical_map[k] = canonical

    return canonical_map


def _is_omny(description: str) -> bool:
    upper = description.upper()
    return any(p in upper for p in _OMNY_PATTERNS)


def _is_omny_pair(desc_a: str, desc_b: str) -> bool:
    return _is_omny(desc_a) and _is_omny(desc_b)


def find_duplicate_candidates(
    conn: duckdb.DuckDBPyConnection,
    window_days: int = 7,
    amount_tolerance: float = 0.01,
    start_date: date | None = None,
    end_date: date | None = None,
    card_no: str | None = None,
    category: str | None = None,
    description_search: str | None = None,
) -> list[dict]:
    if start_date is None:
        start_date = conn.execute("SELECT MIN(transaction_date) FROM transactions").fetchone()[0]
    if end_date is None:
        end_date = conn.execute("SELECT MAX(transaction_date) FROM transactions").fetchone()[0]

    extra_clauses = ""
    params = [start_date, end_date]
    if card_no:
        extra_clauses += " AND card_no = ?"
        params.append(card_no)
    if category:
        extra_clauses += " AND category = ?"
        params.append(category)
    if description_search:
        extra_clauses += " AND UPPER(description) LIKE UPPER(?)"
        params.append(f"%{description_search}%")
    params += [amount_tolerance, window_days]

    sql = _CANDIDATES_SQL.replace("WHERE transaction_date BETWEEN ? AND ?",
                                  f"WHERE transaction_date BETWEEN ? AND ?{extra_clauses}")

    df: pd.DataFrame = conn.execute(sql, params).df()
    canonical_map = build_canonical_merchant_map(conn)

    def _canonical(desc: str) -> str:
        key = normalize_merchant(desc)
        return canonical_map.get(key, key)

    candidates = []
    for row in df.itertuples(index=False):
        norm_a = _canonical(row.a_description)
        norm_b = _canonical(row.b_description)
        if norm_a != norm_b:
            continue

        candidates.append({
            "alert_type": "duplicate",
            "normalized_merchant": norm_a,
            "days_apart": int(row.days_apart),
            "amount_diff": float(row.amount_diff),
            "is_omny": _is_omny_pair(row.a_description, row.b_description),
            "txn_a": {
                "row_hash": row.a_hash,
                "transaction_date": row.a_transaction_date,
                "posted_date": row.a_posted_date,
                "card_no": row.a_card_no,
                "description": row.a_description,
                "category": row.a_category,
                "debit": float(row.a_debit) if row.a_debit is not None else None,
                "credit": float(row.a_credit) if row.a_credit is not None else None,
                "source_file": row.a_source_file,
                "file_row": row.a_file_row,
            },
            "txn_b": {
                "row_hash": row.b_hash,
                "transaction_date": row.b_transaction_date,
                "posted_date": row.b_posted_date,
                "card_no": row.b_card_no,
                "description": row.b_description,
                "category": row.b_category,
                "debit": float(row.b_debit) if row.b_debit is not None else None,
                "credit": float(row.b_credit) if row.b_credit is not None else None,
                "source_file": row.b_source_file,
                "file_row": row.b_file_row,
            },
        })

    return sorted(candidates, key=lambda c: c["txn_a"]["transaction_date"], reverse=True)


def find_unusual_amount_candidates(
    conn: duckdb.DuckDBPyConnection,
    z_threshold: float = 2.0,
    min_history: int = 5,
    min_delta: float = _MIN_UNUSUAL_DELTA,
    lookback_days: int = 365,
    start_date: date | None = None,
    end_date: date | None = None,
    card_no: str | None = None,
    category: str | None = None,
    description_search: str | None = None,
) -> list[dict]:
    """Find transactions where the amount is unusually high for a known merchant.

    A merchant is "known" if it has at least `min_history` transactions in the
    lookback window. Anomaly scoring uses z-score (high-side only); swap this
    step to use IQR or MAD by replacing the z_score computation and threshold
    comparison below.
    """
    if start_date is None:
        start_date = conn.execute("SELECT MIN(transaction_date) FROM transactions").fetchone()[0]
    if end_date is None:
        end_date = conn.execute("SELECT MAX(transaction_date) FROM transactions").fetchone()[0]

    lookback_start = end_date - timedelta(days=lookback_days)
    canonical_map = build_canonical_merchant_map(conn)

    def _canonical(desc: str) -> str:
        key = normalize_merchant(desc)
        return canonical_map.get(key, key)

    # --- Build merchant stats from the full lookback window (no card/category filters) ---
    history_df: pd.DataFrame = conn.execute("""
        SELECT description, CAST(debit AS DOUBLE) AS debit
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND debit IS NOT NULL AND debit > 0
    """, [lookback_start, end_date]).df()

    stats_map: dict[str, dict] = {}
    for merchant_key, group in history_df.assign(
        merchant_key=history_df["description"].map(_canonical)
    ).groupby("merchant_key")["debit"]:
        n = len(group)
        if n < min_history:
            continue
        mean = float(group.mean())
        stddev = float(group.std(ddof=0))
        if stddev <= 0:
            continue
        stats_map[merchant_key] = {"n": n, "mean": mean, "stddev": stddev}

    if not stats_map:
        return []

    # --- Fetch transactions to evaluate (search range + optional filters) ---
    extra_clauses = ""
    params: list = [start_date, end_date]
    if card_no:
        extra_clauses += " AND card_no = ?"
        params.append(card_no)
    if category:
        extra_clauses += " AND category = ?"
        params.append(category)
    if description_search:
        extra_clauses += " AND UPPER(description) LIKE UPPER(?)"
        params.append(f"%{description_search}%")

    txn_df: pd.DataFrame = conn.execute(f"""
        SELECT *
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND debit IS NOT NULL AND debit > 0
          {extra_clauses}
        ORDER BY transaction_date DESC
    """, params).df()

    # --- Score each transaction and flag anomalies ---
    results = []
    for row in txn_df.itertuples(index=False):
        merchant_key = _canonical(row.description)
        stats = stats_map.get(merchant_key)
        if stats is None:
            continue
        delta = float(row.debit) - stats["mean"]
        if delta < min_delta:
            continue
        # z-score: swap this block to use a different anomaly method
        z_score = delta / stats["stddev"]
        if z_score < z_threshold:
            continue
        results.append({
            "alert_type": "unusual_amount",
            "normalized_merchant": merchant_key,
            "z_score": round(z_score, 2),
            "delta": round(delta, 2),
            "merchant_stats": {
                "n": stats["n"],
                "mean": round(stats["mean"], 2),
                "stddev": round(stats["stddev"], 2),
            },
            "transaction": {
                "row_hash": row.row_hash,
                "transaction_date": row.transaction_date,
                "posted_date": row.posted_date,
                "card_no": row.card_no,
                "description": row.description,
                "category": row.category,
                "debit": float(row.debit),
                "credit": float(row.credit) if row.credit is not None else None,
                "source_file": row.source_file,
                "file_row": int(row.file_row),
            },
        })

    return results  # already ordered by transaction_date DESC from SQL


def find_subscription_candidates(
    conn: duckdb.DuckDBPyConnection,
    lookback_days: int = 400,   # 400d: enough to catch quarterly (3×90d=270d) with buffer for annual
    min_charges: int = 3,
    cv_threshold: float = 0.3,  # coefficient of variation (stddev/mean) of intervals; < 0.3 = "clock-like"
    min_span_days: int = 60,    # coupled to min_charges: 3 monthly charges span exactly 60d (2 intervals)
    card_no: str | None = None,
    category: str | None = None,
    description_search: str | None = None,
) -> list[dict]:
    """Find merchants with a regular recurring charge pattern (possible forgotten subscriptions).

    A merchant qualifies if it has at least `min_charges` debits in the lookback window,
    the coefficient of variation (CV = stddev/mean) of inter-charge intervals is below
    `cv_threshold`, and the charges span at least `min_span_days`.

    CV measures how clock-like a pattern is relative to its own cadence: a monthly
    subscription charging ±2 days has CV ≈ 0.07; an irregular vendor charging every
    2–8 weeks has CV ≈ 0.5+.
    """
    from collections import defaultdict
    from datetime import date as date_type

    end_date = conn.execute("SELECT MAX(transaction_date) FROM transactions").fetchone()[0]
    lookback_start = end_date - timedelta(days=lookback_days)
    canonical_map = build_canonical_merchant_map(conn)

    def _canonical(desc: str) -> str:
        key = normalize_merchant(desc)
        return canonical_map.get(key, key)

    extra_clauses = ""
    params: list = [lookback_start, end_date]
    if card_no:
        extra_clauses += " AND card_no = ?"
        params.append(card_no)
    if category:
        extra_clauses += " AND category = ?"
        params.append(category)
    if description_search:
        extra_clauses += " AND UPPER(description) LIKE UPPER(?)"
        params.append(f"%{description_search}%")

    rows = conn.execute(f"""
        SELECT row_hash, description, transaction_date::VARCHAR, posted_date::VARCHAR,
               card_no, category, CAST(debit AS DOUBLE) AS debit,
               credit, source_file, file_row
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND debit IS NOT NULL AND debit > 0
          {extra_clauses}
        ORDER BY description, transaction_date
    """, params).fetchall()

    merchant_charges: dict[str, list] = defaultdict(list)
    for row in rows:
        row_hash, desc, txn_date, posted_date, card, cat, debit, credit, source, file_row = row
        key = _canonical(desc)
        merchant_charges[key].append({
            "row_hash": row_hash,
            "description": desc,
            "transaction_date": txn_date,
            "posted_date": posted_date,
            "card_no": card,
            "category": cat,
            "debit": debit,
            "credit": float(credit) if credit is not None else None,
            "source_file": source,
            "file_row": int(file_row),
        })

    results = []
    for merchant_key, charges in merchant_charges.items():
        if len(charges) < min_charges:
            continue

        charges.sort(key=lambda c: c["transaction_date"])
        dates = [date_type.fromisoformat(c["transaction_date"]) for c in charges]
        amounts = [c["debit"] for c in charges]
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

        if (dates[-1] - dates[0]).days < min_span_days:
            continue

        mean_interval = sum(intervals) / len(intervals)
        variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
        stddev = variance ** 0.5
        cv = stddev / mean_interval if mean_interval > 0 else 1.0

        if cv >= cv_threshold:
            continue

        # Bucket by mean interval; thresholds are midpoints between canonical cadences
        # (weekly≈7, biweekly≈14, monthly≈30, quarterly≈90, semi-annual≈180, annual≈365)
        if mean_interval < 10:
            pattern = "weekly"
        elif mean_interval < 22:
            pattern = "biweekly"
        elif mean_interval < 60:
            pattern = "monthly"
        elif mean_interval < 135:
            pattern = "quarterly"
        elif mean_interval < 270:
            pattern = "semi-annual"
        else:
            pattern = "annual"

        results.append({
            "alert_type": "subscription",
            "normalized_merchant": merchant_key,
            "pattern": pattern,
            "mean_interval_days": round(mean_interval, 1),
            "cv": round(cv, 3),
            "charge_count": len(charges),
            "typical_amount": round(sum(amounts) / len(amounts), 2),
            "total_spent": round(sum(amounts), 2),
            "first_charge": charges[0]["transaction_date"],
            "last_charge": charges[-1]["transaction_date"],
            "transaction": charges[-1],
        })

    return sorted(results, key=lambda r: r["total_spent"], reverse=True)
