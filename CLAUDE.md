# Spend Sleuth

LLM-powered personal finance alert system. Analyzes personal credit card transaction data to surface meaningful alerts. Learning/demo project — prototype quality with good evals, not production.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full project plan, timeline, and architectural decisions.

## Current State

End of Week 1. Vertical slice complete: detection → agent → Streamlit UI working end-to-end.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Data store | DuckDB |
| Ingestion | Pandas |
| Agent framework | LangGraph |
| LLM | Anthropic API (`langchain-anthropic` / `claude-sonnet-4-6`) |
| Frontend | Streamlit |
| Hosting | AWS App Runner (not yet deployed) |
| Vector DB | Chroma (not yet integrated) |
| Evals | Not yet integrated |
| Tests | pytest |

## Repo Structure

```
data/                        ← raw CSVs (gitignored, sensitive)
demo_data/                   ← sanitized demo data (not yet populated)
src/
  spend_sleuth/
    db.py                    ← DuckDB connection, schema init
    load.py                  ← CSV ingestion
    detect.py                ← duplicate charge detection
    cli.py                   ← CLI: load, stats, detect, investigate
    app.py                   ← Streamlit app
    agent/
      tools.py               ← 3 LangGraph tools (query_history, recurring_pattern, user_context)
      graph.py               ← LangGraph StateGraph (ReAct loop)
      run.py                 ← investigate() entrypoint
tests/
  conftest.py                ← in-memory DuckDB fixture + insert_transaction helper
  test_detect.py             ← 14 smoke tests for detection layer
investigation_cache.json     ← persisted agent results (gitignored)
```

## Data

- Multiple card CSV folders under `data/`
- Fields: `Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit`
- Debit and Credit are **separate columns** (not a signed amount field)
- Merchant strings often include location codes: `TRADER JOE'S #123`, `SQ *`, `TST*`
- OMNY transit taps (`OMNY * MTA`, `MTA*NYCT PAYGO`) look like duplicates but are valid distinct charges
- ~6-12 months of transactions across all cards

## DuckDB Schema

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
    file_row         INTEGER,
    row_hash         VARCHAR UNIQUE
);
```

`row_hash` = SHA-256 of `transaction_date|posted_date|card_no|description|debit|credit|source_file|file_row`. `file_row` is included so valid within-file duplicates (OMNY taps) are preserved.

## Alert Taxonomy

1. **Duplicate charge** ✅ — same/near-same amount, same merchant, within N days
2. **Unusual amount for known merchant** — not yet implemented
3. **Forgotten subscription** — not yet implemented

## Agent Tools

1. `query_transaction_history(merchant, days_back, min_amount, max_amount)` ✅ — search transactions by merchant substring
2. `get_recurring_pattern(merchant)` ✅ — detect recurring-charge pattern (stddev/mean < 0.3 → regular)
3. `get_user_context()` ✅ — top categories, top merchants, avg monthly spend
4. `lookup_merchant(raw_string)` — optional, not yet implemented

## CLI Commands

```bash
uv run spend-sleuth load <data_dir>          # ingest CSVs
uv run spend-sleuth stats                    # row count, date range, by card
uv run spend-sleuth detect [--window N] [--tolerance F] [--start DATE] [--end DATE]
uv run spend-sleuth investigate [--stub|--no-stub] [--window N] [--start DATE] [--end DATE]
uv run pytest                                # run test suite
```

## Streamlit App

```bash
uv run --env-file .env streamlit run src/spend_sleuth/app.py
```

Features: date range presets (30d/60d/90d/YTD), card/category/description filters, monthly spend chart, category breakdown, transaction table, duplicate candidates section with per-pair Investigate buttons. Investigation results cached to `investigation_cache.json`. Stub mode on by default; toggle off with `ANTHROPIC_API_KEY` set.

## Git Conventions
- Never commit directly to main
- Always create a feature branch before starting work: `git checkout -b <type>/<short-description>`
- Commit incrementally after each logical unit of work — not everything at the end

## Off-limits

Never read files under `data/`. This directory contains real personal financial data and is gitignored for that reason. Infer the CSV schema from the documentation in this file only. This applies to all tools: `Read`, `Bash` (`cat`, `head`, etc.), and any MCP file tools.

## Memory

At the start of each session, read the project memory index and load any relevant memory files.
