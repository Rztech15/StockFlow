"use strict";
/* Pure helpers for the Calculator and Assistant tabs: no DOM, no network.
   Loaded by the page (as a global) and by the Node tests (as a module). */
(function (root) {
  const round2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;

  const calc = {
    profit(cost, price) {
      const profit = round2(price - cost);
      return {
        profit,
        margin: price ? round2((profit / price) * 100) : null,
        markup: cost ? round2((profit / cost) * 100) : null,
      };
    },
    markup(cost, pct) {
      const price = round2(cost * (1 + pct / 100));
      return { price, profit: round2(price - cost) };
    },
    discount(price, pct) {
      const amount = round2((price * pct) / 100);
      return { amount, final: round2(price - amount) };
    },
    tax(amount, rate, included) {
      if (included) {
        const net = round2(amount / (1 + rate / 100));
        return { net, tax: round2(amount - net), gross: round2(amount) };
      }
      const tax = round2((amount * rate) / 100);
      return { net: round2(amount), tax, gross: round2(amount + tax) };
    },
    breakEven(fixed, variable, price) {
      if (!(price > variable)) return { error: "The selling price must be higher than the variable cost." };
      const units = Math.ceil(fixed / (price - variable) - 1e-9);
      return { units, revenue: round2(units * price) };
    },
  };

  /* ---------- assistant: fixed rules over the user's own records ---------- */

  const INTENTS = [
    ["unusual", /unusual|strange|odd|suspicious|anomal|weird/],
    ["reorder", /reorder|re-order|restock|re-stock|order (this|next)|running out|need to (buy|order)|low stock|shortage/],
    ["fast", /fast|best.?sell|top.?sell|selling most|most popular/],
    ["slow", /slow|not sold|dead stock|idle|no sales|stuck/],
    ["forecast", /forecast|predict|next (month|week)|demand|expect/],
    ["summary", /summary|overview|how.*(doing|going)|status|performance|report/],
    ["stock", /how many|in stock|do i have|stock of|quantity of|left of/],
  ];

  function classify(question) {
    const s = String(question).toLowerCase();
    for (const [name, re] of INTENTS) if (re.test(s)) return name;
    return "help";
  }

  function findProduct(question, rows) {
    const s = String(question).toLowerCase();
    let best = null;
    rows.forEach((r) => {
      const hits = [r.name, r.sku].filter((t) => t && s.includes(String(t).toLowerCase()));
      const size = hits.reduce((m, t) => Math.max(m, String(t).length), 0);
      if (size && (!best || size > best.size)) best = { row: r, size };
    });
    return best ? best.row : null;
  }

  function median(values) {
    const a = values.slice().sort((x, y) => x - y);
    const mid = a.length / 2;
    return a.length % 2 ? a[Math.floor(mid)] : (a[mid - 1] + a[mid]) / 2;
  }

  function unusualMovements(movements) {
    const outs = movements.filter((m) => m.movement_type === "OUT");
    if (outs.length < 5) return [];
    const limit = Math.max(10, median(outs.map((m) => m.quantity)) * 4);
    return outs.filter((m) => m.quantity >= limit);
  }

  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

  function answerReorder(rows) {
    const rank = { "reorder-now": 0, "reorder-soon": 1 };
    const list = rows.filter((r) => r.suggested_reorder > 0)
      .sort((a, b) => (rank[a.status] ?? 2) - (rank[b.status] ?? 2) || a.name.localeCompare(b.name));
    if (!list.length) return { text: "Nothing needs reordering right now. Every product has enough stock, counting what is already on order." };
    return {
      text: plural(list.length, "product needs", "products need") + " reordering:",
      items: list.slice(0, 8).map((r) => r.name + ": order " + r.suggested_reorder + " " + (r.unit || "units")
        + " (" + r.quantity + " left"
        + (r.days_of_cover === null ? "" : ", about " + Math.floor(r.days_of_cover) + " days of cover")
        + (r.status === "reorder-now" ? ", urgent" : "") + ")"),
    };
  }

  function answerFast(rows) {
    const list = rows.filter((r) => r.avg_daily_demand > 0)
      .sort((a, b) => b.avg_daily_demand - a.avg_daily_demand).slice(0, 3);
    if (!list.length) return { text: "No sales were recorded in this period, so there are no fast movers yet." };
    return { text: "Fastest sellers:", items: list.map((r) => r.name + ": about " + r.avg_daily_demand + " a day") };
  }

  function answerSlow(rows) {
    const list = rows.filter((r) => r.status === "idle" && r.quantity > 0);
    if (!list.length) return { text: "No slow movers found: everything in stock has sold recently." };
    return {
      text: plural(list.length, "product has", "products have") + " not sold recently:",
      items: list.slice(0, 8).map((r) => r.name + ": " + r.quantity + " in stock"),
      note: "Consider a promotion or stopping reorders.",
    };
  }

  function answerForecast(rows, horizon) {
    const list = rows.filter((r) => r.forecast_units > 0).sort((a, b) => b.forecast_units - a.forecast_units).slice(0, 6);
    if (!list.length) return { text: "There is not enough sales history to forecast yet." };
    return {
      text: "Expected sales over the next " + horizon + " days:",
      items: list.map((r) => r.name + ": about " + r.forecast_units + " units (" + r.trend + ")"),
      note: "A straight-line estimate from recent sales. Seasonal effects are not included.",
    };
  }

  function answerStock(row) {
    if (!row) return { text: "I couldn't find that product. Try its exact name or SKU." };
    return {
      text: row.name + ": " + row.quantity + " in stock (minimum " + row.minimum_stock + ")"
        + (row.on_order ? ", " + row.on_order + " on order" : "") + ".",
    };
  }

  function answerUnusual(list) {
    if (!list.length) return { text: "Nothing unusual in the latest stock movements." };
    return {
      text: plural(list.length, "stock-out is", "stock-outs are") + " much bigger than usual:",
      items: list.slice(0, 6).map((m) => m.product_name + ": " + m.quantity + " out on " + String(m.created_at).slice(0, 10)),
      note: "Worth checking with your team.",
    };
  }

  function answerSummary(dashboard, insights) {
    const s = dashboard.summary;
    return {
      text: s.products + " products and " + s.units_in_stock + " units in stock. " + s.low_stock + " low and " + s.out_of_stock + " out of stock.",
      items: insights.slice(0, 3).map((i) => i.message),
    };
  }

  root.StockFlowTools = {
    round2, calc, classify, findProduct, median, unusualMovements,
    answerReorder, answerFast, answerSlow, answerForecast, answerStock, answerUnusual, answerSummary,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = root.StockFlowTools;
})(typeof window !== "undefined" ? window : globalThis);
