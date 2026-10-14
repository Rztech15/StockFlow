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
  if (view === "reports") await loadReports();
  if (view === "insights") await loadInsights();
  if (view === "team") await Promise.all([loadMembers(), loadActivity()]);
  if (view === "settings") await loadSettings();
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
