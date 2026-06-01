import streamlit as st
import pandas as pd
import altair as alt
from pathlib import Path

from spend_sleuth.db import get_connection, DB_PATH
from spend_sleuth.detect import find_duplicate_candidates


@st.cache_resource
def get_conn():
    return get_connection(DB_PATH, read_only=True)


@st.cache_data
def load_duplicate_candidates(start_date, end_date) -> list[dict]:
    return find_duplicate_candidates(get_conn(), start_date=start_date, end_date=end_date)


@st.cache_data
def load_transactions() -> pd.DataFrame:
    conn = get_conn()
    df = conn.execute("""
        SELECT
            transaction_date,
            posted_date,
            card_no,
            description,
            category,
            debit,
            credit,
            source_file
        FROM transactions
        ORDER BY transaction_date DESC
    """).df()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["posted_date"] = pd.to_datetime(df["posted_date"])
    return df


st.set_page_config(page_title="Spend Sleuth", layout="wide")
st.title("Spend Sleuth")

df = load_transactions()

# --- Sidebar filters ---
st.sidebar.header("Filters")

date_min = df["transaction_date"].min().date()
date_max = df["transaction_date"].max().date()
date_range = st.sidebar.date_input("Date range", value=(date_min, date_max), min_value=date_min, max_value=date_max)

cards = ["All"] + sorted(df["card_no"].dropna().unique().tolist())
selected_card = st.sidebar.selectbox("Card", cards)

categories = ["All"] + sorted(df["category"].dropna().unique().tolist())
selected_category = st.sidebar.selectbox("Category", categories)

search = st.sidebar.text_input("Search description")

# --- Apply filters ---
mask = (
    (df["transaction_date"].dt.date >= date_range[0]) &
    (df["transaction_date"].dt.date <= date_range[1])
) if len(date_range) == 2 else pd.Series(True, index=df.index)

if selected_card != "All":
    mask &= df["card_no"] == selected_card
if selected_category != "All":
    mask &= df["category"] == selected_category
if search:
    mask &= df["description"].str.contains(search, case=False, na=False)

filtered = df[mask]

# --- Summary metrics ---
total_debit = filtered["debit"].sum()
total_credit = filtered["credit"].sum()
col1, col2, col3 = st.columns(3)
col1.metric("Transactions", f"{len(filtered):,}")
col2.metric("Total Debits", f"${total_debit:,.2f}")
col3.metric("Total Credits", f"${total_credit:,.2f}")

st.divider()

# --- Spend over time ---
st.subheader("Monthly Spend")
monthly = (
    filtered[filtered["debit"].notna()]
    .assign(month=filtered["transaction_date"].dt.to_period("M").astype(str))
    .groupby("month", as_index=False)["debit"]
    .sum()
)
if not monthly.empty:
    chart = (
        alt.Chart(monthly)
        .mark_bar()
        .encode(
            x=alt.X("month:O", title="Month", sort=None),
            y=alt.Y("debit:Q", title="Total Debits ($)"),
            tooltip=["month", alt.Tooltip("debit:Q", format="$,.2f")],
        )
        .properties(height=250)
    )
    st.altair_chart(chart, use_container_width=True)

# --- Spend by category ---
st.subheader("Spend by Category")
by_cat = (
    filtered[filtered["debit"].notna()]
    .groupby("category", as_index=False)["debit"]
    .sum()
    .sort_values("debit", ascending=False)
    .head(15)
)
if not by_cat.empty:
    cat_chart = (
        alt.Chart(by_cat)
        .mark_bar()
        .encode(
            x=alt.X("debit:Q", title="Total Debits ($)"),
            y=alt.Y("category:N", sort="-x", title=None),
            tooltip=["category", alt.Tooltip("debit:Q", format="$,.2f")],
        )
        .properties(height=350)
    )
    st.altair_chart(cat_chart, use_container_width=True)

st.divider()

# --- Transaction table ---
st.subheader(f"Transactions ({len(filtered):,})")
display_cols = ["transaction_date", "description", "category", "debit", "credit", "card_no"]
st.dataframe(
    filtered[display_cols].rename(columns={
        "transaction_date": "Date",
        "description": "Description",
        "category": "Category",
        "debit": "Debit",
        "credit": "Credit",
        "card_no": "Card",
    }),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Debit": st.column_config.NumberColumn(format="$%.2f"),
        "Credit": st.column_config.NumberColumn(format="$%.2f"),
    },
)

st.divider()

# --- Duplicate charge candidates ---
st.subheader("Duplicate Charge Candidates")

candidates = load_duplicate_candidates(
    date_range[0] if len(date_range) == 2 else date_min,
    date_range[1] if len(date_range) == 2 else date_max,
)

if not candidates:
    st.info("No duplicate candidates detected.")
else:
    non_omny = [c for c in candidates if not c["is_omny"]]
    omny = [c for c in candidates if c["is_omny"]]
    st.caption(f"{len(non_omny)} flagged pair(s)  |  {len(omny)} OMNY pair(s) (expected transit taps, shown for completeness)")

    for c in candidates:
        a, b = c["txn_a"], c["txn_b"]
        label = (
            f"{'[OMNY] ' if c['is_omny'] else ''}"
            f"{c['normalized_merchant']}  —  "
            f"${a['debit']:.2f}  |  {c['days_apart']} day(s) apart"
        )
        with st.expander(label, expanded=not c["is_omny"]):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Transaction A**")
                st.write(a)
            with col_b:
                st.markdown("**Transaction B**")
                st.write(b)
            st.caption(f"Amount diff: ${c['amount_diff']:.4f}  |  Normalized merchant: `{c['normalized_merchant']}`")
