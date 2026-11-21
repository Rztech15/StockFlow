import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const T = createRequire(import.meta.url)("../../public/tools.js");

test("profit, margin and markup", () => {
  assert.deepEqual(T.calc.profit(40, 55), { profit: 15, margin: 27.27, markup: 37.5 });
  assert.deepEqual(T.calc.profit(0, 10), { profit: 10, margin: 100, markup: null });
  assert.deepEqual(T.calc.profit(5, 0), { profit: -5, margin: null, markup: -100 });
});

test("markup, discount and tax round half up to 2 decimals", () => {
  assert.deepEqual(T.calc.markup(40, 30), { price: 52, profit: 12 });
  assert.deepEqual(T.calc.discount(120, 15), { amount: 18, final: 102 });
  assert.deepEqual(T.calc.tax(100, 17, false), { net: 100, tax: 17, gross: 117 });
  assert.deepEqual(T.calc.tax(117, 17, true), { net: 100, tax: 17, gross: 117 });
  assert.equal(T.calc.discount(1.005, 0).final, 1.01);
});

test("break-even rounds units up and rejects a price below cost", () => {
  assert.deepEqual(T.calc.breakEven(5000, 12, 20), { units: 625, revenue: 12500 });
  assert.deepEqual(T.calc.breakEven(100, 3, 10), { units: 15, revenue: 150 });
  assert.match(T.calc.breakEven(100, 10, 10).error, /higher than the variable cost/);
  assert.match(T.calc.breakEven(100, 10, 5).error, /higher/);
});

test("questions are routed to the right intent", () => {
  const cases = {
    "What should I reorder this week?": "reorder", "which products are low stock": "reorder",
    "Anything unusual?": "unusual", "What sells fastest": "fast", "any dead stock?": "slow",
    "Predict next month's demand": "forecast", "How is my business doing?": "summary",
    "How many Notebook A5 do I have": "stock", "tell me a joke": "help",
  };
  for (const [q, want] of Object.entries(cases)) assert.equal(T.classify(q), want, q);
});

test("a product is found by name or SKU, longest match wins", () => {
  const rows = [{ name: "Pens", sku: "P-1" }, { name: "Pens blue", sku: "P-2" }];
  assert.equal(T.findProduct("how many pens blue are left", rows).sku, "P-2");
  assert.equal(T.findProduct("stock of p-1?", rows).name, "Pens");
  assert.equal(T.findProduct("something else", rows), null);
});

test("unusual stock-outs are far above the typical size", () => {
  const out = (q, name = "X") => ({ movement_type: "OUT", quantity: q, product_name: name, created_at: "2026-11-01T10:00:00Z" });
  const calm = [out(2), out(3), out(2), out(4), out(3)];
  assert.deepEqual(T.unusualMovements(calm), []);
  const spike = [...calm, out(60, "Lamp")];
  assert.deepEqual(T.unusualMovements(spike).map((m) => m.product_name), ["Lamp"]);
  assert.deepEqual(T.unusualMovements([out(500)]), []); // too little history to judge
  assert.deepEqual(T.unusualMovements([...calm, { ...out(900), movement_type: "IN" }]), []);
});

test("reorder answer lists urgent products first and mentions units", () => {
  const rows = [
    { name: "B", unit: "box", status: "reorder-soon", suggested_reorder: 5, quantity: 8, days_of_cover: 9.4 },
    { name: "A", unit: "piece", status: "reorder-now", suggested_reorder: 12, quantity: 2, days_of_cover: 1.2 },
    { name: "C", unit: "pack", status: "ok", suggested_reorder: 0, quantity: 99, days_of_cover: null },
  ];
  const a = T.answerReorder(rows);
  assert.equal(a.text, "2 products need reordering:");
  assert.match(a.items[0], /^A: order 12 piece \(2 left, about 1 day of cover, urgent\)$/);
  assert.match(a.items[1], /^B: order 5 box/);
  assert.match(a.items[1], /about 9 days of cover\)$/);
  assert.match(T.answerReorder([rows[2]]).text, /Nothing needs reordering/);
});

test("other answers handle empty data without inventing numbers", () => {
  assert.match(T.answerFast([]).text, /No sales/);
  assert.match(T.answerSlow([{ status: "ok", quantity: 5 }]).text, /No slow movers/);
  assert.match(T.answerForecast([], 30).text, /not enough sales history/);
  assert.match(T.answerStock(null).text, /couldn't find/);
  assert.match(T.answerUnusual([]).text, /Nothing unusual/);
  const s = T.answerSummary({ summary: { products: 3, units_in_stock: 40, low_stock: 1, out_of_stock: 0 } }, [{ message: "m1" }]);
  assert.equal(s.text, "3 products and 40 units in stock. 1 low and 0 out of stock.");
  assert.deepEqual(s.items, ["m1"]);
});

test("stock levels are worked backwards from today's level", () => {
  const r = T.levelsFromNet(100, [10, -5, 20]);
  assert.deepEqual(r.levels, [85, 80, 100]);
  assert.equal(r.before, 75);
  assert.deepEqual(T.levelsFromNet(50, [0, 0]).levels, [50, 50]);
});

test("percentage change handles zero and no change", () => {
  assert.deepEqual(T.pctDelta(108, 100), { text: "+8%", dir: "up" });
  assert.deepEqual(T.pctDelta(75, 100), { text: "-25%", dir: "down" });
  assert.deepEqual(T.pctDelta(100, 100), { text: "0%", dir: "flat" });
  assert.deepEqual(T.pctDelta(3, 0), { text: "+3", dir: "up" });
  assert.deepEqual(T.pctDelta(0, 0), { text: "0%", dir: "flat" });
  assert.equal(T.pctDelta(1006, 1000).text, "+1%");
});

test("chart axis uses round numbers with four equal steps", () => {
  assert.deepEqual(T.niceScale(843).ticks, [0, 250, 500, 750, 1000]);
  assert.deepEqual(T.niceScale(95).ticks, [0, 25, 50, 75, 100]);
  assert.deepEqual(T.niceScale(0).ticks, [0, 0.25, 0.5, 0.75, 1]);
  assert.equal(T.niceScale(4200).top, 6000);
  assert.equal(T.axisLabel(1000), "1K");
  assert.equal(T.axisLabel(750), "750");
  assert.equal(T.axisLabel(2500), "2.5K");
});

test("past quantities and counts come from the movement history", () => {
  const day = 864e5, now = Date.parse("2026-11-08T12:00:00Z"), cutoff = now - 7 * day;
  const at = (d) => new Date(now - d * day).toISOString();
  const products = [
    { id: "a", quantity: 5, minimum_stock: 10, created_at: at(30) },   // now low
    { id: "b", quantity: 0, minimum_stock: 4, created_at: at(30) },    // now out
    { id: "c", quantity: 20, minimum_stock: 5, created_at: at(3) },    // created after the cutoff
  ];
  const moves = [
    { product_id: "a", movement_type: "OUT", quantity: 20, created_at: at(2) },   // had 25 a week ago
    { product_id: "b", movement_type: "OUT", quantity: 6, created_at: at(1) },    // had 6 a week ago
    { product_id: "c", movement_type: "IN", quantity: 20, created_at: at(3) },
    { product_id: "a", movement_type: "IN", quantity: 99, created_at: at(10) },   // before the cutoff: ignored
  ];
  assert.deepEqual(T.quantitiesAt(products, moves, cutoff), { a: 25, b: 6 });
  assert.deepEqual(T.stockCountsAt(products, moves, cutoff), { low: 0, out: 0, products: 2 });
  assert.deepEqual(T.stockCountsAt(products, moves, now), { low: 1, out: 1, products: 3 });
});

test("activity rows get a readable title, kind and right-hand text", () => {
  const d = T.describeActivity;
  assert.deepEqual(d({ action: "stock_in", entity: "product", detail: "50 units" }), { title: "Stock Updated", kind: "stock", right: "+50 units" });
  assert.deepEqual(d({ action: "stock_out", entity: "product", detail: "5 units" }), { title: "Stock Updated", kind: "stock", right: "-5 units" });
  assert.deepEqual(d({ action: "created", entity: "sales_order", detail: "2 line(s)" }), { title: "New Sale", kind: "sale", right: "2 line(s)" });
  assert.equal(d({ action: "shipped", entity: "sales_order" }).title, "Order Shipped");
  assert.equal(d({ action: "created", entity: "purchase_order", detail: "1 line(s)" }).title, "Purchase Order");
  assert.equal(d({ action: "received", entity: "purchase_order" }).title, "Order Received");
  assert.equal(d({ action: "created", entity: "product", detail: "PP-001" }).title, "Product Added");
  assert.equal(d({ action: "imported", entity: "product", detail: "3 created, 2 updated" }).title, "Products Imported");
  assert.equal(d({ action: "role_changed", entity: "member", detail: "manager" }).title, "Member role changed");
});

test("relative times, names and initials", () => {
  const now = Date.parse("2026-11-08T12:00:00Z"), ago = (s) => new Date(now - s * 1000).toISOString();
  assert.equal(T.relTime(ago(10), now), "just now");
  assert.equal(T.relTime(ago(120), now), "2 min ago");
  assert.equal(T.relTime(ago(3 * 3600), now), "3 hours ago");
  assert.equal(T.relTime(ago(3599), now), "59 min ago");
  assert.equal(T.relTime(ago(3600), now), "1 hour ago");
  assert.equal(T.relTime(ago(30 * 3600), now), "yesterday");
  assert.equal(T.relTime(ago(5 * 86400), now), "5 days ago");
  assert.equal(T.displayName("muhammad.ramzan@demo.shop"), "Muhammad Ramzan");
  assert.equal(T.displayName("sara_ali99@x.com"), "Sara Ali");
  assert.equal(T.displayName(""), "");
  assert.equal(T.initials("Muhammad Ramzan"), "MR");
  assert.equal(T.initials("owner"), "O");
  assert.equal(T.initials(""), "?");
});

test("smooth paths pass through every point with one curve per gap", () => {
  const d = T.smoothPath([[0, 10], [10, 20], [20, 5]]);
  assert.match(d, /^M0\.0 10\.0 C/);
  assert.equal((d.match(/C/g) || []).length, 2);
  assert.match(d, /20\.0 5\.0$/);
  assert.equal(T.smoothPath([]), "");
  assert.equal(T.smoothPath([[1, 2]]), "M1.0 2.0");
});
