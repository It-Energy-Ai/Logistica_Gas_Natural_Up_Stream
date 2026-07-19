"""Persistenza SQLite di Vettore: sessioni e stato applicativo condiviso.

Lo stato è GLOBALE (demo single-tenant): tutte le sessioni leggono e
scrivono le stesse chiavi. Per un multi-tenant reale servirebbe una
colonna utente/azienda nella tabella stato.
"""

import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessioni (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    creata_il TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stato (
    chiave TEXT PRIMARY KEY,
    valore TEXT NOT NULL,
    aggiornata_il TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def db_path() -> str:
    predefinito = Path(__file__).resolve().parent.parent / "data" / "vettore.db"
    return os.environ.get("VETTORE_DB", str(predefinito))


def connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
    print(f"[vettore] database: {db_path()}")


def crea_sessione(conn: sqlite3.Connection, token: str, email: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sessioni (token, email) VALUES (?, ?)", (token, email)
    )


def email_sessione(conn: sqlite3.Connection, token: str) -> str | None:
    row = conn.execute(
        "SELECT email FROM sessioni WHERE token = ?", (token,)
    ).fetchone()
    return row["email"] if row else None


def elimina_sessione(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessioni WHERE token = ?", (token,))


def leggi_stato(conn: sqlite3.Connection) -> dict:
    out = {}
    for row in conn.execute("SELECT chiave, valore FROM stato"):
        try:
            out[row["chiave"]] = json.loads(row["valore"])
        except json.JSONDecodeError:
            continue
    return out


def scrivi_stato(conn: sqlite3.Connection, patch: dict) -> None:
    for chiave, valore in patch.items():
        conn.execute(
            "INSERT INTO stato (chiave, valore, aggiornata_il) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT (chiave) DO UPDATE SET valore = excluded.valore, aggiornata_il = datetime('now')",
            (chiave, json.dumps(valore, ensure_ascii=False)),
        )
