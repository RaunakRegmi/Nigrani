import os
import sys
import time

import joblib
import pandas as pd
from dotenv import load_dotenv
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import create_engine, text

# Load DATABASE_URL from .env (never hardcode credentials)
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL is not set. Add it to a .env file next to train_engine.py.")

MODEL_PATH = "frozen_model.pkl"

# Feature contract -- api.py must build exactly these columns, in this order
BOOLEAN_FEATURES = ["weekend_flag", "impossible_travel", "is_vpn"]
NUMERIC_FEATURES = [
    "amount",
    "txn_count_1h",
    "txn_count_24h",
    "total_amount_24h_npr",
    "weekend_flag",
    "churn_risk_score",
    "impossible_travel",
    "is_vpn",
    "km_from_home_district",
    "degree_in",
    "degree_out",
]
CATEGORICAL_FEATURES = ["kyc_tier", "risk_tier", "age_group"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
LABEL = "is_fraud"

# Ledger/velocity columns must be present. Profile, geo and graph data may be
# missing for a transaction: geo/graph get explicit defaults in SQL and
# profiles are imputed, so those rows are kept, never dropped.
REQUIRED_NON_NULL = ["amount", "txn_count_1h", "txn_count_24h", "total_amount_24h_npr", "weekend_flag", LABEL]
REQUIRED_SOURCE_COLUMNS = {
    "customer_profiles": ["account_id", "kyc_tier", "risk_tier", "age_group", "churn_risk_score", "uploaded_at"],
    "geo_events": ["txn_id", "impossible_travel", "is_vpn", "km_from_home_district", "uploaded_at"],
    "account_graph_nodes": ["id", "degree_in", "degree_out", "uploaded_at"],
}

# All raw.* tables are append-only staging, so a key can have several
# versions. Joining them all would duplicate transactions, so each source is
# reduced to its latest row per key (same rule as the core transform in app.py).
# Missing categories become 'UNKNOWN' and missing geo/graph values get their
# defaults here, so training and api.py agree.
FEATURE_SQL = """
WITH latest_profiles AS (
    SELECT *
    FROM (
        SELECT
            p.*,
            ROW_NUMBER() OVER (
                PARTITION BY TRIM(p.account_id)
                ORDER BY p.uploaded_at DESC NULLS LAST, p.ctid DESC
            ) AS rn
        FROM raw.customer_profiles p
        WHERE NULLIF(TRIM(p.account_id), '') IS NOT NULL
    ) ranked
    WHERE rn = 1
),
latest_geo AS (
    SELECT *
    FROM (
        SELECT
            g.*,
            ROW_NUMBER() OVER (
                PARTITION BY TRIM(g.txn_id)
                ORDER BY g.uploaded_at DESC NULLS LAST, g.ctid DESC
            ) AS rn
        FROM raw.geo_events g
        WHERE NULLIF(TRIM(g.txn_id), '') IS NOT NULL
    ) ranked
    WHERE rn = 1
),
latest_nodes AS (
    SELECT *
    FROM (
        SELECT
            n.*,
            ROW_NUMBER() OVER (
                PARTITION BY TRIM(n.id)
                ORDER BY n.uploaded_at DESC NULLS LAST, n.ctid DESC
            ) AS rn
        FROM raw.account_graph_nodes n
        WHERE NULLIF(TRIM(n.id), '') IS NOT NULL
    ) ranked
    WHERE rn = 1
)
SELECT
    c.amount,
    v.txn_count_1h,
    v.txn_count_24h,
    v.total_amount_24h_npr,
    v.weekend_flag,
    -- ::TEXT first: the column is TEXT when app.py creates it, DECIMAL in nigrani_prod_query.sql
    CAST(NULLIF(TRIM(p.churn_risk_score::TEXT), '') AS NUMERIC) AS churn_risk_score,
    COALESCE(CAST(NULLIF(TRIM(g.impossible_travel), '') AS BOOLEAN), FALSE) AS impossible_travel,
    COALESCE(CAST(NULLIF(TRIM(g.is_vpn), '') AS BOOLEAN), FALSE) AS is_vpn,
    COALESCE(CAST(NULLIF(TRIM(g.km_from_home_district), '') AS NUMERIC), 0) AS km_from_home_district,
    COALESCE(CAST(NULLIF(TRIM(n.degree_in), '') AS NUMERIC), 0) AS degree_in,
    COALESCE(CAST(NULLIF(TRIM(n.degree_out), '') AS NUMERIC), 0) AS degree_out,
    COALESCE(NULLIF(UPPER(TRIM(p.kyc_tier)), ''), 'UNKNOWN') AS kyc_tier,
    COALESCE(NULLIF(UPPER(TRIM(p.risk_tier)), ''), 'UNKNOWN') AS risk_tier,
    COALESCE(NULLIF(UPPER(TRIM(p.age_group)), ''), 'UNKNOWN') AS age_group,
    (p.account_id IS NOT NULL) AS has_profile,
    (g.txn_id IS NOT NULL) AS has_geo,
    (n.id IS NOT NULL) AS has_graph_node,
    c.is_fraud
FROM core.transactions c
JOIN features.velocity_snapshots v
    ON c.transaction_id = v.transaction_id
LEFT JOIN latest_profiles p
    ON TRIM(c.account_id) = TRIM(p.account_id)
LEFT JOIN latest_geo g
    ON c.transaction_id = TRIM(g.txn_id)
LEFT JOIN latest_nodes n
    ON TRIM(c.account_id) = TRIM(n.id)
"""

COVERAGE = {
    "has_profile": "KYC profile",
    "has_geo": "geo event",
    "has_graph_node": "graph node",
}


def check_source_schema(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'raw' AND table_name = ANY(:tables)
        """), {"tables": list(REQUIRED_SOURCE_COLUMNS)}).all()
    present = {(t, c) for t, c in rows}
    problems = [
        f"raw.{table}: {', '.join(c for c in cols if (table, c) not in present)}"
        for table, cols in REQUIRED_SOURCE_COLUMNS.items()
        if any((table, c) not in present for c in cols)
    ]
    if problems:
        sys.exit(
            "Missing source columns:\n  " + "\n  ".join(problems)
            + "\nUpload the corresponding extracts through app.py first."
        )


def build_pipeline():
    # Trees don't need scaling; StandardScaler keeps the preprocessor reusable
    # if the estimator is swapped for a linear/distance-based model later.
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical = OneHotEncoder(handle_unknown="ignore")

    preprocessor = ColumnTransformer([
        ("num", numeric, NUMERIC_FEATURES),
        ("cat", categorical, CATEGORICAL_FEATURES),
    ])

    # max_depth and min_samples_leaf cap tree size: fully grown trees on ~2M
    # rows pickled to 600MB+, which is slow to load and heavy to serve.
    return Pipeline([
        ("preprocess", preprocessor),
        ("model", RandomForestClassifier(
            n_estimators=300,
            class_weight="balanced",
            max_depth=20,
            min_samples_leaf=10,
            n_jobs=-1,
            random_state=42,
        )),
    ])


def main():
    # 1. Extract feature-engineered data
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    check_source_schema(engine)
    df = pd.read_sql(FEATURE_SQL, engine)
    engine.dispose()
    print(f"Extracted {len(df):,} rows")

    # 2. Drop rows missing ledger/velocity data; cast to model-friendly dtypes
    # (booleans -> 0/1, NUMERIC arrives as Decimal objects)
    df = df.dropna(subset=REQUIRED_NON_NULL)
    df[BOOLEAN_FEATURES] = df[BOOLEAN_FEATURES].astype(int)
    df[NUMERIC_FEATURES] = df[NUMERIC_FEATURES].astype(float)
    df[LABEL] = df[LABEL].astype(int)
    print(f"Training on {len(df):,} rows after dropping nulls")
    for flag, name in COVERAGE.items():
        print(f"Coverage: {df[flag].mean():.2%} of transactions have a {name}")
        if not df[flag].any():
            print(f"WARNING: no transactions matched a {name}; those features carry no signal.")

    X = df[FEATURES]
    y = df[LABEL]

    n_fraud = int(y.sum())
    print(f"Fraud rate: {n_fraud:,} / {len(y):,} ({y.mean():.2%})")
    if n_fraud < 2 or n_fraud == len(y):
        sys.exit("Need at least 2 fraud and 2 non-fraud rows to train and evaluate.")

    # 3. Hold out 20% for evaluation; stratify so both splits keep the fraud rate
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    # 4. Train preprocessing + model as one unit
    pipeline = build_pipeline()
    started = time.perf_counter()
    pipeline.fit(X_train, y_train)
    print(f"Trained in {time.perf_counter() - started:,.1f}s")

    # 5. Evaluate on the held-out set
    y_proba = pipeline.predict_proba(X_test)[:, 1]
    y_pred = pipeline.predict(X_test)

    print(f"\nROC-AUC: {roc_auc_score(y_test, y_proba):.4f}")

    tn, fp, fn, tp = confusion_matrix(y_test, y_pred, labels=[0, 1]).ravel()
    print("\nConfusion Matrix:")
    print("                 Pred Legit  Pred Fraud")
    print(f"  Actual Legit   {tn:>10,}  {fp:>10,}")
    print(f"  Actual Fraud   {fn:>10,}  {tp:>10,}")

    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["legit", "fraud"], digits=4))

    # 6. Export the whole pipeline (preprocessing + model) as one artifact
    joblib.dump(pipeline, MODEL_PATH)
    print(f"Pipeline exported to {MODEL_PATH}")


if __name__ == "__main__":
    main()
