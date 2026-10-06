-- StockFlow: Smart inventory and stock management
-- Database: MySQL 8.x

CREATE DATABASE IF NOT EXISTS stockflow;
USE stockflow;

-- 1. Users (who logs in and records stock movements)
CREATE TABLE users (
  user_id      INT AUTO_INCREMENT PRIMARY KEY,
  full_name    VARCHAR(100) NOT NULL,
  email        VARCHAR(120) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role         ENUM('admin','manager','staff') NOT NULL DEFAULT 'staff',
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Categories
CREATE TABLE categories (
  category_id  INT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(80) NOT NULL UNIQUE
);

-- 3. Suppliers
CREATE TABLE suppliers (
  supplier_id  INT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(120) NOT NULL,
  phone        VARCHAR(30),
  email        VARCHAR(120),
  address      VARCHAR(255)
);

-- 4. Products (quantity is kept up to date by the trigger below)
CREATE TABLE products (
  product_id   INT AUTO_INCREMENT PRIMARY KEY,
  sku          VARCHAR(30) NOT NULL UNIQUE,
  name         VARCHAR(120) NOT NULL,
  category_id  INT,
  supplier_id  INT,
  unit_price   DECIMAL(10,2) NOT NULL DEFAULT 0,
  quantity     INT NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  min_level    INT NOT NULL DEFAULT 10,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_id) REFERENCES categories(category_id),
  FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
);

-- 5. Stock movements (every stock in / stock out is one row)
CREATE TABLE stock_movements (
  movement_id  INT AUTO_INCREMENT PRIMARY KEY,
  product_id   INT NOT NULL,
  user_id      INT NOT NULL,
  type         ENUM('IN','OUT') NOT NULL,
  quantity     INT NOT NULL CHECK (quantity > 0),
  note         VARCHAR(255),
  moved_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (product_id) REFERENCES products(product_id),
  FOREIGN KEY (user_id) REFERENCES users(user_id),
  INDEX idx_moved_at (moved_at)
);

-- 6. Purchase orders (created when "Reorder" is pressed)
CREATE TABLE purchase_orders (
  po_id        INT AUTO_INCREMENT PRIMARY KEY,
  supplier_id  INT NOT NULL,
  created_by   INT NOT NULL,
  status       ENUM('pending','received','cancelled') NOT NULL DEFAULT 'pending',
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
  FOREIGN KEY (created_by) REFERENCES users(user_id)
);

CREATE TABLE purchase_order_items (
  po_id        INT NOT NULL,
  product_id   INT NOT NULL,
  quantity     INT NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (po_id, product_id),
  FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
  FOREIGN KEY (product_id) REFERENCES products(product_id)
);

-- Triggers: block overselling, then update product stock automatically
DELIMITER //
CREATE TRIGGER trg_movement_check BEFORE INSERT ON stock_movements
FOR EACH ROW
BEGIN
  IF NEW.type = 'OUT' AND NEW.quantity >
     (SELECT quantity FROM products WHERE product_id = NEW.product_id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Not enough stock for this product';
  END IF;
END//

CREATE TRIGGER trg_movement_apply AFTER INSERT ON stock_movements
FOR EACH ROW
BEGIN
  UPDATE products
  SET quantity = quantity + IF(NEW.type = 'IN', NEW.quantity, -NEW.quantity)
  WHERE product_id = NEW.product_id;
END//
DELIMITER ;

-- View: products with their status (same logic as the dashboard)
CREATE VIEW v_product_status AS
SELECT p.product_id, p.sku, p.name, p.quantity, p.min_level,
       CASE WHEN p.quantity = 0 THEN 'out'
            WHEN p.quantity <= p.min_level THEN 'low'
            ELSE 'ok' END AS status
FROM products p
WHERE p.is_active = TRUE;

-- Sample data
INSERT INTO users (full_name, email, password_hash, role)
VALUES ('Admin', 'admin@stockflow.com', 'replace_with_hash', 'admin');

INSERT INTO categories (name) VALUES ('Electronics'), ('Stationery'), ('Accessories');
INSERT INTO suppliers (name, phone) VALUES ('TechSource Ltd', '0300-0000000'), ('PaperWorld', '0300-1111111');

INSERT INTO products (sku, name, category_id, supplier_id, unit_price, min_level) VALUES
('SF-1001', 'Wireless mouse', 1, 1, 12.00, 20),
('SF-1002', 'USB-C cable 1m', 1, 1, 4.50, 30),
('SF-1004', 'Notebook A5', 2, 2, 2.00, 40),
('SF-1005', 'Gel pens (box of 12)', 2, 2, 5.00, 15);

INSERT INTO stock_movements (product_id, user_id, type, quantity, note) VALUES
(1, 1, 'IN', 60, 'Opening stock'),
(2, 1, 'IN', 40, 'Opening stock'),
(3, 1, 'IN', 100, 'Opening stock'),
(4, 1, 'IN', 20, 'Opening stock'),
(1, 1, 'OUT', 12, 'Order #1'),
(2, 1, 'OUT', 28, 'Order #2');

-- Queries the dashboard needs
-- A) Summary numbers
SELECT COUNT(*) AS products,
       SUM(quantity) AS units_in_stock,
       SUM(status = 'low') AS low_stock,
       SUM(status = 'out') AS out_of_stock
FROM v_product_status;

-- B) Stock in and out, last 7 days
SELECT DATE(moved_at) AS day,
       SUM(CASE WHEN type = 'IN'  THEN quantity ELSE 0 END) AS stock_in,
       SUM(CASE WHEN type = 'OUT' THEN quantity ELSE 0 END) AS stock_out
FROM stock_movements
WHERE moved_at >= CURDATE() - INTERVAL 6 DAY
GROUP BY DATE(moved_at)
ORDER BY day;

-- C) Needs restocking (suggested reorder = twice the minimum minus current stock)
SELECT name, quantity, min_level, (min_level * 2 - quantity) AS reorder_qty
FROM v_product_status
WHERE status IN ('low', 'out')
ORDER BY quantity;
