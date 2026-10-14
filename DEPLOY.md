# StockFlow — verify, upload, deploy

> **Honest status.** This code was written and syntax-checked (Python compile, JavaScript `node --check`)
> but **never executed against PostgreSQL**; the unit and database tests have not been run. Do step 1
> before you rely on it. If a test fails, send me the output and I will fix it.

## 1. Verify locally (one command at a time, inside `backend`, venv active)

1. Put your **development** Neon hostname in `.env` as `TEST_DATABASE_HOST=ep-xxxx.region.aws.neon.tech`
   (hostname only). `DATABASE_URL` and `MIGRATION_DATABASE_URL` must use that same host.
2. `pip install -e ".[dev]"`
3. `python -m ruff check .`   (if only import order is reported: `python -m ruff check . --fix`)
4. `python -m pytest -m "not db" -q`
5. `python -m alembic upgrade head`   (applies 0003 to 0009 to the DEV branch; refuses any other host)
6. `python -m pytest -m guard -q`
7. `python -m pytest -m "db and rls" -q`
8. `python -m uvicorn app.main:create_app --factory --port 8000` and open http://127.0.0.1:8000

The database tests TRUNCATE inventory and auth tables. They only start if the host guard passes.

## 2. Upload to GitHub
Upload the contents of this folder to `Rztech15/StockFlow` (branch `main`). Never upload `.env`
(it is in `.gitignore`). Your existing `dashboard/`, `assets/`, `legacy/` and `schema.sql` are untouched.

## 3. Production database (Neon)
Use a separate **production** branch/database. Roles `stockflow_owner` and `stockflow_app` must exist there
(Neon branches copy roles from their parent). Then either:
- GitHub → Settings → Secrets → add `MIGRATION_DATABASE_URL` (owner URL of the production branch), then
  Actions → "Migrate production database" → Run workflow (it runs `upgrade 0005`, then `upgrade 0007`, then
  `upgrade head`, so each group is committed before the next runs); or
- locally, in one terminal only: `set ALLOW_PRODUCTION_MIGRATION=I_UNDERSTAND`, set `MIGRATION_DATABASE_URL`
  to the production owner URL, run `python -m alembic upgrade 0005`, `python -m alembic upgrade 0007` and `python -m alembic upgrade head`, then close the terminal.

## 4. Vercel
New Project → import the repo → **Root Directory: `backend`** (framework: FastAPI/Other). Environment variables:

| Name | Value |
|---|---|
| `APP_ENV` | `production` |
| `APP_BASE_URL` | `https://<your-project>.vercel.app` (or your domain) |
| `ALLOWED_ORIGINS` | the same origin(s), comma separated, no trailing slash |
| `DATABASE_URL` | production **stockflow_app** URL (Neon pooled `-pooler` host is fine) |
| `PASSWORD_PEPPER_V1` | new random string, at least 32 characters (not the dev one) |
| `DB_POOL_MAX` | `3` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` | placeholders, e.g. `localhost`, `1025`, `StockFlow <no-reply@localhost>` (required by config; mail is not sent yet) |

Do **not** set `MIGRATION_DATABASE_URL` or `TEST_DATABASE_HOST` on Vercel (the app refuses to start with the owner URL in production).
Deploy, then open the site, create an account and log in. Preview deployments have different URLs; add them to
`ALLOWED_ORIGINS` if you want to log in there.

## What the app does now
- Accounts, workspaces (switch / create), members with roles (owner, admin, manager, staff, viewer).
- Products with categories, units (free text with suggestions) and a custom SKU convention (prefix, digits,
  next number; leave the SKU empty to auto-generate).
- Stock in/out with an append-only ledger; suppliers and purchase orders (receiving books the stock-in).
- Insights: demand forecast (moving average), suggested reorder quantities, supplier lead-time analysis and
  plain-language, rule-based insights. This is statistics on your own data, **not** machine learning or an AI model.
- Reports, an activity log (who did what), in-app alerts with per-user notification preferences.
- API keys (Bearer) with read or read/write scope, and CSV exports for products and movements.
- Plans (free / pro / business) with usage limits. **No payment provider is connected.** To change a plan an
  administrator runs, with the owner connection:
  `SELECT set_config('app.tenant_id', '<workspace uuid>', false); UPDATE public.workspace_settings SET plan = 'pro';`
- Light, dark and automatic themes; responsive layout for phones.

## API usage
Create a key under Settings, then: `curl -H "Authorization: Bearer sf_..." https://<your-site>/api/v1/products`.
Read keys can only read; read/write keys can also create products, movements, suppliers and orders. Keys can never
manage members or settings. CSV: `/api/v1/export/products.csv` and `/api/v1/export/movements.csv`.

## Known limits (not built)
- Direct QuickBooks/Xero/Shopify/WooCommerce connectors, webhooks and online payments (they need your own
  accounts, OAuth apps or API credentials). API keys and CSV exports are the integration points until then.
- Email: notifications are in-app only; no email verification, password reset or email invitations.
- A real AI model for insights (would need an API key and your decision to send inventory data to a third party).
- Per-IP rate limiting, product prices/costs and stock valuation, CSV import, foreign keys from products and
  stock movements to workspaces, a session clean-up job. The Docker image does not include the dashboard files.
Vercel's build settings were not verified by me; if the first deploy fails, send me the build log.
