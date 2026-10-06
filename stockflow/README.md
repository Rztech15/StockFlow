<p align="center">
  <img src="assets/logo-any-background.png" alt="StockFlow logo" width="520">
</p>

<p align="center"><b>Smart inventory and stock management system</b></p>

StockFlow helps a small business know what it has, what is running low, and what to reorder. Every stock in and stock out is recorded, product quantities update automatically, and a dashboard shows the current picture at a glance.

## Features

- Dashboard with summary numbers: products, units in stock, low stock, out of stock
- Stock in and out chart for the last 7 days
- Needs restocking list with a Reorder button
- Product table with search and All / Low / Out filters
- Works on phone and laptop, with light and dark themes
- Database that blocks overselling and updates stock automatically

## Project status

| Part | Status |
| --- | --- |
| Brand and logo | Done |
| Dashboard UI | Done, runs on sample data (not yet connected to the database) |
| Database schema | Done, written for MySQL 8 |
| Backend API | Planned |

## Project structure

```
stockflow/
├── README.md
├── assets/       logo, banner, favicon files
├── database/
│   └── schema.sql
└── dashboard/
    └── index.html
```

## Setup

### 1. Get the project

```bash
git clone https://github.com/<your-username>/stockflow.git
cd stockflow
```

### 2. Run the dashboard

No install needed. Open `dashboard/index.html` in a browser by double-clicking it.

To serve it locally instead:

```bash
cd dashboard
python3 -m http.server 8000
```

Then open http://localhost:8000.

### 3. Create the database

You need MySQL 8 or newer.

```bash
mysql -u root -p < database/schema.sql
```

This creates the `stockflow` database, all tables, triggers, a status view, and a few sample rows. Check it worked:

```sql
USE stockflow;
SELECT * FROM v_product_status;
```

Try a stock movement. The product quantity updates by itself:

```sql
INSERT INTO stock_movements (product_id, user_id, type, quantity, note)
VALUES (1, 1, 'OUT', 5, 'Test sale');
```

Trying to take out more than what is in stock gives the error "Not enough stock for this product".

### 4. Put the dashboard online (optional)

1. Push the repository to GitHub.
2. Open Settings, then Pages.
3. Choose the `main` branch and the root folder, then Save.
4. After a minute the dashboard is live at `https://<your-username>.github.io/stockflow/dashboard/`.

## Database overview

| Table | Purpose |
| --- | --- |
| `users` | People who log in (admin, manager, staff) |
| `categories` | Product groups |
| `suppliers` | Who you buy from |
| `products` | Items, current quantity and minimum level |
| `stock_movements` | Every stock in and out |
| `purchase_orders` and `purchase_order_items` | Reorders sent to suppliers |

A product is **Out** at 0 units, **Low** at or below its minimum level, and **In stock** otherwise.

## Roadmap

- [ ] Backend API (login, products, movements, reorders)
- [ ] Connect the dashboard to the database
- [ ] Barcode scanning
- [ ] CSV import and export
- [ ] Email alerts for low stock

## Brand

| | |
| --- | --- |
| Navy | `#1B2742` |
| Slate blue | `#2C3E66` |
| Orange | `#F26A1B` to `#FFB547` |
| Font | Poppins |

Which logo file to use:

- White background: `assets/logo-horizontal.png`
- Dark background: `assets/logo-horizontal-dark.png`
- Unknown background: `assets/logo-any-background.png`
- App icon: `assets/logo-icon-512.png`
- Favicon: `assets/favicon.ico`

## Author

Your Name. Add your link here.
