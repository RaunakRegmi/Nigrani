"""Forensic EDA: rank every available column by its relationship with is_fraud.

Usage:
    venv/bin/python eda_hunter.py                     # 10% sample of transactions
    venv/bin/python eda_hunter.py --sample-pct 25
    venv/bin/python eda_hunter.py --include-ledger    # also scan raw.transactions_upload
"""
import argparse
import os
import re
import sys
import time

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from scipy.stats import norm
from sklearn.feature_selection import mutual_info_classif
from sklearn.preprocessing import OrdinalEncoder
from sqlalchemy import create_engine, text

# Load DATABASE_URL from .env (never hardcode credentials)
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL is not set. Add it to a .env file next to eda_hunter.py.")

LABEL = "is_fraud"

# alias -> (schema, table, key column for latest-version dedup, columns to skip)
# Skipped: join keys/identifiers, load metadata, and exact duplicates of
# columns already pulled from core.transactions.
SOURCES = {
    "c": ("core", "transactions", None, {"transaction_id", "account_id", LABEL}),
    "v": ("features", "velocity_snapshots", None,
          {"transaction_id", "account_id", "transaction_timestamp", "amount_npr"}),
    "p": ("raw", "customer_profiles", "account_id", {"account_id", "uploaded_at"}),
    "g": ("raw", "geo_events", "txn_id", {"txn_id", "account_id", "uploaded_at", "timestamp"}),
    "n": ("raw", "account_graph_nodes", "id", {"id", "uploaded_at"}),
}
# Opt-in: ledger fields that never made it into core.transactions. Its own
# is_fraud copy is the label itself, so it is always skipped.
LEDGER_SOURCE = ("raw", "transactions_upload", "txn_id",
                 {"txn_id", "account_id", "uploaded_at", "timestamp", "amount_npr", LABEL})
JOINS = {
    "v": "JOIN features.velocity_snapshots v ON v.transaction_id = c.transaction_id",
    "p": "LEFT JOIN latest_p p ON TRIM(p.account_id) = TRIM(c.account_id)",
    "g": "LEFT JOIN latest_g g ON TRIM(g.txn_id) = c.transaction_id",
    "n": "LEFT JOIN latest_n n ON TRIM(n.id) = TRIM(c.account_id)",
    "t": "LEFT JOIN latest_t t ON TRIM(t.txn_id) = c.transaction_id",
}
SOURCE_NAMES = {
    "c": "core.transactions", "v": "velocity_snapshots", "p": "customer_profiles",
    "g": "geo_events", "n": "account_graph_nodes", "t": "transactions_upload", "time": "derived(timestamp)",
}

# raw.fraud_labels is never joined: fraud_type, fraud_confidence,
# financial_loss_npr, recovery_status etc. are outcomes of the label.
# Anything else whose name mentions fraud/label is kept but flagged.
SUSPECT_LEAK = re.compile(r"fraud|label|target|chargeback", re.IGNORECASE)

MAX_CATEGORIES = 1000      # more distinct text values than this = identifier/free text
MI_BINS = 20               # continuous columns are quantile-binned before MI
MI_PERMUTATIONS = 3        # shuffled-label runs used to estimate the MI noise floor
BOOL_VALUES = {"true": 1, "false": 0, "t": 1, "f": 0, "yes": 1, "no": 0, "y": 1, "n": 0}
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}"


def parse_args():
    parser = argparse.ArgumentParser(description="Rank every column by its relationship with is_fraud.")
    parser.add_argument("--sample-pct", type=float, default=10.0,
                        help="Percent of core.transactions to sample (default 10). 100 = all rows.")
    parser.add_argument("--include-ledger", action="store_true",
                        help="Also scan raw.transactions_upload fields not carried into core.transactions.")
    parser.add_argument("--top", type=int, default=15, help="Leaderboard size (default 15).")
    return parser.parse_args()


def discover_columns(conn, sources):
    """Return {alias: [columns]} from information_schema -- only columns that exist."""
    found = {}
    for alias, (schema, table, _, skip) in sources.items():
        cols = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
        """), {"schema": schema, "table": table}).scalars().all()
        if not cols:
            print(f"  skipping {schema}.{table}: table not found")
            continue
        found[alias] = [c for c in cols if c not in skip]
    return found


def build_query(sources, columns, quote, sample_pct):
    # raw.* tables are append-only staging: keep the latest version per key so
    # joins never duplicate transactions (same rule as train_engine.py).
    ctes = []
    for alias, (schema, table, key, _) in sources.items():
        if key is None or alias not in columns:
            continue
        ctes.append(f"""latest_{alias} AS (
    SELECT * FROM (
        SELECT x.*, ROW_NUMBER() OVER (
            PARTITION BY TRIM(x.{quote(key)})
            ORDER BY x.uploaded_at DESC NULLS LAST, x.ctid DESC
        ) AS rn
        FROM {schema}.{table} x
        WHERE NULLIF(TRIM(x.{quote(key)}), '') IS NOT NULL
    ) ranked WHERE rn = 1
)""")

    # Columns are prefixed with their source alias (p__risk_tier vs n__risk_tier)
    select = [
        f"{alias}.{quote(col)} AS {quote(f'{alias}__{col}')}"
        for alias, cols in columns.items() for col in cols
    ]
    # Time-of-day / calendar rules are a classic synthetic-label pattern
    select += [
        "EXTRACT(HOUR FROM c.transaction_timestamp) AS time__hour_of_day",
        "EXTRACT(ISODOW FROM c.transaction_timestamp) AS time__day_of_week",
        "EXTRACT(DAY FROM c.transaction_timestamp) AS time__day_of_month",
        "EXTRACT(MONTH FROM c.transaction_timestamp) AS time__month",
    ]
    select.append(f"c.{LABEL} AS {LABEL}")

    sample = ""
    if sample_pct < 100:
        sample = f" TABLESAMPLE BERNOULLI ({sample_pct}) REPEATABLE (42)"
    joins = "\n".join(JOINS[a] for a in columns if a in JOINS)

    return (
        ("WITH " + ",\n".join(ctes) + "\n" if ctes else "")
        + "SELECT\n    " + ",\n    ".join(select)
        + f"\nFROM core.transactions c{sample}\n{joins}"
    )


def coerce_column(s: pd.Series) -> tuple[pd.Series, str]:
    """Raw staging columns are TEXT; recover their real type. Returns (series, kind)."""
    if pd.api.types.is_bool_dtype(s):
        return s.astype(float), "boolean"
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float), "numeric"

    values = s.dropna()
    if values.empty:
        return s, "empty"
    as_str = values.astype(str).str.strip()

    lowered = as_str.str.lower()
    if lowered.isin(BOOL_VALUES).all():
        return s.astype(str).str.strip().str.lower().map(BOOL_VALUES).astype(float), "boolean"

    numeric = pd.to_numeric(values, errors="coerce")  # also handles Decimal from NUMERIC
    if numeric.notna().mean() >= 0.95:
        return pd.to_numeric(s, errors="coerce").astype(float), "numeric"

    if as_str.str.match(DATE_PATTERN).mean() >= 0.95:
        dates = pd.to_datetime(s, errors="coerce", format="mixed")
        return (dates - pd.Timestamp("1970-01-01")).dt.days.astype(float), "date->days"

    return s.astype(str).where(s.notna(), None), "categorical"


def mi_codes(series: pd.Series, kind: str) -> pd.Series:
    """Discretize one (un-imputed) feature for MI. Missing values get their own
    code (-1): missingness itself can be the signal (e.g. fx_rate is blank for
    every NPR transaction). Quantile bins also keep the estimate stable on
    heavily tied columns, where k-NN MI on raw values is badly biased."""
    if kind == "categorical" or series.nunique(dropna=True) <= MI_BINS:
        codes = pd.Series(pd.factorize(series)[0], index=series.index)
    else:
        codes = pd.qcut(series, q=MI_BINS, labels=False, duplicates="drop")
    return codes.fillna(-1).astype(int)


def clean(df: pd.DataFrame):
    """Type-recover, impute and encode every feature.

    Returns (X for Pearson: imputed + ordinal-encoded, X_mi: discrete codes,
    kinds, skipped)."""
    features, kinds, skipped = {}, {}, []
    for col in df.columns.drop(LABEL):
        series, kind = coerce_column(df[col])
        if kind == "empty" or series.nunique(dropna=True) < 2:
            skipped.append((col, "constant/empty"))
            continue
        if kind == "categorical" and series.nunique(dropna=True) > MAX_CATEGORIES:
            skipped.append((col, f"{series.nunique():,} distinct values (identifier/free text)"))
            continue
        features[col], kinds[col] = series, kind

    X = pd.DataFrame(features, index=df.index)
    X_mi = pd.DataFrame({c: mi_codes(s, kinds[c]) for c, s in features.items()}, index=df.index)

    categorical = [c for c, k in kinds.items() if k == "categorical"]
    numeric = [c for c in X.columns if c not in categorical]
    X[numeric] = X[numeric].fillna(X[numeric].median())
    if categorical:
        X[categorical] = X[categorical].fillna("MISSING")
        X[categorical] = OrdinalEncoder(dtype=np.float64).fit_transform(X[categorical])

    return X, X_mi, kinds, skipped


def print_leaderboard(title, scores, kinds, noise, noise_label, top):
    """noise: a scalar floor, or a per-feature Series of floors."""
    floor = noise if isinstance(noise, pd.Series) else pd.Series(noise, index=scores.index)
    print(f"\n{'=' * 78}\n {title}\n{'=' * 78}")
    print(f" {'#':>2}  {'feature':<38} {'source':<20} {'type':<12} {'score':>8}")
    print(f" {'-' * 2}  {'-' * 38} {'-' * 20} {'-' * 12} {'-' * 8}")
    for rank, (feature, score) in enumerate(scores.head(top).items(), 1):
        alias, _, name = feature.partition("__")
        flag = "  <-- SUSPECT LEAK" if SUSPECT_LEAK.search(name) else ""
        below = "  (noise)" if score <= floor[feature] else ""
        print(f" {rank:>2}  {name:<38} {SOURCE_NAMES.get(alias, alias):<20} "
              f"{kinds.get(feature, ''):<12} {score:>8.5f}{flag}{below}")
    print(f"     {noise_label}")


def main():
    args = parse_args()
    sources = dict(SOURCES)
    if args.include_ledger:
        sources["t"] = LEDGER_SOURCE

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    started = time.perf_counter()
    with engine.connect() as conn:
        print("Discovering columns...")
        columns = discover_columns(conn, sources)
        sql = build_query(sources, columns, conn.dialect.identifier_preparer.quote, args.sample_pct)
        print(f"Extracting {sum(map(len, columns.values())) + 4} columns "
              f"from {len(columns)} tables ({args.sample_pct:g}% sample)...")
        df = pd.read_sql(text(sql), conn)
    engine.dispose()

    df = df.dropna(subset=[LABEL])
    y = df[LABEL].astype(int)
    print(f"Extracted {len(df):,} rows, {y.sum():,} fraud ({y.mean():.3%}) "
          f"in {time.perf_counter() - started:,.0f}s")
    if y.nunique() < 2:
        sys.exit("Need both fraud and non-fraud rows in the sample.")

    X, X_mi, kinds, skipped = clean(df)

    print(f"Scoring {X.shape[1]} features")
    if skipped:
        print("Skipped:")
        for col, reason in skipped:
            alias, _, name = col.partition("__")
            print(f"  - {name} [{SOURCE_NAMES.get(alias, alias)}]: {reason}")

    # 1. Linear: absolute Pearson correlation with the label. Noise floor is
    # the |r| a truly unrelated feature exceeds with p < 0.05, Bonferroni-
    # corrected for the number of features tested.
    pearson = X.corrwith(y).abs().fillna(0.0)
    pearson_floor = norm.ppf(1 - 0.05 / (2 * X.shape[1])) / np.sqrt(len(X))

    # 2. Non-linear: mutual information on discretized features (catches
    # thresholds and value bands Pearson misses). Noise floor per feature is
    # the highest MI it reaches against shuffled labels.
    print("Computing mutual information...")
    t = time.perf_counter()
    mi = pd.Series(mutual_info_classif(X_mi, y, discrete_features=True), index=X_mi.columns)
    rng = np.random.default_rng(42)
    mi_floor = pd.concat([
        pd.Series(mutual_info_classif(X_mi, rng.permutation(y.values), discrete_features=True), index=X_mi.columns)
        for _ in range(MI_PERMUTATIONS)
    ], axis=1).max(axis=1)
    print(f"  done in {time.perf_counter() - t:,.0f}s")

    print_leaderboard(f"TOP {args.top} LINEAR FEATURES  (|Pearson r| vs is_fraud)",
                      pearson.sort_values(ascending=False), kinds, pearson_floor,
                      f"noise floor: |r| <= {pearson_floor:.5f} (p<0.05, Bonferroni)", args.top)
    print_leaderboard(f"TOP {args.top} NON-LINEAR FEATURES  (Mutual Information, nats)",
                      mi.sort_values(ascending=False), kinds, mi_floor,
                      f"noise floor: per-feature max MI over {MI_PERMUTATIONS} shuffled-label runs", args.top)
    p = y.mean()
    label_entropy = -(p * np.log(p) + (1 - p) * np.log(1 - p))
    print(f"\nLabel entropy (max possible MI): {label_entropy:.5f} nats")


if __name__ == "__main__":
    main()
