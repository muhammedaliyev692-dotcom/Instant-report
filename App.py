import streamlit as st
import pandas as pd
import plotly.express as px
import re
import pycountry

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


# ---------------------------------------------------------
# ANALYTICAL CLASSIFICATION (deterministic, no AI/LLM)
# ---------------------------------------------------------
# The core principle: don't calculate something just because it's
# mathematically possible. A column's business meaning determines whether
# it should be summed, averaged, or left alone (percentages, identifiers).
def _norm(name) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()
    return f" {s} "


def _has_kw(norm: str, keyword: str) -> bool:
    kw = re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip()
    return f" {kw} " in norm


# Ordered from most specific (multi-word) to least specific. First match
# wins, so "total cost" is caught before the bare "cost" check would
# otherwise misfire, and "unit price" is caught before bare "price".
PRIORITY_KEYWORDS = [
    ("total cost", "additive"), ("total price", "additive"), ("total revenue", "additive"),
    ("total sales", "additive"), ("total profit", "additive"), ("gross sales", "additive"),
    ("net revenue", "additive"), ("gross revenue", "additive"), ("discount amount", "additive"),
    ("total expense", "additive"), ("total expenses", "additive"), ("total fare", "additive"),
    ("unit price", "average"), ("unit cost", "average"), ("avg price", "average"),
    ("average price", "average"), ("price per unit", "average"), ("avg rating", "average"),
    ("customer rating", "average"),
    ("load factor", "percentage"), ("profit margin", "percentage"), ("conversion rate", "percentage"),
    ("occupancy rate", "percentage"), ("discount rate", "percentage"), ("discount percent", "percentage"),
    ("growth rate", "percentage"), ("margin percent", "percentage"), ("occupancy percent", "percentage"),
    ("percent", "percentage"), ("pct", "percentage"), ("margin", "percentage"), ("ratio", "percentage"),
    ("rate", "percentage"), ("occupancy", "percentage"), ("conversion", "percentage"),
    ("revenue", "additive"), ("sales", "additive"), ("profit", "additive"), ("cost", "additive"),
    ("quantity", "additive"), ("units", "additive"), ("fuel", "additive"), ("passengers", "additive"),
    ("flights", "additive"), ("expense", "additive"), ("expenses", "additive"), ("amount", "additive"),
    ("price", "average"), ("age", "average"), ("salary", "average"), ("rating", "average"),
    ("temperature", "average"), ("duration", "average"), ("score", "average"), ("satisfaction", "average"),
]

PRIORITY_CATEGORY_KEYWORDS = [
    "product", "category", "type", "channel", "city", "region", "country",
    "store", "department", "route", "payment method", "branch", "location",
    "service", "segment", "airport",
]


def classify_metric(col_name: str, series: pd.Series) -> str:
    """Classifies a numeric column as 'additive' (SUM), 'average' (MEAN),
    'percentage' (MEAN, never summed), or 'identifier' (not calculated).
    Falls back to 'additive' when genuinely uncertain — most unlabeled
    business numbers are summable quantities, so that's the safer default.
    """
    if looks_like_identifier(series, col_name):
        return "identifier"
    if "%" in str(col_name):
        return "percentage"
    norm = _norm(col_name)
    for kw, mtype in PRIORITY_KEYWORDS:
        if _has_kw(norm, kw):
            return mtype
    return "additive"


def choose_aggregation(metric_type: str):
    if metric_type == "additive":
        return "sum"
    if metric_type in ("average", "percentage"):
        return "mean"
    return None


def calculate_metric(df, col, metric_type):
    agg = choose_aggregation(metric_type)
    if agg == "sum":
        return df[col].sum()
    elif agg == "mean":
        return df[col].mean()
    return None


def metric_label_prefix(metric_type: str) -> str:
    """'Total' for additive metrics, 'Average' for average/percentage —
    so a card never reads 'Total Unit Price' or 'Total Discount %'."""
    return "Total" if metric_type == "additive" else "Average"


def choose_breakdown_dimension(metric_col: str, category_cols: list, df: pd.DataFrame):
    """Picks the most business-meaningful category to break a metric down
    by — preferring Product/City/Channel-style columns over high-cardinality
    or person-identifying columns, unless the metric is people-relevant
    (e.g. a rating, where Employee/Customer becomes a sensible dimension).
    """
    if not category_cols:
        return None
    n = len(df)
    metric_norm = _norm(metric_col)
    is_people_relevant = any(_has_kw(metric_norm, kw) for kw in ["rating", "satisfaction", "score", "performance"])

    best_col, best_score = None, float("-inf")
    for col in category_cols:
        norm = _norm(col)
        score = 0.0
        if any(_has_kw(norm, kw) for kw in PRIORITY_CATEGORY_KEYWORDS):
            score += 3.0
        nunique = df[col].nunique()
        uniqueness_ratio = (nunique / n) if n else 1.0
        score += (1 - uniqueness_ratio) * 2.0
        is_person_like = any(_has_kw(norm, kw) for kw in ["name", "customer", "client"])
        if is_person_like:
            score += 0.5 if is_people_relevant else -1.5
        if _has_kw(norm, "employee") and is_people_relevant:
            score += 1.0
        if score > best_score:
            best_score, best_col = score, col
    return best_col


def _find_by_keywords(cols, keywords):
    for kw in keywords:
        for c in cols:
            if _has_kw(_norm(c), kw):
                return c
    return None


def detect_business_roles(classifications: dict):
    """Finds which columns play which business role (gross sales, profit,
    quantity, etc.) based on classification + name — used to build KPI
    cards and key findings without guessing at columns that don't exist.
    """
    additive_cols = [c for c, t in classifications.items() if t == "additive"]
    average_cols = [c for c, t in classifications.items() if t == "average"]
    percentage_cols = [c for c, t in classifications.items() if t == "percentage"]

    gross_sales_col = _find_by_keywords(additive_cols, ["gross sales", "gross revenue"])
    net_revenue_col = _find_by_keywords(additive_cols, ["net revenue"])
    if not net_revenue_col:
        remaining = [c for c in additive_cols if c != gross_sales_col]
        net_revenue_col = _find_by_keywords(remaining, ["revenue"])
    profit_col = _find_by_keywords(additive_cols, ["profit"])
    quantity_col = _find_by_keywords(additive_cols, ["quantity", "units", "unit count"])
    discount_amount_col = _find_by_keywords(additive_cols, ["discount amount", "discount"])
    cost_col = _find_by_keywords(additive_cols, ["total cost", "cost"])
    unit_price_col = _find_by_keywords(average_cols, ["unit price", "price"])
    rating_col = _find_by_keywords(average_cols, ["rating", "satisfaction", "score"])
    margin_pct_col = _find_by_keywords(percentage_cols, ["margin"])
    discount_pct_col = _find_by_keywords(percentage_cols, ["discount"])

    return {
        "gross_sales_col": gross_sales_col, "net_revenue_col": net_revenue_col,
        "profit_col": profit_col, "quantity_col": quantity_col,
        "discount_amount_col": discount_amount_col, "cost_col": cost_col,
        "unit_price_col": unit_price_col, "rating_col": rating_col,
        "margin_pct_col": margin_pct_col, "discount_pct_col": discount_pct_col,
        "additive_cols": additive_cols, "average_cols": average_cols,
        "percentage_cols": percentage_cols,
    }


def calculate_derived_kpis(df, roles):
    """Only computes a derived KPI when every column it needs actually
    exists in the file — never invents a metric from partial data.
    """
    derived = {}
    gsc, qc = roles["gross_sales_col"], roles["quantity_col"]
    if gsc and qc:
        qty_total = df[qc].sum()
        if qty_total:
            derived["avg_selling_price"] = {"value": df[gsc].sum() / qty_total, "formula": f"{gsc} / {qc}"}

    dac, gsc2 = roles["discount_amount_col"], roles["gross_sales_col"]
    if dac and gsc2:
        gross_total = df[gsc2].sum()
        if gross_total:
            derived["effective_discount_rate"] = {
                "value": (df[dac].sum() / gross_total) * 100, "formula": f"{dac} / {gsc2} * 100"
            }

    pc = roles["profit_col"]
    denom_col = roles["net_revenue_col"] or roles["gross_sales_col"]
    if pc and denom_col:
        denom_total = df[denom_col].sum()
        if denom_total:
            derived["overall_profit_margin"] = {
                "value": (df[pc].sum() / denom_total) * 100, "formula": f"{pc} / {denom_col} * 100"
            }

    rc = roles["rating_col"]
    if rc:
        derived["avg_customer_rating"] = {"value": df[rc].mean(), "formula": None}

    return derived


def generate_business_kpis(df, classifications, roles, max_primary=4):
    """Ordered list of primary KPI cards, prioritizing real business
    meaning over column order: Gross Sales, Net Revenue, Profit, Quantity."""
    priority_order = [roles["gross_sales_col"], roles["net_revenue_col"], roles["profit_col"], roles["quantity_col"]]
    chosen = [c for c in priority_order if c]
    remaining_additive = [c for c in roles["additive_cols"] if c not in chosen]
    chosen += remaining_additive
    chosen = chosen[:max_primary]
    return [(c, df[c].sum(), "additive") for c in chosen]


def generate_key_findings(df, roles, derived, fmt_func):
    """Deterministic, template-based findings — every number traces
    directly to a real calculation, nothing free-generated."""
    order_noun = "orders" if any(_has_kw(_norm(c), "order") for c in df.columns) else "records"
    n = len(df)
    findings = []

    gsc = roles["gross_sales_col"]
    if gsc:
        findings.append(f"Gross sales reached {fmt_func(df[gsc].sum())} across {n:,} {order_noun}.")

    qc = roles["quantity_col"]
    if qc and "avg_selling_price" in derived:
        findings.append(
            f"{fmt_func(df[qc].sum())} units were sold, corresponding to an average selling price of "
            f"{fmt_func(derived['avg_selling_price']['value'])} per unit."
        )
    elif qc:
        findings.append(f"{fmt_func(df[qc].sum())} units were sold across {n:,} {order_noun}.")

    if "effective_discount_rate" in derived:
        findings.append(f"Discounts reduced gross sales by approximately {fmt_func(derived['effective_discount_rate']['value'])}%.")

    nrc, pc = roles["net_revenue_col"], roles["profit_col"]
    if nrc and pc:
        findings.append(f"Net revenue reached {fmt_func(df[nrc].sum())} and total profit was {fmt_func(df[pc].sum())}.")
    elif pc:
        findings.append(f"Total profit was {fmt_func(df[pc].sum())}.")

    if "overall_profit_margin" in derived:
        findings.append(f"Overall profit margin was {fmt_func(derived['overall_profit_margin']['value'])}%.")

    if "avg_customer_rating" in derived:
        findings.append(f"Average customer rating was {fmt_func(derived['avg_customer_rating']['value'])}.")

    return findings


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


def top_bottom_by_category(df, num_col, cat_col, agg="sum"):
    """Which category leads and which trails for this metric, and by how
    much — using the correct aggregation for the metric's type (sum for
    additive metrics, mean for average/percentage ones). Uses min_count=1
    on sums so an all-missing group shows as excluded, not a fake 0.
    """
    grouped = df.groupby(cat_col)[num_col]
    values = grouped.sum(min_count=1) if agg == "sum" else grouped.mean()
    rows = grouped.count()
    stats = pd.DataFrame({"value": values, "rows": rows}).dropna(subset=["value"])
    stats = stats[stats["rows"] > 0].sort_values("value", ascending=False)
    if len(stats) == 0:
        return None
    top, bottom = stats.iloc[0], stats.iloc[-1]
    return {
        "top_name": stats.index[0], "top_value": top["value"], "top_rows": int(top["rows"]),
        "bottom_name": stats.index[-1], "bottom_value": bottom["value"], "bottom_rows": int(bottom["rows"]),
        "agg": agg,
    }


def count_anomalies(df, col):
    """Simple outlier count: values more than 2.5 standard deviations from the mean."""
    series = df[col].dropna()
    if len(series) < 5 or series.std() == 0:
        return 0
    z_scores = (series - series.mean()).abs() / series.std()
    return int((z_scores > 2.5).sum())


# ---------------------------------------------------------
# GEO / MAP HELPERS
# ---------------------------------------------------------
# Common non-standard names/abbreviations pycountry's own lookup won't
# always catch on its own — filled in by hand for the most frequent cases.
COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "britain": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    "uae": "United Arab Emirates", "south korea": "Korea, Republic of",
    "north korea": "Korea, Democratic People's Republic of",
    "russia": "Russian Federation", "vietnam": "Viet Nam",
    "iran": "Iran, Islamic Republic of", "syria": "Syrian Arab Republic",
    "laos": "Lao People's Democratic Republic", "moldova": "Moldova, Republic of",
    "tanzania": "Tanzania, United Republic of", "bolivia": "Bolivia, Plurinational State of",
    "venezuela": "Venezuela, Bolivarian Republic of", "brunei": "Brunei Darussalam",
    "czechia": "Czechia", "ivory coast": "Côte d'Ivoire",
    "turkey": "Türkiye", "turkiye": "Türkiye", "türkiye": "Türkiye",
}

_country_alpha3_cache = {}

def country_name_to_alpha3(raw_value):
    """Best-effort lookup of a country name/code to its ISO alpha-3 code.
    Returns None if it can't confidently match — never guesses silently.
    """
    if raw_value in _country_alpha3_cache:
        return _country_alpha3_cache[raw_value]
    if not isinstance(raw_value, str) or not raw_value.strip():
        _country_alpha3_cache[raw_value] = None
        return None
    key = raw_value.strip()
    lookup_value = COUNTRY_ALIASES.get(key.lower(), key)
    result = None
    try:
        result = pycountry.countries.lookup(lookup_value).alpha_3
    except LookupError:
        try:
            matches = pycountry.countries.search_fuzzy(lookup_value)
            if matches:
                result = matches[0].alpha_3
        except LookupError:
            result = None
    _country_alpha3_cache[raw_value] = result
    return result


def detect_country_column(df, candidate_cols, sample_size=60, match_threshold=0.6):
    """Checks each candidate text column to see if most of its distinct
    values look like real country names/codes. Returns the best match, or
    None if nothing qualifies — this only ever confirms a real match, it
    doesn't force a column to be treated as countries.
    """
    best_col, best_rate = None, 0.0
    for col in candidate_cols:
        uniques = df[col].dropna().astype(str).unique()
        if len(uniques) < 2:
            continue
        sample = uniques[:sample_size]
        matched = sum(1 for v in sample if country_name_to_alpha3(v) is not None)
        rate = matched / len(sample)
        if rate >= match_threshold and rate > best_rate:
            best_col, best_rate = col, rate
    return best_col


def detect_lat_lon_columns(df):
    """Finds a latitude/longitude column pair by name, if present."""
    lat_pattern = re.compile(r"^(lat|latitude)$", re.IGNORECASE)
    lon_pattern = re.compile(r"^(lon|lng|long|longitude)$", re.IGNORECASE)
    lat_col = next((c for c in df.columns if lat_pattern.match(str(c).strip())), None)
    lon_col = next((c for c in df.columns if lon_pattern.match(str(c).strip())), None)
    return lat_col, lon_col


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

    # ---- Metric classification (additive / average / percentage) ----
    # This drives every calculation below: sums only happen for metrics
    # where summing makes business sense (Revenue, Quantity, Profit...);
    # rates and per-unit metrics (Unit Price, Discount %, Margin %) are
    # averaged instead, never summed.
    classifications = {c: classify_metric(c, df[c]) for c in numeric_cols}
    roles = detect_business_roles(classifications)
    derived_kpis = calculate_derived_kpis(df, roles)

    # ---- Geo detection (countries, or explicit lat/lon columns) ----
    detected_country_col = detect_country_column(df, category_cols)
    detected_lat_col, detected_lon_col = detect_lat_lon_columns(df)

    # ---- Business KPIs (whole-file, always shown) ----
    primary_kpis = generate_business_kpis(df, classifications, roles)
    if primary_kpis:
        st.subheader("Key business metrics")
        cols = st.columns(len(primary_kpis))
        for i, (label, value, mtype) in enumerate(primary_kpis):
            with cols[i]:
                st.metric(f"{metric_label_prefix(mtype)} {label}", fmt(value))

        secondary_items = []
        if "avg_selling_price" in derived_kpis:
            secondary_items.append(("Average selling price", derived_kpis["avg_selling_price"]))
        if "effective_discount_rate" in derived_kpis:
            secondary_items.append(("Effective discount rate", derived_kpis["effective_discount_rate"]))
        if "overall_profit_margin" in derived_kpis:
            secondary_items.append(("Overall profit margin", derived_kpis["overall_profit_margin"]))
        if "avg_customer_rating" in derived_kpis:
            secondary_items.append(("Average customer rating", derived_kpis["avg_customer_rating"]))

        if secondary_items:
            cols2 = st.columns(len(secondary_items))
            for i, (label, info) in enumerate(secondary_items):
                unit = "%" if "rate" in label.lower() or "margin" in label.lower() else ""
                tooltip = f"Calculated: {info['formula']}" if info.get("formula") else "Average across all records."
                with cols2[i]:
                    st.metric(label, f"{fmt(info['value'])}{unit}", help=tooltip)

    # ---- Key findings (deterministic, template-based — no free text generation) ----
    findings = generate_key_findings(df, roles, derived_kpis, fmt)
    if findings:
        st.subheader("Key findings")
        for f in findings:
            st.markdown(f"- {f}")

    # ---- Overview table: correct aggregation per column type ----
    if numeric_cols:
        with st.expander("Full column overview"):
            overview_rows = []
            for c in numeric_cols:
                mtype = classifications[c]
                value = calculate_metric(df, c, mtype)
                overview_rows.append({
                    "Column": c,
                    "Type": mtype.capitalize(),
                    f"{metric_label_prefix(mtype)}": fmt(value),
                    "Min": fmt(df[c].min()),
                    "Max": fmt(df[c].max()),
                })
            st.dataframe(pd.DataFrame(overview_rows), hide_index=True, use_container_width=True)
            st.caption("Totals are shown only for additive metrics; rate/per-unit metrics show their average instead.")

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
        st.caption(
            "Each metric is automatically summed or averaged based on what kind of "
            "metric it is (e.g. Revenue is summed, Unit Price is averaged) — not manually chosen."
        )

    # ---- Key metrics (focus-specific) ----
    if focus_numeric_cols:
        st.subheader("Key metrics")
        cols = st.columns(min(3, len(focus_numeric_cols)))
        for i, col in enumerate(focus_numeric_cols[:3]):
            mtype = classifications[col]
            agg_str = "Sum" if choose_aggregation(mtype) == "sum" else "Average"
            value = calculate_metric(df, col, mtype)

            # Prefer a real calendar-period comparison; fall back to the
            # cruder first-half/second-half trend if there's no usable date.
            pv = None
            if primary_date_col and chosen_granularity:
                pv = period_over_period_value(df, primary_date_col, col, chosen_granularity, agg_str)

            if pv:
                pct_change = pv["change"]
                tooltip = (
                    f"{pv['granularity']} comparison ({pv['agg'].lower()}, based on this being "
                    f"a {mtype} metric): {pv['latest_label']} = {fmt(pv['latest_val'])}, "
                    f"{pv['previous_label']} = {fmt(pv['previous_val'])}."
                )
                if pv["partial"]:
                    tooltip += " Most recent, still-in-progress period was excluded from this comparison."
            else:
                pct_change = trend_pct(df, col) if agg_str == "Sum" else 0.0
                tooltip = (
                    f"No usable date column to compare real time periods. This is a {mtype} metric, "
                    f"so its {agg_str.lower()} is shown."
                )

            with cols[i]:
                st.metric(f"{metric_label_prefix(mtype)} {col}", fmt(value), f"{pct_change:+.1f}%", help=tooltip)
    else:
        st.info("No numeric metric columns detected for this focus.")

    # ---- Deep analysis per focus metric ----
    st.subheader("Deep analysis")
    metric_trends = {}  # col -> pct_change, used later for the relationship section
    for col in focus_numeric_cols:
        mtype = classifications[col]
        agg_str = "Sum" if choose_aggregation(mtype) == "sum" else "Average"
        value = calculate_metric(df, col, mtype)
        anomaly_count = count_anomalies(df, col) if mtype == "additive" else 0

        with st.expander(f"**{col}** ({mtype})", expanded=True):
            bullets = [
                f"{metric_label_prefix(mtype)}: {fmt(value)} "
                f"(range {fmt(df[col].min())}\u2013{fmt(df[col].max())}).",
            ]

            # Prefer a real calendar-period comparison when we have dates;
            # fall back to the cruder first-half/second-half trend otherwise.
            pop_text = None
            if primary_date_col and chosen_granularity:
                pop_text = period_over_period(df, primary_date_col, col, chosen_granularity, agg_str)

            if pop_text:
                bullets.append(pop_text)
                pct_change = trend_pct(df, col) if agg_str == "Sum" else 0.0
                metric_trends[col] = pct_change
            else:
                pct_change = trend_pct(df, col) if agg_str == "Sum" else 0.0
                metric_trends[col] = pct_change
                if agg_str == "Sum" and abs(pct_change) > 1:
                    direction = "trending up" if pct_change > 0 else "trending down"
                    bullets.append(
                        f"{direction.capitalize()}, roughly {fmt(abs(pct_change))}% "
                        f"{'increase' if pct_change > 0 else 'decrease'} comparing the first half of the "
                        f"file's rows to the second half (no usable date column to compare real periods)."
                    )
                elif agg_str == "Sum":
                    bullets.append("Fairly flat across the file (no meaningful trend detected).")
                else:
                    bullets.append("No usable date column to compare real time periods for this metric.")

            # Top / bottom contributor — auto-picks a business-meaningful
            # category (Product, City, Channel...) rather than always using
            # the first category column, and never sums an average/percentage
            # metric when finding the leader.
            if focus_category_cols:
                best_cat_col = choose_breakdown_dimension(col, focus_category_cols, df)
                tb_agg = "sum" if agg_str == "Sum" else "mean"
                tb = top_bottom_by_category(df, col, best_cat_col, agg=tb_agg) if best_cat_col else None
                if tb and tb["top_name"] != tb["bottom_name"]:
                    if tb_agg == "sum":
                        top_rows_note = f" across {tb['top_rows']} record{'s' if tb['top_rows'] != 1 else ''}" if tb["top_rows"] > 1 else ""
                        bottom_rows_note = f" across {tb['bottom_rows']} record{'s' if tb['bottom_rows'] != 1 else ''}" if tb["bottom_rows"] > 1 else ""
                        bullets.append(
                            f"By **{best_cat_col}**: \"{tb['top_name']}\" leads with {fmt(tb['top_value'])} total"
                            f"{top_rows_note}, while \"{tb['bottom_name']}\" trails at {fmt(tb['bottom_value'])}"
                            f"{bottom_rows_note}."
                        )
                    else:
                        bullets.append(
                            f"By **{best_cat_col}**: \"{tb['top_name']}\" had the highest average {col} "
                            f"({fmt(tb['top_value'])}), while \"{tb['bottom_name']}\" had the lowest "
                            f"({fmt(tb['bottom_value'])})."
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
        num_col_mtype = classifications[num_col]
        chart_agg = choose_aggregation(num_col_mtype)  # "sum" or "mean", based on this metric's type
        st.caption(f"Showing **{metric_label_prefix(num_col_mtype).lower()}** of {num_col} per period (a {num_col_mtype} metric).")
        chart_df = df[[date_col, num_col]].copy()
        chart_df[date_col] = pd.to_datetime(chart_df[date_col], errors="coerce")
        chart_df = chart_df.dropna(subset=[date_col]).sort_values(date_col)

        if len(chart_df) > 1:
            freq = GRANULARITY_FREQ[chosen_granularity]
            grouped = chart_df.set_index(date_col).resample(freq)[num_col]
            grouped = grouped.sum() if chart_agg == "sum" else grouped.mean()
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
        with col_b:
            if len(focus_numeric_cols) > 1:
                metric_for_breakdown = st.selectbox(
                    "Measured by", focus_numeric_cols, key="breakdown_metric_col"
                )
            elif focus_numeric_cols:
                metric_for_breakdown = focus_numeric_cols[0]
            else:
                metric_for_breakdown = None

        recommended_cat_col = (
            choose_breakdown_dimension(metric_for_breakdown, focus_category_cols, df)
            if metric_for_breakdown else focus_category_cols[0]
        )
        with col_a:
            if len(focus_category_cols) > 1:
                default_idx = focus_category_cols.index(recommended_cat_col) if recommended_cat_col in focus_category_cols else 0
                cat_col = st.selectbox(
                    "Break down by", focus_category_cols, index=default_idx, key="breakdown_cat_col",
                    help=f"Recommended: {recommended_cat_col} (based on category relevance and cardinality).",
                )
            else:
                cat_col = focus_category_cols[0]
                st.caption(f"Breaking down by **{cat_col}**")

        breakdown_chart_type = st.radio(
            "Chart type", ["Bar", "Horizontal bar", "Pie", "Donut", "Line"],
            horizontal=True, key="breakdown_chart_type"
        )
        if metric_for_breakdown:
            metric_mtype = classifications[metric_for_breakdown]
            breakdown_agg = choose_aggregation(metric_mtype)
            grouped = df.groupby(cat_col)[metric_for_breakdown]
            breakdown = (grouped.sum(min_count=1) if breakdown_agg == "sum" else grouped.mean()).dropna()
            breakdown = breakdown.sort_values(ascending=False).head(10)
            y_label = f"{metric_label_prefix(metric_mtype)} {metric_for_breakdown}"
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

    # ---- Map ----
    if detected_country_col or (detected_lat_col and detected_lon_col):
        st.subheader("Map")

        if detected_country_col:
            metric_for_map = None
            if numeric_cols:
                metric_for_map = st.selectbox(
                    "Color the map by", numeric_cols, key="map_metric_col",
                    help=f"Detected \"{detected_country_col}\" as a country column.",
                )
            map_df = df[[detected_country_col]].copy()
            if metric_for_map:
                map_df[metric_for_map] = df[metric_for_map]
                grouped = map_df.groupby(detected_country_col)[metric_for_map].sum().reset_index()
            else:
                grouped = map_df[detected_country_col].value_counts().reset_index()
                grouped.columns = [detected_country_col, "Count"]
                metric_for_map = "Count"

            grouped["iso_alpha3"] = grouped[detected_country_col].apply(country_name_to_alpha3)
            mappable = grouped.dropna(subset=["iso_alpha3"])
            unmatched = grouped[grouped["iso_alpha3"].isna()][detected_country_col].tolist()

            if len(mappable) > 0:
                fig_map = px.choropleth(
                    mappable,
                    locations="iso_alpha3",
                    color=metric_for_map,
                    hover_name=detected_country_col,
                    color_continuous_scale="Blues",
                )
                fig_map.update_geos(showframe=False, showcoastlines=True)
                fig_map.update_layout(margin=dict(l=0, r=0, t=0, b=0))
                st.plotly_chart(fig_map, use_container_width=True)
                if unmatched:
                    st.caption(f"Couldn't place on the map (unrecognized country names): {', '.join(map(str, unmatched))}")
            else:
                st.info(f"Detected \"{detected_country_col}\" as a country-like column, but couldn't match any values to real countries.")

        if detected_lat_col and detected_lon_col:
            points = df[[detected_lat_col, detected_lon_col]].dropna()
            points = points.rename(columns={detected_lat_col: "lat", detected_lon_col: "lon"})
            points = points[(points["lat"].between(-90, 90)) & (points["lon"].between(-180, 180))]
            if len(points) > 0:
                st.map(points)
            else:
                st.info(f"Found \"{detected_lat_col}\"/\"{detected_lon_col}\" columns, but no valid coordinate values to plot.")

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
