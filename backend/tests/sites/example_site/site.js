// Demo Supply Co. - a local static test site for the example adapter. Not a real supplier.
// Cart state lives in localStorage, like a guest cart in browser storage.
const CATALOG = [
  {sku: "GLV-NM-100", title: "Nitrile Exam Gloves, Size Medium, Powder-Free", price: 849,
   desc: "Blue nitrile gloves for food handling. Size medium. Box of 100."},
  {sku: "GLV-NM-1000", title: "Nitrile Exam Gloves, Size Medium, Bulk Case", price: 6999,
   desc: "Blue nitrile gloves. Size medium. Case of 1,000 gloves in 10 boxes."},
  {sku: "GLV-LX-L-100", title: "Latex Gloves, Size Large", price: 799,
   desc: "Powdered latex gloves. Size large. Box of 100."},
  {sku: "BOX-10105-50", title: "White Bakery Box, 10 x 10 x 5 in", price: 3250,
   desc: "White paperboard bakery box with window. Bundle of 50."},
  {sku: "BOX-886-100", title: "White Bakery Box, 8 x 8 x 6 in", price: 3800,
   desc: "White paperboard bakery box. Bundle of 100."},
];
const money = (cents) => "$" + (cents / 100).toFixed(2);
const bySku = (sku) => CATALOG.find((p) => p.sku === sku);
const getCart = () => JSON.parse(localStorage.getItem("cart") || "[]");
const setCart = (c) => localStorage.setItem("cart", JSON.stringify(c));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

function header() {
  const n = getCart().reduce((a, l) => a + l.qty, 0);
  document.getElementById("hdr").innerHTML =
    '<b>Demo Supply Co.</b> <a href="index.html">Search</a> <a id="cart-link" href="cart.html">Cart (' + n + ')</a>';
}

function renderSearch() {
  const q = (new URLSearchParams(location.search).get("q") || "").toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  const hits = CATALOG.map((p, i) => ({p, i, n: words.filter((w) => p.title.toLowerCase().includes(w)).length}))
    .filter((h) => h.n > 0).sort((a, b) => b.n - a.n || a.i - b.i);
  document.getElementById("main").innerHTML =
    "<h1>Results for &quot;" + esc(q) + "&quot;</h1>" +
    (hits.length ? hits.map(({p}) =>
      '<div class="result"><a class="title" href="product.html?sku=' + p.sku + '">' + esc(p.title) + "</a>" +
      '<div class="snippet">' + money(p.price) + " - " + esc(p.desc) + "</div></div>").join("")
     : "<p>No products match.</p>");
}

function renderProduct() {
  const p = bySku(new URLSearchParams(location.search).get("sku"));
  if (!p) { document.getElementById("main").innerHTML = "<h1>Product not found</h1>"; return; }
  document.getElementById("main").innerHTML =
    '<div class="product"><h1 id="title">' + esc(p.title) + "</h1>" +
    '<div>Item # <span id="sku">' + p.sku + "</span></div>" +
    '<div id="price">' + money(p.price) + "</div>" +
    '<p id="desc">' + esc(p.desc) + "</p><p>In stock. Ships in 1 business day.</p></div>" +
    '<label for="qty">Quantity</label> <input id="qty" name="qty" type="number" min="1" value="1"> ' +
    '<button id="add">Add to cart</button> <span id="added"></span>';
  document.getElementById("add").onclick = () => {
    const qty = Math.max(1, parseInt(document.getElementById("qty").value || "1", 10));
    const cart = getCart();
    const line = cart.find((l) => l.sku === p.sku);
    if (line) line.qty += qty; else cart.push({sku: p.sku, qty});
    setCart(cart); header();
    document.getElementById("added").textContent = "Added " + qty + ".";
  };
}

function totals() {
  const sub = getCart().reduce((a, l) => a + bySku(l.sku).price * l.qty, 0);
  const ship = sub >= 7500 || sub === 0 ? 0 : 995;
  const tax = Math.round(sub * 0.06);
  return {sub, ship, tax, saved: Math.round(sub * 0.04), total: sub + ship + tax};
}

function renderCart() {
  const cart = getCart();
  const main = document.getElementById("main");
  if (!cart.length) { main.innerHTML = "<h1>Your cart</h1><p>Your cart is empty.</p>"; return; }
  const t = totals();
  main.innerHTML = '<h1>Your cart</h1><p class="banner">Free shipping on orders over $75.00</p><table id="lines">' +
    cart.map((l) => { const p = bySku(l.sku); return '<tr class="line" data-sku="' + p.sku + '">' +
      '<td class="sku">' + p.sku + "</td><td>" + esc(p.title) + '</td><td>Qty <span class="qty">' + l.qty +
      '</span></td><td class="unit">' + money(p.price) + ' each</td><td class="linetotal">' + money(p.price * l.qty) +
      '</td><td><button class="remove" data-sku="' + p.sku + '">Remove</button></td></tr>'; }).join("") +
    '</table><div id="totals">' +
    "<div>Subtotal (" + cart.length + " items) " + money(t.sub) + "</div>" +
    "<div>Estimated shipping " + money(t.ship) + "</div>" +
    "<div>Estimated tax " + money(t.tax) + "</div>" +
    "<div>You saved " + money(t.saved) + " with web pricing</div>" +
    "<div>Order total " + money(t.total) + "</div></div>" +
    '<button id="checkout">Checkout</button>';
  for (const b of document.querySelectorAll("button.remove")) {
    b.onclick = () => { setCart(getCart().filter((l) => l.sku !== b.dataset.sku)); header(); renderCart(); };
  }
  document.getElementById("checkout").onclick = () => { location.href = "checkout.html"; };
}

function renderCheckout() {
  document.getElementById("continue").onclick = (e) => {
    e.preventDefault();
    const f = document.getElementById("ship");
    if (!f.reportValidity()) return;
    sessionStorage.setItem("ship", JSON.stringify(Object.fromEntries(new FormData(f))));
    location.href = "payment.html";
  };
}

function renderPayment() {
  document.getElementById("place").onclick = (e) => {
    e.preventDefault();
    document.getElementById("msg").textContent = "This is a test site. No order was placed.";
  };
}

document.addEventListener("DOMContentLoaded", () => {
  header();
  const page = document.body.dataset.page;
  ({search: renderSearch, product: renderProduct, cart: renderCart, checkout: renderCheckout,
    payment: renderPayment}[page] || (() => {}))();
});
