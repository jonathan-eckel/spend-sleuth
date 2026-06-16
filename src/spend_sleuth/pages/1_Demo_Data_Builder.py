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

# --- Current demo dataset preview ---
# Read fresh each run (uncached) so it reflects saves/resets, which st.rerun().
demo_df = demo_gen.read_demo_rows()
with st.expander(f"📂 Current demo dataset ({len(demo_df):,} rows)", expanded=False):
    if demo_df.empty:
        st.caption(f"Empty — nothing saved yet. Target: `{demo_gen.DEMO_DB_PATH}`")
    else:
        st.dataframe(
            demo_df,
            use_container_width=True,
            hide_index=True,
            height=280,
            column_config={
                "transaction_date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
                "card_no": st.column_config.TextColumn("Card"),
                "description": st.column_config.TextColumn("Merchant"),
                "category": st.column_config.TextColumn("Category"),
                "debit": st.column_config.NumberColumn("Debit", format="$%.2f"),
                "credit": st.column_config.NumberColumn("Credit", format="$%.2f"),
            },
        )
        st.download_button(
            "⬇ Download as CSV",
            demo_df.to_csv(index=False),
            file_name="demo_dataset.csv",
            mime="text/csv",
        )

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

# --- Step 2: what to build ---
st.subheader("2. Choose what to build")
alert_type = st.radio(
    "Output type",
    ["Anonymize only", "Duplicate charge", "Unusual amount", "Forgotten subscription"],
    horizontal=True,
    help=(
        "Anonymize only saves each selected record as-is (no alert). Duplicate "
        "clones each selected charge. Unusual amount and Subscription combine "
        "the whole selection into one inferred synthetic series."
    ),
)
combine = alert_type in ("Unusual amount", "Forgotten subscription")
passthrough = alert_type == "Anonymize only"

# --- Step 3: anonymize ---
st.subheader("3. Anonymize")

if not combine:
    # Duplicate / Anonymize-only: one editable identity per distinct (card,
    # merchant) among the picks.
    _action = "saved as-is" if passthrough else "cloned into a duplicate"
    st.caption(
        f"{len(bases)} transaction(s) selected — each is {_action}. "
        "Each distinct real merchant/card below is replaced on its generated rows."
    )
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
    # Re-key the editor on the selection so changing picks resets the defaults.
    _sel_sig = "|".join(sorted(f"{c}~{m}" for c, m in _seen))
    edited_map = st.data_editor(
        pd.DataFrame(list(_seen.values())),
        hide_index=True,
        use_container_width=True,
        disabled=["Original merchant", "Original card"],
        key=f"anon_map::{_sel_sig}",
    )
    # (real_card, real_norm) -> (fake_card, fake_merchant); cleared cells default.
    anon_map: dict[tuple[str, str], tuple[str, str]] = {}
    for _, r in edited_map.iterrows():
        real_card, real_norm = str(r["Original card"]), str(r["Original merchant"])
        fake_merchant = str(r["Anonymized merchant"]).strip() or demo_gen.anonymize_merchant(real_norm)
        fake_card = str(r["Anonymized card"]).strip() or demo_gen.anonymize_card(real_card)
        anon_map[(real_card, real_norm)] = (fake_card, fake_merchant)
else:
    # Combine: collapse the selection to one synthetic identity. Use the most
    # common selected merchant (tie-break: first selected) as the template.
    norm_counts: dict[str, int] = {}
    norm_to_raw: dict[str, dict] = {}
    for b in bases:
        n = demo_gen.normalize_merchant(b["description"])
        norm_counts[n] = norm_counts.get(n, 0) + 1
        norm_to_raw.setdefault(n, b)
    dominant_norm = max(norm_counts, key=lambda n: norm_counts[n])
    dominant = norm_to_raw[dominant_norm]
    st.caption(
        f"Combining {len(bases)} charge(s) into **one** synthetic merchant "
        f"(from `{dominant_norm}`)."
    )
    _id_key = f"{dominant['card_no']}|{dominant_norm}"
    icol1, icol2 = st.columns(2)
    anon_merchant = icol1.text_input(
        "Merchant name",
        value=demo_gen.anonymize_merchant(dominant["description"]),
        key=f"combine_merchant::{_id_key}",
        help=f"Original: {dominant_norm}",
    )
    anon_card = icol2.text_input(
        "Card number",
        value=demo_gen.anonymize_card(dominant["card_no"]),
        key=f"combine_card::{_id_key}",
        help=f"Original: {dominant['card_no']}",
    )
    anon_merchant = anon_merchant.strip() or demo_gen.anonymize_merchant(dominant["description"])
    anon_card = anon_card.strip() or demo_gen.anonymize_card(dominant["card_no"])

# --- Step 4: shape into an alert ---
st.subheader("4. Shape into a stronger alert")

generated: list[dict] = []

if alert_type == "Anonymize only":
    st.caption(
        "No shaping — each selected record is saved as-is, with only the card "
        "number and merchant anonymized. Useful for seeding realistic background data."
    )
    for b in bases:
        norm = demo_gen.normalize_merchant(b["description"])
        fake_card, fake_merchant = anon_map[(str(b["card_no"]), norm)]
        anon_base = {**b, "card_no": fake_card, "description": fake_merchant}
        generated += demo_gen.gen_passthrough(anon_base, anonymize=False)
    st.caption(f"{len(generated)} record(s) ready from {len(bases)} selection(s).")

elif alert_type == "Duplicate charge":
    c1, c2, c3 = st.columns(3)
    copies = c1.number_input("Copies", min_value=2, max_value=10, value=2)
    days_apart = c2.number_input("Days apart", min_value=0, max_value=7, value=0)
    amount_diff = c3.number_input("Amount diff ($)", min_value=0.0, max_value=10.0, value=0.0, step=0.25)
    for b in bases:
        norm = demo_gen.normalize_merchant(b["description"])
        fake_card, fake_merchant = anon_map[(str(b["card_no"]), norm)]
        anon_base = {**b, "card_no": fake_card, "description": fake_merchant}
        generated += demo_gen.gen_duplicate(
            anon_base, copies=int(copies), days_apart=int(days_apart),
            amount_diff=float(amount_diff), anonymize=False,
        )
    st.caption(f"{len(generated)} row(s) generated from {len(bases)} selection(s).")

elif alert_type == "Forgotten subscription":
    summary = demo_gen.summarize_selection(bases)
    inferred_pattern = summary.get("pattern", "monthly")
    interval_note = (
        f"avg {summary['mean_interval_days']} days over {summary['count']} charges"
        if "mean_interval_days" in summary
        else "single charge — pick more to infer cadence"
    )
    st.caption(
        f"Inferred cadence: **{inferred_pattern}** ({interval_note}); "
        f"typical amount **${summary['mean_amount']:.2f}**."
    )
    patterns = list(demo_gen.PATTERN_INTERVAL_DAYS.keys())
    c1, c2, c3 = st.columns(3)
    pattern = c1.selectbox("Cadence", patterns, index=patterns.index(inferred_pattern))
    count = c2.number_input("Number of charges", min_value=3, max_value=24,
                            value=max(3, summary["count"]))
    amount = c3.number_input("Amount ($)", min_value=0.01, value=float(summary["mean_amount"]), step=1.0)
    combined_base = {
        "transaction_date": summary["last_date"],
        "card_no": anon_card,
        "description": anon_merchant,
        "category": dominant.get("category") or "Uncategorized",
        "debit": float(amount),
    }
    generated = demo_gen.gen_subscription(
        combined_base, pattern=pattern, count=int(count), anonymize=False,
    )

else:  # Unusual amount
    summary = demo_gen.summarize_selection(bases)
    baseline_default, spike_default = demo_gen.infer_unusual_baseline_spike(bases)
    st.caption(
        f"Baseline **${baseline_default:.2f}** from {max(0, len(bases) - 1)} charge(s); "
        f"spike = largest selected **${spike_default:.2f}** "
        f"(Δ ${spike_default - baseline_default:.2f})."
    )
    c1, c2, c3 = st.columns(3)
    baseline = c1.number_input("Baseline ($)", min_value=0.01, value=float(baseline_default), step=1.0)
    spike = c2.number_input("Spike amount ($)", min_value=0.01, value=float(spike_default), step=1.0)
    history = c3.number_input("History charges", min_value=5, max_value=40, value=8)
    if spike - baseline < 10:
        st.warning(
            "Spike is less than $10 above baseline — the detector's minimum delta "
            "won't be met, so this may not fire. Increase the spike or lower the baseline."
        )
    combined_base = {
        "transaction_date": summary["last_date"],
        "card_no": anon_card,
        "description": anon_merchant,
        "category": dominant.get("category") or "Uncategorized",
        "debit": float(baseline),
    }
    generated = demo_gen.gen_unusual_amount(
        combined_base, history=int(history), baseline=float(baseline),
        spike_amount=float(spike), anonymize=False,
    )

# --- Step 5: preview & edit ---
st.subheader("5. Preview & edit")
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

# --- Step 6: save ---
st.subheader("6. Save")
if st.button("💾 Save to demo dataset", type="primary"):
    rows = _rows_from_editor(edited)
    inserted = demo_gen.write_demo_rows(rows)
    total = demo_gen.demo_db_count()
    if inserted:
        st.success(f"Added {inserted} row(s) — {total:,} total in the demo dataset.")
    else:
        st.info(f"No new rows added (already present). {total:,} total in the demo dataset.")
    st.rerun()
