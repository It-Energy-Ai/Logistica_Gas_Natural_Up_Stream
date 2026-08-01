"""Persistenza SQLite di Vettore: sessioni e stato applicativo per utente."""

import json
import os
import sqlite3
from pathlib import Path

SESSIONI_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessioni (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    creata_il TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

STATO_SCHEMA = """
CREATE TABLE IF NOT EXISTS stato (
    email TEXT NOT NULL,
    chiave TEXT NOT NULL,
    valore TEXT NOT NULL,
    aggiornata_il TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (email, chiave)
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
    # Il backend FastAPI può servire più richieste contemporaneamente. Un
    # timeout e WAL evitano errori intermittenti "database is locked" durante
    # salvataggi ravvicinati dalla UI.
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SESSIONI_SCHEMA)
        _inizializza_stato(conn)
        conn.execute("PRAGMA journal_mode = WAL")
    print(f"[vettore] database: {db_path()}")


def _inizializza_stato(conn: sqlite3.Connection) -> None:
    """Crea lo stato per utente e conserva in backup l'eventuale schema v1.

    La v1 aveva una sola chiave primaria globale: non è possibile attribuire i
    valori esistenti a un utente senza rischiare di assegnarli a quello
    sbagliato. Per questo la migrazione non li espone al primo login casuale,
    ma li lascia nella tabella ``stato_legacy`` per un recupero consapevole.
    """
    colonne = {row["name"] for row in conn.execute("PRAGMA table_info(stato)")}
    if not colonne:
        conn.executescript(STATO_SCHEMA)
    elif "email" not in colonne:
        # Una migrazione interrotta non va a sovrascrivere il backup dell'utente.
        conn.execute("ALTER TABLE stato RENAME TO stato_legacy")
        conn.executescript(STATO_SCHEMA)

    _importa_legacy_se_richiesto(conn)


def _importa_legacy_se_richiesto(conn: sqlite3.Connection) -> None:
    """Assegna il backup v1 all'utente indicato esplicitamente dall'operatore."""
    proprietario = os.environ.get("VETTORE_LEGACY_EMAIL", "").strip().lower()
    if not proprietario:
        return
    tabella_legacy = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stato_legacy'"
    ).fetchone()
    if not tabella_legacy:
        return
    righe = list(conn.execute("SELECT chiave, valore, aggiornata_il FROM stato_legacy"))
    for riga in righe:
        conn.execute(
            "INSERT INTO stato (email, chiave, valore, aggiornata_il) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (email, chiave) DO UPDATE SET valore = excluded.valore, aggiornata_il = excluded.aggiornata_il",
            (proprietario, riga["chiave"], riga["valore"], riga["aggiornata_il"]),
        )
    conn.execute("DROP TABLE stato_legacy")


SESSIONE_GIORNI = 30  # allineata al max_age del cookie


def crea_sessione(conn: sqlite3.Connection, token: str, email: str) -> None:
    conn.execute(
        "DELETE FROM sessioni WHERE creata_il < datetime('now', ?)",
        (f"-{SESSIONE_GIORNI} days",),
    )
    conn.execute(
        "INSERT OR REPLACE INTO sessioni (token, email) VALUES (?, ?)", (token, email)
    )


def email_sessione(conn: sqlite3.Connection, token: str) -> str | None:
    row = conn.execute(
        "SELECT email FROM sessioni WHERE token = ? AND creata_il > datetime('now', ?)",
        (token, f"-{SESSIONE_GIORNI} days"),
    ).fetchone()
    return row["email"] if row else None


def elimina_sessione(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessioni WHERE token = ?", (token,))


def leggi_stato(conn: sqlite3.Connection, email: str) -> dict:
    out = {}
    for row in conn.execute(
        "SELECT chiave, valore FROM stato WHERE email = ?", (email,)
    ):
        try:
            out[row["chiave"]] = json.loads(row["valore"])
        except json.JSONDecodeError:
            continue
    return out


def scrivi_stato(conn: sqlite3.Connection, email: str, patch: dict) -> None:
    for chiave, valore in patch.items():
        conn.execute(
            "INSERT INTO stato (email, chiave, valore, aggiornata_il) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT (email, chiave) DO UPDATE SET valore = excluded.valore, aggiornata_il = datetime('now')",
            (email, chiave, json.dumps(valore, ensure_ascii=False)),
        )
