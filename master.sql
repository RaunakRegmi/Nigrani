SQL Master 


CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS features;


CREATE TABLE IF NOT EXISTS raw.transactions_upload (
    raw_txn_id TEXT,
    raw_timestamp TEXT,
    raw_account_id TEXT,
    raw_amount TEXT,
    raw_txn_type TEXT,
    raw_channel TEXT,
    raw_ip_address TEXT,
    raw_country TEXT
);


CREATE TABLE IF NOT EXISTS core.transactions (
    transaction_id VARCHAR(50) PRIMARY KEY,
    transaction_timestamp TIMESTAMP NOT NULL,
    account_id VARCHAR(50) NOT NULL,
    amount NUMERIC(15, 2) NOT NULL,
    transaction_type VARCHAR(30),
    transaction_channel VARCHAR(30),
    ip_address INET,
    ip_country VARCHAR(3)
);


CREATE OR REPLACE VIEW features.velocity_snapshots AS
SELECT 
    transaction_id,
    account_id,
    transaction_timestamp,
    amount AS amount_npr,
    COUNT(*) OVER (PARTITION BY account_id ORDER BY transaction_timestamp RANGE BETWEEN INTERVAL '1 HOUR' PRECEDING AND CURRENT ROW) AS txn_count_1h,
    COUNT(*) OVER (PARTITION BY account_id ORDER BY transaction_timestamp RANGE BETWEEN INTERVAL '24 HOURS' PRECEDING AND CURRENT ROW) AS txn_count_24h,
    SUM(amount) OVER (PARTITION BY account_id ORDER BY transaction_timestamp RANGE BETWEEN INTERVAL '24 HOURS' PRECEDING AND CURRENT ROW) AS total_amount_24h_npr,
    CASE WHEN EXTRACT(ISODOW FROM transaction_timestamp) IN (6, 7) THEN TRUE ELSE FALSE END AS weekend_flag
FROM core.transactions;


CREATE OR REPLACE VIEW features.geo_events AS
SELECT 
    transaction_id,
    account_id,
    transaction_timestamp,
    ip_address,
    ip_country,
    LAG(ip_country) OVER (PARTITION BY account_id ORDER BY transaction_timestamp) AS prev_txn_country,
    EXTRACT(EPOCH FROM (transaction_timestamp - LAG(transaction_timestamp) OVER (PARTITION BY account_id ORDER BY transaction_timestamp))) / 60 AS prev_txn_time_delta_min,
    CASE 
        WHEN LAG(ip_country) OVER (PARTITION BY account_id ORDER BY transaction_timestamp) != ip_country 
        AND EXTRACT(EPOCH FROM (transaction_timestamp - LAG(transaction_timestamp) OVER (PARTITION BY account_id ORDER BY transaction_timestamp))) / 60 < 120 
        THEN TRUE ELSE FALSE 
    END AS impossible_travel
FROM core.transactions;