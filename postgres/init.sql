CREATE TABLE IF NOT EXISTS rust_device (
    id SERIAL PRIMARY KEY,
    mac TEXT NOT NULL,
    firmware TEXT NOT NULL
);
