import click
from datetime import date
from pathlib import Path

from .db import get_connection, init_schema, DB_PATH
from .load import load_directory, load_csv
from .detect import find_duplicate_candidates


@click.group()
def cli():
    pass


@cli.command()
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--db", type=click.Path(path_type=Path), default=None, help="DuckDB file path")
def load(data_dir: Path, db: Path | None):
    """Load all CSVs from DATA_DIR into DuckDB."""
    db_path = db or DB_PATH
    conn = get_connection(db_path)
    init_schema(conn)

    results = load_directory(data_dir, conn)
    conn.close()

    total = sum(results.values())
    for fname, count in results.items():
        click.echo(f"  {fname}: {count} rows inserted")
    click.echo(f"Total: {total} new rows  →  {db_path}")


@cli.command()
@click.option("--db", type=click.Path(path_type=Path), default=None)
def stats(db: Path | None):
    """Print basic stats from the transactions table."""
    db_path = db or DB_PATH
    conn = get_connection(db_path)

    try:
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        date_range = conn.execute(
            "SELECT MIN(transaction_date), MAX(transaction_date) FROM transactions"
        ).fetchone()
        cards = conn.execute(
            "SELECT card_no, COUNT(*) FROM transactions GROUP BY card_no ORDER BY COUNT(*) DESC"
        ).fetchall()

        click.echo(f"Total rows   : {total}")
        click.echo(f"Date range   : {date_range[0]}  →  {date_range[1]}")
        click.echo("By card:")
        for card, count in cards:
            click.echo(f"  {card}: {count}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
    finally:
        conn.close()


@cli.command()
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--window", default=7, show_default=True, help="Days window for duplicate check.")
@click.option("--tolerance", default=0.01, show_default=True, help="Amount tolerance ($).")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="Start date (YYYY-MM-DD).")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="End date (YYYY-MM-DD).")
def detect(db: Path | None, window: int, tolerance: float, start, end):
    """Find candidate duplicate charges."""
    db_path = db or DB_PATH
    conn = get_connection(db_path, read_only=True)
    start_date = start.date() if start else None
    end_date = end.date() if end else None
    try:
        candidates = find_duplicate_candidates(
            conn, window_days=window, amount_tolerance=tolerance,
            start_date=start_date, end_date=end_date,
        )
    finally:
        conn.close()

    if not candidates:
        click.echo("No duplicate candidates found.")
        return

    non_omny = [c for c in candidates if not c["is_omny"]]
    omny = [c for c in candidates if c["is_omny"]]
    click.echo(f"Found {len(non_omny)} flagged pair(s), {len(omny)} OMNY pair(s).\n")

    for i, c in enumerate(candidates, 1):
        omny_tag = "  [OMNY - expected]" if c["is_omny"] else ""
        a, b = c["txn_a"], c["txn_b"]
        click.echo(
            f"  {i}. {c['normalized_merchant']}{omny_tag}\n"
            f"     A: {a['transaction_date']}  ${a['debit']:.2f}  {a['description']}\n"
            f"     B: {b['transaction_date']}  ${b['debit']:.2f}  {b['description']}\n"
            f"     Days apart: {c['days_apart']}  |  Amount diff: ${c['amount_diff']:.2f}\n"
        )
