# ADR 0002: Backend language is Python (supersedes the TypeScript stack in ADR 0001)

**Decision.** The backend is Python 3.12: FastAPI, SQLAlchemy 2 (Core) with psycopg 3, Alembic, pydantic-settings, pytest, ruff.

**Reason.** The project owner works in Python; the codebase must be understandable and maintainable by them. Every architectural decision in ADR 0001 is language-independent and still applies unchanged: PostgreSQL 16, shared schema with `tenant_id` + forced RLS, ledger + balance cache, locations from day one, product variants, money as integer minor units, negative stock disabled, authentication behind an adapter, hosting decided later.

**Replaced.** Node, pnpm, TypeScript, Fastify, Kysely, dbmate, Vitest, Playwright, ESLint. The React web shell is dropped for now; `dashboard/` remains the static prototype.

**Mapping of controls.**
- No direct `process.env`: only `app/config.py` reads configuration (ruff bans `os.environ`/`os.getenv` elsewhere).
- No raw driver access: only `app/db.py` imports `psycopg`/`sqlalchemy` (ruff `TID251`); application code uses `Database.tenant_tx()`.
- Migrations: Alembic with the owner role, never run by the API.
- Lockfile: not yet generated (needs network). Generate one with `uv lock` or `pip-compile` on a connected machine and commit it.
