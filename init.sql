CREATE USER backend_user WITH PASSWORD 'user123'; -- Create a new user named backend_user with the password 'user123'
CREATE DATABASE myapp; -- Create a new database named myapp

\c myapp

GRANT ALL PRIVILEGES ON SCHEMA public TO backend_user;

CREATE TABLE test (id SERIAL, name VARCHAR(30)); -- Create a new table named test with an auto-incrementing id and a name field
INSERT INTO test (name) VALUES ('test1'); -- Insert a new row into the test table
