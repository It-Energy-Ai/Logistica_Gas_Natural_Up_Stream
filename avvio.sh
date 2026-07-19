#!/usr/bin/env sh
# Avvio senza Docker (macOS / Linux): serve solo Python 3.11+.
# Crea l'ambiente al primo avvio, poi apre il browser sull'app.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Serve Python 3: scaricalo da https://www.python.org/downloads/" >&2
  exit 1
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python launcher.py
