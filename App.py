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
# STYLE (navy / white, professional look)
# ---------------------------------------------------------
# Most of the theme (background, primary color, font) is set in
# .streamlit/config.toml. This adds a few polish touches Streamlit's
# built-in theming can't reach on its own.
st.markdown("""
<style>
    .main .block-container { padding-top: 2.5rem; max-width: 780px; }
    h1 { color: #1B3A5C; font-weight: 700; letter-spacing: -0.01em; }
    h2, h3 { color: #1B3A5C; font-weight: 600; }
    [data-testid="stMetric"] {
        background: #F4F6F9;
        border: 1px solid #E1E6ED;
        border-radius: 6px;
        padding: 14px 16px 10px;
    }
    [data-testid="stMetricLabel"] { color: #4A5568; font-weight: 600; overflow: visible; }
    [data-testid="stMetricValue"] {
        color: #1B3A5C;
        white-space: normal;
        overflow: visible;
        text-overflow: unset;
        word-break: break-word;
        font-size: 1.5rem;
        line-height: 1.3;
    }
    .streamlit-expanderHeader {
        background: #F4F6F9;
        border-radius: 4px;
        font-weight: 600;
        color: #1B3A5C;
    }
    div[data-testid="stFileUploader"] {
        border: 1.5px dashed #C3CDDA;
        border-radius: 6px;
        padding: 4px;
        background: #FAFBFC;
    }
    .stButton button, .stDownloadButton button {
        background-color: #1B3A5C;
        color: #FFFFFF;
        border-radius: 4px;
        border: none;
        font-weight: 600;
    }
    .stButton button:hover, .stDownloadButton button:hover {
        background-color: #14293F;
        color: #FFFFFF;
    }
    hr { border-color: #E1E6ED; }
</style>
""", unsafe_allow_html=True)

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
    r"(^|[^a-z0-9])(id|code|number|no|num|uid|uuid|reference|ref)([^a-z0-9]|$)",
    re.IGNORECASE,
)

def looks_like_identifier(series: pd.Series, col_name: str) -> bool:
    """Heuristic: is this numeric column actually an ID, not a real metric?

    Name hints are the primary signal. As a fallback, only treat a column as
    an ID if its values look like a sequential code (e.g. 1, 2, 3...) —
    being "mostly unique" alone isn't enough, since real metrics like
    revenue or fuel volume are often unique per row too.
    """
    if ID_NAME_HINTS.search(str(col_name)):
        return True
    non_null = series.dropna()
    if len(non_null) < 6:
        return False
    is_integer_like = (non_null == non_null.round()).mean() > 0.98
    uniqueness = non_null.nunique() / len(non_null)
    if not (is_integer_like and uniqueness > 0.95):
        return False
    # Check whether sorted unique values increase in small, near-constant
    # steps — the signature of a sequential ID, not a real-world metric.
    sorted_vals = non_null.sort_values().unique()
    if len(sorted_vals) > 5:
        diffs = pd.Series(sorted_vals).diff().dropna()
        if len(diffs) > 0 and diffs.mean() > 0 and diffs.mean() <= 10 and diffs.std() < diffs.mean() * 0.5 + 0.5:
            return True
    return False


def looks_like_text_identifier(series: pd.Series, col_name: str) -> bool:
    """Heuristic: is this text column actually an ID/reference, not a real category?

    Real categories (Product, Region, Payment Method) repeat across many
    rows. IDs (Order_ID, Transaction_Ref) are almost always unique per row.
    """
    if ID_NAME_HINTS.search(str(col_name)):
        return True
    non_null = series.dropna().astype(str)
    if len(non_null) < 10:
        return False
    uniqueness = non_null.nunique() / len(non_null)
    return uniqueness > 0.9


def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Strip BOM markers and stray whitespace some CSV/Excel exports add to headers."""
    df = df.copy()
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
    return df


def read_any_file(uploaded_file):
    """Reads a CSV or Excel upload. Returns dict of {sheet_name: DataFrame}."""
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file, sep=None, engine="python", encoding="utf-8-sig")
        return {"Sheet1": clean_column_names(df)}
    else:
        xls = pd.ExcelFile(uploaded_file)
        return {sheet: clean_column_names(xls.parse(sheet)) for sheet in xls.sheet_names}


def fmt(n):
    try:
        return f"{n:,.1f}"
    except Exception:
        return str(n)


def trend_pct(df, col):
    """% change between the first half and second half of the file for this column."""
    half = len(df) // 2
    if half == 0:
        return 0.0
    first_half_mean = df[col].iloc[:half].mean()
    second_half_mean = df[col].iloc[half:].mean()
    if not first_half_mean:
        return 0.0
    return ((second_half_mean - first_half_mean) / first_half_mean) * 100


def top_bottom_by_category(df, num_col, cat_col):
    """Which category leads and which trails for this metric, and by how much.

    Uses min_count=1 so a category whose values are all missing shows up as
    NaN (and gets dropped) rather than silently being summed to a
    misleading 0 — which previously made real minimums look wrong.

    Also returns each leader's row count, since a summed total (e.g. one
    customer's total across all their orders) can legitimately exceed any
    single row's value — without the count, that looks like a contradiction.
    """
    stats = df.groupby(cat_col)[num_col].agg(total="sum", rows="count")
    stats = stats[stats["rows"] > 0].sort_values("total", ascending=False)
    if len(stats) == 0:
        return None
    top = stats.iloc[0]
    bottom = stats.iloc[-1]
    return {
        "top_name": stats.index[0],
        "top_value": top["total"],
        "top_rows": int(top["rows"]),
        "bottom_name": stats.index[-1],
        "bottom_value": bottom["total"],
        "bottom_rows": int(bottom["rows"]),
    }


def count_anomalies(df, col):
    """Simple outlier count: values more than 2.5 standard deviations from the mean."""
    series = df[col].dropna()
    if len(series) < 5 or series.std() == 0:
        return 0
    z_scores = (series - series.mean()).abs() / series.std()
    return int((z_scores > 2.5).sum())


GRANULARITY_FREQ = {
    "Daily": "D", "Weekly": "W", "Monthly": "ME",
    "Quarterly": "QE", "Yearly": "YE",
}


def auto_granularity(dates: pd.Series) -> str:
    """Pick a sensible default period based on how much date range the file covers."""
    non_null = dates.dropna()
    if len(non_null) < 2:
        return "Monthly"
    span_days = (non_null.max() - non_null.min()).days
    if span_days <= 31:
        return "Daily"
    elif span_days <= 180:
        return "Weekly"
    elif span_days <= 730:
        return "Monthly"
    elif span_days <= 365 * 6:
        return "Quarterly"
    else:
        return "Yearly"


def format_period(ts: pd.Timestamp, granularity: str) -> str:
    if granularity == "Daily":
        return ts.strftime("%b %d, %Y")
    elif granularity == "Weekly":
        return "week of " + ts.strftime("%b %d, %Y")
    elif granularity == "Monthly":
        return ts.strftime("%B %Y")
    elif granularity == "Quarterly":
        return f"Q{((ts.month - 1) // 3) + 1} {ts.year}"
    else:
        return str(ts.year)


def period_over_period_value(df, date_col, num_col, granularity, agg="Sum"):
    """Core calendar-period comparison logic. Returns a dict with the raw
    numbers and labels, or None if there isn't enough date range to compare
    two full periods. Both the metric-card tooltip and the Deep Analysis
    sentence are built from this same calculation, so they never disagree.
    """
    tmp = df[[date_col, num_col]].copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
    tmp = tmp.dropna(subset=[date_col])
    if len(tmp) < 2:
        return None
    max_actual_date = tmp[date_col].max()
    freq = GRANULARITY_FREQ[granularity]
    grouped = tmp.set_index(date_col).resample(freq)[num_col]
    grouped = grouped.sum() if agg == "Sum" else grouped.mean()
    grouped = grouped.dropna()
    if len(grouped) < 2:
        return None

    partial = False
    if granularity != "Daily" and len(grouped) >= 1:
        last_period_end = grouped.index[-1]
        if last_period_end > max_actual_date:
            if len(grouped) >= 3:
                grouped = grouped.iloc[:-1]
                partial = True
            else:
                partial = True  # can't drop, only 2 periods — flag it instead

    if len(grouped) < 2:
        return None
    latest_val, previous_val = grouped.iloc[-1], grouped.iloc[-2]
    latest_label = format_period(grouped.index[-1], granularity)
    previous_label = format_period(grouped.index[-2], granularity)
    if not previous_val:
        return None
    change = ((latest_val - previous_val) / previous_val) * 100
    return {
        "change": change,
        "latest_val": latest_val,
        "previous_val": previous_val,
        "latest_label": latest_label,
        "previous_label": previous_label,
        "partial": partial,
        "granularity": granularity,
        "agg": agg,
    }


def period_over_period(df, date_col, num_col, granularity, agg="Sum"):
    """Real calendar-period comparison, e.g. 'August 2026 vs July 2026'.

    Returns a plain-language sentence, or None if there isn't enough date
    range to compare two full periods. Excludes the most recent period from
    the comparison if it's only partially covered by data (e.g. the file
    ends mid-month) — otherwise a partial period looks like a fake decline.
    """
    pv = period_over_period_value(df, date_col, num_col, granularity, agg)
    if not pv:
        return None
    partial_note = ""
    if pv["partial"]:
        partial_note = " (most recent, still-in-progress period excluded from this comparison)"
    direction = "up" if pv["change"] > 1 else "down" if pv["change"] < -1 else "flat"
    return (
        f"{pv['latest_label']} vs {pv['previous_label']}: {fmt(pv['latest_val'])} vs {fmt(pv['previous_val'])} "
        f"({pv['change']:+.1f}%, {direction}){partial_note}"
    )

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

    category_cols_raw = [
        c for c in df.columns
        if c not in raw_numeric_cols and c not in date_cols
    ]
    text_id_cols = [c for c in category_cols_raw if looks_like_text_identifier(df[c], c)]
    category_cols = [c for c in category_cols_raw if c not in text_id_cols]

    all_id_cols = id_cols + text_id_cols
    if all_id_cols:
        st.caption(f"Excluded as likely ID/reference columns (not real metrics or categories): {', '.join(all_id_cols)}")

    # ---- Instant overview: totals & averages for every real numeric column ----
    # Shown immediately, before any focus is picked, so you see the full
    # picture of what's in the file first.
    if numeric_cols:
        st.subheader("Overview")
        overview_rows = []
        for c in numeric_cols:
            overview_rows.append({
                "Column": c,
                "Total": fmt(df[c].sum()),
                "Average": fmt(df[c].mean()),
                "Min": fmt(df[c].min()),
                "Max": fmt(df[c].max()),
            })
        st.dataframe(pd.DataFrame(overview_rows), hide_index=True, use_container_width=True)

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

    # ---- Shared time-period control (used by Key Metrics, Deep Analysis, and the Trend chart) ----
    chosen_granularity = None
    chosen_agg = "Sum"
    if len(date_cols) > 1:
        primary_date_col = st.selectbox(
            "Which date column should drive the trend analysis?", date_cols, key="primary_date_col"
        )
    else:
        primary_date_col = date_cols[0] if date_cols else None
    if primary_date_col:
        st.subheader("Time period")
        auto_label = auto_granularity(pd.to_datetime(df[primary_date_col], errors="coerce"))
        granularity_choice = st.selectbox(
            "Group dates by",
            ["Auto (recommended)"] + list(GRANULARITY_FREQ.keys()),
            index=0,
            key="date_granularity_choice",
        )
        chosen_granularity = auto_label if granularity_choice == "Auto (recommended)" else granularity_choice
        if granularity_choice == "Auto (recommended)":
            st.caption(f"Automatically grouped by **{chosen_granularity}**, based on the date range in your file.")
        chosen_agg = st.radio(
            "Aggregate as", ["Sum", "Average"], horizontal=True, key="date_agg_choice",
            help="Sum makes sense for things like revenue or flights. Average makes more sense for rates or percentages.",
        )

    # ---- Key metrics ----
    if focus_numeric_cols:
        st.subheader("Key metrics")
        cols = st.columns(min(3, len(focus_numeric_cols)))
        for i, col in enumerate(focus_numeric_cols[:3]):
            total = df[col].sum()

            # Prefer a real calendar-period comparison; fall back to the
            # cruder first-half/second-half trend if there's no usable date.
            pv = None
            if primary_date_col and chosen_granularity:
                pv = period_over_period_value(df, primary_date_col, col, chosen_granularity, chosen_agg)

            if pv:
                pct_change = pv["change"]
                tooltip = (
                    f"{pv['granularity']} comparison ({pv['agg'].lower()}): "
                    f"{pv['latest_label']} = {fmt(pv['latest_val'])}, "
                    f"{pv['previous_label']} = {fmt(pv['previous_val'])}."
                )
                if pv["partial"]:
                    tooltip += " Most recent, still-in-progress period was excluded from this comparison."
            else:
                pct_change = trend_pct(df, col)
                tooltip = (
                    "No usable date column to compare real time periods, so this compares the "
                    "average of the first half of the file's rows to the average of the second half."
                )

            with cols[i]:
                st.metric(col, fmt(total), f"{pct_change:+.1f}%", help=tooltip)
    else:
        st.info("No numeric metric columns detected for this focus.")

    # ---- Deep analysis per focus metric ----
    st.subheader("Deep analysis")
    metric_trends = {}  # col -> pct_change, used later for the relationship section
    for col in focus_numeric_cols:
        total = df[col].sum()
        mean = df[col].mean()
        anomaly_count = count_anomalies(df, col)

        with st.expander(f"**{col}**", expanded=True):
            bullets = [
                f"Total: {fmt(total)}, average {fmt(mean)} per record "
                f"(range {fmt(df[col].min())}\u2013{fmt(df[col].max())}).",
            ]

            # Prefer a real calendar-period comparison when we have dates;
            # fall back to the cruder first-half/second-half trend otherwise.
            pop_text = None
            if primary_date_col and chosen_granularity:
                pop_text = period_over_period(df, primary_date_col, col, chosen_granularity, chosen_agg)

            if pop_text:
                bullets.append(pop_text)
                # Still track a rough trend number for the relationships section
                pct_change = trend_pct(df, col)
                metric_trends[col] = pct_change
            else:
                pct_change = trend_pct(df, col)
                metric_trends[col] = pct_change
                if abs(pct_change) > 1:
                    direction = "trending up" if pct_change > 0 else "trending down"
                    bullets.append(
                        f"{direction.capitalize()}, roughly {fmt(abs(pct_change))}% "
                        f"{'increase' if pct_change > 0 else 'decrease'} comparing the first half of the "
                        f"file's rows to the second half (no usable date column to compare real periods)."
                    )
                else:
                    bullets.append("Fairly flat across the file (no meaningful trend detected).")

            # Top / bottom contributor by the first relevant category column
            if focus_category_cols:
                tb = top_bottom_by_category(df, col, focus_category_cols[0])
                if tb and tb["top_name"] != tb["bottom_name"]:
                    top_rows_note = f" across {tb['top_rows']} record{'s' if tb['top_rows'] != 1 else ''}" if tb["top_rows"] > 1 else ""
                    bottom_rows_note = f" across {tb['bottom_rows']} record{'s' if tb['bottom_rows'] != 1 else ''}" if tb["bottom_rows"] > 1 else ""
                    bullets.append(
                        f"By **{focus_category_cols[0]}**: \"{tb['top_name']}\" leads with {fmt(tb['top_value'])} total"
                        f"{top_rows_note}, while \"{tb['bottom_name']}\" trails at {fmt(tb['bottom_value'])}"
                        f"{bottom_rows_note}."
                    )

            if anomaly_count > 0:
                bullets.append(
                    f"⚠️ {anomaly_count} unusual value{'s' if anomaly_count != 1 else ''} detected "
                    f"(far outside the normal range for this column) — worth a manual look."
                )

            for b in bullets:
                st.markdown(f"- {b}")

    # ---- Relationships between selected metrics ----
    if len(focus_numeric_cols) > 1:
        st.subheader("Relationships between what you selected")
        rel_lines = []
        for col, pct in metric_trends.items():
            rel_lines.append(f"**{col}** moved {pct:+.1f}%")
        rel_lines_sorted = sorted(metric_trends.items(), key=lambda x: x[1], reverse=True)
        fastest = rel_lines_sorted[0]
        slowest = rel_lines_sorted[-1]
        st.markdown(
            f"- Across your selected metrics: {', '.join(rel_lines)} over the file's timespan.\n"
            f"- **{fastest[0]}** grew fastest ({fastest[1]:+.1f}%), while **{slowest[0]}** grew slowest or declined most ({slowest[1]:+.1f}%). "
            f"That gap is often where the real story is — e.g. costs rising faster than revenue, or flights increasing without matching profit."
        )
        if focus_category_cols:
            cat_col = focus_category_cols[0]
            leaders = {col: top_bottom_by_category(df, col, cat_col) for col in focus_numeric_cols}
            leader_names = {col: tb["top_name"] for col, tb in leaders.items() if tb}
            unique_leaders = set(leader_names.values())
            if len(unique_leaders) == 1:
                only_leader = list(unique_leaders)[0]
                st.markdown(f"- \"{only_leader}\" leads on every selected metric by {cat_col} — a consistent top performer.")
            elif len(unique_leaders) > 1:
                breakdown_text = "; ".join(f"{col} → \"{name}\"" for col, name in leader_names.items())
                st.markdown(f"- Leadership by {cat_col} differs by metric: {breakdown_text}. No single category dominates everything.")

    # ---- Trend chart ----
    if date_cols and focus_numeric_cols and chosen_granularity:
        st.subheader("Trend over time")
        trend_chart_type = st.radio(
            "Chart type", ["Line", "Area", "Bar"], horizontal=True, key="trend_chart_type"
        )

        date_col = date_cols[0]
        num_col = focus_numeric_cols[0]
        chart_df = df[[date_col, num_col]].copy()
        chart_df[date_col] = pd.to_datetime(chart_df[date_col], errors="coerce")
        chart_df = chart_df.dropna(subset=[date_col]).sort_values(date_col)

        if len(chart_df) > 1:
            freq = GRANULARITY_FREQ[chosen_granularity]
            grouped = chart_df.set_index(date_col).resample(freq)[num_col]
            grouped = grouped.sum() if chosen_agg == "Sum" else grouped.mean()
            grouped = grouped.reset_index()

            if len(grouped) < 2:
                st.info(
                    f"Not enough date range in this file to show a {chosen_granularity.lower()} trend — "
                    "try a finer grouping above (e.g. Daily or Weekly)."
                )
            else:
                if trend_chart_type == "Line":
                    fig = px.line(grouped, x=date_col, y=num_col, markers=len(grouped) <= 60)
                elif trend_chart_type == "Area":
                    fig = px.area(grouped, x=date_col, y=num_col)
                else:
                    fig = px.bar(grouped, x=date_col, y=num_col)
                st.plotly_chart(fig, use_container_width=True)

    # ---- Breakdown chart ----
    if focus_category_cols:
        st.subheader("Breakdown")
        col_a, col_b = st.columns([2, 3])
        with col_a:
            if len(focus_category_cols) > 1:
                cat_col = st.selectbox("Break down by", focus_category_cols, key="breakdown_cat_col")
            else:
                cat_col = focus_category_cols[0]
                st.caption(f"Breaking down by **{cat_col}**")
        with col_b:
            if len(focus_numeric_cols) > 1:
                metric_for_breakdown = st.selectbox(
                    "Measured by", focus_numeric_cols, key="breakdown_metric_col"
                )
            elif focus_numeric_cols:
                metric_for_breakdown = focus_numeric_cols[0]
            else:
                metric_for_breakdown = None

        breakdown_chart_type = st.radio(
            "Chart type", ["Bar", "Horizontal bar", "Pie", "Donut", "Line"],
            horizontal=True, key="breakdown_chart_type"
        )
        if metric_for_breakdown:
            breakdown = df.groupby(cat_col)[metric_for_breakdown].sum().sort_values(ascending=False).head(10)
            y_label = metric_for_breakdown
        else:
            breakdown = df[cat_col].value_counts().head(10)
            y_label = "Count"
        breakdown_df = breakdown.reset_index()
        breakdown_df.columns = [cat_col, y_label]

        if breakdown_chart_type == "Bar":
            fig2 = px.bar(breakdown_df, x=cat_col, y=y_label)
        elif breakdown_chart_type == "Horizontal bar":
            fig2 = px.bar(breakdown_df.sort_values(y_label), x=y_label, y=cat_col, orientation="h")
        elif breakdown_chart_type == "Pie":
            fig2 = px.pie(breakdown_df, values=y_label, names=cat_col)
        elif breakdown_chart_type == "Donut":
            fig2 = px.pie(breakdown_df, values=y_label, names=cat_col, hole=0.5)
        else:  # Line
            fig2 = px.line(breakdown_df, x=cat_col, y=y_label, markers=True)
        st.plotly_chart(fig2, use_container_width=True)

    # ---- Relationship scatter ----
    if len(numeric_cols) >= 2:
        st.subheader("Relationship between two metrics")
        st.caption("Not limited to your focus picks — compare any two numeric columns in the file.")
        col_x = st.selectbox("X axis", numeric_cols, index=0, key="scatter_x")
        col_y = st.selectbox(
            "Y axis", numeric_cols, index=1 if len(numeric_cols) > 1 else 0, key="scatter_y"
        )
        color_arg = focus_category_cols[0] if focus_category_cols else None
        fig3 = px.scatter(df, x=col_x, y=col_y, color=color_arg, trendline=None)
        st.plotly_chart(fig3, use_container_width=True)

    # ---- Distribution (histogram) for any numeric column ----
    if numeric_cols:
        with st.expander("Distribution of a column (histogram)"):
            hist_col = st.selectbox("Column", numeric_cols, key="hist_col")
            fig4 = px.histogram(df, x=hist_col, nbins=30)
            st.plotly_chart(fig4, use_container_width=True)

    # ---- Closing note ----

# ---------------------------------------------------------
# NOTES
# ---------------------------------------------------------
# - Usage tracking is now per-session (st.session_state), so visitors no
#   longer share one global counter. It still resets on refresh/new tab —
#   a persistent per-visitor limit needs a small database next.
# - Excel support requires openpyxl (see requirements.txt).
