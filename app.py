import logging
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

# Load DATABASE_URL from .env (never hardcode credentials)
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# Full error details go to the server console; the UI only gets a clean message
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nigrani.ingest")

CSV_CHUNK_SIZE = 100_000  # rows read from the CSV into RAM at a time
SQL_CHUNK_SIZE = 10_000   # rows per INSERT batch sent to Postgres

# The router: a file goes to a table when its headers contain that table's
# signature columns. "key" is the column downstream joins depend on; a file
# without it is rejected even if the signature matches. Order is display only.
ROUTES = {
    "transactions_upload": {
        "label": "Transaction Ledger",
        "signature": {"txn_id", "timestamp", "counterparty_id"},
        "key": "txn_id",
    },
    "fraud_labels": {
        "label": "Fraud Labels",
        "signature": {"txn_id", "is_fraud", "fraud_type"},
        "key": "txn_id",
    },
    "customer_profiles": {
        "label": "Customer Profiles",
        "signature": {"account_id", "kyc_tier", "risk_tier"},
        "key": "account_id",
    },
    "geo_events": {
        "label": "Geo-Telemetry",
        "signature": {"ip_country", "impossible_travel", "is_vpn"},
        "key": "txn_id",
    },
    "account_graph_nodes": {
        "label": "Account Graph Nodes",
        "signature": {"degree_in", "degree_out", "total_received_npr"},
        "key": "id",
    },
}

# Staging tables are all-TEXT on purpose: every cast happens in the core SQL,
# never through pandas type inference (e.g. 0/1 -> BIGINT can't cast to BOOLEAN).
STAGING_DDL = """
CREATE TABLE IF NOT EXISTS raw.fraud_labels (
    txn_id TEXT,
    is_fraud TEXT,
    fraud_type TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.customer_profiles (
    account_id TEXT,
    kyc_tier TEXT,
    risk_tier TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.geo_events (
    txn_id TEXT,
    ip_country TEXT,
    impossible_travel TEXT,
    is_vpn TEXT,
    km_from_home_district TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.account_graph_nodes (
    id TEXT,
    degree_in TEXT,
    degree_out TEXT,
    total_received_npr TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE raw.transactions_upload ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE raw.fraud_labels ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
"""

# Idempotent: staging is append-only, so re-uploads and corrected labels stack
# up as duplicate txn_ids. Only the newest version of each transaction and
# label survives (rn = 1), so ON CONFLICT never sees the same key twice.
#   - Partition on TRIM(txn_id) because that is the core conflict key.
#   - NULLS LAST so legacy rows without uploaded_at never outrank real uploads.
#   - ctid DESC breaks ties inside one file (all its rows share uploaded_at,
#     since a file loads in one transaction): the later row in the file wins.
CORE_TRANSFORM_SQL = """
WITH latest_transactions AS (
    SELECT *
    FROM (
        SELECT
            t.*,
            ROW_NUMBER() OVER (
                PARTITION BY TRIM(t.txn_id)
                ORDER BY t.uploaded_at DESC NULLS LAST, t.ctid DESC
            ) AS rn
        FROM raw.transactions_upload t
        WHERE NULLIF(TRIM(t.txn_id), '') IS NOT NULL
    ) ranked
    WHERE rn = 1
),
latest_labels AS (
    SELECT *
    FROM (
        SELECT
            f.*,
            ROW_NUMBER() OVER (
                PARTITION BY TRIM(f.txn_id)
                ORDER BY f.uploaded_at DESC NULLS LAST, f.ctid DESC
            ) AS rn
        FROM raw.fraud_labels f
        WHERE NULLIF(TRIM(f.txn_id), '') IS NOT NULL
    ) ranked
    WHERE rn = 1
)
INSERT INTO core.transactions (
    transaction_id,
    transaction_timestamp,
    account_id,
    amount,
    transaction_type,
    transaction_channel,
    ip_address,
    is_fraud
)
SELECT
    TRIM(t.txn_id),
    CAST(t.timestamp AS TIMESTAMP),
    TRIM(t.account_id),
    CAST(t.amount_npr AS NUMERIC(15,2)),
    UPPER(TRIM(t.txn_type)),
    UPPER(TRIM(t.channel)),
    CAST(NULLIF(TRIM(t.ip_address), '') AS INET),
    COALESCE(CAST(NULLIF(TRIM(f.is_fraud), '') AS BOOLEAN), FALSE)
FROM latest_transactions t
LEFT JOIN latest_labels f ON TRIM(t.txn_id) = TRIM(f.txn_id)
ON CONFLICT (transaction_id)
DO UPDATE SET is_fraud = EXCLUDED.is_fraud;
"""


@st.cache_resource
def get_engine():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text(STAGING_DDL))
    return engine


def db_error_summary(e: SQLAlchemyError) -> str:
    # First line of the database's own message, e.g. 'invalid input syntax for
    # type timestamp: "abc"' -- useful to the operator, no stack or SQL text.
    return str(getattr(e, "orig", e)).strip().splitlines()[0]


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def detect_routes(columns) -> list[str]:
    cols = set(columns)
    return [table for table, route in ROUTES.items() if route["signature"] <= cols]


def ingest_csv(file, table: str, engine) -> tuple[int, list[str]]:
    """Append the CSV to raw.<table> in one transaction. Returns (rows, added_columns)."""
    added = []
    rows = 0
    with engine.begin() as conn:
        quote = conn.dialect.identifier_preparer.quote
        for i, chunk in enumerate(pd.read_csv(file, chunksize=CSV_CHUNK_SIZE, dtype=str)):
            chunk = normalize_headers(chunk)
            if i == 0:
                # Raw is a landing zone: new columns in an extract become TEXT
                # columns rather than failing the append.
                existing = {c["name"] for c in inspect(conn).get_columns(table, schema="raw")}
                for col in chunk.columns:
                    if col not in existing:
                        conn.execute(text(f"ALTER TABLE raw.{quote(table)} ADD COLUMN {quote(col)} TEXT"))
                        added.append(col)
            chunk.to_sql(
                table,
                conn,
                schema="raw",
                if_exists="append",
                index=False,
                chunksize=SQL_CHUNK_SIZE,
                method="multi",
            )
            rows += len(chunk)
    return rows, added


def run_core_transformation(engine) -> int:
    with engine.begin() as conn:
        return conn.execute(text(CORE_TRANSFORM_SQL)).rowcount


def staging_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: conn.execute(text(f"SELECT COUNT(*) FROM raw.{table}")).scalar()
            for table in ROUTES
        }


# ---------------------------------------------------------------- UI

st.set_page_config(page_title="NIGRANI Data Portal", layout="wide")
st.title("NIGRANI Smart Ingestion Router")
st.write("Upload any departmental CSV extract. The router detects its type and stages it automatically.")

if not DATABASE_URL:
    st.error("DATABASE_URL is not set. Add it to a .env file next to app.py.")
    st.stop()

try:
    engine = get_engine()
except SQLAlchemyError as e:
    log.exception("Database connection failed")
    st.error(f"Could not connect to the database: {db_error_summary(e)}")
    st.stop()

# Step 1: upload and route
st.header("1. Ingest Raw Extract")
uploaded_file = st.file_uploader("Upload Bank CSV Extract", type=["csv"])

if uploaded_file is not None:
    try:
        preview_df = normalize_headers(pd.read_csv(uploaded_file, nrows=5, dtype=str))
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as e:
        log.exception("Could not parse %s", uploaded_file.name)
        st.error(f"Could not read this file as CSV: {type(e).__name__}")
        st.stop()
    uploaded_file.seek(0)

    st.subheader("Raw Data Preview")
    st.dataframe(preview_df)

    matches = detect_routes(preview_df.columns)
    if not matches:
        expected = "\n".join(
            f"- **{r['label']}**: {', '.join(sorted(r['signature']))}" for r in ROUTES.values()
        )
        st.error(f"Unrecognized file. Headers must include one of these column sets:\n{expected}")
        st.stop()

    if len(matches) == 1:
        target = matches[0]
        st.info(f"Auto-detected: **{ROUTES[target]['label']}** → `raw.{target}`")
    else:
        target = st.selectbox(
            "This file matches more than one type. Choose the destination:",
            matches,
            format_func=lambda t: f"{ROUTES[t]['label']} → raw.{t}",
        )

    key = ROUTES[target]["key"]
    if key not in preview_df.columns:
        st.error(
            f"Detected {ROUTES[target]['label']}, but the file has no `{key}` column, "
            "so its rows could never be joined. Add the column and re-upload."
        )
        st.stop()

    if st.button(f"Load into raw.{target}"):
        try:
            with st.spinner("Loading file in chunks..."):
                rows, added = ingest_csv(uploaded_file, target, engine)
            st.success(f"Loaded {rows:,} rows into raw.{target}.")
            if added:
                st.warning(f"New columns added to raw.{target}: {', '.join(added)}")
        except SQLAlchemyError as e:
            log.exception("Ingestion into raw.%s failed", target)
            st.error(f"Load failed and was rolled back: {db_error_summary(e)}")
        except (pd.errors.ParserError, UnicodeDecodeError) as e:
            log.exception("Could not parse %s", uploaded_file.name)
            st.error(f"Load failed and was rolled back: malformed CSV ({type(e).__name__})")

# Step 2: staging status + core build
st.header("2. Build Core Vault")
try:
    counts = staging_counts(engine)
    cols = st.columns(len(ROUTES))
    for col, (table, n) in zip(cols, counts.items()):
        col.metric(f"raw.{table}", f"{n:,} rows")
except SQLAlchemyError as e:
    log.exception("Could not read staging counts")
    st.error(f"Could not read staging tables: {db_error_summary(e)}")

if st.button("Run Core Transformation"):
    try:
        with st.spinner("Joining ledger with fraud labels..."):
            upserted = run_core_transformation(engine)
        st.success(f"Core transformation complete: {upserted:,} rows inserted/updated in core.transactions.")
        st.subheader("core.transactions (First 100 Rows)")
        st.dataframe(pd.read_sql("SELECT * FROM core.transactions LIMIT 100", engine))
    except SQLAlchemyError as e:
        log.exception("Core transformation failed")
        st.error(f"Core transformation failed and was rolled back: {db_error_summary(e)}")
