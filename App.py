import streamlit as st
import pandas as pd
import plotly.express as px
import re

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
FREE_LIMIT = 3

# Replace with your real Stripe Payment Link once you've created one at
# dashboard.stripe.com/payment-links (no coding needed on Stripe's side).
PAYMENT_LINK_URL = "https://buy.stripe.com/REPLACE_WITH_YOUR_LINK"
UNLOCK_CODE = "UNLOCK2026"  # swap for real codes once payments are wired up

st.set_page_config(page_title="Instant Report", page_icon="📊", layout="centered")

# ---------------------------------------------------------
# USAGE TRACKING
# ---------------------------------------------------------
# NOTE: this now tracks usage per browser session (st.session_state),
# not in one shared file — so visitors no longer share the same 3 free
# reports. The trade-off: a visitor who refreshes or reopens the tab in
# a new session gets a fresh count. That's fine for testing with a small
# group; for a real product where the limit needs to survive refreshes
# and closed tabs, the next step is a lightweight database (e.g. Supabase)
# keyed by a visitor ID stored in a browser cookie.
if "usage_count" not in st.session_state:
    st.session_state.usage_count = 0
if "unlocked" not in st.session_state:
    st.session_state.unlocked = False

remaining = max(0, FREE_LIMIT - st.session_state.usage_count)
at_limit = (not st.session_state.unlocked) and remaining <= 0

# ---------------------------------------------------------
# HEADER
# ---------------------------------------------------------
st.title("📊 Instant Report")
st.write(
    "Drop in a CSV or Excel file of sales, expenses, bookings — anything with "
    "numbers. Get charts and a plain-language summary back in seconds."
)

if st.session_state.unlocked:
    st.success("✨ Unlocked — unlimited reports")
else:
    st.caption(f"{remaining} of {FREE_LIMIT} free reports left (this session)")

# ---------------------------------------------------------
# PAYWALL
# ---------------------------------------------------------
if at_limit:
    st.warning("You've used your free reports for this session.")
    st.markdown(f"[**Upgrade for unlimited reports**]({PAYMENT_LINK_URL})")
    code = st.text_input("Have a code? Enter it to unlock")
    if st.button("Unlock"):
        if code.strip().upper() == UNLOCK_CODE:
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error("That code didn't match.")
    st.stop()

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
ID_NAME_HINTS = re.compile(
    r"\b(id|code|number|no|num|uid|uuid|flight[_ ]?id|reference|ref)\b",
    re.IGNORECASE,
)

def looks_like_identifier(series: pd.Series, col_name: str) -> bool:
    """Heuristic: is this numeric column actually an ID, not a real metric?"""
    if ID_NAME_HINTS.search(str(col_name)):
        return True
    non_null = series.dropna()
    if len(non_null) == 0:
        return False
    is_integer_like = (non_null == non_null.round()).mean() > 0.98
    uniqueness = non_null.nunique() / len(non_null)
    if is_integer_like and uniqueness > 0.9 and len(non_null) > 5:
        return True
    return False


def read_any_file(uploaded_file):
    """Reads a CSV or Excel upload. Returns dict of {sheet_name: DataFrame}."""
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file, sep=None, engine="python")
        return {"Sheet1": df}
    else:
        xls = pd.ExcelFile(uploaded_file)
        return {sheet: xls.parse(sheet) for sheet in xls.sheet_names}


def fmt(n):
    try:
        return f"{n:,.1f}"
    except Exception:
        return str(n)

# ---------------------------------------------------------
# FILE UPLOAD
# ---------------------------------------------------------
uploaded = st.file_uploader("Upload a CSV or Excel file", type=["csv", "xlsx", "xls"])

if uploaded:
    try:
        sheets = read_any_file(uploaded)
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")
        st.stop()

    # ---- Sheet selection (only shown if more than one sheet) ----
    if len(sheets) > 1:
        st.info(f"This workbook has {len(sheets)} sheets.")
        sheet_labels = [f"{name} ({len(df):,} rows)" for name, df in sheets.items()]
        chosen_label = st.selectbox("Which sheet do you want to analyze?", sheet_labels)
        chosen_name = list(sheets.keys())[sheet_labels.index(chosen_label)]
        df = sheets[chosen_name]
    else:
        df = list(sheets.values())[0]

    if len(df) == 0:
        st.error("That sheet has no rows to analyze.")
        st.stop()

    if not st.session_state.unlocked:
        st.session_state.usage_count += 1

    st.success(f"Analyzed {len(df):,} rows, {len(df.columns)} columns")

    # ---- Column classification ----
    raw_numeric_cols = df.select_dtypes(include="number").columns.tolist()

    id_cols = [c for c in raw_numeric_cols if looks_like_identifier(df[c], c)]
    numeric_cols = [c for c in raw_numeric_cols if c not in id_cols]

    date_cols = []
    for col in df.columns:
        if col in raw_numeric_cols:
            continue
        try:
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().mean() > 0.6:
                date_cols.append(col)
        except Exception:
            pass

    category_cols = [
        c for c in df.columns
        if c not in raw_numeric_cols and c not in date_cols
    ]

    if id_cols:
        st.caption(f"Excluded from stats as likely ID columns: {', '.join(id_cols)}")

    # ---- Focus selection ----
    # Build a simple menu of what the client could focus on, based purely on
    # what columns actually exist in this file — no AI involved, just the
    # column classification we already did above.
    focus_options = ["Overall overview"] + numeric_cols + category_cols
    st.subheader("What would you like to focus on?")
    chosen_focus = st.multiselect(
        "Pick one or more areas (leave as Overall overview to see everything)",
        options=focus_options,
        default=["Overall overview"],
    )

    if not chosen_focus or "Overall overview" in chosen_focus:
        focus_numeric_cols = numeric_cols
        focus_category_cols = category_cols
    else:
        focus_numeric_cols = [c for c in chosen_focus if c in numeric_cols] or numeric_cols
        focus_category_cols = [c for c in chosen_focus if c in category_cols] or category_cols

    # ---- Key metrics ----
    if focus_numeric_cols:
        st.subheader("Key metrics")
        cols = st.columns(min(4, len(focus_numeric_cols)))
        for i, col in enumerate(focus_numeric_cols[:4]):
            total = df[col].sum()
            half = len(df) // 2
            first_half_mean = df[col].iloc[:half].mean() if half > 0 else df[col].mean()
            second_half_mean = df[col].iloc[half:].mean() if half > 0 else df[col].mean()
            pct_change = (
                ((second_half_mean - first_half_mean) / first_half_mean * 100)
                if first_half_mean else 0
            )
            with cols[i]:
                st.metric(col, fmt(total), f"{pct_change:+.1f}%")
    else:
        st.info("No numeric metric columns detected for this focus.")

    # ---- Trend chart ----
    if date_cols and focus_numeric_cols:
        st.subheader("Trend over time")
        date_col = date_cols[0]
        num_col = focus_numeric_cols[0]
        chart_df = df[[date_col, num_col]].copy()
        chart_df[date_col] = pd.to_datetime(chart_df[date_col], errors="coerce")
        chart_df = chart_df.dropna().sort_values(date_col)
        if len(chart_df) > 1:
            fig = px.line(chart_df, x=date_col, y=num_col)
            st.plotly_chart(fig, use_container_width=True)

    # ---- Breakdown chart ----
    if focus_category_cols:
        st.subheader(f"Breakdown by {focus_category_cols[0]}")
        cat_col = focus_category_cols[0]
        if focus_numeric_cols:
            breakdown = df.groupby(cat_col)[focus_numeric_cols[0]].sum().sort_values(ascending=False).head(6)
        else:
            breakdown = df[cat_col].value_counts().head(6)
        fig2 = px.pie(values=breakdown.values, names=breakdown.index)
        st.plotly_chart(fig2, use_container_width=True)

    # ---- Narrative summary ----
    st.subheader("Summary")
    lines = [f"This file contains {len(df):,} records across {len(df.columns)} fields."]
    for col in focus_numeric_cols:
        total = df[col].sum()
        mean = df[col].mean()
        lines.append(
            f"- **{col}**: total {fmt(total)}, averaging {fmt(mean)} per record "
            f"(range {fmt(df[col].min())}\u2013{fmt(df[col].max())})."
        )
    if focus_category_cols and focus_numeric_cols:
        top = df.groupby(focus_category_cols[0])[focus_numeric_cols[0]].sum().idxmax()
        lines.append(f"- By **{focus_category_cols[0]}**, \"{top}\" leads the totals.")
    if id_cols:
        lines.append(f"- Columns treated as identifiers (not averaged): {', '.join(id_cols)}.")
    st.markdown("\n".join(lines))

# ---------------------------------------------------------
# NOTES
# ---------------------------------------------------------
# - Usage tracking is now per-session (st.session_state), so visitors no
#   longer share one global counter. It still resets on refresh/new tab —
#   a persistent per-visitor limit needs a small database next.
# - Excel support requires openpyxl (see requirements.txt).
