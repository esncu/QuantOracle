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

-- users
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255) NOT NULL UNIQUE,
    pass_hash   VARCHAR(255) NOT NULL
);

-- admin  (single row, rubric requirement)
-- ---------------------------------------------------------------------------
CREATE TABLE admin (
    id        SERIAL PRIMARY KEY,
    name      VARCHAR(255) NOT NULL UNIQUE,
    pass_hash VARCHAR(255) NOT NULL
);

-- session
--   user_id: positive = users.id, negative = -(admin.id)
--   type:    NORMAL | RECOVERY
--   expires_at refreshed to NOW()+30min on every authenticated action
-- ---------------------------------------------------------------------------
CREATE TABLE session (
    id         UUID PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    type       VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- dashboard  (named containers for stock categorization like "Tech")
-- ---------------------------------------------------------------------------
CREATE TABLE dashboard (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       VARCHAR(255) NOT NULL,
    delete_set BOOLEAN NOT NULL DEFAULT FALSE
    UNIQUE (user_id, name)
);

-- data  (one row per symbol+timeframe inside a dashboard)
--   data_path  → ./data/{user_id}/{symbol}/{timeframe}.json
--   model_path → ./data/{user_id}/{symbol}/{timeframe}_model.pt  (future)
-- ---------------------------------------------------------------------------
CREATE TABLE data (
    id           SERIAL PRIMARY KEY,
    dashboard_id INTEGER NOT NULL REFERENCES dashboard(id) ON DELETE CASCADE,
    symbol_name  VARCHAR(255) NOT NULL,
    timeframe    VARCHAR(50) NOT NULL,
    model_path   VARCHAR(512),
    data_path    VARCHAR(512),
    delete_set   BOOLEAN NOT NULL DEFAULT FALSE
    UNIQUE (dashboard_id, symbol_name, timeframe)
);

-- messages  (user feedback to admin)
-- ---------------------------------------------------------------------------
CREATE TABLE messages (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message    VARCHAR(1000) NOT NULL,
    sent_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Seed: alice (password: password123)
-- ---------------------------------------------------------------------------
INSERT INTO users (email, name, pass_hash) VALUES
    ('alice@example.com', 'alice', '$2b$12$L86TXWLVtMsTMd0Bko.x/eCK5Z.Ro3XLShsDhzpB5BUYIiGrjY8DO');

-- Seed: admin quoracle (password: secure)
-- ---------------------------------------------------------------------------
INSERT INTO admin (name, pass_hash) VALUES
    ('quoracle', '$2b$12$GKCrVkS08hxX.v59wxmKj.11ym/3OR4BONI64FkHTAoAzLiVAhow2');


-- Seed: two dashboards for alice
-- ---------------------------------------------------------------------------
INSERT INTO dashboard (user_id, name) VALUES
    (1, 'Tech'),
    (1, 'Watchlist');

GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO backend_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO backend_user;
