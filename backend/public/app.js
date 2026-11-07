"use strict";

const $ = (id) => document.getElementById(id);
const state = { editing: null, movement: null, view: "inventory", products: [], suppliers: [], role: "",
  categories: [], alerts: [] };

/* ---------- helpers ---------- */

async function api(method, path, body) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const err = new Error(problemMessage(data, res.status));
    err.status = res.status;
    throw err;
  }
  return data;
}

function problemMessage(data, status) {
  if (data && Array.isArray(data.errors) && data.errors.length) {
    return data.errors.map((e) => (e.path ? e.path + ": " : "") + e.message).join("; ");
  }
  if (data && data.detail) return data.detail;
  return "Request failed (" + status + ").";
}

function banner(message) {
  const el = $("banner");
  el.textContent = message || "";
  el.hidden = !message;
}

function info(message) {
  const el = $("info");
  el.textContent = message || "";
  el.hidden = !message;
}

/* ---------- theme (auto / light / dark) ---------- */

const THEMES = ["system", "light", "dark"];
const THEME_LABEL = { system: "Auto", light: "Light", dark: "Dark" };

function storedTheme() {
  try { return localStorage.getItem("stockflow-theme") || "system"; } catch (_) { return "system"; }
}

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", mode);
  try { localStorage.setItem("stockflow-theme", mode); } catch (_) { /* storage unavailable */ }
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  $("hero-logo").src = dark ? "/brand/logo-horizontal-dark.svg" : "/brand/logo-horizontal.svg";
  $("theme-toggle").textContent = "Theme: " + THEME_LABEL[mode];
}

function show(view) {
  $("auth-view").hidden = view !== "auth";
  $("app-view").hidden = view !== "app";
  $("userbar").hidden = view !== "app";
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function button(label, handler, extra) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn small ghost" + (extra ? " " + extra : "");
  b.textContent = label;
  b.addEventListener("click", handler);
  return b;
}

function badge(text, kind) {
  const span = document.createElement("span");
  span.className = "badge " + kind;
  span.textContent = text;
  return span;
}

function option(value, label, selected) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  if (selected) o.selected = true;
  return o;
}

function fmtDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

function fmtDay(iso) {
  const d = new Date(iso + "T00:00:00");
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString();
}

function emptyNote(id, isEmpty) { $(id).hidden = !isEmpty; }

function dialogError(id, message) {
  const el = $(id);
  el.textContent = message;
  el.hidden = false;
}

const can = {
  manageTeam: () => state.role === "owner" || state.role === "admin",
  importFile: () => ["owner", "admin", "manager"].includes(state.role),
  sell: () => ["owner", "admin", "manager", "staff"].includes(state.role),
};

/* ---------- boot / workspaces ---------- */

async function boot() {
  banner("");
  try {
    await api("GET", "/api/v1/session");
    show("app");
    try {
      await loadWorkspaces();
    } catch (_) {
      $("workspace-switch").hidden = true;
      $("new-workspace").hidden = true;
    }
    await Promise.all([loadCategories(), loadNotifications()]);
    await showView("inventory");
  } catch (err) {
    show("auth");
    if (err.status !== 401) banner(err.message);
  }
}

async function loadWorkspaces() {
  const list = await api("GET", "/api/v1/workspaces");
  const select = $("workspace-switch");
  select.replaceChildren();
  list.forEach((w) => select.append(option(w.workspace_id, w.name, w.active)));
  const active = list.find((w) => w.active);
  state.role = active ? active.role : "";
  $("role-label").textContent = state.role;
  $("add-member-card").hidden = !can.manageTeam();
  $("import-open").hidden = !can.importFile();
  $("new-sale").hidden = !can.sell();
  $("new-customer").hidden = !can.sell();
}

async function afterWorkspaceChange() {
  banner("");
  info("");
  await loadWorkspaces();
  await Promise.all([loadCategories(), loadNotifications()]);
  await showView("inventory");
}

/* ---------- views ---------- */

async function showView(view) {
  state.view = view;
  document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.id !== "view-" + view; });
  document.querySelectorAll("[data-view]").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  if (view === "inventory") await Promise.all([loadSummary(), loadProducts(), loadMovements()]);
  if (view === "purchasing") await Promise.all([loadOrders(), loadSuppliers()]);
  if (view === "sales") await refreshSales();
  if (view === "reports") await loadReports();
  if (view === "insights") await loadInsights();
  if (view === "team") await Promise.all([loadMembers(), loadActivity()]);
  if (view === "settings") await loadSettings();
  if (view === "assistant") startAssistant();
}

async function refreshInventory() {
  await Promise.all([loadSummary(), loadProducts(), loadMovements()]);
}

function forbiddenOr(err, message) {
  banner(err.status === 403 ? message : err.message);
}

/* ---------- inventory ---------- */

async function loadSummary() {
  const box = $("summary");
  box.replaceChildren();
  try {
    const s = (await api("GET", "/api/v1/dashboard")).summary;
    [["Products", s.products, false], ["Units in stock", s.units_in_stock, false],
     ["Low stock", s.low_stock, s.low_stock > 0], ["Out of stock", s.out_of_stock, s.out_of_stock > 0]]
      .forEach(([label, value, warn]) => {
        const card = document.createElement("div");
        card.className = "stat" + (warn ? " warn" : "");
        const l = document.createElement("div"); l.className = "label"; l.textContent = label;
        const v = document.createElement("div"); v.className = "value"; v.textContent = String(value);
        card.append(l, v);
        box.append(card);
      });
  } catch (err) {
    if (err.status !== 403) banner(err.message);
  }
}

async function loadProducts() {
  const params = new URLSearchParams({ search: $("search").value.trim(), stock: $("stock-filter").value,
    category: $("category-filter").value });
  try {
    const rows = await api("GET", "/api/v1/products?" + params.toString());
    state.products = rows;
    setStage(rows);
    const body = $("products-body");
    body.replaceChildren();
    emptyNote("products-empty", rows.length === 0);
    rows.forEach((p) => {
      const tr = document.createElement("tr");
      if (p.quantity <= p.minimum_stock) tr.className = "low";
      const actions = document.createElement("td");
      actions.append(
        button("Edit", () => openProduct(p)),
        button("+ In", () => openMovement(p, "IN")),
        button("- Out", () => openMovement(p, "OUT")),
      );
      tr.append(cell(p.sku), cell(p.name), cell(p.category || ""), cell(String(p.quantity), "num"),
        cell(String(p.minimum_stock), "num"), cell(p.unit), actions);
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view products."); }
}

async function loadMovements() {
  try {
    const rows = await api("GET", "/api/v1/movements?limit=50");
    const body = $("movements-body");
    body.replaceChildren();
    emptyNote("movements-empty", rows.length === 0);
    rows.forEach((m) => {
      const tr = document.createElement("tr");
      const type = document.createElement("td");
      type.append(badge(m.movement_type, m.movement_type === "IN" ? "in" : "out"));
      tr.append(cell(fmtDate(m.created_at)), cell(m.sku), cell(m.product_name), type,
        cell(String(m.quantity), "num"), cell(m.note));
      body.append(tr);
    });
  } catch (err) { if (err.status !== 403) banner(err.message); }
}

function openProduct(product) {
  state.editing = product || null;
  const form = $("product-form");
  form.reset();
  $("product-error").hidden = true;
  $("product-dialog-title").textContent = product ? "Edit product" : "Add product";
  if (product) {
    ["sku", "name", "description", "minimum_stock", "unit"].forEach((k) => { form.elements[k].value = product[k]; });
    form.elements.category_id.value = product.category_id || "";
  }
  $("product-dialog").showModal();
}

function openMovement(product, type) {
  state.movement = { product, type };
  $("movement-form").reset();
  $("movement-error").hidden = true;
  $("movement-dialog-title").textContent =
    (type === "IN" ? "Receive stock: " : "Issue stock: ") + product.sku + " (" + product.quantity + " on hand)";
  $("movement-dialog").showModal();
}

/* ---------- purchasing ---------- */

async function loadSuppliers() {
  try {
    const rows = await api("GET", "/api/v1/suppliers");
    state.suppliers = rows;
    const body = $("suppliers-body");
    body.replaceChildren();
    emptyNote("suppliers-empty", rows.length === 0);
    rows.forEach((s) => {
      const tr = document.createElement("tr");
      tr.append(cell(s.name), cell(s.email), cell(s.phone), cell(s.notes));
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view purchasing."); }
}

async function loadOrders() {
  try {
    const rows = await api("GET", "/api/v1/purchase-orders");
    const body = $("orders-body");
    body.replaceChildren();
    emptyNote("orders-empty", rows.length === 0);
    rows.forEach((o) => {
      const tr = document.createElement("tr");
      const items = o.items.map((i) => i.quantity + " x " + i.sku).join(", ");
      const status = document.createElement("td");
      status.append(badge(o.status, o.status));
      const actions = document.createElement("td");
      if (o.status === "ordered") {
        actions.append(
          button("Receive", () => orderAction(o, "receive", "Receive this order and add the items to stock?")),
          button("Cancel", () => orderAction(o, "cancel", "Cancel this order?")),
        );
      }
      tr.append(cell(fmtDate(o.created_at)), cell(o.supplier_name), cell(items), status, actions);
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view purchasing."); }
}

async function orderAction(order, action, question) {
  if (!window.confirm(question)) return;
  try {
    await api("POST", "/api/v1/purchase-orders/" + order.id + "/" + action);
    banner("");
    await loadOrders();
  } catch (err) { forbiddenOr(err, "Your role cannot do that."); }
}

function addOrderLine() {
  const row = document.createElement("div");
  row.className = "line";
  const select = document.createElement("select");
  select.required = true;
  state.products.forEach((p) => select.append(option(p.id, p.sku + " - " + p.name)));
  const qty = document.createElement("input");
  qty.type = "number"; qty.min = "1"; qty.max = "1000000"; qty.required = true; qty.placeholder = "Qty";
  const remove = button("Remove", () => row.remove());
  row.append(select, qty, remove);
  $("order-lines").append(row);
}

async function openOrder() {
  await Promise.all([loadSuppliers(), loadProducts()]);
  if (state.suppliers.length === 0) { banner("Add a supplier first."); return; }
  if (state.products.length === 0) { banner("Add a product first (Inventory tab)."); return; }
  banner("");
  $("order-form").reset();
  $("order-error").hidden = true;
  $("order-lines").replaceChildren();
  const select = $("order-supplier");
  select.replaceChildren();
  state.suppliers.forEach((s) => select.append(option(s.id, s.name)));
  addOrderLine();
  $("order-dialog").showModal();
}

/* ---------- reports ---------- */

async function loadReports() {
  try {
    const data = await api("GET", "/api/v1/reports/movements?days=" + encodeURIComponent($("report-days").value));
    const max = Math.max(1, ...data.daily.map((d) => Math.max(d.units_in, d.units_out)));
    const daily = $("report-daily");
    daily.replaceChildren();
    emptyNote("report-empty", data.daily.length === 0);
    data.daily.forEach((d) => {
      const tr = document.createElement("tr");
      const bars = document.createElement("td");
      const inBar = document.createElement("span");
      inBar.className = "bar in";
      inBar.style.width = Math.round((d.units_in / max) * 160) + "px";
      const outBar = document.createElement("span");
      outBar.className = "bar";
      outBar.style.width = Math.round((d.units_out / max) * 160) + "px";
      bars.append(inBar, document.createElement("br"), outBar);
      tr.append(cell(fmtDay(d.day)), cell(String(d.units_in), "num"), cell(String(d.units_out), "num"), bars);
      daily.append(tr);
    });
    const top = $("report-top");
    top.replaceChildren();
    data.top_products.forEach((p) => {
      const tr = document.createElement("tr");
      tr.append(cell(p.sku), cell(p.name), cell(String(p.units_in), "num"),
        cell(String(p.units_out), "num"), cell(String(p.movements), "num"));
      top.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view reports."); }
}

/* ---------- team ---------- */

async function loadMembers() {
  try {
    const rows = await api("GET", "/api/v1/members");
    const body = $("members-body");
    body.replaceChildren();
    rows.forEach((m) => {
      const tr = document.createElement("tr");
      const roleCell = document.createElement("td");
      if (can.manageTeam()) {
        const select = document.createElement("select");
        ["owner", "admin", "manager", "staff", "viewer"].forEach((r) => select.append(option(r, r, r === m.role)));
        select.addEventListener("change", () => changeRole(m, select));
        roleCell.append(select);
      } else {
        roleCell.textContent = m.role;
      }
      const actions = document.createElement("td");
      if (can.manageTeam()) actions.append(button("Remove", () => removeMember(m)));
      tr.append(cell(m.email), roleCell, cell(fmtDate(m.joined_at)), actions);
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view the team."); }
}

async function changeRole(member, select) {
  try {
    await api("PATCH", "/api/v1/members/" + member.user_id, { role: select.value });
    banner("");
    await loadMembers();
  } catch (err) { banner(err.message); await loadMembers(); }
}

async function removeMember(member) {
  if (!window.confirm("Remove " + member.email + " from this workspace?")) return;
  try {
    await api("DELETE", "/api/v1/members/" + member.user_id);
    banner("");
    await loadMembers();
  } catch (err) { banner(err.message); }
}

/* ---------- categories, insights, settings ---------- */

function fillCategorySelect(select, firstLabel) {
  const keep = select.value;
  select.replaceChildren(option("", firstLabel));
  state.categories.forEach((c) => select.append(option(c.id, c.name)));
  select.value = keep;
}

async function loadCategories() {
  try {
    state.categories = await api("GET", "/api/v1/categories");
    fillCategorySelect($("category-filter"), "All categories");
    fillCategorySelect($("product-form").elements.category_id, "No category");
    const list = $("category-list");
    list.replaceChildren();
    state.categories.forEach((c) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = c.name + " (" + c.products + ")";
      li.append(label, button("Delete", () => deleteCategory(c)));
      list.append(li);
    });
  } catch (err) { if (err.status !== 403) banner(err.message); }
}

async function deleteCategory(category) {
  if (!window.confirm("Delete the category " + category.name + "? Its products keep existing, uncategorised.")) return;
  try {
    await api("DELETE", "/api/v1/categories/" + category.id);
    await Promise.all([loadCategories(), loadProducts()]);
  } catch (err) { banner(err.message); }
}

async function loadNotifications() {
  try {
    const data = await api("GET", "/api/v1/notifications");
    state.alerts = data.items;
    $("bell-count").textContent = String(data.count);
    $("bell-count").hidden = data.count === 0;
  } catch (_) { /* alerts are optional */ }
}

async function loadActivity() {
  try {
    const rows = await api("GET", "/api/v1/activity?limit=30");
    const body = $("activity-body");
    body.replaceChildren();
    rows.forEach((a) => {
      const tr = document.createElement("tr");
      tr.append(cell(fmtDate(a.created_at)), cell(a.actor), cell(a.action.replace("_", " ") + " " + a.entity.replace("_", " ")), cell(a.detail));
      body.append(tr);
    });
  } catch (err) { if (err.status !== 403) banner(err.message); }
}

async function loadInsights() {
  try {
    const data = await api("GET", "/api/v1/insights?days=" + encodeURIComponent($("insight-days").value) + "&horizon=30");
    const list = $("insights-list");
    list.replaceChildren();
    data.insights.forEach((i) => {
      const li = document.createElement("li");
      const msg = document.createElement("span");
      msg.className = "sev-" + i.severity;
      msg.textContent = i.message;
      li.append(msg);
      list.append(li);
    });
    $("insight-note").textContent = "Average supplier lead time used: " + data.lead_days + " days. Suggested order = expected demand over (lead time + 7 days) + minimum stock - on hand - on order. " + data.method + ".";
    const body = $("forecast-body");
    body.replaceChildren();
    data.rows.forEach((r) => {
      const tr = document.createElement("tr");
      const status = document.createElement("td");
      status.append(badge(r.status.replace("-", " "), r.status));
      tr.append(cell(r.sku + " - " + r.name), cell(String(r.quantity), "num"), cell(String(r.on_order), "num"),
        cell(String(r.avg_daily_demand), "num"), cell(r.days_of_cover === null ? "-" : String(r.days_of_cover), "num"),
        cell(r.trend), cell(String(r.forecast_units), "num"), cell(String(r.suggested_reorder), "num"), status);
      body.append(tr);
    });
    const lead = $("lead-body");
    lead.replaceChildren();
    data.suppliers.forEach((s) => {
      const tr = document.createElement("tr");
      const days = (v) => (v === null ? "-" : String(v));
      tr.append(cell(s.name), cell(String(s.orders), "num"), cell(String(s.received), "num"),
        cell(days(s.avg_lead_days), "num"), cell(days(s.min_lead_days), "num"), cell(days(s.max_lead_days), "num"));
      lead.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view insights."); }
}

function renderPlan(plan) {
  const box = $("plan-box");
  box.replaceChildren();
  const title = document.createElement("p");
  title.textContent = "Current plan: " + plan.name.toUpperCase();
  box.append(title);
  Object.keys(plan.usage).forEach((k) => {
    const limit = plan.limits[k];
    const used = plan.usage[k];
    const label = document.createElement("div");
    label.textContent = k.replace("_", " ") + ": " + used + " / " + (limit === null ? "unlimited" : limit);
    const meter = document.createElement("div");
    meter.className = "meter";
    const fill = document.createElement("span");
    fill.style.width = (limit === null ? 0 : Math.min(100, Math.round((used / limit) * 100))) + "%";
    meter.append(fill);
    box.append(label, meter);
  });
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = "Online payments are not connected. To change the plan, ask the system administrator.";
  box.append(note);
}

async function loadSettings() {
  const admin = can.manageTeam();
  $("sku-form").hidden = !admin;
  $("api-card").hidden = !admin;
  try {
    const s = await api("GET", "/api/v1/settings");
    renderPlan(s.plan);
    $("sku-example").textContent = s.sku.example;
    const f = $("sku-form").elements;
    f.prefix.value = s.sku.prefix; f.padding.value = s.sku.padding; f.next_number.value = s.sku.next_number;
  } catch (err) { banner(err.message); }
  await Promise.all([loadCategories(), loadPrefs(), admin ? loadKeys() : Promise.resolve()]);
}

async function loadPrefs() {
  try {
    const p = await api("GET", "/api/v1/preferences");
    const f = $("prefs-form").elements;
    f.notify_low_stock.checked = p.notify_low_stock;
    f.notify_order_events.checked = p.notify_order_events;
  } catch (err) { banner(err.message); }
}

async function loadKeys() {
  try {
    const rows = await api("GET", "/api/v1/api-keys");
    const body = $("keys-body");
    body.replaceChildren();
    rows.forEach((k) => {
      const tr = document.createElement("tr");
      const action = document.createElement("td");
      if (!k.revoked) action.append(button("Revoke", () => revokeKey(k)));
      tr.append(cell(k.name), cell(k.prefix + "..."), cell(k.revoked ? "revoked" : k.scope),
        cell(k.last_used_at ? fmtDate(k.last_used_at) : "never"), action);
      body.append(tr);
    });
  } catch (err) { if (err.status !== 403) banner(err.message); }
}

async function revokeKey(key) {
  if (!window.confirm("Revoke the key " + key.name + "? Anything using it stops working.")) return;
  try { await api("DELETE", "/api/v1/api-keys/" + key.id); await loadKeys(); } catch (err) { banner(err.message); }
}

/* ---------- events ---------- */

/* ---------- bulk import (CSV or JSON, checked on the server) ---------- */

const IMPORT_FIELDS = [
  { key: "name", label: "Name (required)", match: /^(name|product|item|title)/i },
  { key: "sku", label: "SKU", match: /^(sku|code|item ?(no|number|code))/i },
  { key: "quantity", label: "Quantity", match: /^(qty|quantity|stock|on ?hand)/i },
  { key: "minimum_stock", label: "Minimum stock", match: /^(min|minimum|reorder)/i },
  { key: "unit", label: "Unit", match: /^(units?|uom|unit of measure)$/i },
  { key: "description", label: "Description", match: /^(desc|notes?)/i },
];
const IMPORT_DETECT_ORDER = ["sku", "quantity", "minimum_stock", "unit", "description", "name"];
const IMPORT_MAX_BYTES = 2 * 1024 * 1024;
const IMPORT_MAX_ROWS = 1000;
const IMPORT_SHOWN_ROWS = 200;
const imp = { headers: [], rows: [], map: {}, ready: false };

function parseCsv(text) {
  const first = text.split(/\r?\n/, 1)[0] || "";
  const delim = [",", ";", "\t"].map((d) => [d, first.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
  const rows = [];
  let row = [];
  let cur = "";
  let quoted = false;
  const endRow = () => {
    row.push(cur);
    cur = "";
    if (row.some((c) => c.trim() !== "")) rows.push(row);
    row = [];
  };
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === "\"") {
        if (text[i + 1] === "\"") { cur += "\""; i += 1; } else quoted = false;
      } else cur += ch;
    } else if (ch === "\"") quoted = true;
    else if (ch === delim) { row.push(cur); cur = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      endRow();
    } else cur += ch;
  }
  endRow();
  return rows.map((r) => r.map((c) => c.trim()));
}

function tableFromJson(text) {
  const data = JSON.parse(text);
  if (!Array.isArray(data) || !data.length || data.some((o) => o === null || typeof o !== "object" || Array.isArray(o))) {
    throw new Error("A JSON file must be a list of objects, one per product.");
  }
  const headers = [];
  data.forEach((o) => Object.keys(o).forEach((k) => { if (!headers.includes(k)) headers.push(k); }));
  const flat = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? "" : String(v));
  return [headers, ...data.map((o) => headers.map((h) => flat(o[h])))];
}

function detectImportMap(headers) {
  const map = {};
  const used = new Set();
  IMPORT_DETECT_ORDER.forEach((key) => {
    const field = IMPORT_FIELDS.find((f) => f.key === key);
    const at = headers.findIndex((h, i) => !used.has(i) && field.match.test(h));
    if (at >= 0) { map[key] = at; used.add(at); }
  });
  return map;
}

function importPayloadRows() {
  return imp.rows.map((cells, i) => {
    const out = { line: i + 2 };
    IMPORT_FIELDS.forEach((f) => {
      const at = imp.map[f.key];
      if (at !== undefined) out[f.key] = cells[at] === undefined ? "" : cells[at];
    });
    return out;
  });
}

function importMessage(text, ok) {
  const el = $("import-error");
  el.textContent = text || "";
  el.hidden = !text;
  el.classList.toggle("good", Boolean(ok));
}

function importReset() {
  imp.headers = [];
  imp.rows = [];
  imp.map = {};
  imp.ready = false;
  importMessage("");
  $("import-info").textContent = "";
  $("import-map").hidden = true;
  $("import-result").hidden = true;
  $("import-summary").textContent = "";
  $("import-rows").replaceChildren();
  $("import-check").hidden = true;
  $("import-run").hidden = true;
  $("import-cancel").textContent = "Cancel";
}

async function loadImportFile(file) {
  importReset();
  if (!file) return;
  const ext = (file.name.split(".").pop() || "").toLowerCase();
  if (ext === "xlsx" || ext === "xls") {
    importMessage("Excel files are not read directly. In Excel choose Save As, then CSV, and pick that file.");
    return;
  }
  if (!["csv", "txt", "json"].includes(ext)) { importMessage("Unsupported file type. Use a CSV or JSON file."); return; }
  if (file.size === 0) { importMessage("That file is empty."); return; }
  if (file.size > IMPORT_MAX_BYTES) { importMessage("That file is larger than 2 MB. Split it into smaller files."); return; }
  let table;
  try {
    const text = (await file.text()).replace(/^\uFEFF/, "");
    table = ext === "json" ? tableFromJson(text) : parseCsv(text);
  } catch (err) {
    importMessage(err instanceof SyntaxError ? "That file is not valid JSON." : err.message || "Couldn't read that file.");
    return;
  }
  if (table.length < 2) { importMessage("No data rows found. The first line must be the column names, followed by at least one row."); return; }
  if (table.length - 1 > IMPORT_MAX_ROWS) {
    importMessage("This file has " + (table.length - 1) + " rows. Split it into files of up to " + IMPORT_MAX_ROWS + " rows.");
    return;
  }
  imp.headers = table[0];
  imp.rows = table.slice(1);
  imp.map = detectImportMap(imp.headers);
  $("import-info").textContent = file.name + ": " + imp.rows.length + " rows. Check that each column matches the right field.";
  renderImportMap();
}

function renderImportMap() {
  const box = $("import-map");
  box.replaceChildren();
  IMPORT_FIELDS.forEach((f) => {
    const label = document.createElement("label");
    label.append(document.createTextNode(f.label));
    const select = document.createElement("select");
    select.append(option("", "Not in file", imp.map[f.key] === undefined));
    imp.headers.forEach((h, i) => select.append(option(String(i), h || "Column " + (i + 1), imp.map[f.key] === i)));
    select.addEventListener("change", () => {
      if (select.value === "") delete imp.map[f.key]; else imp.map[f.key] = Number(select.value);
      $("import-result").hidden = true;
      $("import-run").hidden = true;
      imp.ready = false;
    });
    label.append(select);
    box.append(label);
  });
  box.hidden = false;
  $("import-check").hidden = false;
}

const IMPORT_STATUS = {
  will_create: ["Will create", "in"], will_update: ["Will update", "ordered"],
  created: ["Created", "in"], updated: ["Updated", "ordered"],
  unchanged: ["No change", "idle"], error: ["Error", "out"],
};

function renderImportResult(data) {
  const body = $("import-rows");
  body.replaceChildren();
  data.rows.slice(0, IMPORT_SHOWN_ROWS).forEach((r) => {
    const tr = document.createElement("tr");
    const [label, kind] = IMPORT_STATUS[r.status] || [r.status, "idle"];
    const status = document.createElement("td");
    status.append(badge(label, kind));
    tr.append(cell(String(r.line), "num"), cell(r.sku), cell(r.name), status, cell(r.errors.join("; ")));
    body.append(tr);
  });
  const parts = data.dry_run
    ? [data.created + " to create", data.updated + " to update"]
    : [data.created + " created", data.updated + " updated", data.unchanged + " unchanged"];
  parts.push(data.failed + " with errors");
  let text = (data.dry_run ? "Checked: " : "Imported: ") + parts.join(", ") + ".";
  if (data.rows.length > IMPORT_SHOWN_ROWS) text += " Showing the first " + IMPORT_SHOWN_ROWS + " rows.";
  $("import-summary").textContent = text;
  $("import-result").hidden = false;
  labelTables();
}

async function runImport(dryRun) {
  if (imp.map.name === undefined) { importMessage("Choose which column holds the product name."); return; }
  importMessage("");
  $("import-check").disabled = true;
  $("import-run").disabled = true;
  try {
    const data = await api("POST", "/api/v1/products/import", { rows: importPayloadRows(), dry_run: dryRun });
    renderImportResult(data);
    if (dryRun) {
      imp.ready = data.created + data.updated > 0;
      $("import-run").hidden = !imp.ready;
      $("import-run").textContent = "Import " + (data.created + data.updated) + " rows";
      if (!imp.ready) importMessage("Nothing can be imported. Fix the errors in your file and choose it again.");
    } else {
      $("import-check").hidden = true;
      $("import-run").hidden = true;
      $("import-map").hidden = true;
      $("import-cancel").textContent = "Close";
      importMessage("Import finished.", true);
      try { await refreshInventory(); } catch (_) { /* the list refreshes the next time it is opened */ }
    }
  } catch (err) {
    importMessage(err.message);
  } finally {
    $("import-check").disabled = false;
    $("import-run").disabled = false;
  }
}

function openImport() {
  importReset();
  $("import-file").value = "";
  $("import-dialog").showModal();
}

/* ---------- mobile: every table row becomes a labelled card ---------- */

function labelTables() {
  document.querySelectorAll("table").forEach((t) => {
    const heads = Array.from(t.querySelectorAll("thead th")).map((th) => th.textContent.trim());
    t.querySelectorAll("tbody tr").forEach((tr) => {
      Array.from(tr.cells).forEach((td, i) => {
        const label = td.colSpan > 1 ? "" : heads[i] || "";
        if (td.getAttribute("data-label") !== label) td.setAttribute("data-label", label);
      });
    });
  });
}

/* ---------- 3D stock bars (canvas, no libraries, works under the strict CSP) ---------- */

const stage = { items: [], angle: 0.6, drag: false, lastX: 0, idleAt: 0, running: false };
const STAGE_COLORS = { ok: [92, 127, 203], low: [242, 163, 59], out: [217, 83, 79] };

function setStage(products) {
  const list = products.slice().sort((a, b) => b.quantity - a.quantity).slice(0, 12);
  const max = Math.max(1, ...list.map((p) => p.quantity));
  const before = new Map(stage.items.map((i) => [i.id, i]));
  stage.items = list.map((p) => ({
    id: p.id, label: p.sku, h: before.has(p.id) ? before.get(p.id).h : 0,
    target: Math.max(p.quantity / max, p.quantity === 0 ? 0.04 : 0.07),
    status: p.quantity === 0 ? "out" : p.quantity <= p.minimum_stock ? "low" : "ok",
  }));
  $("stage-card").hidden = list.length === 0;
  if (!stage.running && list.length) { stage.running = true; requestAnimationFrame(drawStage); }
}

function shade(rgb, k) {
  return "rgb(" + rgb.map((c) => Math.max(0, Math.min(255, Math.round(c * k)))).join(",") + ")";
}

function drawStage(now) {
  const canvas = $("stage");
  if (!canvas || canvas.offsetParent === null || !stage.items.length) { stage.running = false; return; }
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!still && !stage.drag && now - stage.idleAt > 2500) stage.angle += 0.004;
  const n = stage.items.length;
  const cols = Math.min(4, n);
  const rows = Math.ceil(n / cols);
  const gap = 1.5;
  const scale = Math.min(w / ((cols + rows) * gap * 0.9 + 2), h / 5.2);
  const cx = w / 2;
  const baseY = h * 0.64;
  const tall = h * 0.5;
  const cos = Math.cos(stage.angle);
  const sin = Math.sin(stage.angle);
  const text = getComputedStyle(document.body).color;
  const placed = stage.items.map((it, i) => {
    it.h += (it.target - it.h) * (still ? 1 : 0.1);
    const gx = (i % cols - (cols - 1) / 2) * gap;
    const gz = (Math.floor(i / cols) - (rows - 1) / 2) * gap;
    return { it, x: gx * cos - gz * sin, z: gx * sin + gz * cos };
  }).sort((a, b) => (a.x + a.z) - (b.x + b.z));
  const proj = (x, z, y) => [cx + (x - z) * 0.866 * scale, baseY + (x + z) * 0.5 * scale - y * tall];
  const poly = (pts, fill) => {
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  };
  const half = 0.42;
  const corners = [[-half, -half], [half, -half], [half, half], [-half, half]];
  placed.forEach(({ it, x, z }) => {
    const rgb = STAGE_COLORS[it.status];
    const c = corners.map(([dx, dz]) => [x + dx * cos - dz * sin, z + dx * sin + dz * cos]);
    const top = c.map((p) => proj(p[0], p[1], it.h));
    const low = c.map((p) => proj(p[0], p[1], 0));
    [[0, 1, [0, -1]], [1, 2, [1, 0]], [2, 3, [0, 1]], [3, 0, [-1, 0]]].forEach(([a, b, nrm]) => {
      const nx = nrm[0] * cos - nrm[1] * sin;
      const nz = nrm[0] * sin + nrm[1] * cos;
      if (nx + nz <= 0) return;
      poly([low[a], low[b], top[b], top[a]], shade(rgb, 0.78 + 0.16 * (nx - nz)));
    });
    poly(top, shade(rgb, 1.12));
    const [lx, ly] = proj(x, z, 0);
    ctx.fillStyle = text;
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(it.label, lx, ly + 0.9 * scale * 0.5 + 14);
  });
  requestAnimationFrame(drawStage);
}

function bindStage() {
  const canvas = $("stage");
  canvas.addEventListener("pointerdown", (e) => { stage.drag = true; stage.lastX = e.clientX; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!stage.drag) return;
    stage.angle -= (e.clientX - stage.lastX) * 0.01;
    stage.lastX = e.clientX;
  });
  const stop = () => { stage.drag = false; stage.idleAt = performance.now(); };
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);
}

function bindImport() {
  $("import-open").addEventListener("click", openImport);
  $("import-cancel").addEventListener("click", () => $("import-dialog").close());
  $("import-file").addEventListener("change", (e) => loadImportFile(e.target.files[0]));
  $("import-check").addEventListener("click", () => runImport(true));
  $("import-run").addEventListener("click", () => runImport(false));
  const drop = $("import-drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("hot"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("hot"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("hot");
    if (e.dataTransfer.files[0]) loadImportFile(e.dataTransfer.files[0]);
  });
  let pending = 0;
  new MutationObserver(() => {
    if (!pending) pending = requestAnimationFrame(() => { pending = 0; labelTables(); });
  }).observe(document.body, { childList: true, subtree: true });
  bindStage();
}

/* ---------- calculator and assistant tabs (logic lives in tools.js) ---------- */

const TOOLS = window.StockFlowTools;
const fmtNum = (x) => (x === null || Number.isNaN(x) ? "n/a" : Number(x).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtPct = (x) => (x === null ? "n/a" : fmtNum(x) + "%");

const CALCULATORS = [
  { title: "Profit and margin", fields: [["cost", "Cost", 40], ["price", "Selling price", 55]],
    run: (v) => { const r = TOOLS.calc.profit(v.cost, v.price); return [["Profit", fmtNum(r.profit)], ["Margin", fmtPct(r.margin)], ["Markup", fmtPct(r.markup)]]; } },
  { title: "Markup price", fields: [["cost", "Cost", 40], ["pct", "Markup %", 30]],
    run: (v) => { const r = TOOLS.calc.markup(v.cost, v.pct); return [["Selling price", fmtNum(r.price)], ["Profit per unit", fmtNum(r.profit)]]; } },
  { title: "Discount", fields: [["price", "Original price", 120], ["pct", "Discount %", 15]],
    run: (v) => { const r = TOOLS.calc.discount(v.price, v.pct); return [["Discount amount", fmtNum(r.amount)], ["Final price", fmtNum(r.final)]]; } },
  { title: "Sales tax or VAT", fields: [["amount", "Amount", 100], ["rate", "Rate %", 17], ["mode", "Amount is", [["ex", "Without tax"], ["in", "With tax included"]]]],
    run: (v) => { const r = TOOLS.calc.tax(v.amount, v.rate, v.mode === "in"); return [["Net", fmtNum(r.net)], ["Tax", fmtNum(r.tax)], ["Gross", fmtNum(r.gross)]]; } },
  { title: "Break-even", fields: [["fixed", "Fixed costs", 5000], ["variable", "Variable cost per unit", 12], ["price", "Selling price per unit", 20]],
    run: (v) => { const r = TOOLS.calc.breakEven(v.fixed, v.variable, v.price); return r.error ? [[r.error, ""]] : [["Break-even units", String(r.units)], ["Break-even revenue", fmtNum(r.revenue)]]; } },
];

function buildCalculators() {
  const grid = $("calc-grid");
  if (grid.childElementCount) return;
  CALCULATORS.forEach((c) => {
    const card = document.createElement("div");
    card.className = "card";
    const title = document.createElement("h3");
    title.textContent = c.title;
    const out = document.createElement("div");
    out.className = "calc-out";
    const inputs = {};
    const refresh = () => {
      const values = {};
      c.fields.forEach(([key, , def]) => {
        values[key] = Array.isArray(def) ? inputs[key].value : Number(inputs[key].value) || 0;
      });
      out.replaceChildren();
      c.run(values).forEach(([label, value]) => {
        const row = document.createElement("div");
        const l = document.createElement("span");
        l.textContent = label;
        const v = document.createElement("strong");
        v.textContent = value;
        row.append(l, v);
        out.append(row);
      });
    };
    card.append(title);
    c.fields.forEach(([key, label, def]) => {
      const wrap = document.createElement("label");
      wrap.append(document.createTextNode(label));
      let input;
      if (Array.isArray(def)) {
        input = document.createElement("select");
        def.forEach(([value, text], i) => input.append(option(value, text, i === 0)));
      } else {
        input = document.createElement("input");
        input.type = "number";
        input.step = "any";
        input.inputMode = "decimal";
        input.value = String(def);
      }
      input.addEventListener("input", refresh);
      inputs[key] = input;
      wrap.append(input);
      card.append(wrap);
    });
    card.append(out);
    grid.append(card);
    refresh();
  });
}

const CHAT_CHIPS = ["What should I reorder?", "How is my business doing?", "Anything unusual?", "What sells fastest?", "Which products are not selling?", "Predict next month's demand"];
const chat = { started: false, busy: false };

function chatBubble(kind, content) {
  const log = $("chat-log");
  const box = document.createElement("div");
  box.className = "bubble " + kind;
  if (typeof content === "string") {
    box.textContent = content;
  } else {
    const p = document.createElement("p");
    p.textContent = content.text;
    box.append(p);
    if (content.items && content.items.length) {
      const ul = document.createElement("ul");
      content.items.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.append(li); });
      box.append(ul);
    }
    if (content.note) {
      const n = document.createElement("p");
      n.className = "muted";
      n.textContent = content.note;
      box.append(n);
    }
  }
  log.append(box);
  log.scrollTop = log.scrollHeight;
  return box;
}

async function answerQuestion(question) {
  const intent = TOOLS.classify(question);
  if (intent === "help") {
    return { text: "I can answer questions about reorders, stock levels, fast and slow sellers, demand forecasts, unusual stock-outs and a quick summary.", note: "Try one of the suggestions below." };
  }
  if (intent === "unusual") return TOOLS.answerUnusual(TOOLS.unusualMovements(await api("GET", "/api/v1/movements?limit=200")));
  if (intent === "summary") {
    const [dash, ins] = await Promise.all([api("GET", "/api/v1/dashboard"), api("GET", "/api/v1/insights?days=60&horizon=30")]);
    return TOOLS.answerSummary(dash, ins.insights);
  }
  const data = await api("GET", "/api/v1/insights?days=60&horizon=30");
  if (intent === "reorder") return TOOLS.answerReorder(data.rows);
  if (intent === "fast") return TOOLS.answerFast(data.rows);
  if (intent === "slow") return TOOLS.answerSlow(data.rows);
  if (intent === "forecast") return TOOLS.answerForecast(data.rows, data.horizon);
  return TOOLS.answerStock(TOOLS.findProduct(question, data.rows));
}

async function askAssistant(raw) {
  const question = raw.trim().slice(0, 200);
  if (!question || chat.busy) return;
  chat.busy = true;
  $("chat-input").value = "";
  chatBubble("me", question);
  const waiting = chatBubble("ai", "Looking at your records");
  try {
    const answer = await answerQuestion(question);
    waiting.remove();
    chatBubble("ai", answer);
  } catch (err) {
    waiting.remove();
    chatBubble("ai", err.status === 403 ? "Your role cannot view that data." : "Sorry, I couldn't load your records. " + err.message);
  } finally {
    chat.busy = false;
  }
}

function startAssistant() {
  if (chat.started) return;
  chat.started = true;
  chatBubble("ai", { text: "Hello. Ask me about your stock, reorders or sales trends.", note: "Answers use fixed rules on your own records. I am not a language model and I never change your data." });
  const chips = $("chat-chips");
  CHAT_CHIPS.forEach((t) => chips.append(button(t, () => askAssistant(t))));
}

function bindTools() {
  buildCalculators();
  $("chat-form").addEventListener("submit", (e) => { e.preventDefault(); askAssistant($("chat-input").value); });
}

/* ---------- sales: customers and orders that reserve stock ---------- */

state.customers = [];
state.availability = [];

async function loadCustomers() {
  try {
    const rows = await api("GET", "/api/v1/customers");
    state.customers = rows;
    const body = $("customers-body");
    body.replaceChildren();
    emptyNote("customers-empty", rows.length === 0);
    rows.forEach((c) => {
      const tr = document.createElement("tr");
      tr.append(cell(c.name), cell(c.email), cell(c.phone), cell(String(c.open_orders), "num"));
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view customers."); }
}

async function loadAvailability() {
  try {
    const rows = await api("GET", "/api/v1/sales/availability");
    state.availability = rows;
    const body = $("availability-body");
    body.replaceChildren();
    emptyNote("availability-empty", rows.length === 0);
    rows.forEach((p) => {
      const tr = document.createElement("tr");
      tr.append(cell(p.sku + " - " + p.name), cell(String(p.quantity), "num"), cell(String(p.reserved), "num"), cell(String(p.available), "num"));
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view availability."); }
}

const SALE_BADGES = { open: "ordered", shipped: "received", cancelled: "cancelled" };

async function loadSales() {
  try {
    const rows = await api("GET", "/api/v1/sales-orders");
    const body = $("sales-body");
    body.replaceChildren();
    emptyNote("sales-empty", rows.length === 0);
    rows.forEach((o) => {
      const tr = document.createElement("tr");
      const items = o.items.map((i) => i.quantity + " x " + i.sku).join(", ");
      const status = document.createElement("td");
      status.append(badge(o.status, SALE_BADGES[o.status] || "idle"));
      const actions = document.createElement("td");
      if (o.status === "open" && can.sell()) {
        actions.append(
          button("Ship", () => saleAction(o, "ship", "Ship this order? The units leave your stock.")),
          button("Cancel", () => saleAction(o, "cancel", "Cancel this order and free its reserved stock?")),
        );
      }
      tr.append(cell(fmtDate(o.created_at)), cell(o.customer_name), cell(items), status, actions);
      body.append(tr);
    });
  } catch (err) { forbiddenOr(err, "Your role cannot view sales."); }
}

async function refreshSales() {
  await Promise.all([loadSales(), loadCustomers(), loadAvailability()]);
}

async function saleAction(order, action, question) {
  if (!window.confirm(question)) return;
  try {
    await api("POST", "/api/v1/sales-orders/" + order.id + "/" + action);
    banner("");
    await refreshSales();
  } catch (err) {
    banner(err.status === 403 ? "Your role cannot do that." : err.message);
    await refreshSales();
  }
}

function addSaleLine() {
  const row = document.createElement("div");
  row.className = "line";
  const select = document.createElement("select");
  select.required = true;
  state.availability.filter((p) => p.available > 0).forEach((p) => {
    select.append(option(p.id, p.sku + " - " + p.name + " (" + p.available + " available)"));
  });
  const qty = document.createElement("input");
  qty.type = "number"; qty.min = "1"; qty.max = "1000000"; qty.required = true; qty.placeholder = "Qty";
  row.append(select, qty, button("Remove", () => row.remove()));
  $("sale-lines").append(row);
}

async function openSale() {
  await Promise.all([loadCustomers(), loadAvailability()]);
  if (state.customers.length === 0) { banner("Add a customer first."); return; }
  if (!state.availability.some((p) => p.available > 0)) { banner("There is no available stock to sell."); return; }
  banner("");
  $("sale-form").reset();
  $("sale-error").hidden = true;
  $("sale-lines").replaceChildren();
  const select = $("sale-customer");
  select.replaceChildren();
  state.customers.forEach((c) => select.append(option(c.id, c.name)));
  addSaleLine();
  $("sale-dialog").showModal();
}

function bindSales() {
  $("new-sale").addEventListener("click", openSale);
  $("sale-cancel").addEventListener("click", () => $("sale-dialog").close());
  $("sale-add-line").addEventListener("click", addSaleLine);
  $("sale-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const items = Array.from($("sale-lines").children).map((row) => ({
      product_id: row.querySelector("select").value, quantity: Number(row.querySelector("input").value),
    }));
    try {
      await api("POST", "/api/v1/sales-orders", { customer_id: $("sale-customer").value, note: event.target.elements.note.value, items });
      $("sale-dialog").close();
      await refreshSales();
    } catch (err) {
      dialogError("sale-error", err.status === 403 ? "Your role cannot create sales orders." : err.message);
    }
  });
  $("new-customer").addEventListener("click", () => {
    $("customer-form").reset();
    $("customer-error").hidden = true;
    $("customer-dialog").showModal();
  });
  $("customer-cancel").addEventListener("click", () => $("customer-dialog").close());
  $("customer-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("POST", "/api/v1/customers", { name: f.name.value.trim(), email: f.email.value.trim(), phone: f.phone.value.trim(), notes: f.notes.value });
      $("customer-dialog").close();
      await loadCustomers();
    } catch (err) {
      dialogError("customer-error", err.status === 403 ? "Your role cannot add customers." : err.message);
    }
  });
}

function bind() {
  const tabs = { login: $("tab-login"), register: $("tab-register") };
  tabs.login.addEventListener("click", () => switchTab("login"));
  tabs.register.addEventListener("click", () => switchTab("register"));
  function switchTab(which) {
    tabs.login.classList.toggle("active", which === "login");
    tabs.register.classList.toggle("active", which === "register");
    $("login-form").hidden = which !== "login";
    $("register-form").hidden = which !== "register";
    banner("");
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("POST", "/api/v1/auth/login", { email: f.email.value, password: f.password.value });
      event.target.reset();
      await boot();
    } catch (err) { banner(err.message); }
  });

  $("register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("POST", "/api/v1/auth/register", {
        workspace_name: f.workspace_name.value, email: f.email.value, password: f.password.value,
      });
      event.target.reset();
      await boot();
    } catch (err) { banner(err.message); }
  });

  $("logout").addEventListener("click", async () => {
    try { await api("POST", "/api/v1/auth/logout"); } catch (_) { /* ignore */ }
    show("auth");
    banner("");
  });

  document.querySelectorAll("[data-view]").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

  $("workspace-switch").addEventListener("change", async (event) => {
    try {
      await api("POST", "/api/v1/workspaces/switch", { workspace_id: event.target.value });
      await afterWorkspaceChange();
    } catch (err) { banner(err.message); await loadWorkspaces(); }
  });

  $("new-workspace").addEventListener("click", async () => {
    const name = window.prompt("Name of the new workspace:");
    if (!name || !name.trim()) return;
    try {
      await api("POST", "/api/v1/workspaces", { name: name.trim() });
      await afterWorkspaceChange();
    } catch (err) { banner(err.message); }
  });

  let timer = null;
  $("search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(loadProducts, 250); });
  $("stock-filter").addEventListener("change", loadProducts);
  $("add-product").addEventListener("click", () => openProduct(null));
  bindImport();
  bindTools();
  bindSales();
  $("product-cancel").addEventListener("click", () => $("product-dialog").close());
  $("movement-cancel").addEventListener("click", () => $("movement-dialog").close());

  $("product-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    const payload = {
      name: f.name.value.trim(), description: f.description.value,
      minimum_stock: Number(f.minimum_stock.value), unit: f.unit.value.trim(),
      category_id: f.category_id.value || null,
    };
    const sku = f.sku.value.trim();
    if (sku || state.editing) payload.sku = sku;  // empty on create: the server generates one
    try {
      if (state.editing) await api("PATCH", "/api/v1/products/" + state.editing.id, payload);
      else await api("POST", "/api/v1/products", payload);
      $("product-dialog").close();
      await refreshInventory();
    } catch (err) {
      dialogError("product-error", err.status === 403 ? "Your role cannot change the catalogue." : err.message);
    }
  });

  $("movement-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("POST", "/api/v1/movements", {
        product_id: state.movement.product.id, movement_type: state.movement.type,
        quantity: Number(f.quantity.value), note: f.note.value,
      });
      $("movement-dialog").close();
      await refreshInventory();
    } catch (err) {
      dialogError("movement-error", err.status === 403 ? "Your role cannot record stock movements." : err.message);
    }
  });

  $("new-supplier").addEventListener("click", () => {
    $("supplier-form").reset();
    $("supplier-error").hidden = true;
    $("supplier-dialog").showModal();
  });
  $("supplier-cancel").addEventListener("click", () => $("supplier-dialog").close());
  $("supplier-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("POST", "/api/v1/suppliers", {
        name: f.name.value.trim(), email: f.email.value.trim(), phone: f.phone.value.trim(), notes: f.notes.value,
      });
      $("supplier-dialog").close();
      await loadSuppliers();
    } catch (err) {
      dialogError("supplier-error", err.status === 403 ? "Your role cannot add suppliers." : err.message);
    }
  });

  $("new-order").addEventListener("click", openOrder);
  $("order-cancel").addEventListener("click", () => $("order-dialog").close());
  $("order-add-line").addEventListener("click", addOrderLine);
  $("order-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const items = Array.from($("order-lines").children).map((row) => ({
      product_id: row.querySelector("select").value, quantity: Number(row.querySelector("input").value),
    }));
    try {
      await api("POST", "/api/v1/purchase-orders", {
        supplier_id: $("order-supplier").value, note: event.target.elements.note.value, items,
      });
      $("order-dialog").close();
      await loadOrders();
    } catch (err) {
      dialogError("order-error", err.status === 403 ? "Your role cannot create orders." : err.message);
    }
  });

  $("report-days").addEventListener("change", loadReports);

  $("member-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("POST", "/api/v1/members", { email: f.email.value, role: f.role.value });
      event.target.reset();
      banner("");
      await loadMembers();
    } catch (err) { banner(err.message); }
  });
  applyTheme(storedTheme());
  $("theme-toggle").addEventListener("click", () => {
    applyTheme(THEMES[(THEMES.indexOf(storedTheme()) + 1) % THEMES.length]);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(storedTheme()));
  $("bell").addEventListener("click", async () => {
    await loadNotifications();
    info(state.alerts.length ? state.alerts.map((a) => a.message).join("  ") : "No alerts. Everything looks fine.");
  });
  $("category-filter").addEventListener("change", loadProducts);
  $("insight-days").addEventListener("change", loadInsights);

  $("sku-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      const r = await api("PUT", "/api/v1/settings/sku", {
        prefix: f.prefix.value.trim(), padding: Number(f.padding.value), next_number: Number(f.next_number.value),
      });
      $("sku-example").textContent = r.example;
      banner("");
    } catch (err) { banner(err.message); }
  });

  $("category-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("POST", "/api/v1/categories", { name: event.target.elements.name.value });
      event.target.reset();
      banner("");
      await loadCategories();
    } catch (err) { banner(err.status === 403 ? "Your role cannot manage categories." : err.message); }
  });

  $("prefs-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      await api("PUT", "/api/v1/preferences", {
        notify_low_stock: f.notify_low_stock.checked, notify_order_events: f.notify_order_events.checked,
      });
      banner("");
      await loadNotifications();
    } catch (err) { banner(err.message); }
  });

  $("key-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.target.elements;
    try {
      const k = await api("POST", "/api/v1/api-keys", { name: f.name.value, scope: f.scope.value });
      const box = $("new-key");
      box.textContent = "Copy this key now. It will not be shown again: " + k.key;
      box.hidden = false;
      event.target.reset();
      banner("");
      await loadKeys();
    } catch (err) { banner(err.message); }
  });
}

bind();
boot();
