-- 1. CLEAN RESET
DROP SCHEMA IF EXISTS features CASCADE;
DROP SCHEMA IF EXISTS core CASCADE;
DROP SCHEMA IF EXISTS raw CASCADE;

CREATE SCHEMA raw;
CREATE SCHEMA core;
CREATE SCHEMA features;

-- 2. RAW STAGING TABLE
-- Accepts raw strings directly from CSV before sanitization
CREATE TABLE raw.transactions_upload (
    txn_id TEXT,
    timestamp TEXT,
    account_id TEXT,
    merchant_id TEXT,
    txn_type TEXT,
    amount_npr TEXT,
    currency TEXT,
    channel TEXT,
    device_id TEXT,
    ip_address TEXT,
    mcc TEXT,
    terminal_id TEXT,
    session_id TEXT,
    auth_method TEXT,
    retry_count TEXT,
    latency_ms TEXT,
    is_fraud TEXT,
    fx_rate TEXT,
    memo TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. CORE VAULT TABLE (Production Source of Truth)
CREATE TABLE core.transactions (
    transaction_id VARCHAR(64) PRIMARY KEY,
    transaction_timestamp TIMESTAMP NOT NULL,
    account_id VARCHAR(64) NOT NULL,
    amount NUMERIC(15,2) NOT NULL,
    transaction_type VARCHAR(32) NOT NULL,
    transaction_channel VARCHAR(32) NOT NULL,
    ip_address INET,
    ip_country VARCHAR(3),
    is_fraud BOOLEAN DEFAULT FALSE
);

-- 4. FEATURE ENGINE VIEWS

-- A. Velocity Feature View
CREATE OR REPLACE VIEW features.velocity_snapshots AS
SELECT 
    transaction_id,
    account_id,
    transaction_timestamp,
    amount AS amount_npr,
    COUNT(*) OVER (
        PARTITION BY account_id 
        ORDER BY transaction_timestamp 
        RANGE BETWEEN INTERVAL '1 HOUR' PRECEDING AND CURRENT ROW
    ) AS txn_count_1h,
    COUNT(*) OVER (
        PARTITION BY account_id 
        ORDER BY transaction_timestamp 
        RANGE BETWEEN INTERVAL '24 HOURS' PRECEDING AND CURRENT ROW
    ) AS txn_count_24h,
    SUM(amount) OVER (
        PARTITION BY account_id 
        ORDER BY transaction_timestamp 
        RANGE BETWEEN INTERVAL '24 HOURS' PRECEDING AND CURRENT ROW
    ) AS total_amount_24h_npr,
    CASE 
        WHEN EXTRACT(ISODOW FROM transaction_timestamp) IN (6, 7) THEN TRUE 
        ELSE FALSE 
    END AS weekend_flag
FROM core.transactions;

-- B. Geographic Anomaly Feature View
CREATE OR REPLACE VIEW features.geo_events AS
SELECT 
    transaction_id,
    account_id,
    transaction_timestamp,
    ip_address,
    LAG(ip_address) OVER (
        PARTITION BY account_id 
        ORDER BY transaction_timestamp
    ) AS prev_ip_address,
    EXTRACT(EPOCH FROM (
        transaction_timestamp - LAG(transaction_timestamp) OVER (
            PARTITION BY account_id 
            ORDER BY transaction_timestamp
        )
    )) / 60 AS prev_txn_time_delta_min
FROM core.transactions;