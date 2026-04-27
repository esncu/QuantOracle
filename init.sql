CREATE USER backend_user WITH PASSWORD 'user123';
GRANT CONNECT ON DATABASE stocks TO backend_user;
GRANT USAGE ON SCHEMA public TO backend_user;
GRANT CREATE ON SCHEMA public TO backend_user;


CREATE TABLE users (
    id SERIAL PRIMARY KEY, 
    name VARCHAR(255) NOT NULL UNIQUE, 
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
);

CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    symbol_name VARCHAR(255) NOT NULL,
    timeframe VARCHAR(50) NOT NULL,
    model_path VARCHAR(255) NOT NULL,
    data_path VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
);

CREATE TABLE session (
    id UUID PRIMARY KEY, 
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    expires_at TIMESTAMP NOT NULL    
);

CREATE TABLE admin (
    id SERIAL PRIMARY KEY, 
    name VARCHAR(255) NOT NULL UNIQUE, 
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
);

CREATE TABLE settings (
    id SERIAL PRIMARY KEY,
    api_key VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO settings (api_key) VALUES ('testingapikey12345');
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO backend_user; 
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO backend_user;