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
    """Which category leads and which trails for this metric, and by how much."""
    grouped = df.groupby(cat_col)[num_col].sum().sort_values(ascending=False)
    if len(grouped) == 0:
        return None
    return {
        "top_name": grouped.index[0],
        "top_value": grouped.iloc[0],
        "bottom_name": grouped.index[-1],
        "bottom_value": grouped.iloc[-1],
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


def period_over_period(df, date_col, num_col, granularity, agg="Sum"):
    """Real calendar-period comparison, e.g. 'August 2026 vs July 2026'.

    Returns a plain-language sentence, or None if there isn't enough date
    range to compare two full periods. Excludes the most recent period from
    the comparison if it's only partially covered by data (e.g. the file
    ends mid-month) — otherwise a partial period looks like a fake decline.
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

    partial_note = ""
    if granularity != "Daily" and len(grouped) >= 1:
        last_period_end = grouped.index[-1]
        if last_period_end > max_actual_date:
            if len(grouped) >= 3:
                # Drop the incomplete trailing period and compare the two full ones before it
                grouped = grouped.iloc[:-1]
                partial_note = " (most recent, still-in-progress period excluded from this comparison)"
            else:
                partial_note = " — note: the most recent period is still in progress, so this may look skewed"

    if len(grouped) < 2:
        return None
    latest_val, previous_val = grouped.iloc[-1], grouped.iloc[-2]
    latest_label = format_period(grouped.index[-1], granularity)
    previous_label = format_period(grouped.index[-2], granularity)
    if not previous_val:
        return None
    change = ((latest_val - previous_val) / previous_val) * 100
    direction = "up" if change > 1 else "down" if change < -1 else "flat"
    return (
        f"{latest_label} vs {previous_label}: {fmt(latest_val)} vs {fmt(previous_val)} "
        f"({change:+.1f}%, {direction}){partial_note}"
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
            pct_change = trend_pct(df, col)
            with cols[i]:
                st.metric(col, fmt(total), f"{pct_change:+.1f}%")
    else:
        st.info("No numeric metric columns detected for this focus.")

    # ---- Shared time-period control (used by Deep Analysis + Trend chart) ----
    chosen_granularity = None
    chosen_agg = "Sum"
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
                    bullets.append(
                        f"By **{focus_category_cols[0]}**: \"{tb['top_name']}\" leads with {fmt(tb['top_value'])}, "
                        f"while \"{tb['bottom_name']}\" trails at {fmt(tb['bottom_value'])}."
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
        st.subheader(f"Breakdown by {focus_category_cols[0]}")
        breakdown_chart_type = st.radio(
            "Chart type", ["Bar", "Horizontal bar", "Pie", "Donut", "Line"],
            horizontal=True, key="breakdown_chart_type"
        )
        cat_col = focus_category_cols[0]
        if focus_numeric_cols:
            breakdown = df.groupby(cat_col)[focus_numeric_cols[0]].sum().sort_values(ascending=False).head(10)
            y_label = focus_numeric_cols[0]
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

    # ---- Relationship scatter (only when 2+ metrics are selected) ----
    if len(focus_numeric_cols) >= 2:
        st.subheader("Relationship between two metrics")
        col_x = st.selectbox("X axis", focus_numeric_cols, index=0, key="scatter_x")
        col_y = st.selectbox("Y axis", focus_numeric_cols, index=1, key="scatter_y")
        color_arg = focus_category_cols[0] if focus_category_cols else None
        fig3 = px.scatter(df, x=col_x, y=col_y, color=color_arg, trendline=None)
        st.plotly_chart(fig3, use_container_width=True)

    # ---- Distribution (histogram) for any selected metric ----
    if focus_numeric_cols:
        with st.expander("Distribution of a metric (histogram)"):
            hist_col = st.selectbox("Column", focus_numeric_cols, key="hist_col")
            fig4 = px.histogram(df, x=hist_col, nbins=30)
            st.plotly_chart(fig4, use_container_width=True)

    # ---- Closing note ----
    if id_cols:
        st.caption(f"Columns treated as identifiers, not metrics: {', '.join(id_cols)}")

# ---------------------------------------------------------
# NOTES
# ---------------------------------------------------------
# - Usage tracking is now per-session (st.session_state), so visitors no
#   longer share one global counter. It still resets on refresh/new tab —
#   a persistent per-visitor limit needs a small database next.
# - Excel support requires openpyxl (see requirements.txt).
