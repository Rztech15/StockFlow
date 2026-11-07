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
  assert.match(a.items[0], /^A: order 12 piece \(2 left, about 1 days of cover, urgent\)$/);
  assert.match(a.items[1], /^B: order 5 box/);
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
