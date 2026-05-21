# Spend Sleuth

LLM-powered personal finance alert system. Analyzes personal credit card transaction data to surface meaningful alerts. Learning/demo project — prototype quality with good evals, not production.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full project plan, timeline, and architectural decisions.

## Current State

Pre-Week-1. Data downloaded, no code written yet.

## Stack (planned)

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Data store | DuckDB |
| Ingestion | Pandas |
| Agent framework | LangGraph |
| LLM | Anthropic API (direct) |
| Frontend | Streamlit |
| Hosting | AWS App Runner |
| Vector DB | Chroma (persistent) |
| Evals | LangSmith or Braintrust (not yet integrated) |

## Repo Structure (planned)

```
data/              ← raw CSVs (gitignored, sensitive)
src/
  spend_sleuth/
    db.py          ← DuckDB connection, schema init
    load.py        ← CSV ingestion
    cli.py         ← CLI entrypoint
```

## Data

- Multiple card CSV folders under `data/`
- Fields: `Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit`
- Debit and Credit are **separate columns** (not a signed amount field)
- Merchant strings often include location codes: `TRADER JOE'S #123`, `SQ *`, `TST*`
- OMNY transit taps look like duplicates (same date/amount/description) but are valid distinct charges
- ~6-12 months of transactions across all cards

## Planned DuckDB Schema

```sql
CREATE TABLE transactions (
    transaction_date DATE,
    posted_date      DATE,
    card_no          VARCHAR,
    description      VARCHAR,
    category         VARCHAR,
    debit            DECIMAL(10,2),
    credit           DECIMAL(10,2),
    source_file      VARCHAR,
    row_hash         VARCHAR UNIQUE
);
```

## Alert Taxonomy (locked)

1. **Duplicate charge** — same/near-same amount, same merchant, within N days
2. **Unusual amount for known merchant** — significant deviation from spend distribution
3. **Forgotten subscription** — recurring charge with high temporal regularity

## Agent Tools (planned)

1. `query_transaction_history(filters)` — search by merchant, category, date range, amount
2. `get_recurring_pattern(merchant)` — detect recurring-charge pattern for a merchant
3. `get_user_context()` — synthesized spend profile: typical amounts per category, top merchants
4. `lookup_merchant(raw_string)` *(optional)* — normalize messy merchant strings via LLM

## Memory

At the start of each session, read the project memory index and load any relevant memory files.
