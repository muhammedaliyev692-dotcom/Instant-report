import re
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(
    page_title="Instant Report",
    page_icon="📊",
    layout="wide",
)

# ---------------------------------------------------------
# HELPERS: FILE READING
# ---------------------------------------------------------
def read_csv_safely(file_bytes: bytes) -> pd.DataFrame:
    """Read a CSV and auto-detect the delimiter where possible."""
    try:
        return pd.read_csv(BytesIO(file_bytes), sep=None, engine="python")
    except Exception:
        return pd.read_csv(BytesIO(file_bytes))


def read_excel_sheet(file_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name)


# ---------------------------------------------------------
# HELPERS: COLUMN UNDERSTANDING
# ---------------------------------------------------------
ID_NAME_PATTERNS = [
    r"(^|[_\s-])id($|[_\s-])",
    r"(^|[_\s-])uuid($|[_\s-])",
    r"(^|[_\s-])code($|[_\s-])",
    r"(^|[_\s-])ref($|[_\s-])",
    r"reference",
    r"flight[ _-]*(no|num|number)",
    r"invoice[ _-]*(no|num|number)",
    r"order[ _-]*(no|num|number)",
    r"booking[ _-]*(no|num|number)",
]

DATE_NAME_HINTS = (
    "date",
    "time",
    "day",
    "month",
    "year",
    "timestamp",
    "created",
    "updated",
    "departure",
    "arrival",
)

AVERAGE_NAME_HINTS = (
    "age",
    "rate",
    "ratio",
    "margin",
    "percent",
    "percentage",
    "%",
    "score",
    "price",
    "temperature",
    "duration",
    "time",
    "load factor",
    "yield",
    "intensity",
)


def looks_like_id(col_name: str) -> bool:
    name = str(col_name).strip().lower()
    return any(re.search(pattern, name) for pattern in ID_NAME_PATTERNS)


def try_parse_date(series: pd.Series, col_name: str):
    """Return parsed dates if the column strongly looks like a date; otherwise None."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")

    # Do not try converting ordinary numeric columns to timestamps.
    if pd.api.types.is_numeric_dtype(series):
        return None

    non_null = series.dropna()
    if non_null.empty:
        return None

    name = str(col_name).lower()
    has_name_hint = any(hint in name for hint in DATE_NAME_HINTS)

    # Avoid treating short categorical/text columns as dates unless the name strongly suggests it.
    if not has_name_hint and non_null.nunique() <= 12:
        return None

    try:
        parsed = pd.to_datetime(series, errors="coerce")
    except Exception:
        return None

    success_rate = parsed.notna().mean()
    threshold = 0.60 if has_name_hint else 0.85
    return parsed if success_rate >= threshold else None


def try_parse_numeric(series: pd.Series):
    """Convert numeric-looking text such as '$1,250' or '12%' to numbers."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    non_null = series.dropna()
    if non_null.empty:
        return None

    text = series.astype("string")
    cleaned = (
        text.str.strip()
        .str.replace(r"[€$£₼¥]", "", regex=True)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(r"\s+", "", regex=True)
    )
    numeric = pd.to_numeric(cleaned, errors="coerce")

    if numeric.notna().mean() >= 0.85:
        return numeric
    return None


def prepare_dataframe(raw_df: pd.DataFrame):
    """Clean column names and infer useful analytical roles."""
    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Remove completely empty rows/columns only. We do not otherwise alter client data.
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")

    date_cols = []
    id_cols = []
    numeric_cols = []
    category_cols = []

    # 1) Dates first
    for col in df.columns:
        parsed_date = try_parse_date(df[col], col)
        if parsed_date is not None:
            df[col] = parsed_date
            date_cols.append(col)

    # 2) IDs, numeric metrics, and categories
    for col in df.columns:
        if col in date_cols:
            continue

        if looks_like_id(col):
            id_cols.append(col)
            continue

        parsed_numeric = try_parse_numeric(df[col])
        if parsed_numeric is not None:
            df[col] = parsed_numeric
            numeric_cols.append(col)
            continue

        category_cols.append(col)

    return df, {
        "numeric": numeric_cols,
        "date": date_cols,
        "category": category_cols,
        "id": id_cols,
    }


def aggregation_for_metric(metric: str) -> str:
    name = str(metric).lower()
    if any(hint in name for hint in AVERAGE_NAME_HINTS):
        return "mean"
    return "sum"


def format_number(value) -> str:
    if pd.isna(value):
        return "—"
    value = float(value)
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:,.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:,.2f}K"
    return f"{value:,.2f}"


def suitable_category_columns(df: pd.DataFrame, category_cols: list[str]) -> list[str]:
    result = []
    rows = max(len(df), 1)
    for col in category_cols:
        unique = df[col].nunique(dropna=True)
        # Good for grouping; avoids free-text fields with almost one value per row.
        if 2 <= unique <= 50 and unique / rows <= 0.50:
            result.append(col)
    return result


# ---------------------------------------------------------
# HELPERS: TIME ANALYSIS
# ---------------------------------------------------------
def make_time_series(df: pd.DataFrame, date_col: str, metric: str):
    temp = df[[date_col, metric]].dropna().copy()
    if temp.empty:
        return None, None, None

    temp[date_col] = pd.to_datetime(temp[date_col], errors="coerce")
    temp = temp.dropna(subset=[date_col])
    if temp.empty:
        return None, None, None

    span_days = max((temp[date_col].max() - temp[date_col].min()).days, 0)
    if span_days > 730:
        freq, label = "Y", "Year"
    elif span_days > 120:
        freq, label = "M", "Month"
    elif span_days > 35:
        freq, label = "W", "Week"
    else:
        freq, label = "D", "Day"

    agg = aggregation_for_metric(metric)
    temp["Period"] = temp[date_col].dt.to_period(freq).dt.to_timestamp()
    grouped = temp.groupby("Period", as_index=False)[metric].agg(agg)

    pct_change = None
    if len(grouped) >= 2:
        prev = grouped[metric].iloc[-2]
        latest = grouped[metric].iloc[-1]
        if pd.notna(prev) and prev != 0:
            pct_change = (latest - prev) / abs(prev) * 100

    return grouped, pct_change, label


# ---------------------------------------------------------
# HELPERS: REPORT CONTENT
# ---------------------------------------------------------
def metric_summary_table(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    rows = []
    for metric in metrics:
        s = pd.to_numeric(df[metric], errors="coerce")
        if s.notna().sum() == 0:
            continue
        rows.append(
            {
                "Metric": metric,
                "Total": s.sum(),
                "Average": s.mean(),
                "Median": s.median(),
                "Minimum": s.min(),
                "Maximum": s.max(),
                "Missing": int(s.isna().sum()),
            }
        )
    return pd.DataFrame(rows)


def build_findings(
    df: pd.DataFrame,
    selected_metrics: list[str],
    selected_categories: list[str],
    date_cols: list[str],
    good_categories: list[str],
):
    findings = []

    for metric in selected_metrics[:3]:
        s = pd.to_numeric(df[metric], errors="coerce")
        if s.notna().sum() == 0:
            continue

        agg_word = "average" if aggregation_for_metric(metric) == "mean" else "total"
        base_value = s.mean() if agg_word == "average" else s.sum()
        findings.append(f"**{metric}** has an overall {agg_word} of **{format_number(base_value)}**.")

        if date_cols:
            _, pct_change, period_label = make_time_series(df, date_cols[0], metric)
            if pct_change is not None:
                direction = "increased" if pct_change > 0 else "decreased" if pct_change < 0 else "was unchanged"
                findings.append(
                    f"In the latest {period_label.lower()} comparison, **{metric} {direction} by {abs(pct_change):.1f}%**."
                )

        cat = selected_categories[0] if selected_categories else (good_categories[0] if good_categories else None)
        if cat:
            temp = df[[cat, metric]].dropna()
            if not temp.empty:
                agg = aggregation_for_metric(metric)
                grouped = temp.groupby(cat)[metric].agg(agg).sort_values(ascending=False)
                if not grouped.empty:
                    findings.append(
                        f"By **{cat}**, **{grouped.index[0]}** has the highest {metric.lower()} ({format_number(grouped.iloc[0])})."
                    )

    if not selected_metrics:
        for cat in selected_categories[:2]:
            counts = df[cat].value_counts(dropna=True)
            if not counts.empty:
                findings.append(
                    f"The most common **{cat}** is **{counts.index[0]}**, appearing in **{counts.iloc[0]:,}** records."
                )

    missing = int(df.isna().sum().sum())
    duplicates = int(df.duplicated().sum())
    if missing:
        findings.append(f"The dataset contains **{missing:,} missing cells** that may affect some calculations.")
    if duplicates:
        findings.append(f"The dataset contains **{duplicates:,} duplicate rows** worth reviewing.")

    return findings[:7]


def render_report(raw_df: pd.DataFrame, label: str, key_prefix: str):
    df, roles = prepare_dataframe(raw_df)

    if df.empty or len(df.columns) == 0:
        st.warning(f"{label} does not contain usable data.")
        return

    numeric_cols = roles["numeric"]
    date_cols = roles["date"]
    category_cols = roles["category"]
    id_cols = roles["id"]
    good_categories = suitable_category_columns(df, category_cols)

    st.markdown(f"## {label}")
    st.caption(f"{len(df):,} rows • {len(df.columns)} columns")

    # ---- Detected structure ----
    with st.expander("Detected structure", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown("**Numeric metrics**")
        c1.write(", ".join(numeric_cols) if numeric_cols else "None detected")
        c2.markdown("**Dates**")
        c2.write(", ".join(date_cols) if date_cols else "None detected")
        c3.markdown("**Categories**")
        c3.write(", ".join(category_cols) if category_cols else "None detected")
        c4.markdown("**IDs / references**")
        c4.write(", ".join(id_cols) if id_cols else "None detected")

    # ---- Focus selection ----
    focus_options = ["Overall overview"] + numeric_cols + good_categories
    if len(focus_options) == 1:
        focus_options += category_cols[:10]

    selected_focus = st.multiselect(
        "What would you like this report to focus on?",
        options=focus_options,
        default=["Overall overview"],
        max_selections=3,
        key=f"{key_prefix}_focus",
        help="Instant Report created these options from the columns it detected in your file.",
    )

    if not selected_focus:
        st.info("Choose at least one focus area to generate the report.")
        return

    overall = "Overall overview" in selected_focus
    selected_metrics = [x for x in selected_focus if x in numeric_cols]
    selected_categories = [x for x in selected_focus if x in category_cols]

    if overall:
        # Keep any explicit choices and supplement them with a few detected metrics.
        explicit_metrics = selected_metrics.copy()
        selected_metrics = explicit_metrics + [m for m in numeric_cols if m not in explicit_metrics]
        selected_metrics = selected_metrics[:4]
        if not selected_categories and good_categories:
            selected_categories = [good_categories[0]]

    # ---- Data Quality ----
    missing_cells = int(df.isna().sum().sum())
    duplicate_rows = int(df.duplicated().sum())
    quality_cols = st.columns(4)
    quality_cols[0].metric("Rows", f"{len(df):,}")
    quality_cols[1].metric("Columns", f"{len(df.columns):,}")
    quality_cols[2].metric("Missing cells", f"{missing_cells:,}")
    quality_cols[3].metric("Duplicate rows", f"{duplicate_rows:,}")

    # ---- KPI Cards ----
    if selected_metrics:
        st.subheader("Key metrics")
        card_cols = st.columns(min(4, len(selected_metrics)))
        for i, metric in enumerate(selected_metrics[:4]):
            s = pd.to_numeric(df[metric], errors="coerce")
            agg = aggregation_for_metric(metric)
            value = s.mean() if agg == "mean" else s.sum()
            delta = None
            if date_cols:
                _, pct_change, _ = make_time_series(df, date_cols[0], metric)
                if pct_change is not None:
                    delta = f"{pct_change:+.1f}% vs previous period"
            card_cols[i].metric(metric, format_number(value), delta)

        details = metric_summary_table(df, selected_metrics)
        if not details.empty:
            display_details = details.copy()
            for col in ["Total", "Average", "Median", "Minimum", "Maximum"]:
                display_details[col] = display_details[col].map(format_number)
            st.dataframe(display_details, use_container_width=True, hide_index=True)

    # ---- Key Findings ----
    findings = build_findings(
        df,
        selected_metrics,
        selected_categories,
        date_cols,
        good_categories,
    )
    if findings:
        st.subheader("Key findings")
        for finding in findings:
            st.markdown(f"- {finding}")

    # ---- Trend ----
    if date_cols and selected_metrics:
        st.subheader("Trend over time")
        metric_for_trend = selected_metrics[0]
        time_df, pct_change, period_label = make_time_series(df, date_cols[0], metric_for_trend)
        if time_df is not None and len(time_df) > 1:
            fig = px.line(
                time_df,
                x="Period",
                y=metric_for_trend,
                markers=True,
                title=f"{metric_for_trend} by {period_label.lower()}",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Not enough dated records to build a meaningful trend chart.")

    # ---- Breakdown ----
    category_for_breakdown = selected_categories[0] if selected_categories else (good_categories[0] if good_categories else None)
    if category_for_breakdown:
        st.subheader(f"Breakdown by {category_for_breakdown}")

        if selected_metrics:
            metric = selected_metrics[0]
            agg = aggregation_for_metric(metric)
            breakdown_full = (
                df[[category_for_breakdown, metric]]
                .dropna()
                .groupby(category_for_breakdown)[metric]
                .agg(agg)
                .sort_values(ascending=False)
            )
            breakdown = breakdown_full.head(10).reset_index()
            if not breakdown.empty:
                fig = px.bar(
                    breakdown,
                    x=category_for_breakdown,
                    y=metric,
                    title=f"Top {category_for_breakdown} by {metric}",
                )
                st.plotly_chart(fig, use_container_width=True)

                if len(breakdown_full) >= 2:
                    top_name, top_value = breakdown_full.index[0], breakdown_full.iloc[0]
                    bottom_name, bottom_value = breakdown_full.index[-1], breakdown_full.iloc[-1]
                    c1, c2 = st.columns(2)
                    c1.success(f"Top: {top_name} — {format_number(top_value)}")
                    c2.info(f"Bottom: {bottom_name} — {format_number(bottom_value)}")
        else:
            counts = (
                df[category_for_breakdown]
                .value_counts(dropna=True)
                .head(10)
                .rename_axis(category_for_breakdown)
                .reset_index(name="Records")
            )
            if not counts.empty:
                fig = px.bar(
                    counts,
                    x=category_for_breakdown,
                    y="Records",
                    title=f"Records by {category_for_breakdown}",
                )
                st.plotly_chart(fig, use_container_width=True)

    # ---- Data quality detail ----
    st.subheader("Data quality")
    quality_messages = []
    if missing_cells == 0:
        quality_messages.append("No missing cells detected.")
    else:
        quality_messages.append(f"{missing_cells:,} missing cells detected.")
    if duplicate_rows == 0:
        quality_messages.append("No duplicate rows detected.")
    else:
        quality_messages.append(f"{duplicate_rows:,} duplicate rows detected.")

    if date_cols:
        valid_dates = pd.to_datetime(df[date_cols[0]], errors="coerce").dropna()
        if not valid_dates.empty:
            quality_messages.append(
                f"Date coverage: {valid_dates.min().date()} to {valid_dates.max().date()} using '{date_cols[0]}'."
            )

    for message in quality_messages:
        st.write(f"• {message}")

    with st.expander("Preview data", expanded=False):
        st.dataframe(df.head(100), use_container_width=True)


# ---------------------------------------------------------
# APP HEADER
# ---------------------------------------------------------
st.title("📊 Instant Report")
st.write(
    "Upload a CSV or Excel workbook. Instant Report will inspect the data, "
    "suggest useful focus areas, and build a report around what you choose."
)
st.caption("Prototype mode: payments and account limits are intentionally disabled while the reporting engine is being tested.")

# ---------------------------------------------------------
# FILE UPLOAD
# ---------------------------------------------------------
uploaded = st.file_uploader(
    "Upload a CSV or Excel file",
    type=["csv", "xlsx"],
    help="CSV and .xlsx files are supported in this version.",
)

if uploaded is None:
    st.info("Upload a file to start.")
    st.stop()

file_bytes = uploaded.getvalue()
file_name = uploaded.name
extension = file_name.rsplit(".", 1)[-1].lower()

# ---------------------------------------------------------
# CSV FLOW
# ---------------------------------------------------------
if extension == "csv":
    try:
        csv_df = read_csv_safely(file_bytes)
    except Exception as exc:
        st.error(f"Couldn't read this CSV file: {exc}")
        st.stop()

    st.success(f"File loaded: {file_name}")
    render_report(csv_df, "CSV report", "csv")

# ---------------------------------------------------------
# EXCEL FLOW
# ---------------------------------------------------------
elif extension == "xlsx":
    try:
        excel_file = pd.ExcelFile(BytesIO(file_bytes))
        sheet_names = excel_file.sheet_names
    except Exception as exc:
        st.error(f"Couldn't read this Excel workbook: {exc}")
        st.stop()

    st.success(f"Workbook loaded: {file_name} — {len(sheet_names)} sheet(s) found")

    # Lightweight workbook overview before the user chooses sheets.
    overview_rows = []
    for sheet in sheet_names:
        try:
            temp = read_excel_sheet(file_bytes, sheet)
            overview_rows.append(
                {
                    "Sheet": sheet,
                    "Rows": len(temp),
                    "Columns": len(temp.columns),
                }
            )
        except Exception:
            overview_rows.append({"Sheet": sheet, "Rows": "—", "Columns": "—"})

    st.subheader("Workbook overview")
    st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)

    selected_sheets = st.multiselect(
        "Which sheet(s) would you like to analyze?",
        options=sheet_names,
        default=[sheet_names[0]] if sheet_names else [],
        key="selected_sheets",
        help="You can analyze several sheets in one run. In this version they are analyzed separately; automatic sheet merging will come later if users actually need it.",
    )

    if not selected_sheets:
        st.info("Choose at least one worksheet to continue.")
        st.stop()

    for index, sheet in enumerate(selected_sheets):
        if index > 0:
            st.divider()
        try:
            sheet_df = read_excel_sheet(file_bytes, sheet)
        except Exception as exc:
            st.error(f"Couldn't read sheet '{sheet}': {exc}")
            continue

        render_report(sheet_df, f"Sheet: {sheet}", f"sheet_{index}_{sheet}")

else:
    st.error("Unsupported file type. Please upload CSV or XLSX.")
