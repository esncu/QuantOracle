DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'backend_user') THEN
        CREATE USER backend_user WITH PASSWORD 'user123';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE stocks TO backend_user;
GRANT USAGE ON SCHEMA public TO backend_user;
GRANT CREATE ON SCHEMA public TO backend_user;

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    pass_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE admin (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    pass_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Stores trained model + data file references per symbol/timeframe per user.
-- is_deleted = TRUE means user has flagged it for deletion (TMPDELETE).
-- Admin hard-deletes rows where is_deleted = TRUE via DELETE action.
CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol_name VARCHAR(255) NOT NULL,
    timeframe VARCHAR(50) NOT NULL,
    model_path VARCHAR(255) NOT NULL,
    data_path VARCHAR(255) NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE session (
    id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

-- One row per symbol the user has added to their dashboard view.
-- is_deleted mirrors data.is_deleted for display purposes.
CREATE TABLE dashboard (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol_name VARCHAR(255) NOT NULL,
    timeframe VARCHAR(50) NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE settings (
    id SERIAL PRIMARY KEY,
    api_key VARCHAR(255) DEFAULT ''
);

INSERT INTO settings (api_key) VALUES ('');

-- Candle data table — stores both historical (fake=FALSE) and forecast (fake=TRUE) rows.
CREATE TABLE candles (
    id SERIAL PRIMARY KEY,
    data_id INTEGER NOT NULL REFERENCES data(id) ON DELETE CASCADE,
    candle_close_timestamp BIGINT NOT NULL,
    open NUMERIC(12,4) NOT NULL,
    high NUMERIC(12,4) NOT NULL,
    low NUMERIC(12,4) NOT NULL,
    close NUMERIC(12,4) NOT NULL,
    fake BOOLEAN NOT NULL DEFAULT FALSE
);

-- -------------------------------------------------------------------------
-- Seed users  (passwords are bcrypt of "password123")
-- -------------------------------------------------------------------------
INSERT INTO users (name, pass_hash, role) VALUES
    ('alice',  '$2b$12$Sr6G1ga0WKnFgL3F74OybuYz1Tr6HubRStpRH391WORaO1snkA5lK', 'user'),
    ('bob',    '$2b$12$Sr6G1ga0WKnFgL3F74OybuYz1Tr6HubRStpRH391WORaO1snkA5lK', 'user'),
    ('charlie','$2b$12$Sr6G1ga0WKnFgL3F74OybuYz1Tr6HubRStpRH391WORaO1snkA5lK', 'user');


-- seed admin
INSERT INTO admin (name, pass_hash) VALUES ('quoracle', 'secure');

-- -------------------------------------------------------------------------
-- Seed data rows
-- -------------------------------------------------------------------------
INSERT INTO data (user_id, symbol_name, timeframe, model_path, data_path, is_deleted) VALUES
    (1, 'NVDA', '5M',  'models/alice/nvda_5m.pkl',  'data/alice/nvda_5m.json',  FALSE),
    (1, 'NVDA', '1D',  'models/alice/nvda_1d.pkl',  'data/alice/nvda_1d.json',  FALSE),
    (1, 'AAPL', '1H',  'models/alice/aapl_1h.pkl',  'data/alice/aapl_1h.json',  FALSE),
    (1, 'AAPL', '1W',  'models/alice/aapl_1w.pkl',  'data/alice/aapl_1w.json',  TRUE),  -- flagged
    (2, 'TSLA', '1D',  'models/bob/tsla_1d.pkl',    'data/bob/tsla_1d.json',    FALSE),
    (2, 'MSFT', '1H',  'models/bob/msft_1h.pkl',    'data/bob/msft_1h.json',    FALSE),
    (3, 'GOOG', '1D',  'models/charlie/goog_1d.pkl','data/charlie/goog_1d.json', FALSE);

-- -------------------------------------------------------------------------
-- Seed dashboard rows  (one per data row, mirroring is_deleted)
-- -------------------------------------------------------------------------
INSERT INTO dashboard (user_id, symbol_name, timeframe, is_deleted) VALUES
    (1, 'NVDA', '5M',  FALSE),
    (1, 'NVDA', '1D',  FALSE),
    (1, 'AAPL', '1H',  FALSE),
    (1, 'AAPL', '1W',  TRUE),
    (2, 'TSLA', '1D',  FALSE),
    (2, 'MSFT', '1H',  FALSE),
    (3, 'GOOG', '1D',  FALSE);

-- -------------------------------------------------------------------------
-- Seed candles for alice / NVDA / 1D
-- 30 historical daily bars ending 2026-05-30, then 5 forecast bars
-- Prices loosely modelled on NVDA ~$100-130 range
-- -------------------------------------------------------------------------
INSERT INTO candles (data_id, candle_close_timestamp, open, high, low, close, fake) VALUES
-- data_id=1 is alice/NVDA/5M — leave empty (no 5M seed data)
-- data_id=2 is alice/NVDA/1D — seed 30 historical + 5 forecast
(2, 1743292800, 100.20, 103.50, 99.80,  102.40, FALSE), -- 2025-03-30
(2, 1743379200, 102.40, 105.10, 101.90, 104.80, FALSE), -- 2025-03-31
(2, 1743638400, 104.80, 107.30, 104.20, 106.50, FALSE), -- 2025-04-03
(2, 1743724800, 106.50, 108.90, 105.60, 107.20, FALSE), -- 2025-04-04
(2, 1743811200, 107.20, 109.40, 106.10, 108.90, FALSE), -- 2025-04-07
(2, 1743897600, 108.90, 111.20, 108.00, 110.30, FALSE), -- 2025-04-08
(2, 1743984000, 110.30, 112.50, 109.50, 111.80, FALSE), -- 2025-04-09
(2, 1744243200, 111.80, 114.00, 111.10, 113.20, FALSE), -- 2025-04-12
(2, 1744329600, 113.20, 115.30, 112.40, 114.60, FALSE), -- 2025-04-13
(2, 1744416000, 114.60, 116.80, 113.70, 115.90, FALSE), -- 2025-04-14
(2, 1744502400, 115.90, 118.10, 115.20, 117.30, FALSE), -- 2025-04-15
(2, 1744588800, 117.30, 119.50, 116.40, 118.70, FALSE), -- 2025-04-16
(2, 1744848000, 118.70, 121.00, 117.80, 120.10, FALSE), -- 2025-04-19
(2, 1744934400, 120.10, 122.30, 119.20, 121.50, FALSE), -- 2025-04-20
(2, 1745020800, 121.50, 123.70, 120.60, 122.90, FALSE), -- 2025-04-21
(2, 1745107200, 122.90, 125.10, 122.00, 124.30, FALSE), -- 2025-04-22
(2, 1745193600, 124.30, 126.50, 123.40, 125.70, FALSE), -- 2025-04-23
(2, 1745452800, 125.70, 127.90, 124.80, 127.10, FALSE), -- 2025-04-26
(2, 1745539200, 127.10, 129.30, 126.20, 128.50, FALSE), -- 2025-04-27
(2, 1745625600, 128.50, 130.70, 127.60, 129.90, FALSE), -- 2025-04-28
(2, 1745712000, 129.90, 132.10, 129.00, 131.30, FALSE), -- 2025-04-29
(2, 1745798400, 131.30, 133.50, 130.40, 132.70, FALSE), -- 2025-04-30
(2, 1746057600, 132.70, 134.90, 131.80, 134.10, FALSE), -- 2025-05-03
(2, 1746144000, 134.10, 136.30, 133.20, 135.50, FALSE), -- 2025-05-04
(2, 1746230400, 135.50, 137.70, 134.60, 136.90, FALSE), -- 2025-05-05
(2, 1746316800, 136.90, 139.10, 136.00, 138.30, FALSE), -- 2025-05-06
(2, 1746403200, 138.30, 140.50, 137.40, 139.70, FALSE), -- 2025-05-07
(2, 1746662400, 139.70, 141.90, 138.80, 141.10, FALSE), -- 2025-05-10
(2, 1746748800, 141.10, 143.30, 140.20, 142.50, FALSE), -- 2025-05-11
(2, 1746835200, 142.50, 144.70, 141.60, 143.90, FALSE), -- 2025-05-12
-- 5 forecast bars (fake=TRUE)
(2, 1748044800, 143.90, 147.20, 143.10, 146.00, TRUE),  -- 2025-05-24
(2, 1748131200, 146.00, 149.50, 145.20, 148.30, TRUE),  -- 2025-05-25
(2, 1748217600, 148.30, 151.80, 147.40, 150.60, TRUE),  -- 2025-05-26
(2, 1748304000, 150.60, 154.10, 149.70, 152.90, TRUE),  -- 2025-05-27
(2, 1748390400, 152.90, 156.40, 152.00, 155.20, TRUE);  -- 2025-05-28

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO backend_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO backend_user;
