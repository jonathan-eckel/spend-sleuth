import pytest
import duckdb
from spend_sleuth.db import init_schema


@pytest.fixture
def conn():
    """In-memory DuckDB connection with schema initialized."""
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def insert_transaction(conn, **kwargs):
    """Insert a single transaction row, computing a unique row_hash from kwargs."""
    import hashlib
    defaults = dict(
        transaction_date="2025-01-01",
        posted_date="2025-01-02",
        card_no="1234",
        description="TEST MERCHANT",
        category="Shopping",
        debit=10.00,
        credit=None,
        source_file="test.csv",
        file_row=0,
    )
    row = {**defaults, **kwargs}
    key = "|".join(str(row[c]) for c in [
        "transaction_date", "posted_date", "card_no",
        "description", "debit", "credit", "source_file", "file_row",
    ])
    row["row_hash"] = hashlib.sha256(key.encode()).hexdigest()
    conn.execute("""
        INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        row["transaction_date"], row["posted_date"], row["card_no"],
        row["description"], row["category"], row["debit"], row["credit"],
        row["source_file"], row["file_row"], row["row_hash"],
    ])
    return row
