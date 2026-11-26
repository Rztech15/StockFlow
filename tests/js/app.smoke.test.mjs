// Loads the real app.js in a stubbed DOM and drives the Calculator, Assistant, Import and 3D code.
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
const tools = fs.readFileSync(new URL("../../public/tools.js", import.meta.url), "utf8");
const importers = fs.readFileSync(new URL("../../public/importers.js", import.meta.url), "utf8");
const app = fs.readFileSync(new URL("../../public/app.js", import.meta.url), "utf8");

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.attrs = {};
    this.hidden = false; this.value = ""; this.textContent = ""; this.className = ""; this.scrollTop = 0; this.scrollHeight = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false }; }
  get childElementCount() { return this.children.length; }
  append(...n) { n.forEach((c) => { if (typeof c === "string") this.children.push({ text: c }); else this.children.push(c); }); }
  appendChild(c) { this.children.push(c); return c; }
  insertBefore(c) { this.children.push(c); return c; }
  get firstChild() { return this.children[0] || null; }
  replaceChildren(...n) { this.children = []; this.append(...n); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  setAttribute(k, v) { this.attrs[k] = v; } getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  toggleAttribute(k, on) { if (on) this.attrs[k] = ""; else delete this.attrs[k]; }
  getBoundingClientRect() { return { width: 600, height: 300, left: 0, top: 0 }; }
  remove() {} focus() {} showModal() {} close() {} reset() {} select() {} setPointerCapture() {}
  querySelectorAll() { return []; } closest() { return null; }
  querySelector(sel) { const walk = (n) => { for (const c of n.children || []) { if (c.tag === sel) return c; const r = walk(c); if (r) return r; } return null; }; return walk(this); }
  getContext() {
    return new Proxy({}, { get: (_, k) => (k === "createLinearGradient" ? () => ({ addColorStop() {} }) : k === "measureText" ? () => ({ width: 40 }) : () => {}), set: () => true });
  }
}
const byId = {};
const created = [];
const doc = {
  getElementById: (id) => (byId[id] ||= new El(id)),
  createElement: (t) => { const e = new El(t); created.push(e); return e; },
  createElementNS: (ns, t) => new El(t),
  createTextNode: (t) => ({ text: t }),
  querySelectorAll: () => [], querySelector: () => (byId.__q ||= new El("q")), body: new El("body"), documentElement: new El("html"),
  addEventListener() {}, hidden: false,
};
const routes = {
  "/api/v1/insights": { horizon: 30, insights: [{ message: "m1" }, { message: "m2" }], rows: [
    { name: "Lamp", sku: "L-1", unit: "piece", status: "reorder-now", suggested_reorder: 12, quantity: 2, days_of_cover: 1.5, avg_daily_demand: 2, forecast_units: 60, trend: "rising", minimum_stock: 5, on_order: 0 },
    { name: "Pens", sku: "P-1", unit: "box", status: "ok", suggested_reorder: 0, quantity: 50, days_of_cover: 40, avg_daily_demand: 1, forecast_units: 30, trend: "steady", minimum_stock: 10, on_order: 5 }] },
  "/api/v1/dashboard": { summary: { products: 2, units_in_stock: 52, low_stock: 1, out_of_stock: 0 } },
  "/api/v1/movements": [{ movement_type: "OUT", quantity: 2, product_name: "A", created_at: "2026-11-01T00:00:00Z" }, { movement_type: "OUT", quantity: 3, product_name: "A", created_at: "2026-11-01T00:00:00Z" },
    { movement_type: "OUT", quantity: 2, product_name: "A", created_at: "2026-11-01T00:00:00Z" }, { movement_type: "OUT", quantity: 3, product_name: "A", created_at: "2026-11-01T00:00:00Z" },
    { movement_type: "OUT", quantity: 2, product_name: "A", created_at: "2026-11-01T00:00:00Z" }, { movement_type: "OUT", quantity: 80, product_name: "Lamp", created_at: "2026-11-02T00:00:00Z" }],
};
const calls = [];
const ctx = vm.createContext({
  document: doc, console, TextDecoder, TextEncoder, Blob, Response, DecompressionStream, setTimeout, clearTimeout, performance: { now: () => 0 }, requestAnimationFrame: () => 0,
  matchMedia: () => ({ matches: false, addEventListener() {} }), getComputedStyle: () => ({ color: "#000" }), MutationObserver: class { observe() {} },
  location: { hash: "" }, URLSearchParams, URL, addEventListener() {}, scrollTo() {}, localStorage: { getItem: () => null, setItem() {} }, navigator: {},
  fetch: async (path) => { calls.push(path); const key = path.split("?")[0];
    if (key in routes) return { ok: true, status: 200, json: async () => routes[key] };
    return { ok: false, status: 401, json: async () => ({ detail: "Sign in required" }) }; },
});
ctx.window = ctx;
vm.runInContext(tools, ctx);
vm.runInContext(importers, ctx);
vm.runInContext(app, ctx);                      // runs bind() and boot() like the browser
await new Promise((r) => setTimeout(r, 20));
const run = (code) => vm.runInContext(code, ctx);


const text = (b) => [b.textContent, ...b.children.flatMap((c) => (c.children?.length ? c.children.map((l) => l.textContent) : [c.textContent]))].filter(Boolean).join(" / ");
const wait = (ms = 5) => new Promise((r) => setTimeout(r, ms));

test("app.js loads and runs its start-up code", () => {
  assert.ok(calls.includes("/api/v1/session"));
});

test("calculator tab builds five cards with correct results", () => {
  run("buildCalculators()");
  const grid = byId["calc-grid"];
  assert.equal(grid.children.length, 5);
  const row = (card) => card.children.at(-1).children.map((r) => r.children.map((c) => c.textContent).join("="));
  assert.deepEqual(row(grid.children[0]), ["Profit=15.00", "Margin=27.27%", "Markup=37.50%"]);
  assert.deepEqual(row(grid.children[4]), ["Break-even units=625", "Break-even revenue=12,500.00"]);
});

test("assistant answers from the API data and handles unknown questions", async () => {
  run("startAssistant()");
  assert.equal(byId["chat-chips"].children.length, 6);
  const ask = async (q) => { await run(`askAssistant(${JSON.stringify(q)})`); await wait(); return text(byId["chat-log"].children.at(-1)); };
  assert.match(await ask("What should I reorder?"), /1 product needs reordering: \/ Lamp: order 12 piece/);
  assert.match(await ask("How is my business doing?"), /2 products and 52 units in stock\. 1 low and 0 out of stock\. \/ m1 \/ m2/);
  assert.match(await ask("Anything unusual?"), /Lamp: 80 out on 2026-11-02/);
  assert.match(await ask("how many pens do I have"), /Pens: 50 in stock \(minimum 10\), 5 on order\./);
  assert.match(await ask("tell me a joke"), /I can answer questions about reorders/);
});

test("import: a CSV is mapped, checked, then imported, and bad files are refused", async () => {
  const importCalls = [];
  ctx.fetch = async (path, opts) => {
    if (path === "/api/v1/products/import") {
      const body = JSON.parse(opts.body); importCalls.push(body);
      return { ok: true, status: 200, json: async () => ({ dry_run: body.dry_run, created: 1, updated: 1, unchanged: 0, failed: 1, rows: [
        { line: 2, sku: "N-1", name: "Notebook", status: body.dry_run ? "will_create" : "created", errors: [] },
        { line: 3, sku: "P-1", name: "Pens", status: body.dry_run ? "will_update" : "updated", errors: [] },
        { line: 4, sku: "", name: "", status: "error", errors: ["Name is missing"] }] }) };
    }
    return { ok: false, status: 401, json: async () => ({ detail: "x" }) };
  };
  const file = (name, content, size) => {
    const bytes = typeof content === "string" ? new TextEncoder().encode(content) : Uint8Array.from(content);
    return { name, size: size === undefined ? bytes.length : size, arrayBuffer: async () => bytes.buffer };
  };
  const csv = 'Product Name,Item Code,Qty,Unit Price\n"Notebook, A5",N-1,12,3.5\nPens,P-1,5,1\n,,3,1\n';
  await run("loadImportFile")(file("stock.csv", csv));
  assert.match(byId["import-info"].textContent, /stock\.csv \(Text\): 3 rows/);
  assert.equal(byId["import-map"].children.length, 6);
  await run("runImport")(true);
  assert.deepEqual(importCalls[0].rows[0], { line: 2, name: "Notebook, A5", sku: "N-1", quantity: "12" });
  assert.equal(importCalls[0].dry_run, true);
  assert.match(byId["import-summary"].textContent, /Checked: 1 to create, 1 to update, 1 with errors\./);
  assert.equal(byId["import-run"].textContent, "Import 2 rows");
  await run("runImport")(false);
  assert.equal(importCalls[1].dry_run, false);
  assert.match(byId["import-summary"].textContent, /Imported: 1 created, 1 updated/);
  assert.equal(byId["import-error"].textContent, "Import finished.");
  assert.equal(byId["import-cancel"].textContent, "Close");
  const refuse = async (f) => { await run("loadImportFile")(f); return byId["import-error"].textContent; };
  assert.match(await refuse(file("a.xls", [0xd0, 0xcf, 0x11, 0xe0, 0, 0, 0, 0])), /Old Excel \(\.xls\)/);
  assert.match(await refuse(file("a.pdf", "%PDF-1.4 ...")), /That PDF could not be read/);
  assert.match(await refuse(file("a.csv", "", 0)), /empty/);
  assert.match(await refuse(file("a.csv", "x", 6e6)), /larger than 5 MB/);
  assert.match(await refuse(file("a.json", "{bad")), /not valid JSON/);
  assert.match(await refuse(file("a.csv", "name\n")), /No data rows/);
  const json = '[{"name":"Lamp","sku":"L-1","quantity":4}]';
  await run("loadImportFile")(file("stock.json", json));
  assert.match(byId["import-info"].textContent, /stock\.json \(JSON\): 1 rows/);
});

test("3D stage colours bars by status, draws without errors and shows an invitation when empty", () => {
  run("setStage")([{ id: "1", sku: "A", quantity: 10, minimum_stock: 3 }, { id: "2", sku: "B", quantity: 0, minimum_stock: 3 }, { id: "3", sku: "C", quantity: 2, minimum_stock: 3 }]);
  assert.equal(run("stage.items.map(i => i.label + ':' + i.status).join(' ')"), "A:ok C:low B:out");
  Object.assign(byId["stage"], { offsetParent: {}, clientWidth: 400, clientHeight: 260 });
  run("drawStage(stage, 5000)");
  assert.ok(run("stage.items.every(i => i.h > 0)"));
  run("setStage")([]);
  assert.equal(run("stage.ghost"), true, "with no products the card shows an invitation instead of disappearing");
  assert.equal(byId["stage-card"].hidden, false);
  run("drawStage(stage, 6000)");
});

test("sales: lists render, Ship and Cancel call the API, and a new order posts its lines", async () => {
  const posts = [];
  const data = {
    "/api/v1/sales-orders": [
      { id: "o1", status: "open", created_at: "2026-11-01T10:00:00Z", customer_name: "Acme", items: [{ quantity: 3, sku: "L-1" }] },
      { id: "o2", status: "shipped", created_at: "2026-10-30T10:00:00Z", customer_name: "Acme", items: [{ quantity: 1, sku: "L-1" }] },
    ],
    "/api/v1/customers": [{ id: "c1", name: "Acme", email: "", phone: "", open_orders: 1 }],
    "/api/v1/sales/availability": [
      { id: "p1", sku: "L-1", name: "Lamp", quantity: 10, reserved: 3, available: 7 },
      { id: "p2", sku: "X-1", name: "Sold out", quantity: 0, reserved: 0, available: 0 },
    ],
  };
  ctx.confirm = () => true;
  ctx.fetch = async (path, opts) => {
    if (opts.method === "POST") { posts.push([path, opts.body ? JSON.parse(opts.body) : null]); return { ok: true, status: 201, json: async () => ({}) }; }
    return { ok: true, status: 200, json: async () => data[path] };
  };
  run('state.role = "manager"');
  await run("refreshSales()");
  const rows = byId["sales-body"].children;
  assert.equal(rows.length, 2);
  assert.equal(rows[0].children[1].textContent, "Acme");
  assert.equal(rows[0].children[2].textContent, "3 x L-1");
  assert.equal(rows[0].children[4].children.length, 2, "an open order can be shipped or cancelled");
  assert.equal(rows[1].children[4].children.length, 0, "a shipped order has no actions");
  assert.equal(byId["availability-body"].children.length, 2);
  assert.equal(byId["customers-body"].children.length, 1);

  rows[0].children[4].children[0].listeners.click[0]();
  await wait(10);
  assert.deepEqual(posts.at(-1), ["/api/v1/sales-orders/o1/ship", null]);
  byId["sales-body"].children[0].children[4].children[1].listeners.click[0]();
  await wait(10);
  assert.deepEqual(posts.at(-1), ["/api/v1/sales-orders/o1/cancel", null]);

  await run("openSale()");
  const line = byId["sale-lines"].children[0];
  assert.equal(line.children[0].children.length, 1, "only products with available stock can be picked");
  byId["sale-customer"].value = "c1";
  line.children[0].value = "p1";
  line.children[1].value = "2";
  await byId["sale-form"].listeners.submit[0]({ preventDefault() {}, target: { elements: { note: { value: "rush" } } } });
  assert.deepEqual(posts.at(-1), ["/api/v1/sales-orders", { customer_id: "c1", note: "rush", items: [{ product_id: "p1", quantity: 2 }] }]);

  run('state.role = "viewer"');
  await run("refreshSales()");
  assert.equal(byId["sales-body"].children[0].children[4].children.length, 0, "a viewer sees no actions");
});

test("dashboard: KPIs, trends, chart and activity are built from the API data", async () => {
  const day = 864e5, now = Date.now(), at = (d) => new Date(now - d * day).toISOString();
  const today = new Date().toISOString().slice(0, 10);
  const data = {
    "/api/v1/dashboard": { summary: { products: 3, units_in_stock: 100, low_stock: 1, out_of_stock: 1 } },
    "/api/v1/reports/movements": { daily: [{ day: today, units_in: 30, units_out: 10 }] },
    "/api/v1/products": [
      { id: "a", sku: "A", quantity: 90, minimum_stock: 10, created_at: at(40) },
      { id: "b", sku: "B", quantity: 10, minimum_stock: 10, created_at: at(40) },
      { id: "c", sku: "C", quantity: 0, minimum_stock: 5, created_at: at(40) },
    ],
    "/api/v1/movements": [{ product_id: "a", movement_type: "IN", quantity: 20, created_at: at(1) }],
    "/api/v1/activity": [
      { action: "created", entity: "sales_order", detail: "2 line(s)", created_at: new Date(now - 120000).toISOString() },
      { action: "stock_in", entity: "product", detail: "50 units", created_at: new Date(now - 300000).toISOString() },
      { action: "created", entity: "category", detail: "Tools", created_at: at(2) },
    ],
    "/api/v1/notifications": { count: 1, items: [{ message: "Notebook is low: 8 left (minimum 20)." }] },
    "/api/v1/settings": { plan: { name: "free" } },
  };
  ctx.fetch = async (path) => {
    const key = path.split("?")[0];
    return key in data ? { ok: true, status: 200, json: async () => data[key] } : { ok: false, status: 404, json: async () => ({ detail: "x" }) };
  };
  assert.equal(await run("loadDashboard()"), true);
  assert.equal(byId["kpi-products"].textContent, "3");
  assert.equal(byId["kpi-units"].textContent, "100");
  assert.equal(byId["kpi-units-delta"].children.at(-1).text, "+25%", "100 units now, 80 a week ago");
  assert.equal(byId["kpi-products-delta"].children.at(-1).text, "0%");
  assert.equal(byId["kpi-low-delta"].children.at(-1).text, "0%");
  ["products", "units", "low", "out"].forEach((k) => assert.ok(byId["spark-" + k].childElementCount > 0, "sparkline " + k));
  assert.ok(byId["overview-chart"].childElementCount > 8, "the chart has axes, curve and points");
  const rows = byId["activity-list"].children;
  assert.equal(rows.length, 3, "two real events plus the low-stock alert, the category event is skipped");
  assert.equal(rows[0].children[1].children[0].textContent, "New Sale");
  assert.equal(rows[1].children[2].textContent, "+50 units");
  assert.equal(rows[2].children[1].children[0].textContent, "Low Stock Alert");
  assert.equal(rows[2].children[2].textContent, "Notebook");
  assert.equal(byId["upgrade-card"].hidden, false, "free plan shows the upgrade card");
  assert.equal(byId["promo"].hidden, false);
  assert.equal(byId["welcome-title"].textContent, "Welcome back!", "no email stored, so no name");
  assert.equal(run("dashStage.items.length"), 3, "the dashboard has its own 3D scene with every product");
});

test("dashboard: a role without report access gets false so the app falls back to the inventory", async () => {
  ctx.fetch = async () => ({ ok: false, status: 403, json: async () => ({ detail: "no" }) });
  assert.equal(await run("loadDashboard()"), false);
});

test("planning: summary, class matrix, product rows and load more come from the API", async () => {
  const row = (o) => ({ unit: "piece", on_order: 0, avg_daily_demand: 2, std_daily_demand: 1, cv: 0.5, abc: "A", xyz: "X", class: "AX", safety_stock: 6, reorder_point: 20, order_up_to: 34, suggested_order: 0, status: "ok", ...o });
  const data = {
    method: "ABC and XYZ classes, safety stock and reorder points; not machine learning",
    parameters: { days: 90, review_days: 7, lead_days: 5.2, lead_days_std: 1.1, lead_orders: 4, service_level: null },
    summary: { products: 3, abc: { A: 1, B: 1, C: 0, N: 1 }, matrix: { AX: 1, BY: 1 }, safety_stock_units: 12, reorder_now: 1, reorder_soon: 0, overstock: 0, idle: 1, units_to_order: 18 },
    total: 3, limit: 2, offset: 0,
    rows: [row({ sku: "L-1", name: "Lamp", quantity: 3, suggested_order: 18, status: "reorder-now" }), row({ sku: "P-1", name: "Pens", abc: "B", xyz: "Y", class: "BY", quantity: 90 })],
  };
  const calls = [];
  ctx.fetch = async (path) => { calls.push(path); return { ok: true, status: 200, json: async () => data }; };
  await run("loadPlanning(false)");
  assert.ok(calls[0].startsWith("/api/v1/planning?days=90&review_days=7&limit=50&offset=0"));
  assert.equal(byId["plan-kpis"].children.length, 6);
  assert.equal(byId["plan-body"].children.length, 2);
  assert.equal(byId["plan-body"].children[0].children[1].textContent, "L-1 - Lamp");
  assert.equal(byId["plan-body"].children[0].children[8].textContent, "18 piece");
  assert.equal(byId["plan-matrix"].children[0].children[1].textContent, "1", "A and X");
  assert.equal(byId["plan-matrix"].children[1].children[2].textContent, "1", "B and Y");
  assert.match(byId["plan-note"].textContent, /5\.2 days \(spread 1\.1\), from 4 received orders/);
  assert.equal(byId["plan-more"].hidden, false, "2 of 3 shown, so there is more");
  data.rows = [row({ sku: "Z-1", name: "Zip", class: "N", abc: "N", xyz: "N", status: "idle" })];
  await run("loadPlanning(true)");
  assert.ok(calls[1].endsWith("offset=2"));
  assert.equal(byId["plan-body"].children.length, 3, "the next page is appended");
  assert.equal(byId["plan-more"].hidden, true);
});
