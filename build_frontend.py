#!/usr/bin/env python3
"""Genera app/static/index.html dal file di design (design/design.html).

Il markup del design resta identico al carattere: cambiano solo
gli pseudo-stili (style-hover / style-focus), che diventano regole CSS
generate, perché gli attributi custom non possono esprimere :hover/:focus.
Il template resta con la sintassi sc-if / sc-for / {{ var }} interpretata
a runtime da runtime.js.
"""

import re
import sys
from pathlib import Path

BASE = Path(__file__).parent
DESIGN = BASE / "design" / "design.html"
OUT = BASE / "app" / "static" / "index.html"


def main() -> None:
    html = DESIGN.read_text()

    m = re.search(r"<style>(.*?)</style>", html, re.S)
    if not m:
        sys.exit("stile del design non trovato")
    base_css = m.group(1).strip()

    start = html.find('<div data-theme=')
    end = html.rfind("</div>\n</x-dc>")
    if start == -1 or end == -1:
        sys.exit("template del design non trovato")
    template = html[start : end + len("</div>")]

    # style-hover / style-focus -> attributi data-vh / data-vf + regole CSS.
    rules: dict[tuple[str, str], int] = {}

    def sub(match: re.Match) -> str:
        kind, css = match.group(1), match.group(2)
        attr = "data-vh" if kind == "hover" else "data-vf"
        key = (kind, css)
        if key not in rules:
            rules[key] = len(rules)
        return f' {attr}="{rules[key]}"'

    template = re.sub(r'\s+style-(hover|focus)="([^"]*)"', sub, template)

    # Deviazione documentata (vedi logic.js, deviazione 1): i campi del login
    # diventano controllati, con Invio per accedere. Nel design sono input
    # senza binding e doLogin ignora i valori; senza binding, l'apertura del
    # modal SSO (re-render) li azzererebbe. Anchor sui placeholder, unici.
    login_bindings = [
        ('<input type="email" placeholder="nome@azienda.it"',
         '<input type="email" placeholder="nome@azienda.it" value="{{ loginEmail }}" onChange="{{ setLoginEmail }}" onKeyDown="{{ loginKey }}"'),
        ('<input type="password" placeholder="••••••••••"',
         '<input type="password" placeholder="••••••••••" value="{{ loginPass }}" onChange="{{ setLoginPass }}" onKeyDown="{{ loginKey }}"'),
    ]
    for vecchio, nuovo in login_bindings:
        occorrenze = template.count(vecchio)
        if occorrenze != 1:
            # il placeholder password del GME ha 12 pallini, questo 10: se il
            # design cambia e l'anchor non è più univoco, meglio fermarsi.
            sys.exit(f"anchor login non univoco ({occorrenze} occorrenze): {vecchio[:60]}")
        template = template.replace(vecchio, nuovo)

    # Deviazione documentata (vedi logic.js, deviazione 6): l'effetto "mazzo"
    # delle carte del hub passa da onMouseEnter/Leave (che nel runtime
    # causerebbe un re-render totale a ogni hover, perdendo le transizioni)
    # a puro CSS :hover, che riproduce l'animazione fluida del design.
    hub_anchor = '<div onMouseEnter="{{ hc.enter }}" onMouseLeave="{{ hc.leave }}" style="position:relative;width:252px;height:352px;cursor:pointer">'
    if template.count(hub_anchor) != 1:
        sys.exit("anchor carta hub non trovato: il design è cambiato, aggiorna build_frontend.py")
    template = template.replace(
        hub_anchor,
        '<div class="hub-card" style="position:relative;width:252px;height:352px;cursor:pointer">',
    )
    hub_css = """
.hub-card:hover>div:nth-child(1){transform:rotate(-13deg) translate(-36px,10px) !important}
.hub-card:hover>div:nth-child(2){transform:rotate(9deg) translate(32px,0px) !important}
.hub-card:hover>div:nth-child(3){transform:translateY(16px) rotate(-3deg) !important}
.hub-card:hover>div:nth-child(4){transform:translateY(-14px) scale(1.03) rotate(.5deg) !important;box-shadow:0 26px 52px color-mix(in oklab,var(--prim) 28%,transparent) !important}
"""

    # ------------------------------------------------------------------
    # Deviazione documentata (logic.js, deviazione 7): avvio pulito.
    # L'identità hardcoded del canvas (Marco Rossi / Azienda 1) diventa
    # dinamica, le date fisse diventano il giorno gas reale e la
    # scenografia demo è racchiusa in sc-if {{ demoOn }} attivabile dal
    # Configuratore. Il modal SSO resta com'era: è un IdP finto di scena.
    # ------------------------------------------------------------------

    def sostituisci(vecchio: str, nuovo: str, dove: str) -> None:
        nonlocal template
        occorrenze = template.count(vecchio)
        if occorrenze != 1:
            sys.exit(f"anchor non univoco per {dove} ({occorrenze} occorrenze)")
        template = template.replace(vecchio, nuovo)

    def avvolgi_demo(vecchio: str, dove: str) -> None:
        sostituisci(vecchio, f'<sc-if value="{{{{ demoOn }}}}">{vecchio}</sc-if>', dove)

    def avvolgi_blocco_demo(inizio: str, fine: str, dove: str) -> None:
        """Avvolge in sc-if demoOn il blocco che va da `inizio` (incluso)
        fino alla prima occorrenza successiva di `fine` (esclusa)."""
        nonlocal template
        i = template.find(inizio)
        if i == -1 or template.count(inizio) != 1:
            sys.exit(f"anchor di inizio non univoco per {dove}")
        j = template.find(fine, i)
        if j == -1:
            sys.exit(f"anchor di fine non trovato per {dove}")
        template = (
            template[:i]
            + '<sc-if value="{{ demoOn }}">'
            + template[i:j]
            + "</sc-if>"
            + template[j:]
        )

    # Identità dinamica
    sostituisci(
        'font-weight:700">MR</span>\n    <span style="display:flex;flex-direction:column;line-height:1.25">\n      <span style="font-size:13px;font-weight:600">Marco Rossi</span>\n      <span style="font-size:11px;color:var(--ink3)">Azienda 1</span>',
        'font-weight:700">{{ utenteIniziali }}</span>\n    <span style="display:flex;flex-direction:column;line-height:1.25">\n      <span style="font-size:13px;font-weight:600">{{ utenteNome }}</span>\n      <span style="font-size:11px;color:var(--ink3)">{{ utenteAzienda }}</span>',
        "identità header",
    )
    sostituisci("Buongiorno, Marco</h1>", "{{ saluto }}</h1>", "saluto hub")
    sostituisci(
        "Logistica Gas · Posizione shipper — Azienda 1</p>",
        "Logistica Gas · Posizione shipper — {{ utenteAzienda }}</p>",
        "sottotitolo dashboard",
    )
    sostituisci(
        ">Configuratore — Azienda 1</p>",
        ">Configuratore — {{ utenteAzienda }}</p>",
        "intestazione impostazioni",
    )
    sostituisci(
        "Mappa un nuovo utente sull'ambiente Azienda 1</span>",
        "Mappa un nuovo utente sull'ambiente {{ utenteAzienda }}</span>",
        "sottotitolo wizard",
    )
    sostituisci(
        "contratto Stogit · Azienda 1</p>",
        "contratto Stogit · {{ utenteAzienda }}</p>",
        "sottotitolo stoccaggio",
    )

    # Blocchi solo-demo (numeri statici del canvas)
    avvolgi_demo(
        '<span style="font-size:12px;font-weight:600;color:var(--primText);background:color-mix(in oklab,var(--prim) 10%,transparent);padding:8px 12px;border-radius:8px">Ciclo R3 in corso · chiude 14:00</span>',
        "badge ciclo nomine",
    )
    avvolgi_demo(
        '<span style="font-family:\'IBM Plex Mono\',monospace;font-size:12px;color:var(--ink2);background:var(--surface);border:1px solid var(--line);padding:7px 12px;border-radius:8px">Aggiornato 11:45</span>',
        "chip aggiornato bilancio",
    )
    avvolgi_demo(
        '<span style="font-family:\'IBM Plex Mono\',monospace;font-size:12px;color:var(--ink2);background:var(--surface);border:1px solid var(--line);padding:7px 12px;border-radius:8px">Anno termico stoccaggio 26/27</span>',
        "chip anno termico stoccaggio",
    )
    avvolgi_demo(
        '<span style="font-size:12px;font-weight:600;color:var(--acc);background:color-mix(in oklab,var(--acc) 12%,transparent);padding:8px 12px;border-radius:8px">Fase di Iniezione · 01/04 → 31/10</span>',
        "chip fase iniezione",
    )
    avvolgi_demo(">Ultimo agg. 06:31</span>", "chip report")
    sostituisci(">5 contratti</span>", ">{{ capChip }}</span>", "chip capacità")

    inizio_ds = '<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-top:16px">\n    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px">\n      <div style="display:flex;flex-direction:column;gap:3px"><h3 style="margin:0;font-size:15px;font-weight:600">Disequilibrio (DS)'
    fine_blocchi = '\n  <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-top:16px;align-items:start">'
    avvolgi_blocco_demo(inizio_ds, fine_blocchi, "card disequilibrio bilancio")

    inizio_riemp = '<div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-top:16px">\n    <div style="display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:14px">\n      <div style="display:flex;flex-direction:column;gap:3px"><h3 style="margin:0;font-size:15px;font-weight:600">Riempimento dello Spazio'
    avvolgi_blocco_demo(inizio_riemp, fine_blocchi, "card riempimento stoccaggio")

    # Interruttore modalità demo nella card "Stato servizi" (stile knob esistente)
    anchor_servizi = 'color:{{ sv.fg }}">{{ sv.stato }}</span>\n        </div>\n      </sc-for>'
    riga_demo = """
      <div style="display:flex;align-items:center;gap:12px;min-height:52px;padding:2px 0;border-top:1px solid var(--line)">
        <span style="display:flex;flex-direction:column;gap:2px;flex:1"><span style="font-size:13px;font-weight:600">Modalità demo</span><span style="font-size:11px;color:var(--ink3)">Popola il portale con dati di esempio</span></span>
        <span onClick="{{ demoToggle }}" style="width:38px;height:22px;border-radius:99px;background:{{ demoKBg }};border:1px solid var(--line);position:relative;cursor:pointer;transition:background .2s;flex:none;box-sizing:border-box">
          <span style="position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transform:{{ demoKX }};transition:transform .2s"></span>
        </span>
      </div>"""
    sostituisci(anchor_servizi, anchor_servizi + riga_demo, "interruttore demo")

    # Banner di stato vuoto in dashboard
    anchor_dash_fine_header = '<sc-if value="{{ dashNotToday }}" hint-placeholder-val="{{ false }}">\n        <button onClick="{{ dashToday }}"'
    i = template.find(anchor_dash_fine_header)
    if i == -1:
        sys.exit("anchor dashboard non trovato per il banner stato vuoto")
    chiusura = template.find('\n  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:22px">', i)
    if chiusura == -1:
        sys.exit("chiusura header dashboard non trovata")
    banner = '\n  <sc-if value="{{ vuotoDash }}"><div style="margin-top:14px;padding:12px 16px;border:1px dashed var(--line);border-radius:10px;font-size:13px;color:var(--ink2);background:var(--surface)">Il portale è in attesa dei primi dati. Per un giro di prova attiva la <strong>modalità demo</strong> in Configuratore → Sistema.</div></sc-if>'
    template = template[:chiusura] + banner + template[chiusura:]

    # Date fisse del canvas -> giorno gas reale (in demo resta il 17/07/2026)
    template = template.replace("17/07/2026", "{{ giornoGas }}")
    pseudo_css = "\n".join(
        f'[data-{"vh" if kind == "hover" else "vf"}="{idx}"]:{kind}{{{css}}}'
        for (kind, css), idx in rules.items()
    )

    leftovers = re.findall(r"style-(?:hover|focus)", template)
    if leftovers:
        sys.exit(f"pseudo-stili non convertiti: {len(leftovers)}")

    out = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vettore · Logistica Gas</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{base_css}
{pseudo_css}
{hub_css}
</style>
</head>
<body>
<div id="app"></div>
<template id="app-template">
{template}
</template>
<script src="/static/runtime.js"></script>
<script src="/static/logic.js"></script>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out)
    print(f"generato {OUT.relative_to(BASE)}: {len(out)} caratteri, {len(rules)} regole pseudo-stile")


if __name__ == "__main__":
    main()
