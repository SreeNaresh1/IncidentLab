CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL
);

INSERT INTO products (id, name, price) VALUES
    (42, 'Wireless Mouse', 24.99),
    (7, 'Mechanical Keyboard', 89.99),
    (13, 'USB-C Hub', 34.50)
ON CONFLICT (id) DO NOTHING;
