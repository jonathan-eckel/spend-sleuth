"""Streamlit page: build anonymized demo data from real transactions.

Pick one or more real transactions for a merchant, choose an alert type, let a
preset shape them into a stronger signal, hand-edit the preview if you like,
and save to a dedicated demo DuckDB that accumulates across sessions.

Discovered automatically by Streamlit (this lives in `pages/` next to the
entrypoint `app.py`), so it appears in the sidebar nav under the same
`streamlit run src/spend_sleuth/app.py` launch.
"""
import pandas as pd
import streamlit as st

from spend_sleuth.db import get_connection, DB_PATH
from spend_sleuth import demo_gen


@st.cache_resource
def get_source_conn():
    """Read-only connection to the real (source) transactions DB."""
    return get_connection(DB_PATH, read_only=True)


@st.cache_data
def load_source_transactions() -> pd.DataFrame:
    df = get_source_conn().execute("""
        SELECT transaction_date, posted_date, card_no, description,
               category, debit, credit
        FROM transactions
        WHERE debit IS NOT NULL AND debit > 0
        ORDER BY transaction_date DESC
    """).df()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["posted_date"] = pd.to_datetime(df["posted_date"])
    return df


PREVIEW_COLUMNS = [
    "transaction_date", "posted_date", "card_no", "description",
    "category", "debit", "credit",
]

_EDITOR_COLUMN_CONFIG = {
    "transaction_date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
    "posted_date": st.column_config.DateColumn("Posted", format="YYYY-MM-DD"),
    "card_no": st.column_config.TextColumn("Card"),
    "description": st.column_config.TextColumn("Merchant"),
    "category": st.column_config.TextColumn("Category"),
    "debit": st.column_config.NumberColumn("Debit", format="$%.2f"),
    "credit": st.column_config.NumberColumn("Credit", format="$%.2f"),
}


def _rows_from_editor(edited: pd.DataFrame) -> list[dict]:
    """Rebuild fully-hashed demo rows from the (possibly edited) preview table.

    Re-anonymization is NOT applied here — the preview already shows anonymized
    values; we just re-hash with sequential file_row so the batch is consistent.
    """
    rows = []
    for i, r in enumerate(edited.itertuples(index=False)):
        rows.append(demo_gen.make_row(
            transaction_date=pd.to_datetime(r.transaction_date).date(),
            posted_date=pd.to_datetime(r.posted_date).date(),
            card_no=str(r.card_no),
            description=str(r.description),
            category=str(r.category),
            debit=float(r.debit),
            credit=None if pd.isna(r.credit) else float(r.credit),
            file_row=i,
        ))
    return rows


st.set_page_config(page_title="Demo Data Builder", layout="wide")
st.title("Demo Data Builder")
st.caption(
    "Turn real transactions into anonymized demo data that trips the detectors. "
    "Card numbers and merchant names are replaced; categories, dates and amounts "
    "are preserved unless you edit them."
)

# --- Sidebar: demo dataset status ---
st.sidebar.header("Demo dataset")
st.sidebar.caption(f"Target: `{demo_gen.DEMO_DB_PATH}`")
st.sidebar.metric("Rows saved", f"{demo_gen.demo_db_count():,}")
with st.sidebar.expander("Reset dataset"):
    confirm = st.checkbox("Yes, delete all saved demo rows")
    if st.button("🗑 Reset demo dataset", disabled=not confirm, use_container_width=True):
        demo_gen.clear_demo_db()
        st.success("Demo dataset cleared.")
        st.rerun()

# --- Load source data ---
try:
    df = load_source_transactions()
except Exception as e:  # noqa: BLE001 — surface any DB/connection issue to the user
    st.error(f"Could not read the source database at `{DB_PATH}`: {e}")
    st.stop()

if df.empty:
    st.warning("No transactions in the source database. Load some data first.")
    st.stop()

# --- Step 1: pick transactions ---
st.subheader("1. Pick transactions for a merchant")

fcol1, fcol2, fcol3 = st.columns([2, 1, 1])
search = fcol1.text_input("Search merchant", placeholder="e.g. TRADER JOE'S")
cards = ["All"] + sorted(df["card_no"].dropna().unique().tolist())
sel_card = fcol2.selectbox("Card", cards)
cats = ["All"] + sorted(df["category"].dropna().unique().tolist())
sel_cat = fcol3.selectbox("Category", cats)

mask = pd.Series(True, index=df.index)
if search:
    mask &= df["description"].str.contains(search, case=False, na=False)
if sel_card != "All":
    mask &= df["card_no"] == sel_card
if sel_cat != "All":
    mask &= df["category"] == sel_cat

picks = df[mask].reset_index(drop=True)
st.caption(f"{len(picks):,} matching transaction(s). Select one or more rows below.")

event = st.dataframe(
    picks,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="multi-row",
    height=280,
    column_config={
        "transaction_date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
        "posted_date": st.column_config.DateColumn("Posted", format="YYYY-MM-DD"),
        "debit": st.column_config.NumberColumn("Debit", format="$%.2f"),
        "credit": st.column_config.NumberColumn("Credit", format="$%.2f"),
    },
)

selected_rows = event.selection.rows
if not selected_rows:
    st.info("Select one or more transactions to seed the demo series.")
    st.stop()

bases = [picks.iloc[i].to_dict() for i in selected_rows]
st.caption(f"{len(bases)} transaction(s) selected — the preset is applied to each.")

# --- Step 2: anonymize ---
st.subheader("2. Anonymize")
st.caption(
    "Each distinct real merchant/card below is replaced on **every** generated "
    "row. Pre-filled with deterministic anonymized defaults — edit any value."
)

# One mapping row per distinct (card, normalized merchant) among the picks.
_seen: dict[tuple[str, str], dict] = {}
for b in bases:
    norm = demo_gen.normalize_merchant(b["description"])
    key = (str(b["card_no"]), norm)
    if key not in _seen:
        _seen[key] = {
            "Original merchant": norm,
            "Original card": str(b["card_no"]),
            "Anonymized merchant": demo_gen.anonymize_merchant(b["description"]),
            "Anonymized card": demo_gen.anonymize_card(b["card_no"]),
        }

# Re-key the editor on the selection so changing picks resets the defaults
# rather than keeping a stale edit from a previous selection.
_sel_sig = "|".join(sorted(f"{c}~{m}" for c, m in _seen))
edited_map = st.data_editor(
    pd.DataFrame(list(_seen.values())),
    hide_index=True,
    use_container_width=True,
    disabled=["Original merchant", "Original card"],
    key=f"anon_map::{_sel_sig}",
)

# (real_card, real_norm_merchant) -> (fake_card, fake_merchant); cleared cells
# fall back to the anonymized default.
anon_map: dict[tuple[str, str], tuple[str, str]] = {}
for _, r in edited_map.iterrows():
    real_card, real_norm = str(r["Original card"]), str(r["Original merchant"])
    fake_merchant = str(r["Anonymized merchant"]).strip() or demo_gen.anonymize_merchant(real_norm)
    fake_card = str(r["Anonymized card"]).strip() or demo_gen.anonymize_card(real_card)
    anon_map[(real_card, real_norm)] = (fake_card, fake_merchant)

# --- Step 3: shape into an alert ---
st.subheader("3. Shape into a stronger alert")

alert_type = st.radio(
    "Alert type",
    ["Duplicate charge", "Unusual amount", "Forgotten subscription"],
    horizontal=True,
)

if alert_type == "Duplicate charge":
    c1, c2, c3 = st.columns(3)
    copies = c1.number_input("Copies", min_value=2, max_value=10, value=2)
    days_apart = c2.number_input("Days apart", min_value=0, max_value=7, value=0)
    amount_diff = c3.number_input("Amount diff ($)", min_value=0.0, max_value=10.0, value=0.0, step=0.25)
    gen_fn = demo_gen.gen_duplicate
    gen_params = dict(copies=int(copies), days_apart=int(days_apart), amount_diff=float(amount_diff))
elif alert_type == "Unusual amount":
    c1, c2 = st.columns(2)
    history = c1.number_input("History charges", min_value=5, max_value=40, value=8)
    multiplier = c2.number_input("Spike multiplier", min_value=2.0, max_value=20.0, value=3.0, step=0.5)
    # Baseline is each picked row's own amount (the spike = baseline × multiplier).
    gen_fn = demo_gen.gen_unusual_amount
    gen_params = dict(history=int(history), multiplier=float(multiplier))
else:
    c1, c2 = st.columns(2)
    pattern = c1.selectbox("Cadence", list(demo_gen.PATTERN_INTERVAL_DAYS.keys()), index=2)
    count = c2.number_input("Number of charges", min_value=3, max_value=24, value=6)
    gen_fn = demo_gen.gen_subscription
    gen_params = dict(pattern=pattern, count=int(count))

# Apply the preset to every selected row, using its mapped anonymized identity.
generated: list[dict] = []
for b in bases:
    norm = demo_gen.normalize_merchant(b["description"])
    fake_card, fake_merchant = anon_map[(str(b["card_no"]), norm)]
    anon_base = {**b, "card_no": fake_card, "description": fake_merchant}
    generated += gen_fn(anon_base, anonymize=False, **gen_params)

st.caption(f"{len(generated)} row(s) generated from {len(bases)} selection(s).")

# --- Step 4: preview & edit ---
st.subheader("4. Preview & edit")
st.caption("Every field is editable. Add or remove rows too. Rows are re-hashed on save.")
preview_df = pd.DataFrame(generated)[PREVIEW_COLUMNS]
preview_df["transaction_date"] = pd.to_datetime(preview_df["transaction_date"])
preview_df["posted_date"] = pd.to_datetime(preview_df["posted_date"])
edited = st.data_editor(
    preview_df,
    use_container_width=True,
    hide_index=True,
    num_rows="dynamic",
    column_config=_EDITOR_COLUMN_CONFIG,
    key="demo_preview_editor",
)

# --- Step 5: save ---
st.subheader("5. Save")
if st.button("💾 Save to demo dataset", type="primary"):
    rows = _rows_from_editor(edited)
    inserted = demo_gen.write_demo_rows(rows)
    total = demo_gen.demo_db_count()
    if inserted:
        st.success(f"Added {inserted} row(s) — {total:,} total in the demo dataset.")
    else:
        st.info(f"No new rows added (already present). {total:,} total in the demo dataset.")
    st.rerun()
