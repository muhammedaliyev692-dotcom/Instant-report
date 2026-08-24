import streamlit as st
import pandas as pd
import plotly.express as px
import json
import os

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
FREE_LIMIT = 3
USAGE_FILE = "usage.json"  # NOTE: on Streamlit Cloud this resets on redeploy.
                            # For real per-user tracking online, swap this
                            # for a small database later (see notes at bottom).

# Replace with your real Stripe Payment Link once you've created one at
# dashboard.stripe.com/payment-links (no coding needed on Stripe's side).
PAYMENT_LINK_URL = "https://buy.stripe.com/REPLACE_WITH_YOUR_LINK"
UNLOCK_CODE = "UNLOCK2026"  # swap for real codes once payments are wired up

st.set_page_config(page_title="Instant Report", page_icon="📊", layout="centered")

# ---------------------------------------------------------
# USAGE TRACKING (simple local file — good enough to test with)
# ---------------------------------------------------------
def load_usage():
    if os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "r") as f:
            return json.load(f)
    return {"count": 0, "unlocked": False}

def save_usage(data):
    with open(USAGE_FILE, "w") as f:
        json.dump(data, f)

if "usage" not in st.session_state:
    st.session_state.usage = load_usage()

usage = st.session_state.usage
remaining = max(0, FREE_LIMIT - usage["count"])
at_limit = (not usage["unlocked"]) and remaining <= 0

# ---------------------------------------------------------
# HEADER
# ---------------------------------------------------------
st.title("📊 Instant Report")
st.write(
    "Drop in a CSV of sales, expenses, bookings — anything with numbers. "
    "Get charts and a plain-language summary back in seconds."
)

if usage["unlocked"]:
    st.success("✨ Unlocked — unlimited reports")
else:
    st.caption(f"{remaining} of {FREE_LIMIT} free reports left")

# ---------------------------------------------------------
# PAYWALL
# ---------------------------------------------------------
if at_limit:
    st.warning("You've used your free reports.")
    st.markdown(f"[**Upgrade for unlimited reports**]({PAYMENT_LINK_URL})")
    code = st.text_input("Have a code? Enter it to unlock")
    if st.button("Unlock"):
        if code.strip().upper() == UNLOCK_CODE:
            usage["unlocked"] = True
            save_usage(usage)
            st.rerun()
        else:
            st.error("That code didn't match.")
    st.stop()

# ---------------------------------------------------------
# FILE UPLOAD + ANALYSIS
# ---------------------------------------------------------
uploaded = st.file_uploader("Upload a CSV file", type=["csv"])

if uploaded:
    try:
        df = pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")
        st.stop()

    if not usage["unlocked"]:
        usage["count"] += 1
        save_usage(usage)

    st.success(f"Analyzed {len(df):,} rows, {len(df.columns)} columns")

    # Detect column types
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    date_cols = []
    for col in df.columns:
        if col in numeric_cols:
            continue
        try:
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().mean() > 0.6:
                date_cols.append(col)
        except Exception:
            pass
    category_cols = [c for c in df.columns if c not in numeric_cols and c not in date_cols]

    # ---- Key metrics ----
    if numeric_cols:
        st.subheader("Key metrics")
        cols = st.columns(min(4, len(numeric_cols)))
        for i, col in enumerate(numeric_cols[:4]):
            total = df[col].sum()
            mean = df[col].mean()
            half = len(df) // 2
            first_half_mean = df[col].iloc[:half].mean() if half > 0 else mean
            second_half_mean = df[col].iloc[half:].mean() if half > 0 else mean
            pct_change = (
                ((second_half_mean - first_half_mean) / first_half_mean * 100)
                if first_half_mean else 0
            )
            with cols[i]:
                st.metric(col, f"{total:,.1f}", f"{pct_change:+.1f}%")

    # ---- Trend chart ----
    if date_cols and numeric_cols:
        st.subheader("Trend over time")
        date_col = date_cols[0]
        num_col = numeric_cols[0]
        chart_df = df[[date_col, num_col]].copy()
        chart_df[date_col] = pd.to_datetime(chart_df[date_col], errors="coerce")
        chart_df = chart_df.dropna().sort_values(date_col)
        fig = px.line(chart_df, x=date_col, y=num_col)
        st.plotly_chart(fig, use_container_width=True)

    # ---- Breakdown chart ----
    if category_cols:
        st.subheader(f"Breakdown by {category_cols[0]}")
        cat_col = category_cols[0]
        if numeric_cols:
            breakdown = df.groupby(cat_col)[numeric_cols[0]].sum().sort_values(ascending=False).head(6)
        else:
            breakdown = df[cat_col].value_counts().head(6)
        fig2 = px.pie(values=breakdown.values, names=breakdown.index)
        st.plotly_chart(fig2, use_container_width=True)

    # ---- Narrative summary ----
    st.subheader("Summary")
    lines = [f"This file contains {len(df):,} records across {len(df.columns)} fields."]
    for col in numeric_cols:
        total = df[col].sum()
        mean = df[col].mean()
        lines.append(
            f"- **{col}**: total {total:,.1f}, averaging {mean:,.1f} per record "
            f"(range {df[col].min():,.1f}–{df[col].max():,.1f})."
        )
    if category_cols and numeric_cols:
        top = df.groupby(category_cols[0])[numeric_cols[0]].sum().idxmax()
        lines.append(f"- By **{category_cols[0]}**, \"{top}\" leads the totals.")
    st.markdown("\n".join(lines))

# ---------------------------------------------------------
# NOTES FOR GOING LIVE (read, then delete this block)
# ---------------------------------------------------------
# 1. This usage.json approach only works well for local testing or a
#    single-user demo — on Streamlit Cloud every visitor currently shares
#    the same counter. For real per-visitor free limits online, the
#    simplest upgrade is Streamlit's built-in st.connection to a free
#    database (e.g. Supabase or a Google Sheet) keyed by a visitor ID.
# 2. To deploy: push this file + requirements.txt to a GitHub repo,
#    then deploy free at share.streamlit.io.
