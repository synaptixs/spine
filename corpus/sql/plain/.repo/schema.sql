CREATE TABLE customer (
    id INT PRIMARY KEY,
    email VARCHAR(255)
);

CREATE TABLE orders (
    id INT PRIMARY KEY,
    customer_id INT REFERENCES customer(id),
    total INT
);

CREATE VIEW active_orders AS
SELECT id, total FROM orders;
