"""
Generate a lightweight synthetic demo database for smoke testing.
No anomalies — clean, realistic-looking transactions across 2 cards.

Usage:
    uv run python demo_data/seed.py [--output PATH]
"""
import hashlib
import random
import argparse
from datetime import date, timedelta
from pathlib import Path

import duckdb

# Reproducible output
random.seed(42)

OUTPUT_DEFAULT = Path(__file__).parent / "demo.db"

CARDS = ["1234", "5678"]

MERCHANTS = {
    "Dining": [
        ("SWEETGREEN", 14, 22),
        ("TACOMBI", 18, 35),
        ("BLUE BOTTLE COFFEE", 5, 9),
        ("PIZZA LOVES EMILY", 22, 55),
        ("SUPERIORITY BURGER", 12, 20),
    ],
    "Groceries": [
        ("TRADER JOE'S #142", 25, 90),
        ("WHOLE FOODS MARKET", 40, 120),
        ("BOGOPA RED HOOK", 30, 80),
        ("KEY FOOD", 20, 60),
    ],
    "Transportation": [
        ("UBER   *TRIP", 8, 35),
        ("MTA*NYCT PAYGO", 2.90, 2.90),
        ("LYFT   *RIDE", 10, 40),
    ],
    "Entertainment": [
        ("AMC THEATRES", 15, 28),
        ("SPOTIFY USA", 10.99, 10.99),
        ("NETFLIX.COM", 15.49, 15.49),
    ],
    "Shopping": [
        ("AMAZON.COM", 12, 120),
        ("TARGET", 25, 80),
        ("CVS PHARMACY", 8, 40),
    ],
    "Health Care": [
        ("DUANE READE", 10, 45),
        ("CITYMD URGENT CARE", 50, 150),
    ],
    "Phone/Cable": [
        ("VERIZON WIRELESS", 45, 85),
        ("SPECTRUM", 60, 90),
    ],
}


def _hash(row: dict) -> str:
    key = "|".join(str(row[c]) for c in [
        "transaction_date", "posted_date", "card_no",
        "description", "debit", "credit", "source_file", "file_row",
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def generate_transactions(start: date, end: date) -> list[dict]:
    rows = []
    file_row = 0
    current = start
    while current <= end:
        # 2-5 transactions per day on average, skip some days
        if random.random() < 0.6:
            current += timedelta(days=1)
            continue
        n = random.randint(1, 4)
        for _ in range(n):
            category = random.choice(list(MERCHANTS.keys()))
            desc, lo, hi = random.choice(MERCHANTS[category])
            amount = round(random.uniform(lo, hi), 2)
            card = random.choice(CARDS)
            posted = current + timedelta(days=random.randint(0, 2))
            row = {
                "transaction_date": current,
                "posted_date": posted,
                "card_no": card,
                "description": desc,
                "category": category,
                "debit": amount,
                "credit": None,
                "source_file": "demo_seed.csv",
                "file_row": file_row,
            }
            row["row_hash"] = _hash(row)
            rows.append(row)
            file_row += 1
        current += timedelta(days=1)
    return rows


def build(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    conn = duckdb.connect(str(output))
    conn.execute("""
        CREATE TABLE transactions (
            transaction_date DATE,
            posted_date      DATE,
            card_no          VARCHAR,
            description      VARCHAR,
            category         VARCHAR,
            debit            DECIMAL(10,2),
            credit           DECIMAL(10,2),
            source_file      VARCHAR,
            file_row         INTEGER,
            row_hash         VARCHAR UNIQUE
        )
    """)

    end = date.today()
    start = end.replace(month=1, day=1)          # YTD
    rows = generate_transactions(start, end)

    conn.executemany("""
        INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)
    """, [
        [r["transaction_date"], r["posted_date"], r["card_no"], r["description"],
         r["category"], r["debit"], r["credit"], r["source_file"], r["file_row"], r["row_hash"]]
        for r in rows
    ])

    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    print(f"Built {output} — {count} transactions ({start} → {end})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    build(args.output)
