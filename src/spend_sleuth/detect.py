import re
from datetime import date
import duckdb
import pandas as pd

_PREFIX_PATTERNS = [
    r"^SQ \*",
    r"^TST\*",
    r"^AUT \*",
    r"^PP\*",
    r"^AMZN MKTP",
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


_OMNY_PATTERNS = ("OMNY", "MTA*NYCT PAYGO")


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
) -> list[dict]:
    if start_date is None:
        start_date = conn.execute("SELECT MIN(transaction_date) FROM transactions").fetchone()[0]
    if end_date is None:
        end_date = conn.execute("SELECT MAX(transaction_date) FROM transactions").fetchone()[0]

    df: pd.DataFrame = conn.execute(
        _CANDIDATES_SQL, [start_date, end_date, amount_tolerance, window_days]
    ).df()

    candidates = []
    for row in df.itertuples(index=False):
        norm_a = normalize_merchant(row.a_description)
        norm_b = normalize_merchant(row.b_description)
        if norm_a != norm_b:
            continue

        candidates.append({
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
