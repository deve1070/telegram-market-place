// Browser-level test of the Mini App: the REAL page + JS (in jsdom) against the REAL server and database.
// Usage: BOT_TOKEN=... BASE=http://127.0.0.1:8099 node e2e.mjs
import crypto from "node:crypto";
import fs from "node:fs";
import { JSDOM, ResourceLoader, VirtualConsole } from "jsdom";

const BASE = process.env.BASE || "http://127.0.0.1:8099";
const TOKEN = process.env.BOT_TOKEN;
const HTML = fs.readFileSync(new URL("../../app/webapp/static/index.html", import.meta.url), "utf8");

function initData(userId, { token = TOKEN, first = "Hanna", lang = "en" } = {}) {
  const f = { auth_date: String(Math.floor(Date.now() / 1000)), query_id: "AAH", user: JSON.stringify({ id: userId, first_name: first, language_code: lang }) };
  const check = Object.keys(f).sort().map(k => `${k}=${f[k]}`).join("\n");
  const secret = crypto.createHmac("sha256", "WebAppData").update(token).digest();
  f.hash = crypto.createHmac("sha256", secret).update(check).digest("hex");
  return new URLSearchParams(f).toString();
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(name, cond, extra = "") { console.log(`${cond ? "ok  " : "FAIL"} ${name}${!cond && extra ? "  -> " + extra : ""}`); if (!cond) failures++; }

class SameOriginLoader extends ResourceLoader {
  fetch(url, options) { return url.startsWith(BASE) ? super.fetch(url, options) : null; }
}

async function openApp(init, search = "", opts = {}) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errors.push(String(e.message || e)));
  vc.on("error", e => errors.push(String(e)));
  const tgState = { main: { text: "", visible: false, enabled: true, handler: null }, back: { visible: false, handler: null }, closed: false, confirms: [] };
  const dom = new JSDOM(HTML, {
    url: `${BASE}/app${search}${opts.hash || ""}`, runScripts: "dangerously", resources: new SameOriginLoader(), pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.scrollTo = () => {};
      w.fetch = (u, o) => (opts.fetchOverride ? opts.fetchOverride(u, o) : fetch(new URL(u, BASE), o));
      const m = tgState.main;
      if (!opts.noSdk) w.Telegram = { WebApp: {
        initData: init, initDataUnsafe: {}, ready() {}, expand() {}, close() { tgState.closed = true; },
        showConfirm(msg, cb) { tgState.confirms.push(msg); cb(true); },
        MainButton: { show() { m.visible = true; }, hide() { m.visible = false; }, setText(t) { m.text = t; }, enable() { m.enabled = true; }, disable() { m.enabled = false; },
          onClick(fn) { m.handler = fn; }, offClick(fn) { if (m.handler === fn) m.handler = null; }, showProgress() {}, hideProgress() {} },
        BackButton: { show() { tgState.back.visible = true; }, hide() { tgState.back.visible = false; }, onClick(fn) { tgState.back.handler = fn; }, offClick() {} },
        HapticFeedback: { impactOccurred() {}, notificationOccurred() {} } } };
    },
  });
  const doc = dom.window.document;
  const $ = s => doc.querySelector(s), $$ = s => [...doc.querySelectorAll(s)];
  const waitFor = async (fn, what, ms = 6000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch (e) { /* retry */ } await sleep(40); } throw new Error("timeout waiting for " + what); };
  const click = el => el.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  const type = (el, value, ev = "input") => { el.value = value; el.dispatchEvent(new dom.window.Event(ev, { bubbles: true })); };
  const tapMain = async () => { const h = tgState.main.handler; if (!h) throw new Error("no MainButton handler"); await h(); };
  const text = () => { const c = doc.body.cloneNode(true); c.querySelectorAll("script,style").forEach(n => n.remove()); return c.textContent.replace(/\s+/g, " "); };
  return { dom, doc, $, $$, waitFor, click, type, tapMain, text, tg: tgState, errors };
}

const BUYER = 3001;
const api = async (path, uid = BUYER, opts = {}) => {
  const r = await fetch(BASE + path, { ...opts, headers: { "X-Telegram-Init-Data": initData(uid), "Content-Type": "application/json" } });
  return { status: r.status, body: await r.json().catch(() => null) };
};

try {
  // ---------------------------------------------------------------- 1. not signed in
  {
    const a = await openApp("");
    await a.waitFor(() => /Telegram bot/.test(a.text()), "sign-in message");
    check("opened outside Telegram: asks to open from the bot", true);
    const b = await openApp(initData(BUYER, { token: "9:wrong-token" }));
    await b.waitFor(() => /Telegram bot/.test(b.text()), "401 message");
    check("forged login is rejected by the server and the page says so", true);
  }

  // ---------------------------------------------------------------- 2. shopping journey
  const app = await openApp(initData(BUYER), "");
  const { $, $$, waitFor, click, type, tapMain, text, tg, doc } = app;
  await waitFor(() => $$(".stall").length === 2, "two shops");
  check("shop list shows verified shop first", /Verified/.test($$(".stall")[0].textContent) && /Quiet Corner/.test($$(".stall")[1].textContent));
  check("shop names are escaped (no injected element, no script ran)", !$("img[src=x]") && !app.dom.window.__xss && /<img src=x/.test($$(".stall")[0].textContent));
  check("each shop has a woven awning tile", $$(".stall .tile.awning").length === 2);

  type($("#shopq"), "quiet");
  check("shop search filters locally", $$(".stall").length === 1);
  type($("#shopq"), "");

  click($$(".stall")[0]);
  await waitFor(() => $$(".pcard").length === 3, "three products");
  check("shop page: products + categories + back button", $$(".cat").length === 3 && tg.back.visible);
  check("product without photo gets an awning placeholder", $$(".pcard .pimg.awning").length >= 2);
  check("products with a photo render an <img> pointing at the photo endpoint", /^\/api\/photo\/\d+$/.test($(".pcard img").getAttribute("src")));
  $(".pcard img").dispatchEvent(new app.dom.window.Event("error"));   // jsdom doesn't load images, so simulate the failure
  check("a photo that fails to load falls back to the awning placeholder", $$(".pcard img").length === 0 && $$(".pcard .pimg.awning").length === 3);
  check("sold-out product is labelled", /Sold out/.test($$(".pcard")[0].textContent));

  type($("#q"), "shoe");
  await waitFor(() => $$(".pcard").length === 1, "search result");
  check("product search hits the server", /Leather shoes/.test($(".pcard").textContent));
  type($("#q"), "");
  await waitFor(() => $$(".pcard").length === 3, "all products again");
  click($$(".cat")[2]);                                   // Electronics
  await waitFor(() => $$(".pcard").length === 2, "category filter");
  check("category filter works", $$(".cat")[2].getAttribute("aria-pressed") === "true");
  click($$(".cat")[0]);
  await waitFor(() => $$(".pcard").length === 3, "all");

  click($$(".pcard").find(p => /Leather shoes/.test(p.textContent)));
  await waitFor(() => /Add to cart/.test(tg.main.text), "add to cart button");
  check("product page: native main button shows price", /1,500 ETB/.test(tg.main.text) && tg.main.enabled);
  click($$("[data-act=pqty]")[1]);
  await waitFor(() => /3,000 ETB/.test(tg.main.text), "qty 2 price");
  check("quantity stepper updates the total", true);
  await tapMain();
  await waitFor(() => $(".toast") && !/Add to cart/.test(tg.main.text) || $$(".pcard").length === 3, "back on shop page");
  check("added to cart: cart tab shows badge 2", $(".tabs .dot") && $(".tabs .dot").textContent === "2");
  check("cart persisted to localStorage", JSON.parse(app.dom.window.localStorage.getItem("cart.v1")).items[0].qty === 2);

  // sold-out product can't be added
  click($$(".pcard").find(p => /Sold out lamp/.test(p.textContent)));
  await waitFor(() => tg.main.text === "Sold out", "sold out main button");
  check("sold-out product: main button disabled", tg.main.enabled === false);
  tg.back.handler();

  click($$(".tabs button")[1]);
  await waitFor(() => /Checkout/.test(tg.main.text), "cart view");
  check("cart view lists the item with total", /Leather shoes/.test(text()) && /3,000 ETB/.test(tg.main.text));
  click($("[data-act=cq][data-d='-1']"));
  await waitFor(() => /1,500 ETB/.test(tg.main.text), "qty 1");
  click($("[data-act=cq][data-d='1']"));
  await waitFor(() => /3,000 ETB/.test(tg.main.text), "qty 2 again");

  await tapMain();                                       // -> checkout
  await waitFor(() => $("#f-phone"), "checkout form");
  check("checkout: zone select, COD + Telebirr options", $$("#f-zone option").length === 3 && $$(".opt").length === 2 && $("input[value=cod]").checked);
  check("online payment label hides the account number", !/251911223344/.test(text()));

  await tapMain();                                       // empty form -> validation, nothing sent
  check("validation: bad phone explained", /valid Ethiopian phone/.test($("#err").textContent));
  type($("#f-phone"), "0922334455");
  await tapMain();
  check("validation: missing address explained", /address or a landmark/.test($("#err").textContent));
  type($("#f-address"), "Bole, near Edna Mall");
  await tapMain();
  check("validation: missing zone explained", /delivery area/.test($("#err").textContent));
  check("no order was created by invalid attempts", (await api("/api/orders")).body.orders.length === 0);

  type($("#f-zone"), "1", "change");
  await waitFor(() => /3,050 ETB/.test(tg.main.text), "total with delivery");
  check("choosing a zone adds the fee to the total", /Delivery/.test($("#sum").textContent) && /50 ETB/.test($("#sum").textContent));
  await tapMain();
  await waitFor(() => /Order placed/.test(text()), "order placed screen");
  check("order placed (cash on delivery)", /We will message you in the chat/.test(text()) && /#M-\d+/.test(text()));
  const orders = (await api("/api/orders")).body.orders;
  check("server stored the order with delivery fee + server-side total", orders.length === 1 && orders[0].total === 3050 && orders[0].delivery_zone === "Bole" && orders[0].status === "placed");
  check("cart was emptied", JSON.parse(app.dom.window.localStorage.getItem("cart.v1")).items.length === 0);

  // ---------------------------------------------------------------- 3. online payment hand-off
  await tapMain();                                       // Done -> orders tab
  await waitFor(() => $(".order"), "orders list");
  check("orders tab shows the order with status", /Placed/.test($(".order").textContent) && /Leather shoes/.test($(".order").textContent));
  click($$(".tabs button")[0]);
  await waitFor(() => $$(".stall").length, "shops");
  click($$(".stall")[0]); await waitFor(() => $$(".pcard").length === 3, "products");
  click($$(".pcard").find(p => /Old radio/.test(p.textContent))); await waitFor(() => /Add to cart/.test(tg.main.text), "radio");
  await tapMain();
  await waitFor(() => $(".tabs .dot"), "cart badge");
  click($$(".tabs button")[1]); await waitFor(() => /Checkout/.test(tg.main.text), "cart");
  await tapMain(); await waitFor(() => $("#f-phone"), "checkout 2");
  check("phone is remembered from the previous order", $("#f-phone").value === "0922334455");
  type($("#f-address"), "Piassa"); type($("#f-zone"), "2", "change");
  click($("input[name=pay]:not([value=cod])")); $("input[name=pay]:not([value=cod])").checked = true;
  $("input[name=pay]:not([value=cod])").dispatchEvent(new app.dom.window.Event("change", { bubbles: true }));
  await tapMain();
  await waitFor(() => /Order placed/.test(text()), "order 2 placed");
  check("online payment: tells the buyer to continue in the chat", /send the transaction reference in the chat/.test(text()) && /Open chat/.test(tg.main.text));
  const orders2 = (await api("/api/orders")).body.orders;
  check("online order waits for payment on the server", orders2[0].status === "awaiting_payment" && orders2[0].awaiting_payment === true);
  await tapMain();
  check("'Open chat' closes the Mini App", tg.closed === true);

  // ---------------------------------------------------------------- 4. orders: cancel, language
  tg.closed = false;
  const b2 = await openApp(initData(BUYER));
  await b2.waitFor(() => b2.$$(".stall").length, "shops");
  b2.click(b2.$$(".tabs button")[2]);
  await b2.waitFor(() => b2.$$(".order").length === 2, "two orders");
  check("unpaid order offers payment proof + cancel", /Send payment proof/.test(b2.$$(".order")[0].textContent) && /Cancel order/.test(b2.$$(".order")[0].textContent));
  b2.click(b2.$("[data-act=cancel]"));
  await b2.waitFor(() => /Cancelled/.test(b2.$$(".order")[0].textContent), "cancelled");
  check("cancel works from the app", true);

  b2.click(b2.$$(".tabs button")[0]);
  await b2.waitFor(() => b2.$("[data-act=lang]"), "language button");
  b2.click(b2.$("[data-act=lang]"));
  await b2.waitFor(() => /ሱቆች/.test(b2.text()), "amharic");
  check("Amharic interface", /ሱቆች/.test(b2.$("h1").textContent));
  await b2.waitFor(async () => false, "tick", 1).catch(() => {});
  let saved = ""; for (let i = 0; i < 50 && saved !== "am"; i++) { saved = (await api("/api/me")).body.language; if (saved !== "am") await sleep(60); }
  check("language preference saved on the server", saved === "am", saved);

  // ---------------------------------------------------------------- 5. deep link
  await api("/api/lang", BUYER, { method: "POST", body: JSON.stringify({ language: "en" }) });
  const dl = await openApp(initData(BUYER), "?shop=2");
  await dl.waitFor(() => /Quiet Corner/.test(dl.text()) && dl.$$(".pcard").length === 1, "deep link shop");
  check("deep link /app?shop=<id> opens that shop", true);


  // ---------------------------------------------------------------- 6. seller dashboard
  const SELLER = 2001;
  const sa = await openApp(initData(SELLER, { first: "Abel" }));
  await sa.waitFor(() => sa.$$(".tabs button").length === 4, "four tabs for a seller");
  check("seller gets a 4th tab 'My shop'; buyer tabs remain", /My shop/.test(sa.$$(".tabs button")[3].textContent) && /Shops/.test(sa.$$(".tabs button")[0].textContent));
  sa.click(sa.$$(".tabs button")[3]);
  await sa.waitFor(() => /New orders/.test(sa.text()) && sa.$$(".price").length >= 5, "overview");
  check("overview: shop name escaped, verified chip, plan shown", !sa.$("img[src=x]") && /Verified/.test(sa.text()) && /Free/.test(sa.text()) && /of 10 products/.test(sa.text()));
  const tileValue = label => [...sa.$$(".stall")].find(b => b.textContent.includes(label)).querySelector(".price").textContent.trim();
  check("overview: 1 new order waiting", tileValue("New orders") === "1");

  sa.click([...sa.$$(".stall")].find(b => b.textContent.includes("New orders")));
  await sa.waitFor(() => sa.$(".order"), "orders tab");
  const card = () => sa.$(".order");
  check("order card shows buyer, call link, address, total", /Hanna/.test(card().textContent) && sa.$("a[href='tel:+251922334455']") && /Bole, near Edna Mall/.test(card().textContent) && /3,050/.test(card().textContent));
  check("order card offers the next step and cancel", /Accept order/.test(card().textContent) && /Cancel order/.test(card().textContent));
  for (const [button, status] of [["Accept order", "Accepted"], ["Mark as packed", "Packed"], ["Mark as shipped", "On the way"]]) {
    sa.click([...sa.$$(".order .btn")].find(b => b.textContent.trim() === button));
    await sa.waitFor(() => (status === "On the way" ? !sa.$(".order") : sa.$(".order .status") && sa.$(".order .status").textContent === status), "status " + status);
  }
  check("shipped order leaves the 'To do' list", /No orders here/.test(sa.text()));
  sa.click([...sa.$$("[data-act=sFilter]")].find(b => b.textContent === "On the way"));
  await sa.waitFor(() => sa.$(".order"), "shipped filter");
  check("…and shows under 'On the way' with no further action", /On the way/.test(sa.$(".order .status").textContent) && !/Mark as/.test(sa.text()) && !/Cancel order/.test(sa.text()),
    JSON.stringify({ status: sa.$(".order .status").textContent, markAs: /Mark as/.test(sa.text()), cancel: /Cancel order/.test(sa.text()) }));
  check("the buyer can now confirm receipt (server state)", (await api("/api/orders")).body.orders.find(o => o.code === sa.$(".order b").textContent.slice(1)).can_receive === true);

  // products: list, add (with validation), edit, hide
  sa.click([...sa.$$("[data-act=sTab]")].find(b => /Products/.test(b.textContent)));
  await sa.waitFor(() => sa.$$("[data-act=sEdit]").length === 3, "three products");
  check("products tab lists all products with stock", /Leather shoes/.test(sa.text()) && /3 in stock/.test(sa.text()) && /Add product/.test(sa.tg.main.text),
    JSON.stringify({ main: sa.tg.main.text, text: sa.text().slice(0, 400) }));
  await sa.tapMain();
  await sa.waitFor(() => sa.$("#p-title"), "editor");
  check("editor: back button shown, categories offered", sa.tg.back.visible && sa.$$("#p-cat option").length > 3);
  await sa.tapMain();                                                             // empty name
  check("validation: empty name explained, nothing created", /Enter a product name/.test(sa.$(".toast").textContent) && (await api("/api/shops/1/products")).body.items.length === 3);
  sa.type(sa.$("#p-title"), "E2E Lamp"); sa.type(sa.$("#p-price"), "abc"); sa.type(sa.$("#p-stock"), "3");
  await sa.tapMain();
  await sa.waitFor(() => [...sa.$$(".toast")].some(t => /price above 0/.test(t.textContent)), "price error");
  check("validation: bad price explained by the server", true);
  sa.type(sa.$("#p-price"), "75,5"); sa.type(sa.$("#p-cat"), "2", "change"); sa.type(sa.$("#p-desc"), "Brass <b>lamp</b>");
  await sa.tapMain();
  await sa.waitFor(() => sa.$$("[data-act=sEdit]").length === 4, "four products");
  check("product created (comma decimal accepted)", /E2E Lamp/.test(sa.text()) && (await api("/api/shops/1/products")).body.items.some(p => p.title === "E2E Lamp" && p.price === 75.5 && p.stock === 3));
  sa.click([...sa.$$("[data-act=sEdit]")].find(b => /E2E Lamp/.test(b.textContent)));
  await sa.waitFor(() => sa.$("#p-title") && sa.$("#p-title").value === "E2E Lamp", "edit form");
  check("edit form is prefilled; description kept verbatim (not interpreted as HTML)", sa.$("#p-price").value === "75.5" && sa.$("#p-desc").value === "Brass <b>lamp</b>");
  sa.type(sa.$("#p-stock"), "9");
  await sa.tapMain();
  await sa.waitFor(() => /9 in stock/.test(sa.text()), "stock updated");
  check("stock edit saved", true);
  sa.click([...sa.$$("[data-act=sEdit]")].find(b => /E2E Lamp/.test(b.textContent)));
  await sa.waitFor(() => sa.$("[data-act=sToggle]"), "hide button");
  sa.click(sa.$("[data-act=sToggle]"));
  await sa.waitFor(() => /Show to buyers/.test(sa.text()), "hidden");
  check("hide from buyers: gone from the shop API, still visible to the seller", !(await api("/api/shops/1/products")).body.items.some(p => p.title === "E2E Lamp") && (await api("/api/seller/products", SELLER)).body.items.some(p => p.title === "E2E Lamp" && p.status === "hidden"));
  sa.tg.back.handler();
  await sa.waitFor(() => /Hidden/.test(sa.text()), "hidden tag in list");
  check("hidden product is marked in the list", true);

  // settings
  sa.click([...sa.$$("[data-act=sTab]")].find(b => /Settings/.test(b.textContent)));
  await sa.waitFor(() => sa.$("#sf-city"), "settings");
  check("settings: shop details, zones, accounts listed", sa.$("#sf-city").value === "Addis Ababa" && /Bole/.test(sa.text()) && /Telebirr/.test(sa.text()),
    JSON.stringify({ city: sa.$("#sf-city").value, text: sa.text().slice(0, 300) }));
  sa.type(sa.$("#sf-city"), "Bahir Dar"); sa.click(sa.$("[data-act=sSaveInfo]"));
  let city = ""; for (let i = 0; i < 60 && city !== "Bahir Dar"; i++) { city = (await api("/api/seller/settings", SELLER)).body.shop.city; if (city !== "Bahir Dar") await sleep(50); }
  check("shop details saved", city === "Bahir Dar", city);
  sa.click(sa.$("[data-act=sCod]"));
  await sa.waitFor(() => /Off: buyers must pay first/.test(sa.text()), "cod off");
  check("cash on delivery switched off (a payment account exists)", (await api("/api/shops/1")).body.methods.cod === false);
  sa.click(sa.$("[data-act=sCod]"));
  await sa.waitFor(() => /On: buyers can pay/.test(sa.text()), "cod on");
  sa.type(sa.$("[data-sf=zname]"), "Piassa"); sa.type(sa.$("[data-sf=zfee]"), "70"); sa.click(sa.$("[data-act=sZoneAdd]"));
  await sa.waitFor(() => /Piassa/.test(sa.text()), "zone added");
  check("delivery zone added and visible to buyers", (await api("/api/shops/1")).body.zones.some(z => z.name === "Piassa" && z.fee === 70));
  sa.click([...sa.$$("[data-act=sZoneDel]")].find(b => /Piassa/.test(b.closest(".line").textContent)));
  await sa.waitFor(() => !/Piassa/.test(sa.text()), "zone removed");
  sa.type(sa.$("#sf-prov"), "mpesa", "change");
  await sa.waitFor(() => sa.$("[data-sf=anum]").placeholder === "Wallet phone number", "provider changes the form");
  sa.type(sa.$("[data-sf=anum]"), "0711223344"); sa.type(sa.$("[data-sf=aholder]"), "Abel Sam"); sa.click(sa.$("[data-act=sAcctAdd]"));
  await sa.waitFor(() => /251711223344/.test(sa.text()), "account added");
  check("payment account added (phone normalised)", true);
  sa.click([...sa.$$("[data-act=sAcctDel]")].find(b => /251711223344/.test(b.closest(".line").textContent)));
  await sa.waitFor(() => !/251711223344/.test(sa.text()), "account removed");
  check("payment account removed", true);

  // a plain buyer never gets the dashboard, even by URL
  const nb = await openApp(initData(BUYER), "?view=seller");
  await nb.waitFor(() => nb.$$(".stall").length, "buyer shops");
  check("buyer: only 3 tabs, ?view=seller does nothing, no seller script state", nb.$$(".tabs button").length === 3 && !nb.dom.window.__app.V.seller && /Shops/.test(nb.$("h1").textContent));
  check("buyer cannot call the seller API (403)", (await api("/api/seller/overview", BUYER)).status === 403);

  // ---------------------------------------------------------------- 7. when Telegram's own script cannot be loaded
  const noSdkApp = await (async () => {
    const theme = JSON.stringify({ bg_color: "#112233", text_color: "#eeeeee" });
    const hash = "#tgWebAppData=" + encodeURIComponent(initData(3003, { first: "Mulu" })) + "&tgWebAppThemeParams=" + encodeURIComponent(theme);
    const n = await openApp("", "?shop=2", { noSdk: true, hash });
    await n.waitFor(() => /Coffee beans/.test(n.text()), "deep-linked shop without the SDK", 12000);
    check("without Telegram's script the app still starts (login read from the address) and opens the deep-linked shop", true);
    check("…and applies Telegram's theme colours", n.doc.documentElement.style.getPropertyValue("--tg-theme-bg-color") === "#112233");
    const pmain = () => n.$(".pmain"), pback = () => n.$(".pback");
    check("…an on-page back button stands in for the native one", pback() && !pback().hidden);
    n.click(n.$(".pcard"));
    await n.waitFor(() => pmain() && !pmain().hidden && /Add to cart/.test(pmain().textContent), "stand-in main button");
    check("…and an on-page 'Add to cart' button replaces the native main button", true);
    n.click(pmain());
    await n.waitFor(() => n.$(".tabs .dot"), "cart badge");
    n.click(n.$$(".tabs button")[1]);
    await n.waitFor(() => pmain() && /Checkout/.test(pmain().textContent), "checkout button");
    n.click(pmain());
    await n.waitFor(() => n.$("#f-phone"), "checkout form");
    n.type(n.$("#f-phone"), "0933445566"); n.type(n.$("#f-address"), "Hawassa lakeside");
    n.click(pmain());
    await n.waitFor(() => /Order placed/.test(n.text()), "order placed without the SDK", 8000);
    check("…a complete order can be placed without Telegram's script", (await api("/api/orders", 3003)).body.orders.length === 1);
    return n;
  })();

  // ---------------------------------------------------------------- 8. a failed start is visible, never a blank page
  const reply = (status, body, type) => (u, o) => (String(u).includes("/api/me") ? Promise.resolve(new Response(body, { status, headers: { "content-type": type } })) : fetch(new URL(u, BASE), o));
  const warn = await openApp(initData(BUYER), "", { fetchOverride: reply(200, "<html>You are about to visit a tunnel</html>", "text/html") });
  await warn.waitFor(() => /unexpected answer/.test(warn.text()), "message for a non-JSON answer");
  check("a non-JSON answer (a tunnel's warning page) shows a clear message and a retry button, not a blank page", /Try again/.test(warn.text()) && warn.dom.window.__rendered === true);
  const down = await openApp(initData(BUYER), "", { fetchOverride: reply(502, "Bad Gateway", "text/plain") });
  await down.waitFor(() => /not reachable/.test(down.text()), "message for an unreachable server");
  check("an unreachable server shows 'not reachable' and a retry button", /Try again/.test(down.text()));
  const gone = await openApp("");
  await gone.waitFor(() => /Telegram bot/.test(gone.text()), "no login data");
  check("opened outside Telegram: explains how to open it (not blank)", gone.dom.window.__rendered === true);

  const errs = [app, b2, dl, sa, nb, noSdkApp, warn, down, gone].flatMap(x => x.errors).filter(e => !/Not implemented|Could not load/.test(e));
  check("no JavaScript errors in any session", errs.length === 0, errs.join(" | "));
} catch (e) {
  failures++;
  console.log("FAIL  " + e.message);
}
console.log(failures ? `\n${failures} FAILED` : "\nALL PASSED");
process.exit(failures ? 1 : 0);
