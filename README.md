<p align="center">
  <img src="assets/logo-any-background.png" alt="StockFlow logo" width="520">
</p>

<p align="center"><b>Smart inventory and stock management system</b></p>

StockFlow helps a small business know what it has, what is running low, and what to reorder. Every stock in and stock out is recorded, product quantities update automatically, and a dashboard shows the current picture at a glance.

## Platform direction

StockFlow is being developed as a scalable, multi-tenant inventory platform (small shops through multi-branch and warehouse operations), not only a single-shop app. Architecture decisions are in [`docs/adr/`](docs/adr/0001-architecture-decisions.md).

**Phase 0 (development foundation) is written but not yet closed:** it still needs its first full validation run. It provides a Python (FastAPI) API skeleton, PostgreSQL 16 with row-level-security tenant isolation covered by automated tests, migrations, Docker and a CI script. **Business functionality begins in later phases.**

- `dashboard/` is the existing static prototype on sample data. It is **not** connected to a database or to the new API.
- `backend/` is the new production application foundation (API, migrations, tests).

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
| Backend API | Phase 0 foundation written (health endpoint only); business endpoints planned |
| Development foundation (Phase 0) | Written, pending first full validation run |

## Project structure

```
stockflow/
├── README.md
├── schema.sql      original MySQL prototype schema (unchanged)
├── assets/         logo, banner, favicon files (unchanged)
├── dashboard/      existing static prototype (not connected to any database or API)
├── legacy/         reference copy of the MySQL schema
├── backend/        NEW production API foundation (Python / FastAPI)
│   ├── app/        config, db (tenant_tx), errors, money, health route
│   ├── migrations/ Alembic migrations (PostgreSQL)
│   ├── scripts/    init_env, bootstrap_db, ci
│   └── tests/      unit, integration, tenant-isolation and guard tests
├── docs/adr/       architecture decision records
└── docker-compose.yml, Dockerfile, .env.example
```

## Development foundation (Phase 0, Python)

Needs Python 3.12 and Docker Desktop. Windows `cmd`, from the repository root:

```
cd backend
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m scripts.init_env
docker compose -f ..\docker-compose.yml up -d --wait db mailpit
python -m scripts.bootstrap_db
alembic upgrade head
uvicorn app.main:create_app --factory --reload
```

Then open http://127.0.0.1:8000/health (it returns `{"status":"ok"}`).

Checks (inside `backend`): `ruff check .`, `pytest -m "not db"`, `pytest -m "db and not rls and not guard"`, `pytest -m rls`, `pytest -m guard`. The whole pipeline: `python -m scripts.ci`.

The API connects as the restricted `stockflow_app` role (no BYPASSRLS, owns nothing, no DDL). Migrations use the separate owner role and are never run by the API. The MySQL steps below apply only to the original prototype schema.

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
mysql -u root -p < schema.sql
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

Muhammad Ramzan
