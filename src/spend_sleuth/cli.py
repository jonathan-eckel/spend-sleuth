import json
import os
import click
from datetime import date
from pathlib import Path

from .db import get_connection, init_schema, DB_PATH
from .load import load_directory, load_csv
from .detect import find_duplicate_candidates, find_unusual_amount_candidates, normalize_merchant, build_canonical_merchant_map


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
    """Find candidate duplicate charges and unusual amounts."""
    db_path = db or DB_PATH
    conn = get_connection(db_path, read_only=True)
    start_date = start.date() if start else None
    end_date = end.date() if end else None
    try:
        dup_candidates = find_duplicate_candidates(
            conn, window_days=window, amount_tolerance=tolerance,
            start_date=start_date, end_date=end_date,
        )
        unusual_candidates = find_unusual_amount_candidates(
            conn, start_date=start_date, end_date=end_date,
        )
    finally:
        conn.close()

    # --- Duplicate charges ---
    click.echo("=== Duplicate Charge Candidates ===")
    if not dup_candidates:
        click.echo("No duplicate candidates found.")
    else:
        non_omny = [c for c in dup_candidates if not c["is_omny"]]
        omny = [c for c in dup_candidates if c["is_omny"]]
        click.echo(f"Found {len(non_omny)} flagged pair(s), {len(omny)} OMNY pair(s).\n")
        for i, c in enumerate(dup_candidates, 1):
            omny_tag = "  [OMNY - expected]" if c["is_omny"] else ""
            a, b = c["txn_a"], c["txn_b"]
            click.echo(
                f"  {i}. {c['normalized_merchant']}{omny_tag}\n"
                f"     A: {a['transaction_date']}  ${a['debit']:.2f}  {a['description']}\n"
                f"     B: {b['transaction_date']}  ${b['debit']:.2f}  {b['description']}\n"
                f"     Days apart: {c['days_apart']}  |  Amount diff: ${c['amount_diff']:.2f}\n"
            )

    # --- Unusual amounts ---
    click.echo("\n=== Unusual Amount Candidates ===")
    if not unusual_candidates:
        click.echo("No unusual amount candidates found.")
    else:
        click.echo(f"Found {len(unusual_candidates)} candidate(s).\n")
        for i, c in enumerate(unusual_candidates, 1):
            t = c["transaction"]
            s = c["merchant_stats"]
            click.echo(
                f"  {i}. {c['normalized_merchant']}\n"
                f"     {t['transaction_date']}  ${t['debit']:.2f}  {t['description']}\n"
                f"     Typical: ${s['mean']:.2f} ± ${s['stddev']:.2f} (n={s['n']})  "
                f"|  Delta: +${c['delta']:.2f}  |  z={c['z_score']:.2f}\n"
            )


@cli.command(name="normalization-report")
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--threshold", default=0.80, show_default=True, help="Fuzzy similarity threshold (0–1).")
@click.option("--all", "show_all", is_flag=True, default=False, help="Include clean merchants with no grouping issues.")
@click.option("--summary", is_flag=True, default=False, help="Print summary stats only, no hierarchy.")
def normalization_report(db: Path | None, threshold: float, show_all: bool, summary: bool):
    """Hierarchical view of merchant clusters using fuzzy name matching.

    Flags normalized merchant keys that are similar but distinct — candidates
    for fixing normalize_merchant() to improve unusual-amount detection.
    Use --all to see every merchant in the hierarchy, not just issues.
    Use --summary for a quick coverage overview without the full hierarchy.
    """
    from collections import defaultdict

    db_path = db or DB_PATH
    conn = get_connection(db_path, read_only=True)
    try:
        rows = conn.execute("""
            SELECT description, COUNT(*) AS n
            FROM transactions
            WHERE debit IS NOT NULL AND debit > 0
            GROUP BY description
            ORDER BY description
        """).fetchall()
        canonical_map = build_canonical_merchant_map(conn, threshold=threshold)
    finally:
        conn.close()

    # Build normalized_key → {count, variants: [(raw_desc, count)]}
    key_data: dict[str, dict] = {}
    for desc, n in rows:
        key = normalize_merchant(desc)
        if key not in key_data:
            key_data[key] = {"count": 0, "variants": []}
        key_data[key]["count"] += n
        key_data[key]["variants"].append((desc, n))

    # Cluster using the same function detection uses — report always matches detection
    clusters: dict[str, list[str]] = defaultdict(list)
    for norm_key, canonical in canonical_map.items():
        clusters[canonical].append(norm_key)
    for members in clusters.values():
        members.sort(key=lambda k: key_data[k]["count"], reverse=True)

    def cluster_count(rep: str) -> int:
        return sum(key_data[k]["count"] for k in clusters[rep])

    merged = {rep: members for rep, members in clusters.items() if len(members) > 1}
    singletons = {rep: members for rep, members in clusters.items() if len(members) == 1}

    total_txns = sum(d["count"] for d in key_data.values())
    normalized_txns = sum(d["count"] for d in key_data.values() if len(d["variants"]) > 1)
    merged_txns = sum(cluster_count(rep) for rep in merged)
    normalized_pct = 100 * normalized_txns / total_txns if total_txns else 0
    merged_pct = 100 * merged_txns / total_txns if total_txns else 0
    click.echo(f"Fuzzy similarity threshold: {threshold}")
    click.echo(f"Grouping coverage:")
    click.echo(f"  {normalized_txns}/{total_txns} transactions ({normalized_pct:.0f}%) are in normalized groups")
    click.echo(f"  {merged_txns}/{total_txns} transactions ({merged_pct:.0f}%) are in merged canonical groups")

    if summary:
        return

    click.echo()

    # --- Canonical merchant groups (merged by canonical map) ---
    if not merged:
        click.echo("No merchant variants being merged (all names are already distinct).")
    else:
        hidden_note = f"  |  {len(singletons)} unmerged merchant(s) not shown (use --all)" if not show_all else ""
        click.echo(f"{len(merged)} canonical merchant group(s) being merged automatically{hidden_note}\n")
        for rep in sorted(merged, key=cluster_count, reverse=True):
            members = merged[rep]
            total = cluster_count(rep)
            click.echo(f"[canonical]  {rep}  ({total} txns, {len(members)} normalized variants)")
            for key in members:
                d = key_data[key]
                click.echo(f"  [normalized]  {key}  ({d['count']} txns)")
                for raw, n in sorted(d["variants"], key=lambda x: x[1], reverse=True):
                    click.echo(f"    [raw]  {raw}  ({n})")
            click.echo()

    # --- Clean merchants (shown only with --all) ---
    if show_all and singletons:
        click.echo("=== Clean merchants ===\n")
        for rep in sorted(singletons, key=cluster_count, reverse=True):
            key = rep
            d = key_data[key]
            click.echo(f"[canonical]  {key}  ({d['count']} txns)")
            for raw, n in sorted(d["variants"], key=lambda x: x[1], reverse=True):
                click.echo(f"  [normalized]  {key}  ({n})")
                click.echo(f"    [raw]  {raw}  ({n})")
            click.echo()


@cli.command()
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--window", default=7, show_default=True, help="Days window for duplicate detection.")
@click.option("--tolerance", default=0.01, show_default=True, help="Amount tolerance ($).")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="Start date (YYYY-MM-DD).")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="End date (YYYY-MM-DD).")
@click.option("--stub/--no-stub", default=None, help="Use stub LLM (default: auto-detect from ANTHROPIC_API_KEY).")
def investigate(db: Path | None, window: int, tolerance: float, start, end, stub: bool | None):
    """Investigate the first duplicate charge candidate with the agent."""
    from .agent.run import investigate as run_investigate

    use_stub = stub if stub is not None else not bool(os.environ.get("ANTHROPIC_API_KEY"))

    db_path = db or DB_PATH
    conn = get_connection(db_path, read_only=True)
    start_date = start.date() if start else None
    end_date = end.date() if end else None

    try:
        candidates = find_duplicate_candidates(
            conn, window_days=window, amount_tolerance=tolerance,
            start_date=start_date, end_date=end_date,
        )
        non_omny = [c for c in candidates if not c["is_omny"]]
        if not non_omny:
            click.echo("No non-OMNY duplicate candidates found to investigate.")
            return

        candidate = non_omny[0]
        mode = "stub" if use_stub else "live (claude-sonnet-4-6)"
        click.echo(f"Investigating: {candidate['normalized_merchant']}  [{mode}]\n")

        result = run_investigate(candidate, conn, use_stub=use_stub)
    finally:
        conn.close()

    click.echo(f"Verdict          : {result['verdict']}  (confidence: {result['confidence']:.2f})")
    click.echo(f"Recommended action: {result['recommended_action']}")
    click.echo(f"Explanation      : {result['explanation']}\n")
    click.echo("Evidence:")
    for e in result.get("evidence", []):
        click.echo(f"  - {e}")
    click.echo("\nInvestigation trace:")
    click.echo(json.dumps(result["investigation_trace"], indent=2, default=str))
