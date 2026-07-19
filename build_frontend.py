#!/usr/bin/env python3
"""Genera app/static/index.html dal design Claude Design (design/design.html).

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
