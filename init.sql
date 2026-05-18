CREATE USER backend_user WITH PASSWORD 'user123';
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

CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol_name VARCHAR(255) NOT NULL,
    timeframe VARCHAR(50) NOT NULL,
    model_path VARCHAR(255) NOT NULL,
    data_path VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
);

CREATE TABLE session (
    id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE dashboard (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    symbol_name VARCHAR(255) NOT NULL,
    timeframe VARCHAR(50) NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE settings (
    id SERIAL PRIMARY KEY,
    api_key VARCHAR(255) DEFAULT''
);

INSERT INTO settings (api_key) VALUES ('');
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO backend_user; 
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO backend_user;