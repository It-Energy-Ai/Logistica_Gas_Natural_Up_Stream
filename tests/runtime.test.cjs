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
  removeAttribute(n) { this.attributes = this.attributes.filter((x) => x.name !== n.toLowerCase()); }
  cloneNode(deep) {
    const c = new El(this.tagName);
    c.attributes = this.attributes.map((a) => ({ ...a }));
    if (deep) c.childNodes = this.childNodes.map((f) => (f.nodeType === 3 ? { ...f } : f.cloneNode(true)));
    return c;
  }
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

// --- sc-repeat: la ripetizione dentro <select> -------------------------------
// Il parser HTML del browser scarta qualunque elemento che non sia
// option/optgroup dentro una <select>: un <sc-for> non arriva mai al runtime.
// La stessa ripetizione espressa come attributo di un <option> sopravvive.

test("sc-repeat ripete l'option con lo scope dell'elemento", () => {
  const VT = caricaVT();
  const root = new El("select");
  const opt = tag("option", { "sc-repeat": "{{ voci }}", "sc-as": "v", value: "{{ v.id }}" }, testo("{{ v.label }}"));
  VT.mount(root, frammento(opt), {
    renderVals: () => ({ voci: [{ id: "a", label: "Alfa" }, { id: "b", label: "Beta" }, { id: "c", label: "Gamma" }] }),
  });
  assert.equal(root.childNodes.length, 3);
  assert.deepEqual(root.childNodes.map((o) => o.textContent), ["Alfa", "Beta", "Gamma"]);
  assert.deepEqual(root.childNodes.map((o) => o.value), ["a", "b", "c"]);
});

test("sc-repeat su lista vuota o assente non produce nulla", () => {
  const VT = caricaVT();
  for (const voci of [[], null, undefined]) {
    const root = new El("select");
    const opt = tag("option", { "sc-repeat": "{{ voci }}", "sc-as": "v" }, testo("{{ v.label }}"));
    VT.mount(root, frammento(opt), { renderVals: () => ({ voci }) });
    assert.equal(root.childNodes.length, 0, `lista ${JSON.stringify(voci)}`);
  }
});

test("sc-repeat non lascia attributi sc-* nel DOM generato", () => {
  const VT = caricaVT();
  const root = new El("select");
  const opt = tag("option", { "sc-repeat": "{{ voci }}", "sc-as": "v", value: "{{ v.id }}" }, testo("x"));
  VT.mount(root, frammento(opt), { renderVals: () => ({ voci: [{ id: "1" }] }) });
  const nomi = root.childNodes[0].attributes.map((a) => a.name);
  assert.ok(!nomi.some((n) => n.startsWith("sc-")), `attributi residui: ${nomi.join(",")}`);
  assert.ok(!nomi.some((n) => n.startsWith("hint-")));
});

test("sc-repeat funziona dentro un sc-for esterno", () => {
  const VT = caricaVT();
  const root = new El("div");
  const opt = tag("option", { "sc-repeat": "{{ g.voci }}", "sc-as": "v" }, testo("{{ g.nome }}:{{ v }} "));
  const forEl = tag("sc-for", { list: "{{ gruppi }}", as: "g" }, opt);
  VT.mount(root, frammento(forEl), {
    renderVals: () => ({ gruppi: [{ nome: "A", voci: ["1", "2"] }, { nome: "B", voci: ["3"] }] }),
  });
  assert.equal(root.textContent, "A:1 A:2 B:3 ");
});

test("il template ripetuto non viene consumato dal primo giro", () => {
  // Se il nodo modello venisse mutato invece che clonato, il secondo render
  // produrrebbe un risultato diverso dal primo.
  const VT = caricaVT();
  const opt = tag("option", { "sc-repeat": "{{ voci }}", "sc-as": "v" }, testo("{{ v }}"));
  const tpl = frammento(opt);
  const vals = { renderVals: () => ({ voci: ["x", "y"] }) };
  const primo = new El("select");
  VT.mount(primo, tpl, vals);
  const secondo = new El("select");
  VT.mount(secondo, tpl, vals);
  assert.equal(primo.textContent, secondo.textContent);
  assert.equal(secondo.textContent, "xy");
});
