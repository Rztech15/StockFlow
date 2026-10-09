> **Update:** the backend language changed to Python; see `0002-python-backend.md`. Technology names for Node/TypeScript below are superseded; all other decisions stand.

# StockFlow: Phase 0 Architecture Decision Record (Final)

Scope: decisions only. No scaffold, migrations or application code. `dashboard/` and `assets/` stay untouched.

## Part 1: Decisions

Format per decision: **Rec** (final recommendation) · **Why** · **Alt** (alternatives) · **Cons** (consequences) · **Rev** (reversal difficulty) · **Sec** (security).

### D1. PostgreSQL 16
- **Rec:** PostgreSQL 16 as the only datastore (data, jobs, rate-limit counters).
- **Why:** Native RLS, partial indexes, `timestamptz`, `SKIP LOCKED` queues, strong constraints. Nothing is in production, so switching from the MySQL prototype is cheapest now.
- **Alt:** MySQL 8 (no RLS, so isolation would rest on app code alone); managed multi-model DBs (unneeded).
- **Cons:** `schema.sql` becomes `legacy/`; the managed provider must allow custom roles.
- **Rev:** Hard after data exists. Decided now for that reason.
- **Sec:** Enables D2.

### D2. Shared schema, `tenant_id`, forced RLS
- **Rec:** One schema; `tenant_id` on every tenant-owned table; RLS enabled and **forced** on all of them; composite FKs `(tenant_id, id)`. Details in Part 2, section I.
- **Why:** Cheapest to operate for a small team; RLS makes isolation a database guarantee, not a convention.
- **Alt:** Schema-per-tenant (migration cost multiplies); DB-per-tenant (reserved for a future enterprise tier); app-only `WHERE` filters (rejected: one missed clause leaks data).
- **Cons:** Every query runs in a context-setting transaction; small planner overhead; tests must run as the restricted role.
- **Rev:** Very hard. Treated as a security invariant.
- **Sec:** Primary tenant barrier. Limit: RLS defends against developer error, not SQL injection (an attacker with arbitrary SQL could set the context). Mitigation: parameterised queries only, least-privilege roles, tests.

### D3. Authoritative ledger + transactional balance cache
- **Rec:** Immutable `inventory_ledger`; `stock_balances` maintained in the same transaction. Ledger wins on any disagreement.
- **Why:** Trustworthy history, audit, rebuildable balances, fast reads.
- **Alt:** Quantity column only (current prototype: no history, drift, races); pure event sourcing (needless complexity).
- **Cons:** Two writes per movement; needs reconciliation job.
- **Rev:** Retrofitting later is very hard.
- **Sec:** Append-only enforced by grants and triggers; every row names its actor.

### D4. Locations from day one, default "Main"
- **Rec:** `locations` table; tenant provisioning creates one default location in the same transaction; every balance and ledger row has a `location_id`. Default location cannot be archived or deleted. UI hides location pickers while a tenant has one location.
- **Why:** Multi-branch (Phase 5) then needs no data migration.
- **Alt:** Add locations later (rewrites every inventory table).
- **Cons:** Slightly wider keys now.
- **Rev:** Hard later, trivial now.
- **Sec:** Location-scoped roles become possible later.

### D5. Product → ProductVariant (one default), single unit
- **Rec:** `products` hold shared data; `product_variants` hold SKU, barcode, price, cost. Creating a product always creates one `is_default` variant in the same transaction (partial unique index: one default per product). **Stock, POs and ledger reference `variant_id` only.** SKU unique per tenant on variants. A plain `unit` text label on the product is informational; no unit conversion.
- **Why:** Avoids the most painful later migration (stock keyed to product) without building variant UX.
- **Alt:** Product-level stock (rejected); full attribute/option matrix (deferred).
- **Cons:** One extra join; API hides the default variant for simple products.
- **Rev:** Hard if skipped, easy to extend.
- **Sec:** Composite FKs prevent cross-tenant variant references.

### D6 (amended). Authentication behind an adapter
- **Rec:** Five separate layers (Part 2, section J). MVP implements a minimal `LocalPasswordProvider` behind an `IdentityProvider` interface. The application **always issues its own session**, whatever the provider.
- **Why:** A provider swap (OIDC, SSO, hosted or self-hosted IdP) then changes only authentication, never memberships, roles, permissions or authorization.
- **Alt:** Hosted IdP from day one (Auth0/Clerk/Cognito: faster MFA/SSO, but cost, lock-in, and a vendor dependency in every dev setup); self-hosted IdP (Keycloak/Zitadel/Ory: mature, but a whole extra service to operate). Both stay open. Trigger to adopt one: first SSO/MFA/social-login requirement or compliance ask.
- **Cons:** We own email verification, reset and abuse controls until then.
- **Rev:** Easy by design, if no code outside the adapter reads provider-specific data.
- **Sec:** See section J; the full controls list is mandatory, not optional.

### D7 (carried forward). Costing
- **Rec:** Record `unit_cost_minor` on receipt ledger rows only. No valuation or FIFO now. **Rev:** easy.

### D8. Monorepo (pnpm workspaces)
- **Rec:** One repo, shared contracts package. **Why:** Shared Zod schemas, money utils and permission catalogue; one CI. **Alt:** Polyrepo (type drift). **Cons:** Needs workspace discipline. **Rev:** Moderate. **Sec:** One place to run secret scanning and audits.

### D9. Hosting and data residency: DECISION GATE
- **Rec:** **Not chosen yet.** Until decided: development and staging use synthetic data only, and no real customer data enters any environment. The stack is provider-neutral (OCI containers + PostgreSQL 16 with custom roles and PITR).
- **Decide by:** end of Phase 3, before the first production database.
- **Criteria:** region(s) and legal residency of customer data (get legal advice for the target markets); managed Postgres allowing non-superuser role setup and RLS; PITR backups; price; latency to users; exit path.
- **Alt:** Choosing a provider now (premature). **Cons:** Staging fidelity is limited until then. **Rev:** Moving a live DB later is costly, hence the gate. **Sec:** Residency may be a legal obligation.

### D10. Money as integer minor units
- **Rec:** `tenants.currency_code` (ISO 4217) plus a `currencies(code, minor_unit_exponent)` reference table. All amounts are `BIGINT` minor units. PKR 1,250.50 = **125050** (exponent 2); JPY ¥500 = 500 (exponent 0); KWD 1.250 = 1250 (exponent 3). Parsing and formatting only through one `Money` utility in `packages/contracts` that parses decimal **strings** (never floats) and rejects excess precision. API amounts are integers; values over 2^53 are rejected. Tenant currency is immutable once any monetary record exists. No conversion.
- **Why:** Exact arithmetic, currency-correct rounding rules.
- **Alt:** `DECIMAL`/floats (rounding bugs). **Cons:** Per-unit prices finer than the minor unit (e.g. bulk goods) are not representable; defer until a real case appears. **Rev:** Moderate. **Sec:** Prevents rounding-based discrepancies.

### D11. Negative stock disabled
- **Rec:** `CHECK (on_hand >= 0)` on balances plus the atomic guard (section K). No tenant toggle in the MVP.
- **Why:** The core guarantee stays unconditional. **Alt:** Per-tenant setting (would require removing the CHECK). **Cons:** Enabling it later is a deliberate migration. **Rev:** Easy to relax, hard to re-tighten. **Sec:** n/a.

### D12. Pricing/plans deferred
- **Rec:** No plan table, column, limits or entitlement code. **Why:** No requirement. **Rev:** Trivial to add.

---

## Part 2: Architecture

### A. Final technology stack
| Concern | Choice |
| --- | --- |
| Language/runtime | TypeScript (strict), Node.js 22 LTS |
| API | Fastify; Zod validation; REST `/api/v1`; RFC 9457 errors |
| DB | PostgreSQL 16, **Kysely** (typed SQL), `kysely-codegen` |
| Migrations | **dbmate** (plain SQL, forward-only in prod) |
| Jobs | Postgres-backed (`pg-boss`), same image as API |
| Frontend | React, Vite, TypeScript, TanStack Query, React Router |
| Passwords | `argon2` (argon2id) |
| Email | SMTP via `nodemailer` behind a `Mailer` interface |
| Tests | Vitest, Testcontainers/CI Postgres, Playwright |
| Tooling | pnpm, ESLint, Prettier, Docker, gitleaks |
| Excluded | Microservices, Kafka, Redis, GraphQL, ML, FIFO, multi-currency, native apps |

### B. Monorepo structure
```
stockflow/
  apps/
    api/            src/modules/<name>/{routes,service,repository,schema}.ts
                    src/platform/{config,auth,tenancy,audit,errors,jobs}
                    src/server.ts  src/worker.ts
    web/            React app (reuses dashboard tokens; built later)
  packages/
    contracts/      Zod schemas, Money, permission catalogue, error codes
    db/             tx helper (withTenantTx), generated types
  db/
    migrations/     SQL files
    bootstrap/      role creation script (dev + prod runbook)
  assets/           existing, unchanged
  dashboard/        existing prototype, unchanged
  legacy/           copy of database/schema.sql (original not deleted)
  docs/adr/         this document
  docker-compose.yml  .env.example  .nvmrc
```
Rule: modules talk through services/events, never each other's tables.

### C. Environment and configuration
- Config is read once in `platform/config.ts`, validated with Zod; **the process refuses to start** on a missing or malformed value. No other code reads `process.env`.
- Variables: `NODE_ENV`, `APP_BASE_URL`, `ALLOWED_ORIGINS`, `DATABASE_URL` (runtime role `stockflow_app`), `MIGRATION_DATABASE_URL` (owner role; deploy job only, never in the running app), `PASSWORD_PEPPER_V1`, `SMTP_*`/mail credentials.
- `.env.example` has names and safe placeholders only; `.env` is gitignored (already). Production secrets come from the host's secret manager; CI uses its secret store. gitleaks runs in CI and as a pre-commit hook. Secrets are never logged; config objects are redacted when printed.
- Pepper is versioned (`v1`, `v2`) so it can be rotated: the hash stores the version, and users are rehashed at next login.

### D. Database migration strategy
- dbmate SQL files, sequential timestamps, applied by `MIGRATION_DATABASE_URL` (owner) in a dedicated step, never at API boot.
- **Roles (created by `db/bootstrap`, not by migrations):** `stockflow_owner` (owns objects, runs migrations), `stockflow_app` (runtime: no `BYPASSRLS`, not owner, no DDL). No cross-tenant role exists; background jobs enumerate tenants through a `SECURITY DEFINER` function and then process each tenant in a normal tenant context.
- Expand/contract: add, backfill, switch, remove in separate releases; no destructive change in the same release as its replacement.
- **CI guard:** a test inspects `pg_catalog` and fails if any table with a `tenant_id` column lacks `ENABLE` + `FORCE` RLS and a policy, or any ledger/audit table grants UPDATE/DELETE.
- Applying to an empty DB must pass in CI; schema drift (committed vs migrated) is checked.
- Legacy MySQL schema is not migrated; no production data exists. Sample data becomes a synthetic seed script.

### E. Local development
`corepack enable` → `pnpm install` → `docker compose up -d db mailpit` → `pnpm db:bootstrap && pnpm db:migrate && pnpm db:seed` (two synthetic tenants) → `pnpm dev`. Node pinned by `.nvmrc`/`engines`. Mail is caught by Mailpit. Seed data is synthetic only. The API connects as `stockflow_app` locally too, so RLS is always exercised.

### F. Docker
- Local: compose runs Postgres 16 (init script runs role bootstrap) and Mailpit; API and web run on the host for fast reload.
- Production image: multi-stage, `node:22-slim`, non-root user, production deps only, no secrets baked in, healthcheck, read-only filesystem where possible. The web build is static assets served by the API container (same origin, which simplifies cookies and CSRF). The worker is the same image with a different command. Base images pinned and scanned.

### G. CI (kept portable: logic lives in `pnpm` scripts)
Blocking stages: frozen-lockfile install → lint + typecheck → unit tests → start Postgres, run bootstrap + migrations from scratch → **integration tests as `stockflow_app`** → **tenant-isolation suite** → **inventory concurrency suite** → catalog guard (RLS/grant check) → build → `pnpm audit` → gitleaks → image build + vulnerability scan. Protected main branch; no merge on red.

### H. Testing strategy
- Unit: money parsing/formatting, permission checks, status and reorder math.
- Integration (real Postgres): repositories, transactions, migrations, RLS.
- **Gate suites** (never skipped): tenant isolation (section I), inventory concurrency (section K), auth abuse tests (section J).
- API contract tests; Playwright E2E for: sign up → verify email → create product → receive PO → adjust stock → logout.
- Property/invariant check: after any randomized concurrent workload, `SUM(ledger.delta) = balance` for every variant/location and no balance is negative.

### I. Tenant-isolation strategy

**How the DB learns the context.** Every database access goes through one helper, `withTenantTx(ctx, fn)`: it opens a transaction and runs
```sql
SELECT set_config('app.tenant_id', $1, true),
       set_config('app.user_id',   $2, true);
```
`true` makes the setting transaction-local, so it cannot leak between pooled connections (safe under PgBouncer transaction pooling). Direct use of the pool is blocked by an ESLint restricted-import rule. The tenant ID comes only from a verified session and an **active membership check**, never from request bodies or client-supplied IDs.

**Policies (template, applied to every tenant-owned table):**
```sql
ALTER TABLE t ENABLE ROW LEVEL SECURITY;
ALTER TABLE t FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON t
  USING      (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
-- app.current_tenant_id() = NULLIF(current_setting('app.tenant_id', true), '')::uuid
```
- **Fail closed:** no context means NULL, which matches nothing.
- One `FOR ALL` policy covers SELECT, INSERT, UPDATE and DELETE; `WITH CHECK` stops a row being written or moved into another tenant.
- **Append-only tables** (`inventory_ledger`, `audit_log`): separate `FOR SELECT` and `FOR INSERT` policies, `UPDATE/DELETE` revoked from `stockflow_app`, plus a trigger that rejects them for any role.
- **Identity tables** (`users`, `auth_identities`, `sessions`, tokens) are not tenant-owned: policy is `id/user_id = app.current_user_id()`. Pre-login lookups (by email) use narrow `SECURITY DEFINER` functions returning only what is needed. `memberships` is visible when `user_id` is the current user or `tenant_id` is the current tenant. Workspace switching runs a user-scoped transaction first to verify membership.
- **Composite FKs** `(tenant_id, x_id) → (tenant_id, id)` make cross-tenant references impossible even for the owner role.

**Automated tests** (run as `stockflow_app`, two seeded tenants A and B, tables discovered from the catalog so new tables are covered automatically):
1. Reading: as A, `SELECT` B's rows from every tenant table → 0 rows.
2. Writing: as A, `INSERT` with B's `tenant_id` → denied; `UPDATE` B's rows → 0 rows; moving an A row to B → denied.
3. Deleting: as A, `DELETE` B's rows → 0 rows.
4. Inventory: A cannot read or adjust B's balances or ledger; a movement referencing B's variant or location fails.
5. Purchase orders: A cannot read, edit, submit or receive B's POs or lines; a PO line pointing at B's variant fails the composite FK.
6. Audit logs: A cannot read B's audit rows; nobody can update or delete audit rows, including inside their own tenant.
7. No context / empty context → zero rows everywhere.
8. Context isolation: the setting does not persist after commit or on a reused pooled connection.
9. API level: user from A with a valid token requests B's resource IDs on every endpoint → 404, never 200 or 403-vs-404 oracle.
10. Catalog guard: every `tenant_id` table has forced RLS and a policy.

### J. Authentication abstraction

| Layer | Responsibility | Where it lives | Replaced by IdP? |
| --- | --- | --- | --- |
| 1. Authentication | Prove who the person is | `IdentityProvider` adapter | **Yes** |
| 2. Application identity | Stable internal user | `users`, `auth_identities(provider, subject)` | No |
| 3. Membership | User ↔ workspace, status | `memberships` | No |
| 4. Roles and permissions | What a role may do | `roles`, `role_permissions`; permission catalogue in `contracts` | No |
| 5. Authorization | Decide per request | `authorize(ctx, permission, resource?)` policy layer | No |

```ts
interface IdentityProvider {
  authenticate(input: unknown): Promise<AuthAssertion>;   // {provider, subject, email, emailVerified}
  // optional: startFlow/handleCallback for OIDC
}
```
The assertion maps to a `users` row through `auth_identities`. **Accounts are never auto-linked by email unless the provider asserts a verified email** (prevents takeover). The app creates its own session after any provider succeeds, so revocation, workspace switching and permissions never depend on the IdP. RLS is only the tenant barrier; permission checks live in layer 5 (deny by default).

**MVP: minimal `LocalPasswordProvider`.** Controls (all required):
- **Password hashing:** argon2id (about 19 to 64 MiB memory, 2 to 3 iterations, tuned on target hardware), unique salt, versioned secret pepper, rehash on login when parameters or pepper version change. Minimum length 12, maximum 128, no composition rules; reject known-breached passwords (k-anonymity check or local list).
- **Session strategy:** opaque 256-bit random token in a `Secure; HttpOnly; SameSite=Lax` cookie (`__Host-` prefix); only its SHA-256 is stored. Server-side sessions record user, active workspace, created/last-seen, user agent and IP. Idle timeout 7 days, absolute 30 days. Permissions are loaded per request (no long-lived claims), so role changes and removed memberships apply immediately.
- **Refresh/rotation:** the token is rotated on login, password change, workspace switch and every 24 h of activity, with a 60 s overlap for concurrent tabs. Presenting a rotated-out token after the overlap revokes the whole session family. If public API or mobile clients appear, add short-lived (about 10 min) access JWTs with rotating refresh tokens and the same reuse detection; API keys stay separate, hashed and scoped.
- **Revocation:** delete the session row; "log out everywhere"; password change/reset revokes all other sessions; disabling a membership blocks the next request.
- **Email verification:** single-use random token (hashed in DB), 24 h expiry; unverified users cannot create or join workspaces or export data.
- **Password reset:** single-use hashed token, 1 h expiry, identical response whether or not the account exists, constant-time work to limit enumeration, all sessions revoked on use, notification email sent.
- **Rate limiting:** Postgres-backed counters on login, reset, verification-resend and signup, keyed by IP and by account+IP; separate global limits on the API.
- **Lockout/abuse:** progressive delay after repeated failures (for example 5 failures → 15 min, doubling), never a permanent lock (avoids lock-out DoS); email on suspicious activity; optional CAPTCHA only if abuse appears.
- **CSRF:** cookie auth makes it applicable. Defences: SameSite=Lax, `Origin`/`Host` validation on all non-GET requests, required custom header, strict CORS allowlist, no state change on GET.
- **Secrets:** pepper and mail credentials via environment/secret manager, versioned and rotatable; never in code or logs.
- **Security testing:** automated tests for enumeration (response and timing), brute-force limits, session fixation (new token at login), rotation/reuse detection, token expiry and single-use, CSRF rejection, cookie flags, and logout/revocation; dependency audit in CI; manual review and an external penetration test before the first production launch. MFA (TOTP) arrives before any enterprise customer, or through an IdP.

### K. Inventory consistency strategy

**Model:** Product → ProductVariant → Location → `stock_balances(tenant, variant, location, on_hand)`; separately the append-only `inventory_ledger(id, tenant, operation_id, line_no, variant, location, type, quantity_delta, reason, reference_type/id, unit_cost_minor, actor, occurred_at)`. Only `on_hand` exists now; `reserved` is added by an expand migration when sales arrive (Phase 6).

**One write path:** `InventoryService.applyMovements()` is the only code that writes the ledger or balances. In one transaction it inserts ledger rows, applies balance deltas, writes the audit entry and stores the idempotency record.

| Risk | Control |
| --- | --- |
| **Negative stock** | Atomic guard: `UPDATE stock_balances SET on_hand = on_hand + :d WHERE tenant_id=:t AND variant_id=:v AND location_id=:l AND on_hand + :d >= 0`; 0 rows → `InsufficientStock` and rollback. First receipt of a variant at a location uses `INSERT ... ON CONFLICT DO UPDATE` with the same guard. `CHECK (on_hand >= 0)` is the last defence. |
| **Lost updates** | Never read-modify-write in the app. Only relative deltas in SQL; the row lock taken by `UPDATE` serialises writers. READ COMMITTED is sufficient with this pattern. |
| **Double receiving** | Receiving locks the PO row (`FOR UPDATE`), then conditionally updates each line: `received_qty = received_qty + :q WHERE received_qty + :q <= ordered_qty` (over-receipt only through an explicit tolerance setting, default 0), and the PO status moves by a guarded transition (`WHERE status IN ('submitted','partially_received')`). The receipt is also protected by its idempotency key. |
| **Duplicate movements** | Mandatory `Idempotency-Key` on stock-changing POSTs. `idempotency_keys(tenant_id, scope, key, request_hash, response)` has a unique constraint and is written in the same transaction. Same key and same payload returns the stored response; same key with a different payload returns 422. The ledger additionally has `UNIQUE (tenant_id, operation_id, line_no)`. |
| **Concurrent overselling** | The atomic guard above, plus multi-line operations lock in a deterministic order (document row first, then balances sorted by `(variant_id, location_id)`); deadlock/serialization errors (`40P01`, `40001`) retry up to 3 times. |
| **Inconsistent cached balances** | Single write path; ledger append-only (grants + trigger); balance and ledger changed in one transaction so they commit or fail together; nightly and on-demand reconciliation compares `SUM(delta)` to `on_hand` per variant/location, alerts on drift and can rebuild from the ledger; a randomized-concurrency invariant test runs in CI. |
| **Corrections** | No edits or deletes; compensating movements with a reason code and a link to the original. |

**Required test:** 100 parallel issues of 1 unit against 50 units must produce exactly 50 successes and 50 `InsufficientStock`; a repeated request with the same idempotency key must apply once; two simultaneous receipts of the same PO line must not exceed the ordered quantity.

### L. Deployment strategy
- Containers (API+web, worker) plus managed PostgreSQL 16, environments: local, staging, production. **Provider not chosen (D9).**
- Release steps: build and scan image → run migrations with the owner role as a separate job → deploy the app using the runtime role → smoke test. Rollback = redeploy the previous image; this is safe because migrations are expand/contract.
- Health and readiness endpoints; structured JSON logs with request IDs; error tracking; uptime checks; DB backups with PITR and a documented, rehearsed restore.
- Staging holds synthetic data only until D9 is decided. TLS everywhere, HSTS, security headers.
- Scale-out later: more API replicas, then a read replica; Kubernetes, Redis and queues beyond Postgres only on measured need.

### M. Phase 0 implementation checklist
1. Record this ADR in `docs/adr/`; confirm D9 is an open gate with a named owner and date.
2. Initialise git (host not assumed), `.gitignore` extended (`dist/`, `coverage/`, `.env*` except example).
3. pnpm workspace, `.nvmrc`, TypeScript base config, ESLint/Prettier, restricted-import rules (no raw DB pool, no `process.env`).
4. Create the `apps/*`, `packages/*`, `db/*` skeleton without feature code.
5. `config` module with Zod validation and `.env.example`.
6. Docker Compose: Postgres 16 with role bootstrap, Mailpit.
7. dbmate wired; baseline migration creating schemas, `app.current_tenant_id()`/`current_user_id()` and the RLS template helper only.
8. `withTenantTx` helper plus the first isolation test using a throwaway table.
9. Catalog guard test (RLS forced, no UPDATE/DELETE on append-only tables).
10. `Money` utility and unit tests (PKR, JPY, KWD cases; rejects floats and excess precision).
11. Error model, request-context object, request-ID logging.
12. `IdentityProvider` interface and permission-catalogue skeleton (types only).
13. Fastify bootstrap with a health endpoint and the central error handler.
14. Vitest + Playwright set up; one passing test of each kind.
15. CI pipeline with every stage in section G, including gitleaks and audit.
16. Production Dockerfile builds, runs non-root, passes the scan.
17. Copy `database/schema.sql` to `legacy/`; originals remain.
18. Exit criteria: green CI from a clean clone, `docker compose up` + `pnpm dev` works in under 10 minutes, isolation test passing as `stockflow_app`.

**Then Phase 1** (identity and tenancy) can begin.
