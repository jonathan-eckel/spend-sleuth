import hashlib
import pandas as pd
from pathlib import Path

import duckdb

from .db import get_connection, init_schema


def _compute_hash(row: pd.Series) -> str:
    key = "|".join(str(row[c]) for c in [
        "transaction_date", "posted_date", "card_no",
        "description", "debit", "credit", "source_file", "file_row",
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def load_csv(csv_path: Path, conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Load a single CSV into the transactions table. Returns number of rows inserted."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        init_schema(conn)

    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    df = df.rename(columns={
        "transaction_date": "transaction_date",
        "posted_date":      "posted_date",
        "card_no.":         "card_no",
        "card_no":          "card_no",
        "description":      "description",
        "category":         "category",
        "debit":            "debit",
        "credit":           "credit",
    })

    required = {"transaction_date", "posted_date", "card_no", "description", "category", "debit", "credit"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path.name}: missing columns {missing}")

    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce").dt.date
    df["posted_date"]      = pd.to_datetime(df["posted_date"], errors="coerce").dt.date
    df["debit"]  = pd.to_numeric(df["debit"],  errors="coerce")
    df["credit"] = pd.to_numeric(df["credit"], errors="coerce")
    df["source_file"] = csv_path.name
    df["file_row"] = range(len(df))
    df["row_hash"] = df.apply(_compute_hash, axis=1)

    rows_before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.register("_staging", df[[
        "transaction_date", "posted_date", "card_no", "description",
        "category", "debit", "credit", "source_file", "file_row", "row_hash",
    ]])
    conn.execute("""
        INSERT INTO transactions
        SELECT * FROM _staging
        ON CONFLICT (row_hash) DO NOTHING
    """)
    rows_after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    if own_conn:
        conn.close()

    return rows_after - rows_before


def load_directory(data_dir: Path, conn: duckdb.DuckDBPyConnection | None = None) -> dict[str, int]:
    """Load all CSVs in a directory. Returns {filename: rows_inserted}."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        init_schema(conn)

    results = {}
    for csv_path in sorted(data_dir.glob("**/*.csv")):
        results[csv_path.name] = load_csv(csv_path, conn)

    if own_conn:
        conn.close()

    return results
