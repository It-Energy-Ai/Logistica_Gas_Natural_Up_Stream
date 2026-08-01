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

# Il registro REMIT è intenzionalmente separato dalla tabella key/value
# ``stato``: gli eventi e gli artefatti non devono essere cancellabili con una
# normale sincronizzazione del browser.
REMIT_REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS remit_report (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    valore TEXT NOT NULL,
    versione INTEGER NOT NULL DEFAULT 1,
    creata_il TEXT NOT NULL,
    aggiornata_il TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remit_report_email ON remit_report(email, aggiornata_il DESC);

CREATE TABLE IF NOT EXISTS remit_evento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    report_id TEXT NOT NULL,
    avvenuto_il TEXT NOT NULL,
    attore TEXT NOT NULL,
    tipo TEXT NOT NULL,
    stato_da TEXT,
    stato_a TEXT,
    dettaglio TEXT NOT NULL,
    hash_precedente TEXT NOT NULL,
    hash_evento TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remit_evento_report ON remit_evento(email, report_id, id);

CREATE TABLE IF NOT EXISTS remit_artifact (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    report_id TEXT NOT NULL,
    tipo TEXT NOT NULL,
    media_type TEXT NOT NULL,
    nome_file TEXT NOT NULL,
    contenuto TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    creato_il TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remit_artifact_email ON remit_artifact(email, report_id);

CREATE TABLE IF NOT EXISTS remit_migrazione (
    email TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    migrata_il TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (email, source_hash)
);

CREATE TABLE IF NOT EXISTS pdr_profilo (
    email TEXT PRIMARY KEY,
    valore TEXT NOT NULL,
    aggiornata_il TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Progressivo allocato in modo atomico per il nome file PDR. Un buco nella
-- sequenza è innocuo e preferibile al rischio di riutilizzare un nome file.
CREATE TABLE IF NOT EXISTS pdr_progressivo (
    data_file TEXT NOT NULL,
    schema_nome TEXT NOT NULL,
    schema_versione TEXT NOT NULL,
    codice_acer TEXT NOT NULL,
    prossimo INTEGER NOT NULL,
    PRIMARY KEY (data_file, schema_nome, schema_versione, codice_acer)
);
"""


class ConflittoStato(RuntimeError):
    """La riga è stata aggiornata dopo la versione letta dal client."""


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
        conn.executescript(REMIT_REPORT_SCHEMA)
        _migra_progressivo_pdr(conn)
        conn.execute("PRAGMA journal_mode = WAL")
    print(f"[vettore] database: {db_path()}")


def _migra_progressivo_pdr(conn: sqlite3.Connection) -> None:
    """Elimina la dimensione utente da un contatore visibile nel nome file.

    Il nome PDR non contiene l'email; quindi la numerazione deve essere unica
    almeno per data/schema/versione/codice ACER. Manteniamo la tabella v1 come
    backup locale e portiamo nel nuovo contatore il valore massimo già emesso.
    """

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(pdr_progressivo)")}
    if "email" not in columns:
        return
    conn.execute("ALTER TABLE pdr_progressivo RENAME TO pdr_progressivo_v1")
    conn.execute(
        "CREATE TABLE pdr_progressivo ("
        "data_file TEXT NOT NULL, schema_nome TEXT NOT NULL, schema_versione TEXT NOT NULL, "
        "codice_acer TEXT NOT NULL, prossimo INTEGER NOT NULL, "
        "PRIMARY KEY (data_file, schema_nome, schema_versione, codice_acer))"
    )
    conn.execute(
        "INSERT INTO pdr_progressivo (data_file, schema_nome, schema_versione, codice_acer, prossimo) "
        "SELECT data_file, schema_nome, schema_versione, codice_acer, MAX(prossimo) "
        "FROM pdr_progressivo_v1 "
        "GROUP BY data_file, schema_nome, schema_versione, codice_acer"
    )


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


def _serializza_report(record: dict) -> str:
    """La versione è colonna SQL, non parte affidabile del payload JSON."""
    valore = {k: v for k, v in record.items() if k != "version"}
    return json.dumps(valore, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _deserializza_report(row: sqlite3.Row) -> dict:
    try:
        valore = json.loads(row["valore"])
    except (TypeError, json.JSONDecodeError):
        valore = {}
    if not isinstance(valore, dict):
        valore = {}
    return {
        **valore,
        "id": row["id"],
        "version": row["versione"],
        "created_at": valore.get("created_at") or row["creata_il"],
        "updated_at": valore.get("updated_at") or row["aggiornata_il"],
    }


def crea_report_remit(conn: sqlite3.Connection, email: str, record: dict) -> None:
    conn.execute(
        "INSERT INTO remit_report (id, email, valore, creata_il, aggiornata_il) VALUES (?, ?, ?, ?, ?)",
        (
            record["id"],
            email,
            _serializza_report(record),
            record["created_at"],
            record["updated_at"],
        ),
    )


def leggi_report_remit(conn: sqlite3.Connection, email: str, report_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, valore, versione, creata_il, aggiornata_il FROM remit_report WHERE email = ? AND id = ?",
        (email, report_id),
    ).fetchone()
    return _deserializza_report(row) if row else None


def leggi_report_remit_tutti(conn: sqlite3.Connection, email: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, valore, versione, creata_il, aggiornata_il FROM remit_report WHERE email = ? "
        "ORDER BY aggiornata_il DESC, id DESC",
        (email,),
    )
    return [_deserializza_report(row) for row in rows]


def aggiorna_report_remit(
    conn: sqlite3.Connection,
    email: str,
    report_id: str,
    record: dict,
    *,
    expected_version: int,
) -> int:
    cur = conn.execute(
        "UPDATE remit_report SET valore = ?, versione = versione + 1, aggiornata_il = ? "
        "WHERE email = ? AND id = ? AND versione = ?",
        (
            _serializza_report(record),
            record["updated_at"],
            email,
            report_id,
            expected_version,
        ),
    )
    if cur.rowcount != 1:
        raise ConflittoStato("Versione non più corrente.")
    return expected_version + 1


def ultimo_hash_evento_remit(conn: sqlite3.Connection, email: str, report_id: str) -> str:
    row = conn.execute(
        "SELECT hash_evento FROM remit_evento WHERE email = ? AND report_id = ? ORDER BY id DESC LIMIT 1",
        (email, report_id),
    ).fetchone()
    return row["hash_evento"] if row else ""


def aggiungi_evento_remit(
    conn: sqlite3.Connection,
    *,
    email: str,
    report_id: str,
    occurred_at: str,
    actor: str,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    detail: dict,
    prev_hash: str,
    event_hash: str,
) -> None:
    conn.execute(
        "INSERT INTO remit_evento (email, report_id, avvenuto_il, attore, tipo, stato_da, stato_a, dettaglio, hash_precedente, hash_evento) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            email,
            report_id,
            occurred_at,
            actor,
            event_type,
            from_status,
            to_status,
            json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            prev_hash,
            event_hash,
        ),
    )


def leggi_eventi_remit(conn: sqlite3.Connection, email: str, report_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, avvenuto_il, attore, tipo, stato_da, stato_a, dettaglio, hash_precedente, hash_evento "
        "FROM remit_evento WHERE email = ? AND report_id = ? ORDER BY id",
        (email, report_id),
    )
    out = []
    for row in rows:
        try:
            detail = json.loads(row["dettaglio"])
        except json.JSONDecodeError:
            detail = {}
        out.append(
            {
                "id": row["id"],
                "occurred_at": row["avvenuto_il"],
                "actor": row["attore"],
                "event_type": row["tipo"],
                "from_status": row["stato_da"],
                "to_status": row["stato_a"],
                "detail": detail,
                "prev_hash": row["hash_precedente"],
                "hash": row["hash_evento"],
            }
        )
    return out


def crea_artifact_remit(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    email: str,
    report_id: str,
    kind: str,
    media_type: str,
    filename: str,
    content: str,
    sha256: str,
) -> None:
    conn.execute(
        "INSERT INTO remit_artifact (id, email, report_id, tipo, media_type, nome_file, contenuto, sha256, creato_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (artifact_id, email, report_id, kind, media_type, filename, content, sha256),
    )


def leggi_artifact_remit(conn: sqlite3.Connection, email: str, artifact_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, report_id, tipo, media_type, nome_file, contenuto, sha256, creato_il "
        "FROM remit_artifact WHERE email = ? AND id = ?",
        (email, artifact_id),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "report_id": row["report_id"],
        "kind": row["tipo"],
        "media_type": row["media_type"],
        "filename": row["nome_file"],
        "content": row["contenuto"],
        "sha256": row["sha256"],
        "created_at": row["creato_il"],
    }


def migrazione_remit_eseguita(conn: sqlite3.Connection, email: str, source_hash: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM remit_migrazione WHERE email = ? AND source_hash = ?",
            (email, source_hash),
        ).fetchone()
    )


def registra_migrazione_remit(conn: sqlite3.Connection, email: str, source_hash: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO remit_migrazione (email, source_hash) VALUES (?, ?)",
        (email, source_hash),
    )


def leggi_profilo_pdr(conn: sqlite3.Connection, email: str) -> dict | None:
    row = conn.execute("SELECT valore FROM pdr_profilo WHERE email = ?", (email,)).fetchone()
    if not row:
        return None
    try:
        valore = json.loads(row["valore"])
    except json.JSONDecodeError:
        return None
    return valore if isinstance(valore, dict) else None


def scrivi_profilo_pdr(conn: sqlite3.Connection, email: str, profile: dict) -> None:
    conn.execute(
        "INSERT INTO pdr_profilo (email, valore, aggiornata_il) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT (email) DO UPDATE SET valore = excluded.valore, aggiornata_il = datetime('now')",
        (email, json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )


def prossimo_progressivo_pdr(
    conn: sqlite3.Connection,
    *,
    data_file: str,
    schema_nome: str,
    schema_versione: str,
    codice_acer: str,
) -> int:
    """Riserva il progressivo PDR senza poter riutilizzare un nome file.

    ``RETURNING`` fa parte di SQLite dalla 3.35 (ampiamente precedente alle
    versioni incluse in Python supportate dal progetto) e mantiene atomica
    l'operazione anche con due richieste simultanee.
    """

    row = conn.execute(
        "INSERT INTO pdr_progressivo "
        "(data_file, schema_nome, schema_versione, codice_acer, prossimo) "
        "VALUES (?, ?, ?, ?, 2) "
        "ON CONFLICT(data_file, schema_nome, schema_versione, codice_acer) "
        "DO UPDATE SET prossimo = pdr_progressivo.prossimo + 1 "
        "RETURNING prossimo - 1 AS progressivo",
        (data_file, schema_nome, schema_versione, codice_acer),
    ).fetchone()
    assert row is not None
    return int(row["progressivo"])
