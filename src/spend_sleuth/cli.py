import click
from pathlib import Path

from .db import get_connection, init_schema, DB_PATH
from .load import load_directory, load_csv


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
