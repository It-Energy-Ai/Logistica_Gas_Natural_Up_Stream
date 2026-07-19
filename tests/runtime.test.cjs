// Test di runtime.js con uno shim DOM minimale (node:test, nessuna dipendenza).
// Copre i rami che il resto della suite non tocca: interpolazione, sc-if,
// sc-for, binding value, e l'accessibilità da tastiera sui cliccabili.
const { test } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const fs = require("node:fs");
const vm = require("node:vm");

// --- Shim DOM essenziale -----------------------------------------------------
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = [];
    this.childNodes = [];
    this.listeners = {};
    this.nodeType = 1;
    this.value = undefined;
  }
  setAttribute(n, v) {
    n = n.toLowerCase(); // il parser HTML del browser abbassa i nomi degli attributi
    const a = this.attributes.find((x) => x.name === n);
    if (a) a.value = v;
    else this.attributes.push({ name: n, value: String(v) });
  }
  getAttribute(n) { const a = this.attributes.find((x) => x.name === n.toLowerCase()); return a ? a.value : null; }
  hasAttribute(n) { return this.attributes.some((x) => x.name === n.toLowerCase()); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  appendChild(c) {
    if (c && c.tagName === "#FRAGMENT") this.childNodes.push(...c.childNodes); // il fragment sposta i suoi figli
    else this.childNodes.push(c);
    return c;
  }
  replaceChildren(...n) {
    this.childNodes = [];
    for (const c of n) this.appendChild(c);
  }
  get textContent() {
    return this.childNodes.map((c) => (c.nodeType === 3 ? c.nodeValue : c.textContent)).join("");
  }
  querySelectorAll(pred) {
    const out = [];
    const walk = (n) => { for (const c of n.childNodes || []) { if (c.nodeType === 1) { if (pred(c)) out.push(c); walk(c); } } };
    walk(this);
    return out;
  }
}
const doc = {
  createElement: (t) => new El(t),
  createTextNode: (t) => ({ nodeType: 3, nodeValue: String(t) }),
  createDocumentFragment: () => new El("#fragment"),
};
const Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };

// Carica runtime.js nel sandbox con lo shim.
function caricaVT() {
  const src = fs.readFileSync(path.join(__dirname, "..", "app", "static", "runtime.js"), "utf8");
  const sandbox = { document: doc, Node, window: {} };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return sandbox.window.VT;
}

// Costruisce un "template" dallo shim a partire da nodi già pronti.
function frammento(...nodi) { const f = new El("#tpl"); f.childNodes = nodi; return { content: f }; }
function tag(name, attrs = {}, ...figli) {
  const el = new El(name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.childNodes = figli;
  return el;
}
function testo(t) { return { nodeType: 3, nodeValue: t }; }

test("interpola le mustache nel testo e negli attributi", () => {
  const VT = caricaVT();
  const root = new El("div");
  const tpl = frammento(tag("span", { title: "ciao {{ nome }}" }, testo("valore: {{ nome }}")));
  VT.mount(root, tpl, { renderVals: () => ({ nome: "Anna" }) });
  const span = root.childNodes[0];
  assert.equal(span.getAttribute("title"), "ciao Anna");
  assert.equal(span.textContent, "valore: Anna");
});

test("sc-if include o esclude in base al valore", () => {
  const VT = caricaVT();
  const mk = (visibile) => {
    const root = new El("div");
    const sc = tag("sc-if", { value: "{{ mostra }}" }, tag("p", {}, testo("dentro")));
    VT.mount(root, frammento(sc), { renderVals: () => ({ mostra: visibile }) });
    return root.textContent;
  };
  assert.equal(mk(true), "dentro");
  assert.equal(mk(false), "");
});

test("sc-for ripete i figli con lo scope dell'elemento", () => {
  const VT = caricaVT();
  const root = new El("div");
  const forEl = tag("sc-for", { list: "{{ righe }}", as: "r" }, tag("i", {}, testo("{{ r.n }}-")));
  VT.mount(root, frammento(forEl), { renderVals: () => ({ righe: [{ n: 1 }, { n: 2 }, { n: 3 }] }) });
  assert.equal(root.textContent, "1-2-3-");
});

test("value su input viene applicato dopo i figli", () => {
  const VT = caricaVT();
  const root = new El("div");
  VT.mount(root, frammento(tag("input", { value: "{{ v }}" })), { renderVals: () => ({ v: "PSV" }) });
  assert.equal(root.childNodes[0].value, "PSV");
});

test("un cliccabile non-button diventa accessibile da tastiera", () => {
  const VT = caricaVT();
  const root = new El("div");
  let attivato = 0;
  const handler = () => { attivato++; };
  VT.mount(root, frammento(tag("div", { onClick: "{{ vai }}" }, testo("card"))), { renderVals: () => ({ vai: handler }) });
  const card = root.childNodes[0];
  assert.equal(card.getAttribute("tabindex"), "0");
  assert.equal(card.getAttribute("role"), "button");
  // Invio e Spazio attivano l'handler; un altro tasto no
  card.listeners.keydown[0]({ key: "Enter", preventDefault() {} });
  card.listeners.keydown[0]({ key: " ", preventDefault() {} });
  card.listeners.keydown[0]({ key: "a", preventDefault() {} });
  assert.equal(attivato, 2);
  // un <button> non riceve né tabindex forzato né role
  const root2 = new El("div");
  VT.mount(root2, frammento(tag("button", { onClick: "{{ vai }}" }, testo("ok"))), { renderVals: () => ({ vai: handler }) });
  assert.equal(root2.childNodes[0].hasAttribute("role"), false);
});
