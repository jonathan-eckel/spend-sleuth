import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "spend_sleuth.db"


def get_connection(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path))


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_date DATE,
            posted_date      DATE,
            card_no          VARCHAR,
            description      VARCHAR,
            category         VARCHAR,
            debit            DECIMAL(10,2),
            credit           DECIMAL(10,2),
            source_file      VARCHAR,
            row_hash         VARCHAR UNIQUE
        )
    """)
