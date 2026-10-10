/* Seller dashboard. Loaded by index.html only for shop owners; it extends the shared app object (state, views, actions).
   Every figure and every change goes through /api/seller/*, where the server derives the shop from the signed login. */
(() => {
"use strict";
const X = window.__app;
if (!X || !X.S || !X.S.me || !X.S.me.is_seller) return;
const { S, V, A, TXT, W, api, t, esc, money, moneyText, draw, go, back, tab, setMain, clearMain, toast, fail, awn, initial, setHtml, hooks, ask, buzz } = X;

/* ---------- text ---------- */
Object.assign(TXT.en, {
  sOverview: "Overview", sOrders: "Orders", sProducts: "Products", sSettings: "Settings",
  newOrders: "New orders", toConfirm: "Payments to confirm", ordersToday: "Orders today", sales30: "Sales, last 30 days",
  toPack: "To pack", onTheWay: "On the way", disputes: "Open disputes", delivered: "Delivered orders",
  plan: "Plan", planUntil: "until {d}", productsUsed: "{u} of {l} products", unlimited: "unlimited", planHint: "To change your plan or get verified, open the bot and use its menu: My plan, Get verified.",
  shareLink: "Your shop link", copy: "Copy link", copied: "Link copied",
  f_todo: "To do", f_payment: "Payments", f_shipped: "On the way", f_done: "Done", f_all: "All", noOrdersS: "No orders here",
  sst_placed: "New", sst_accepted: "Accepted", sst_packed: "Packed", sst_shipped: "On the way", sst_delivered: "Delivered", sst_cancelled: "Cancelled", sst_awaiting_payment: "Waiting for payment",
  do_accepted: "Accept order", do_packed: "Mark as packed", do_shipped: "Mark as shipped", cancel: "Cancel order", cancelAsk: "Cancel this order? The buyer is told and the stock goes back.",
  call: "Call", map: "Open map", payCod: "Pay on delivery", payPaid: "Paid", payReviewS: "Confirm this payment", payAwait: "Waiting for the buyer to pay", payDisputed: "Payment dispute",
  reference: "Reference", showShot: "Show screenshot", got: "Money received", notGot: "Not received", confirmPayHint: "Check your bank or wallet app before answering.",
  addProduct: "Add product", editProduct: "Edit product", noProductsS: "No products yet. Add your first one.", hiddenTag: "Hidden", stockN: "{n} in stock",
  price: "Price (ETB)", pname: "Name", descr: "Description", category: "Category", none: "None", stockLabel: "Stock", changePhoto: "Choose photo",
  save: "Save", saved: "Saved", hide: "Hide from buyers", show: "Show to buyers", uploading: "Uploading photo",
  shopInfo: "Shop details", sname: "Shop name", city: "City", sphone: "Phone", codTitle: "Cash on delivery", codOn: "On: buyers can pay when the order arrives", codOff: "Off: buyers must pay first",
  zones: "Delivery zones", zonesHint: "No zones means free delivery everywhere.", zoneName: "Zone name", zoneFee: "Fee (ETB, 0 = free)", add: "Add",
  accounts: "Payment accounts", accountsHint: "Buyers pay you directly to these accounts.", provider: "Bank or wallet", number: "Account number", walletNumber: "Wallet phone number",
  holder: "Account holder name", bankName: "Bank name", del: "Delete",
  s_title: "Enter a product name.", s_price: "Enter a price above 0.", s_stock: "Enter a stock number, 0 or more.", s_category: "Pick a valid category.",
  s_plan_limit: "Your plan's product limit is reached. Upgrade in the bot (menu: My plan) to add more.", s_need_account: "Add a payment account first: buyers must always have a way to pay.",
  s_bad_state: "This order just changed. Reload and try again.", s_phone: "Enter a valid Ethiopian phone number.", s_number: "Enter a valid number.", s_label: "Enter the bank name.",
  s_holder: "Enter the account holder name.", s_fee: "Enter a fee of 0 or more.", s_name: "Enter a name.", s_city: "Enter the city.", s_too_large: "That photo is too large (5 MB max).",
  s_bad_image: "Please use a JPG or PNG photo.", s_upload_failed: "The photo could not be uploaded. Try again.", s_not_a_seller: "Your shop is not active."
});
Object.assign(TXT.am, {
  sOverview: "አጠቃላይ", sOrders: "ትዕዛዞች", sProducts: "ምርቶች", sSettings: "ቅንብር", newOrders: "አዲስ ትዕዛዞች", toConfirm: "የሚረጋገጡ ክፍያዎች", ordersToday: "የዛሬ ትዕዛዞች", sales30: "የ30 ቀን ሽያጭ",
  f_todo: "የሚሰሩ", f_payment: "ክፍያዎች", f_shipped: "በመንገድ ላይ", f_done: "የተጠናቀቁ", noOrdersS: "እዚህ ትዕዛዝ የለም",
  sst_placed: "አዲስ", sst_accepted: "ተቀባይነት አግኝቷል", sst_packed: "ተጠቅልሏል", sst_shipped: "በመንገድ ላይ", sst_delivered: "ደርሷል", sst_cancelled: "ተሰርዟል",
  do_accepted: "ትዕዛዙን ተቀበል", do_packed: "ተጠቅልሏል በል", do_shipped: "ተልኳል በል", call: "ደውል", got: "ገንዘቡ ደርሶኛል", notGot: "አልደረሰኝም", cancel: "ትዕዛዝ ሰርዝ",
  addProduct: "ምርት ጨምር", save: "አስቀምጥ", saved: "ተቀምጧል", price: "ዋጋ (ብር)", pname: "ስም", descr: "መግለጫ", category: "ምድብ", stockLabel: "ክምችት",
  zones: "የመላኪያ ዞኖች", accounts: "የክፍያ አካውንቶች", add: "ጨምር", del: "ሰርዝ", copy: "ሊንክ ቅዳ", copied: "ሊንክ ተቀድቷል", codTitle: "ሲደርስ በጥሬ ገንዘብ"
});
const STATUS_COLOR = { placed: "var(--clay)", shipped: "var(--saffron)", delivered: "var(--leaf)" };
const serr = e => { const k = "s_" + (e && e.message); const known = (TXT[X.lang] && TXT[X.lang][k]) || TXT.en[k]; return known ? t(k) : t("e_generic"); };
const fail2 = e => { if (e && e.status === 401) return fail(e); toast(serr(e)); };

/* ---------- state ---------- */
S.sel = { tab: "overview", ov: null, orders: null, ofilter: "todo", omore: false, opage: 0, products: null, quota: null, pq: "", pmore: false, ppage: 0,
  settings: null, edit: null, file: null, preview: null, busy: false,
  sf: { aprov: "telebirr", anum: "", aholder: "", alabel: "", zname: "", zfee: "", name: "", description: "", city: "", phone: "" } };
const T = S.sel;
const when = iso => new Date(iso).toLocaleString(X.lang === "am" ? "am-ET" : "en-GB", { dateStyle: "short", timeStyle: "short" });

/* ---------- loading ---------- */
async function loadOverview() { try { T.ov = await api("/api/seller/overview"); } catch (e) { fail2(e); } if (S.view === "seller") draw(); }
async function loadOrders(more) {
  if (!more) { T.opage = 0; T.orders = null; if (S.view === "seller") draw(); }
  try {
    const d = await api(`/api/seller/orders?filter=${T.ofilter}&page=${T.opage}`);
    T.orders = (more ? T.orders || [] : []).concat(d.orders); T.omore = d.has_more;
  } catch (e) { fail2(e); T.orders = T.orders || []; }
  if (S.view === "seller") draw();
}
async function loadProducts(more) {
  if (!more) T.ppage = 0;
  try {
    const d = await api(`/api/seller/products?page=${T.ppage}&q=${encodeURIComponent(T.pq.trim())}`);
    T.products = (more ? T.products || [] : []).concat(d.items); T.pmore = d.has_more; T.quota = d.quota;
  } catch (e) { fail2(e); T.products = T.products || []; }
  if (S.view === "seller") { if (more || !document.getElementById("splist")) draw(); else setHtml("splist", productList()); }
}
function applySettings(d) { T.settings = d; Object.assign(T.sf, { name: d.shop.name, description: d.shop.description, city: d.shop.city, phone: d.shop.phone }); }
async function loadSettings() {
  try { applySettings(await api("/api/seller/settings")); } catch (e) { fail2(e); }
  if (S.view === "seller") draw();
}
function loadTab(name) {
  T.tab = name;
  if (name === "overview") loadOverview(); else if (name === "orders") loadOrders(false); else if (name === "products") loadProducts(false); else if (!T.settings) loadSettings();   // loaded once: reloading would overwrite what the seller is typing
  draw();
}
hooks.seller = () => loadTab(T.tab);

/* ---------- views ---------- */
const spin = () => `<span class="spin" role="status"></span>`;
const subtabs = () => `<div class="cats" role="tablist" style="margin-top:12px">${[["overview", "sOverview"], ["orders", "sOrders"], ["products", "sProducts"], ["settings", "sSettings"]].map(([k, label]) =>
  `<button class="cat" role="tab" data-act="sTab" data-v="${k}" aria-pressed="${T.tab === k}">${t(label)}${k === "orders" && T.ov && T.ov.todo.new ? ` <b style="color:${T.tab === k ? "inherit" : "var(--clay)"}">${T.ov.todo.new}</b>` : ""}</button>`).join("")}</div>`;
const shopHead = () => {
  const s = (T.ov && T.ov.shop) || (T.settings && T.settings.shop) || { name: "" };
  return `<div class="band awning" ${awn((T.ov && T.ov.shop.id) || 1)}></div><h1>${esc(s.name)}</h1>`;
};

V.seller = function () {
  clearMain();
  let body = "";
  if (T.tab === "overview") body = overviewHtml();
  else if (T.tab === "orders") body = ordersHtml();
  else if (T.tab === "products") { setMain(t("addProduct"), newProduct); body = productsHtml(); }
  else body = settingsHtml();
  return `<div class="page">${shopHead()}${subtabs()}${body}</div>`;
};

function tile(label, value, attrs) {
  return `<button class="stall" ${attrs ? `data-act="sGo" ${attrs}` : ""} style="display:block;border:1px solid var(--line);border-radius:14px;padding:12px;margin:0">
    <span class="muted small" style="display:block">${label}</span><span class="price" style="font-size:22px;display:block;margin-top:2px">${value}</span></button>`;
}
function overviewHtml() {
  const d = T.ov;
  if (!d) return spin();
  const todo = d.todo, alerts = [];
  if (todo.payments) alerts.push(`<button class="btn" data-act="sGo" data-tab="orders" data-f="payment">${t("toConfirm")}: ${todo.payments}</button>`);
  if (todo.disputes) alerts.push(`<p class="muted small" style="margin:0">${t("disputes")}: ${todo.disputes}</p>`);
  const planLine = `${esc(d.plan.name)}${d.plan.until ? ", " + t("planUntil", { d: new Date(d.plan.until).toLocaleDateString(X.lang === "am" ? "am-ET" : "en-GB") }) : ""}`;
  const prods = d.plan.unlimited ? `${d.plan.products_used}, ${t("unlimited")}` : t("productsUsed", { u: d.plan.products_used, l: d.plan.products_limit });
  return `<div class="chips" style="margin-top:0">${d.shop.verified ? `<span class="chip ok">✔ ${t("verified")}</span>` : ""}
      <span class="chip">${d.shop.rating_count ? `★ ${d.shop.rating.toFixed(1)} (${d.shop.rating_count})` : t("noReviews")}</span></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0">
      ${tile(t("newOrders"), todo.new, 'data-tab="orders" data-f="todo"')}${tile(t("toConfirm"), todo.payments, 'data-tab="orders" data-f="payment"')}
      ${tile(t("toPack"), todo.pack, 'data-tab="orders" data-f="todo"')}${tile(t("onTheWay"), todo.shipped, 'data-tab="orders" data-f="shipped"')}
      ${tile(t("ordersToday"), d.stats.today)}${tile(t("sales30"), moneyText(d.stats.sales_30d))}</div>
    <div style="display:grid;gap:10px">${alerts.join("")}</div>
    <div class="sum"><div><span>${t("plan")}</span><span>${planLine}</span></div><div><span>${t("sProducts")}</span><span>${prods}</span></div><div><span>${t("delivered")}</span><span>${d.stats.delivered}</span></div></div>
    <p class="muted small">${t("planHint")}</p>
    <button class="btn alt" data-act="sCopy">${t("copy")}</button>`;
}

function ordersHtml() {
  const chips = `<div class="cats" style="margin-top:6px">${["todo", "payment", "shipped", "done", "all"].map(f => `<button class="cat" data-act="sFilter" data-f="${f}" aria-pressed="${T.ofilter === f}">${t("f_" + f)}</button>`).join("")}</div>`;
  if (!T.orders) return chips + spin();
  if (!T.orders.length) return chips + `<div class="empty"><p class="muted">${t("noOrdersS")}</p></div>`;
  return chips + T.orders.map(orderCard).join("") + (T.omore ? `<p><button class="btn alt" data-act="sOrdersMore">${t("more")}</button></p>` : "");
}
function orderCard(o) {
  const items = o.items.map(i => `<div class="small">${i.qty} × ${esc(i.title)}</div>`).join("");
  const payState = o.payment_method === "cod" ? t("payCod") : o.payment_status === "paid" ? t("payPaid") : o.payment_status === "review" ? t("payReviewS")
    : o.payment_status === "disputed" ? t("payDisputed") : o.payment_status === "expired" ? "" : t("payAwait");
  const loc = o.lat != null ? `<button class="ghost small" data-act="sMap" data-lat="${o.lat}" data-lng="${o.lng}">${t("map")}</button>` : "";
  const phone = esc(o.phone), pend = o.pending_payment;
  const pp = pend ? `<div class="sum" style="margin:10px 0 0"><div><span>${t("reference")}</span><span>${esc(pend.reference)}</span></div>
      <div><span>${esc(pend.provider)}</span><span>${moneyText(o.total)}</span></div><p class="muted small" style="margin:6px 0">${t("confirmPayHint")}</p>
      <div style="display:grid;gap:8px">${pend.has_screenshot ? `<button class="btn alt" data-act="sShot" data-id="${pend.id}">${t("showShot")}</button>` : ""}
      <button class="btn" data-act="sPay" data-id="${pend.id}" data-v="1">${t("got")}</button>
      <button class="btn danger" data-act="sPay" data-id="${pend.id}" data-v="0">${t("notGot")}</button></div></div>` : "";
  const next = o.next_status ? `<button class="btn" data-act="sNext" data-id="${o.id}" data-s="${o.next_status}">${t("do_" + o.next_status)}</button>` : "";
  const cancel = o.can_cancel ? `<button class="btn danger" data-act="sCancel" data-id="${o.id}">${t("cancel")}</button>` : "";
  return `<div class="order"><div class="band awning" ${awn(o.id)}></div><div class="in">
    <div class="top"><b>#${esc(o.code)}</b><span class="status" style="color:${STATUS_COLOR[o.status] || "inherit"}">${t("sst_" + o.status)}</span></div>
    <div class="muted small">${when(o.created_at)}</div>
    <div style="margin:8px 0">${items}</div>
    <div class="price">${money(o.total)}</div><div class="small muted">${o.delivery_zone ? esc(o.delivery_zone) + ", " : ""}${esc(o.payment_label || t("cod"))}${payState ? ", " + payState : ""}</div>
    <div style="margin-top:10px"><div>${esc(o.buyer)}</div><div class="small">${esc(o.address)}</div>
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap"><a class="ghost small" style="text-decoration:none;color:inherit" href="tel:${phone}">${t("call")} ${phone}</a>${loc}</div></div>
    ${pp}<div style="display:grid;gap:8px;margin-top:12px">${next}${cancel}</div></div></div>`;
}

function productList() {
  if (!T.products) return spin();
  if (!T.products.length) return `<div class="empty"><p class="muted">${t("noProductsS")}</p></div>`;
  return T.products.map(p => `<button class="stall" data-act="sEdit" data-id="${p.id}" style="opacity:${p.status === "hidden" ? .55 : 1}">
    <span class="tile awning" ${awn(p.shop_id)} style="width:52px;height:52px">${p.has_photo ? `<img alt="" src="/api/photo/${p.id}" style="width:100%;height:100%;object-fit:cover;border-radius:12px">` : `<b>${initial(p.title)}</b>`}</span>
    <span class="grow"><span class="name" style="font-size:15px">${esc(p.title)}</span><span class="muted small" style="display:block">${t("stockN", { n: p.stock })}${p.status === "hidden" ? ", " + t("hiddenTag") : ""}</span></span>
    <span class="price">${money(p.price)}</span></button>`).join("") + (T.pmore ? `<p><button class="btn alt" data-act="sProdMore">${t("more")}</button></p>` : "");
}
function productsHtml() {
  const q = T.quota ? `<p class="muted small" style="margin:4px 0">${T.quota.unlimited ? `${T.products ? T.products.length : 0}` : t("productsUsed", { u: T.quota.used, l: T.quota.limit })}</p>` : "";
  return `<input id="spq" class="search" type="search" placeholder="${t("search")}" value="${esc(T.pq)}" aria-label="${t("search")}">${q}<div id="splist">${productList()}</div>`;
}

function settingsHtml() {
  const d = T.settings, f = T.sf;
  if (!d) return spin();
  const prov = d.providers.find(p => p.key === f.aprov) || d.providers[0];
  return `<h2>${t("shopInfo")}</h2>
    <div class="field"><label for="sf-name">${t("sname")}</label><input id="sf-name" type="text" data-sf="name" data-scope="sf" value="${esc(f.name)}" maxlength="100"></div>
    <div class="field"><label for="sf-desc">${t("descr")}</label><textarea id="sf-desc" data-sf="description" data-scope="sf" maxlength="500">${esc(f.description)}</textarea></div>
    <div class="field"><label for="sf-city">${t("city")}</label><input id="sf-city" type="text" data-sf="city" data-scope="sf" value="${esc(f.city)}" maxlength="60"></div>
    <div class="field"><label for="sf-phone">${t("sphone")}</label><input id="sf-phone" type="tel" data-sf="phone" data-scope="sf" value="${esc(f.phone)}"></div>
    <button class="btn" data-act="sSaveInfo">${t("save")}</button>
    <h2>${t("codTitle")}</h2>
    <button class="opt" data-act="sCod" style="width:100%;background:none;text-align:left" aria-pressed="${d.shop.accept_cod}"><input type="checkbox" tabindex="-1" ${d.shop.accept_cod ? "checked" : ""} style="pointer-events:none">${d.shop.accept_cod ? t("codOn") : t("codOff")}</button>
    <h2>${t("zones")}</h2><p class="muted small">${t("zonesHint")}</p>
    ${d.zones.map(z => `<div class="line"><div class="grow">${esc(z.name)}</div><div>${z.fee ? moneyText(z.fee) : t("free")}</div><button class="ghost small" data-act="sZoneDel" data-id="${z.id}">${t("del")}</button></div>`).join("")}
    <div class="field" style="display:grid;grid-template-columns:1fr 110px;gap:8px"><input type="text" placeholder="${t("zoneName")}" aria-label="${t("zoneName")}" data-sf="zname" data-scope="sf" value="${esc(f.zname)}" maxlength="60">
      <input type="text" inputmode="decimal" placeholder="${t("zoneFee")}" aria-label="${t("zoneFee")}" data-sf="zfee" data-scope="sf" value="${esc(f.zfee)}"></div>
    <button class="btn alt" data-act="sZoneAdd">${t("add")}</button>
    <h2>${t("accounts")}</h2><p class="muted small">${t("accountsHint")}</p>
    ${d.accounts.map(a => `<div class="line"><div class="grow"><div>${esc(a.label)}</div><div class="muted small">${esc(a.number)}, ${esc(a.holder)}</div></div><button class="ghost small" data-act="sAcctDel" data-id="${a.id}">${t("del")}</button></div>`).join("")}
    <div class="field"><label for="sf-prov">${t("provider")}</label><select id="sf-prov" data-sf="aprov" data-scope="sf" data-rerender="1">${d.providers.map(p => `<option value="${p.key}" ${p.key === f.aprov ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></div>
    ${f.aprov === "other" ? `<div class="field"><input type="text" placeholder="${t("bankName")}" aria-label="${t("bankName")}" data-sf="alabel" data-scope="sf" value="${esc(f.alabel)}" maxlength="60"></div>` : ""}
    <div class="field"><input type="${prov.wallet ? "tel" : "text"}" inputmode="numeric" placeholder="${prov.wallet ? t("walletNumber") : t("number")}" aria-label="${t("number")}" data-sf="anum" data-scope="sf" value="${esc(f.anum)}"></div>
    <div class="field"><input type="text" placeholder="${t("holder")}" aria-label="${t("holder")}" data-sf="aholder" data-scope="sf" value="${esc(f.aholder)}" maxlength="100"></div>
    <button class="btn alt" data-act="sAcctAdd">${t("add")}</button>
    ${d.share_link ? `<h2>${t("shareLink")}</h2><p class="small" style="word-break:break-all">${esc(d.share_link)}</p><button class="btn alt" data-act="sCopy">${t("copy")}</button>` : ""}`;
}

/* ---------- product editor ---------- */
V.sprod = function () {
  const p = T.edit, cats = (T.settings && T.settings.categories) || [];
  setMain(T.busy ? t("uploading") : t("save"), saveProduct, T.busy);
  const hasImg = T.preview || (p.id && p.has_photo);
  const img = T.preview ? `<img alt="" src="${T.preview}">` : p.id && p.has_photo ? `<img alt="" src="/api/photo/${p.id}?v=${p.v || 0}">` : `<b style="font-size:40px;color:var(--bg)">${initial(p.title || "?")}</b>`;
  return `<div class="page"><h1>${p.id ? t("editProduct") : t("addProduct")}</h1>
    <div class="pimg ${hasImg ? "" : "awning"}" ${hasImg ? "" : awn(1)} style="max-width:220px;margin:8px 0">${img}</div>
    <input id="pfile" type="file" accept="image/jpeg,image/png" hidden><button class="ghost" data-act="sPick">${t("changePhoto")}</button>
    <div class="field"><label for="p-title">${t("pname")}</label><input id="p-title" type="text" data-sf="title" data-scope="edit" value="${esc(p.title)}" maxlength="150"></div>
    <div class="field" style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><div><label for="p-price" class="muted small">${t("price")}</label><input id="p-price" type="text" inputmode="decimal" data-sf="price" data-scope="edit" value="${esc(p.price)}"></div>
      <div><label for="p-stock" class="muted small">${t("stockLabel")}</label><input id="p-stock" type="text" inputmode="numeric" data-sf="stock" data-scope="edit" value="${esc(p.stock)}"></div></div>
    <div class="field"><label for="p-cat">${t("category")}</label><select id="p-cat" data-sf="category_id" data-scope="edit"><option value="">${t("none")}</option>${cats.map(c => `<option value="${c.id}" ${String(p.category_id) === String(c.id) ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></div>
    <div class="field"><label for="p-desc">${t("descr")}</label><textarea id="p-desc" data-sf="description" data-scope="edit" maxlength="1000">${esc(p.description)}</textarea></div>
    ${p.id ? `<button class="btn alt" data-act="sToggle">${p.status === "hidden" ? t("show") : t("hide")}</button>` : ""}</div>`;
};

/* ---------- helpers ---------- */
async function shrink(file) {
  try {
    const bmp = await createImageBitmap(file), k = Math.min(1, 1280 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas"); c.width = Math.max(1, Math.round(bmp.width * k)); c.height = Math.max(1, Math.round(bmp.height * k));
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return (await new Promise(r => c.toBlob(r, "image/jpeg", 0.85))) || file;
  } catch (e) { return file; }   // no canvas support: send the original
}
async function ensureSettings() { if (!T.settings) { try { applySettings(await api("/api/seller/settings")); } catch (e) { /* the editor still works without categories */ } } }
async function newProduct() { await ensureSettings(); T.edit = { id: null, title: "", price: "", stock: "1", category_id: "", description: "", status: "active", has_photo: false }; T.file = null; T.preview = null; go("sprod"); }
const bodyOf = p => ({ title: p.title, price: String(p.price).replace(",", "."), stock: String(p.stock).trim() === "" ? "x" : p.stock, description: p.description, category_id: p.category_id === "" ? null : Number(p.category_id) });
async function saveProduct() {
  const p = T.edit;
  if (T.busy) return;
  if (!p.title.trim()) { toast(t("s_title")); return; }
  T.busy = true; draw();
  try {
    const res = p.id ? await api(`/api/seller/products/${p.id}`, { method: "PATCH", body: bodyOf(p) }) : await api("/api/seller/products", { method: "POST", body: bodyOf(p) });
    if (T.file) {
      const form = new FormData(); form.append("file", await shrink(T.file), "product.jpg");
      await api(`/api/seller/products/${res.product.id}/photo`, { method: "POST", form });
    }
    T.busy = false; T.file = null; T.preview = null; T.products = null; T.ov = null; buzz();
    back(); toast(t("saved")); loadProducts(false);
  } catch (e) { T.busy = false; draw(); fail2(e); }
}
async function afterOrderChange() { T.ov = null; await loadOrders(false); api("/api/seller/overview").then(d => { T.ov = d; if (S.view === "seller") draw(); }).catch(() => {}); }

/* ---------- actions ---------- */
Object.assign(A, {
  sTab: el => loadTab(el.dataset.v),
  sGo: el => { if (el.dataset.f) T.ofilter = el.dataset.f; loadTab(el.dataset.tab); },
  sFilter: el => { T.ofilter = el.dataset.f; loadOrders(false); },
  sOrdersMore: () => { T.opage += 1; loadOrders(true); },
  sProdMore: () => { T.ppage += 1; loadProducts(true); },
  sNext: async el => { try { await api(`/api/seller/orders/${el.dataset.id}/status`, { method: "POST", body: { status: el.dataset.s } }); buzz(); } catch (e) { fail2(e); } await afterOrderChange(); },
  sCancel: async el => { if (!(await ask(t("cancelAsk")))) return; try { await api(`/api/seller/orders/${el.dataset.id}/status`, { method: "POST", body: { status: "cancelled" } }); } catch (e) { fail2(e); } await afterOrderChange(); },
  sPay: async el => { try { await api(`/api/seller/payments/${el.dataset.id}/decide`, { method: "POST", body: { received: el.dataset.v === "1" } }); buzz(); } catch (e) { fail2(e); } await afterOrderChange(); },
  sShot: async el => {
    try {
      const r = await fetch(`/api/seller/payments/${el.dataset.id}/screenshot`, { headers: { "X-Telegram-Init-Data": W.initData || "" } });
      if (!r.ok) throw new Error("download_failed");
      const url = URL.createObjectURL(await r.blob());
      const box = document.createElement("div");
      box.setAttribute("style", "position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:20;display:grid;place-items:center;padding:12px");
      box.innerHTML = `<img alt="" src="${url}" style="max-width:100%;max-height:100%;border-radius:8px">`;
      box.addEventListener("click", () => { box.remove(); URL.revokeObjectURL(url); });
      document.body.appendChild(box);
    } catch (e) { toast(t("e_generic")); }
  },
  sMap: el => { const u = `https://www.google.com/maps?q=${el.dataset.lat},${el.dataset.lng}`; if (W.openLink) W.openLink(u); else window.open(u, "_blank"); },
  sEdit: async el => {
    const p = T.products.find(x => x.id === Number(el.dataset.id));
    if (!p) return;
    await ensureSettings();
    T.edit = Object.assign({}, p, { price: String(p.price), stock: String(p.stock), category_id: p.category_id == null ? "" : p.category_id, v: 0 });
    T.file = null; T.preview = null; go("sprod");
  },
  sPick: () => { const f = document.getElementById("pfile"); if (f) f.click(); },
  sToggle: async () => {
    const p = T.edit, status = p.status === "hidden" ? "active" : "hidden";
    try { const r = await api(`/api/seller/products/${p.id}`, { method: "PATCH", body: { status } }); p.status = r.product.status; T.products = null; draw(); loadProducts(false); } catch (e) { fail2(e); }
  },
  sSaveInfo: async () => { const f = T.sf; try { applySettings(await api("/api/seller/shop", { method: "PATCH", body: { name: f.name, description: f.description, city: f.city, phone: f.phone } })); T.ov = null; toast(t("saved")); draw(); } catch (e) { fail2(e); } },
  sCod: async () => { try { T.settings = await api("/api/seller/shop", { method: "PATCH", body: { accept_cod: !T.settings.shop.accept_cod } }); draw(); } catch (e) { fail2(e); } },
  sZoneAdd: async () => { const f = T.sf; try { T.settings = await api("/api/seller/zones", { method: "POST", body: { name: f.zname, fee: f.zfee === "" ? 0 : f.zfee.replace(",", ".") } }); f.zname = ""; f.zfee = ""; draw(); } catch (e) { fail2(e); } },
  sZoneDel: async el => { try { T.settings = await api(`/api/seller/zones/${el.dataset.id}`, { method: "DELETE" }); draw(); } catch (e) { fail2(e); } },
  sAcctAdd: async () => { const f = T.sf; try { T.settings = await api("/api/seller/accounts", { method: "POST", body: { provider: f.aprov, number: f.anum, holder: f.aholder, label: f.alabel } }); f.anum = ""; f.aholder = ""; f.alabel = ""; draw(); } catch (e) { fail2(e); } },
  sAcctDel: async el => { try { T.settings = await api(`/api/seller/accounts/${el.dataset.id}`, { method: "DELETE" }); draw(); } catch (e) { fail2(e); } },
  sCopy: async () => {
    let link = T.settings && T.settings.share_link;
    if (!link) { try { T.settings = await api("/api/seller/settings"); link = T.settings.share_link || ""; } catch (e) { fail2(e); return; } }
    try { await navigator.clipboard.writeText(link); toast(t("copied")); } catch (e) { toast(link); }
  }
});

const root = document.getElementById("app");
function bindField(el) {
  if (!el.dataset || !el.dataset.sf) return;
  const scope = el.dataset.scope === "edit" ? T.edit : T.sf;
  if (scope) scope[el.dataset.sf] = el.value;
  if (el.dataset.rerender) draw();
}
root.addEventListener("input", e => {
  bindField(e.target);
  if (e.target.id === "spq") { T.pq = e.target.value; clearTimeout(T.pqt); T.pqt = setTimeout(() => loadProducts(false), 300); }
});
root.addEventListener("change", e => {
  bindField(e.target);
  if (e.target.id === "pfile" && e.target.files && e.target.files[0]) {
    T.file = e.target.files[0];
    try { T.preview = URL.createObjectURL(T.file); } catch (err) { T.preview = null; }
    draw();
  }
});
})();