/* Runtime del template di design: interpreta sc-if / sc-for / {{ var }}
 * e collega gli handler (onClick, onChange, ...). Nessuna dipendenza.
 *
 * Contratto con logic.js:
 *   VT.mount(rootEl, templateEl, app)  — app.renderVals() produce i valori,
 *   app.setState(...) chiama VT.render() di nuovo (salvo aggiornamenti silenziosi).
 */
(function () {
  "use strict";

  const MUSTACHE = /\{\{\s*([\w.]+)\s*\}\}/g;

  function lookup(scopes, path) {
    const [head, ...rest] = path.split(".");
    for (let i = scopes.length - 1; i >= 0; i--) {
      if (scopes[i] != null && head in scopes[i]) {
        let v = scopes[i][head];
        for (const k of rest) {
          if (v == null) return undefined;
          v = v[k];
        }
        return v;
      }
    }
    return undefined;
  }

  function interpolate(text, scopes) {
    return text.replace(MUSTACHE, (_, path) => {
      const v = lookup(scopes, path);
      return v == null ? "" : String(v);
    });
  }

  // L'attributo è un'unica mustache? Allora il valore resta non-stringa (fn, bool).
  function soleExpr(value) {
    const m = value.match(/^\{\{\s*([\w.]+)\s*\}\}$/);
    return m ? m[1] : null;
  }

  const EVENT_ATTRS = {
    onclick: "click",
    onmouseenter: "mouseenter",
    onmouseleave: "mouseleave",
    onkeydown: "keydown",
    // onChange del design ~ React: per gli input di testo si usa "input"
    // (aggiornamento a ogni tasto), per select/checkbox l'evento "change".
    onchange: null,
  };

  function changeEventFor(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "select") return "change";
    if (tag === "input" && ["checkbox", "radio", "file"].includes(el.type)) return "change";
    return "input";
  }

  function renderInto(parent, nodes, scopes) {
    for (const node of nodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node.nodeValue;
        parent.appendChild(
          document.createTextNode(text.includes("{{") ? interpolate(text, scopes) : text)
        );
        continue;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) continue;
      const tag = node.tagName.toLowerCase();

      if (tag === "sc-if") {
        const expr = soleExpr(node.getAttribute("value") || "");
        if (expr && lookup(scopes, expr)) renderInto(parent, node.childNodes, scopes);
        continue;
      }
      if (tag === "sc-for") {
        const expr = soleExpr(node.getAttribute("list") || "");
        const as = node.getAttribute("as") || "item";
        const list = expr ? lookup(scopes, expr) : null;
        if (Array.isArray(list)) {
          for (const item of list) {
            renderInto(parent, node.childNodes, scopes.concat({ [as]: item }));
          }
        }
        continue;
      }

      const el = document.createElement(tag);
      let pendingValue = null;
      for (const attr of node.attributes) {
        const name = attr.name;
        if (name.startsWith("hint-")) continue;
        if (name in EVENT_ATTRS) {
          const expr = soleExpr(attr.value);
          const fn = expr ? lookup(scopes, expr) : null;
          if (typeof fn === "function") {
            const ev = name === "onchange" ? changeEventFor(el) : EVENT_ATTRS[name];
            el.addEventListener(ev, fn);
            // Accessibilità da tastiera: gli elementi cliccabili non nativi
            // (div/span con onClick) diventano focalizzabili con Tab e
            // attivabili con Invio/Spazio, come i <button>.
            if (name === "onclick" && el.tagName !== "BUTTON" && el.tagName !== "A") {
              if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
              if (!el.hasAttribute("role")) el.setAttribute("role", "button");
              el.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); }
              });
            }
          }
          continue;
        }
        if (name === "value" && attr.value.includes("{{")) {
          const expr = soleExpr(attr.value);
          pendingValue = expr ? lookup(scopes, expr) : interpolate(attr.value, scopes);
          continue;
        }
        el.setAttribute(name, attr.value.includes("{{") ? interpolate(attr.value, scopes) : attr.value);
      }
      renderInto(el, node.childNodes, scopes);
      if (pendingValue != null) el.value = String(pendingValue); // dopo i figli: serve alle <select>
      parent.appendChild(el);
    }
  }

  const VT = {
    root: null,
    template: null,
    app: null,

    mount(rootEl, templateEl, app) {
      this.root = rootEl;
      this.template = templateEl;
      this.app = app;
      app._vt = this;
      this.render();
    },

    render() {
      if (!this.root) return;
      const vals = this.app.renderVals();
      const frag = document.createDocumentFragment();
      renderInto(frag, this.template.content.childNodes, [vals]);
      this.root.replaceChildren(frag);
    },
  };

  window.VT = VT;
})();
