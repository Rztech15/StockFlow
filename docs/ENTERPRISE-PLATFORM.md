---
title: "StockFlow Enterprise: Unified Inventory and Supply Chain Platform"
subtitle: "Product blueprint, honest gap analysis and delivery roadmap"
---

# 1. Direction

StockFlow is positioned as one platform for three kinds of large organisations:

| Segment | Typical customer | What they need most |
|---|---|---|
| Wholesale and distribution | National distributors, multi-warehouse wholesalers | Many locations, customer price lists and credit, pick-pack-ship, backorders, supplier lead-time management |
| Manufacturing | Plants with raw material, work in progress and finished goods | Bill of materials, work orders, material requirements planning, lot traceability |
| Multi-branch retail | Chains with a distribution centre and many stores | Store replenishment, allocation, stock transfers, shrinkage, point-of-sale sync |

One shared core (locations, items, stock ledger, procurement, planning, security, integration) serves all three. Industry differences live in **industry packs**: workflows and screens that sit on the same core, not three separate products.

**Design principle:** build the shared core once, correctly and measurably; add industry packs on top. A feature is "done" only when it has tests and has been exercised at a stated data size.

# 2. Where the code stands today (verified)

This section is deliberately plain. It was written after reading the repository, and it separates what exists from what an enterprise buyer will ask for.

## 2.1 What exists

| Area | Present in the code |
|---|---|
| Tenant isolation | PostgreSQL row-level security on every tenant table; the application database role cannot bypass it; guard tests check it |
| Access | Argon2id passwords, server-side sessions, origin checks, strict content security policy, 5 roles with 10+ permissions, API keys (read, read and write) |
| Inventory | Products, categories, SKU conventions, append-only stock ledger, import from many file types (Excel, CSV, JSON, XML and more), CSV export |
| Procurement | Suppliers, purchase orders, receive and cancel, measured supplier lead times |
| Sales | Customers, sales orders that reserve stock (no overselling), ship and cancel |
| Planning | Moving-average demand forecast and reorder suggestions; **new:** ABC and XYZ classes, safety stock, reorder point and order-up-to level |
| Audit | Append-only activity log (the application role can insert and read, not change) |
| Delivery | Responsive web app (dashboard, glass design, 3D stock view), plan limits, notifications, automatic test run on a throw-away PostgreSQL in GitHub Actions |

## 2.2 What an enterprise needs and the code does not have yet

| Capability | Today | Needed | Priority |
|---|---|---|---|
| Multiple locations | One quantity per product | Location hierarchy (company, region, warehouse, zone, bin), stock per location | **Critical** |
| Stock transfers | None | Request, approve, ship, in transit, receive, with variance handling | **Critical** |
| Units of measure, lots, serials, expiry | None | Conversions, lot and serial tracking, expiry rules (FEFO) | High |
| Pagination at scale | Product list is not paginated; movements use a limit of up to 500 | Keyset pagination everywhere, async exports | **Critical** |
| Ledger growth | One table, basic indexes | Monthly partitions, rollup tables for reports | High |
| Abuse and overload protection | No rate limiting, no statement timeouts, no idempotency keys | All three, plus request size limits | **Critical** |
| Enterprise identity | Email and password only | OIDC and SAML single sign-on, SCIM provisioning, MFA | **Critical** |
| Fine-grained access | Workspace-wide roles | Permissions scoped to locations and value limits (attribute based), approval workflows | High |
| Background work | None (serverless request only) | Queue and workers for imports, exports, forecasts, webhooks | **Critical** |
| Integration | CSV and API keys | Stable versioned API, signed webhooks with retry, bulk APIs, SFTP and EDI, ERP connectors | High |
| Pricing and finance | No prices, taxes or currencies on products | Price lists, multi-currency, tax, cost layers and valuation | High |
| Manufacturing | None | Bills of materials, work orders, MRP | Pack |
| Retail replenishment | None | Store min and max, allocation from distribution centre, shrinkage | Pack |
| Observability and operations | Basic health check | Metrics, tracing, alerting, SLOs, restore drills | **Critical** |
| Proof of scale | No load test has been run | Repeatable load tests with published results | **Critical** |

**Important caveat.** The backend has not yet been run end to end against a real PostgreSQL by its author (the deployment notes say so). The automatic GitHub test run added to the repository is the first step to change that. Nothing in this document should be read as a claim that the platform already handles thousands of users or large datasets. Those are design targets that must be proven by tests (section 7).

# 3. Platform architecture

## 3.1 Shape: modular monolith first, services where load demands it

A modular monolith keeps one deployable, one transaction boundary for stock changes (important: stock must never be inconsistent) and clear module boundaries so that a module can later be split out if its load or team demands it.

| Module | Responsibility |
|---|---|
| Identity and access | Organisations, users, SSO, SCIM, roles, scoped permissions, API keys, sessions |
| Organisation and locations | Legal entities, regions, warehouses, stores, plants, zones, bins, calendars |
| Catalogue | Items, variants, units of measure, lots and serials, bills of materials, price lists |
| Inventory ledger | Append-only movements, balances per location, reservations, adjustments, cycle counts, transfers |
| Procurement | Suppliers, requisitions, purchase orders, receipts, supplier scorecards |
| Sales and fulfilment | Customers, orders, allocation, pick, pack, ship, returns |
| Manufacturing pack | Work orders, material consumption, yield, lot genealogy |
| Retail pack | Store replenishment, allocation, shrinkage, point-of-sale sync |
| Planning | Demand history, forecasts, safety stock, reorder, MRP |
| Integration | Public API, webhooks, bulk import and export, connectors |
| Analytics | KPIs, rollups, exports |
| Audit and compliance | Immutable audit trail, retention, export |

## 3.2 Runtime layout for enterprise load

| Layer | Choice | Why |
|---|---|---|
| Web and API | Containers (not serverless functions), horizontally scaled behind a load balancer | Predictable connection counts, long-running work, no cold starts |
| Database | PostgreSQL with PgBouncer, one primary, read replicas for reports, point-in-time recovery | Row-level security already built on it; strong transactions for stock |
| Queue and workers | A durable queue (for example Redis streams or SQS) with idempotent workers | Imports, exports, forecasts, webhooks, scheduled jobs |
| Cache | Redis for sessions, rate limits and hot reference data | Keep the database for facts, not for counting requests |
| Search | PostgreSQL trigram first; OpenSearch only if measured need | Avoid early complexity |
| Storage | Object storage for files, signed URLs | Imports and exports never pass through the API process |
| Observability | OpenTelemetry traces, metrics, structured logs, alerting | Required to meet and prove SLOs |

## 3.3 Data model changes that matter most

1. **Locations:** a tree table (`locations`) with type (warehouse, store, plant, zone, bin) and a path for fast subtree queries.
2. **Balances:** `stock_balances(tenant, item, location, lot, status) -> on_hand, reserved`, updated in the same transaction as the ledger row.
3. **Ledger:** `stock_movements` gains location, lot, unit of measure and document reference; partitioned by month; never updated or deleted.
4. **Transfers:** header and lines with states `requested, approved, shipped, in_transit, received, closed` and recorded variance. Stock in transit is its own location, so totals always add up.
5. **Reservations:** per location and lot, expiring, released on cancel or ship.
6. **Idempotency:** a key table so that repeated API calls and webhook deliveries never double-post stock.
7. **Outbox:** events written in the same transaction, published by a worker (webhooks, integrations, analytics).

**Invariants to test continuously:** the sum of balances equals the sum of ledger movements; balances never go negative unless a location allows it; the sum of every transfer's out and in movements is zero; every row carries a tenant and is covered by row-level security.

# 4. Industry packs

## 4.1 Wholesale and distribution

Customer-specific price lists and contract prices, credit limits and holds, backorders and partial shipments, wave picking, pack and ship with carrier labels, drop-ship, supplier scorecards (on-time, in-full), landed cost, EDI documents (orders, advance ship notices, invoices).

## 4.2 Manufacturing

Bills of materials with versions and substitutes, work orders, backflush or manual consumption, scrap and yield, by-products, lot genealogy (which raw lot went into which finished lot), MRP that nets demand against stock and open orders and proposes purchase and work orders, shop-floor scanning.

## 4.3 Multi-branch retail

Store minimum and maximum with replenishment from the distribution centre, allocation of scarce stock by rule, inter-store transfers, shrinkage and cycle counts, point-of-sale and e-commerce sales sync, markdown and end-of-season stock reports.

# 5. Planning and analytics

**Delivered now (statistical, no machine learning):** ABC classes from units issued, XYZ classes from demand variability, safety stock from service level, demand variability and supplier lead-time variability, reorder point, order-up-to level, overstock and idle stock flags. The endpoint returns pages of products and summaries over the whole catalogue; the database aggregates demand so only two numbers per product leave it.

**Next:** daily demand roll-up tables (so history length does not slow reports), forecasting models with back-testing (exponential smoothing, and Croston for intermittent demand) chosen per product class, MRP, and the KPI set that buyers expect: fill rate, on-time in-full, inventory turns, days of supply, gross margin return on inventory, forecast accuracy.

# 6. Security and compliance

| Topic | Target |
|---|---|
| Identity | OIDC and SAML single sign-on, SCIM user lifecycle, enforced MFA, session management and revocation |
| Authorisation | Roles plus attributes: location scope, value limits, approval thresholds; separation of duties (the person who raises a purchase order cannot approve it) |
| Data protection | Encryption in transit and at rest, field-level encryption for sensitive fields, secrets in a vault, customer-managed keys as an option |
| Audit | Append-only audit trail (already enforced by database permissions), tamper-evident export, retention policies |
| Tenant isolation | Row-level security (present), automated isolation tests on every table in CI (present as a guard test) |
| Resilience | Point-in-time recovery, tested restores, recovery point 5 minutes and recovery time 1 hour as targets |
| Compliance path | Map controls to SOC 2 Type II and ISO 27001, independent penetration test before the first enterprise pilot, data-residency options by region |

# 7. Scale: stated targets and how they will be proven

These are targets, not claims.

| Dimension | Design target | Proof required |
|---|---|---|
| Named users per tenant | 10,000 | Identity and permission load test |
| Concurrent users | 1,000 per region | Load test with a realistic mix of reads and stock postings |
| Items | 5 million | Catalogue import and search benchmark |
| Ledger rows | 100 million per year across tenants | Partitioned-table benchmark, 95th percentile latency under 300 ms for the common calls |
| Locations | 5,000 per tenant | Tree queries and transfer benchmarks |
| Availability | 99.9 percent monthly | Measured, published uptime and incident reviews |
| Stock accuracy | Invariants hold under concurrent postings | Property-based and concurrency tests, run in CI |

Every phase below ends with a measurable exit test, so the claims in sales material always match what was measured.

# 8. Delivery roadmap

| Phase | Weeks | Scope | Exit test |
|---|---|---|---|
| **E0 Verify and harden** | 1 to 3 | Get all tests green on PostgreSQL in CI; fix what fails; rate limiting, statement timeouts, request limits; keyset pagination for products and movements; first load-test baseline | CI green; baseline numbers recorded; no unpaginated list endpoint |
| **E1 Multi-location inventory core** | 4 to 11 | Locations tree, balances per location, ledger with location, migration of existing stock to a default location, transfers with in-transit and variance, units of measure, cycle counts | Invariants pass in CI; 1 million ledger-row benchmark within target latency |
| **E2 Enterprise identity and governance** | 8 to 14 | OIDC and SAML, SCIM, MFA, location-scoped permissions, approval workflows, audit export | Penetration test with no high findings; controls mapped to SOC 2 |
| **E3 Integration platform** | 12 to 18 | Versioned API, idempotency keys, signed webhooks with retry and dead letters, asynchronous bulk APIs, SFTP batches, first ERP connector | A sandbox customer syncs items, orders and receipts without manual steps |
| **E4 Industry packs** | 16 to 30 | Wholesale pack first, then manufacturing, then retail | One pilot customer per pack runs a real process end to end |
| **E5 Planning and analytics at scale** | 20 to 30 | Demand roll-ups, back-tested forecasts, MRP, KPI dashboards, read replica | Forecast accuracy reported per class; reports on 100 million rows meet targets |
| **E6 Scale and reliability** | 24 to 34 | Containers with connection pooling, partitioned ledger, queue workers, multi-zone, restore drills, observability, SLOs | Load test at the stated targets; restore drill meets recovery targets |

**First 30 days in practice:** (1) run the full test suite on PostgreSQL through the GitHub workflow and fix every failure; (2) add rate limiting, timeouts and pagination; (3) write the load-test scenarios; (4) design and review the E1 migration on paper before any code, because changing where stock quantity lives is the highest-risk change in the whole programme.

# 9. Risks and open decisions

| Risk or decision | Position |
|---|---|
| Moving quantity from products to per-location balances touches every stock-changing path (movements, sales shipments, purchase receipts, imports, dashboards) | Do it in one planned migration with the invariant tests first; keep a compatibility view for the old quantity until all screens use balances |
| Serverless hosting limits connections and background work | Move the API to containers with PgBouncer in E6, earlier if the load baseline in E0 shows the limit |
| Three industries at once can dilute the core | Core first (E1 to E3), packs second (E4), one pack at a time, each with a pilot customer |
| Enterprise sales cycles expect compliance evidence | Start SOC 2 control mapping and the penetration test in E2, not at launch |
| Scope honesty | Marketing statements follow measured results; until E6 the supported claim is "multi-tenant, secure, designed for scale", not a user count |
