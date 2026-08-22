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

CREATE TABLE IF NOT EXISTS edigas_nomina (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    identificativo TEXT NOT NULL,
    versione INTEGER NOT NULL,
    tipo_documento TEXT NOT NULL,
    giorno_gas TEXT NOT NULL,
    punto TEXT NOT NULL,
    periodi INTEGER NOT NULL,
    avvisi TEXT NOT NULL,
    xml TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    creato_il TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edigas_nomina_email ON edigas_nomina(email, giorno_gas);
-- Identificativo e versione sono ciò che il trasportatore cita nella risposta:
-- due nomine omonime renderebbero ambiguo l'abbinamento del NOMRES.
CREATE UNIQUE INDEX IF NOT EXISTS idx_edigas_nomina_identita
    ON edigas_nomina(email, identificativo, versione);

-- Riscontri ACKNOW ricevuti: il trasportatore dichiara di aver preso in
-- carico (o respinto) un documento inviato. Sono immutabili come le ricevute
-- PDR: valgono come prova di trasmissione.
CREATE TABLE IF NOT EXISTS edigas_riscontro (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    nomina_id TEXT,
    identificativo TEXT NOT NULL,
    tipo_documento TEXT NOT NULL,
    riferimento TEXT NOT NULL,
    accettato INTEGER NOT NULL,
    esito TEXT NOT NULL DEFAULT 'respinto',
    motivazioni TEXT NOT NULL,
    creato_il TEXT NOT NULL,
    xml TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    importato_il TEXT NOT NULL,
    UNIQUE (email, sha256)
);
CREATE INDEX IF NOT EXISTS idx_edigas_riscontro_nomina
    ON edigas_riscontro(email, nomina_id, importato_il DESC);

-- Segnalazioni EMIR REFIT. Tabella separata da quelle REMIT perché sono due
-- obblighi distinti verso destinatari diversi: mescolarle renderebbe
-- impossibile dire quale file è stato prodotto per quale regolatore.
CREATE TABLE IF NOT EXISTS emir_segnalazione (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    uti TEXT NOT NULL,
    azione TEXT NOT NULL,
    sigla_azione TEXT NOT NULL,
    livello TEXT NOT NULL,
    controparte TEXT NOT NULL,
    avvisi TEXT NOT NULL,
    xml TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    creato_il TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emir_segnalazione_email
    ON emir_segnalazione(email, creato_il DESC);
-- Lo stesso UTI può tornare più volte (nuovo → modifica → valutazione), ma
-- non due volte con la stessa azione: sarebbe un doppio invio.
CREATE UNIQUE INDEX IF NOT EXISTS idx_emir_segnalazione_identita
    ON emir_segnalazione(email, uti, azione);

-- Esiti auth.092 ricevuti dal Trade Repository: come le ricevute PDR valgono
-- come prova di cosa è stato accolto, quindi sono immutabili.
CREATE TABLE IF NOT EXISTS emir_esito (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    data_riferimento TEXT NOT NULL,
    accolte INTEGER NOT NULL,
    respinte INTEGER NOT NULL,
    righe TEXT NOT NULL,
    riepilogo TEXT NOT NULL,
    xml TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    importato_il TEXT NOT NULL,
    UNIQUE (email, sha256)
);
CREATE INDEX IF NOT EXISTS idx_emir_esito_email
    ON emir_esito(email, importato_il DESC);

CREATE TRIGGER IF NOT EXISTS emir_esito_no_update
BEFORE UPDATE ON emir_esito
BEGIN
    SELECT RAISE(ABORT, 'Gli esiti EMIR sono immutabili');
END;

CREATE TRIGGER IF NOT EXISTS emir_esito_no_delete
BEFORE DELETE ON emir_esito
BEGIN
    SELECT RAISE(ABORT, 'Gli esiti EMIR sono immutabili');
END;

-- Interruzioni della capacità interrompibile comunicate da Snam: lo shipper
-- le riceve, non le decide. Trascrizioni manuali dell'operatore, quindi
-- correggibili (a differenza di ricevute ed esiti, che sono prove).
CREATE TABLE IF NOT EXISTS trasporto_interruzione (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    punto TEXT NOT NULL COLLATE NOCASE,
    tipo TEXT NOT NULL CHECK (tipo IN ('totale', 'parziale')),
    data_inizio TEXT NOT NULL,
    data_fine TEXT NOT NULL,
    giorni INTEGER NOT NULL CHECK (giorni BETWEEN 1 AND 366),
    capacita REAL,
    preavviso_ore INTEGER,
    riferimento TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    anno_termico INTEGER NOT NULL,
    avvisi TEXT NOT NULL,
    creato_il TEXT NOT NULL,
    UNIQUE (email, punto, data_inizio)
);
CREATE INDEX IF NOT EXISTS idx_trasporto_interruzione_email
    ON trasporto_interruzione(email, anno_termico DESC, data_inizio DESC);

-- Note giustificative UIOLI preparate (Cap. 7, §4.3): il portale le produce
-- e le fa scaricare, la trasmissione resta dell'operatore.
CREATE TABLE IF NOT EXISTS uioli_nota (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    punto TEXT NOT NULL,
    anno_termico INTEGER NOT NULL,
    capacita_detrazione REAL NOT NULL,
    durata TEXT NOT NULL,
    motivazioni TEXT NOT NULL,
    mittente TEXT NOT NULL,
    scadenza TEXT NOT NULL,
    fuori_termine INTEGER NOT NULL DEFAULT 0,
    testo TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    creato_il TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uioli_nota_email
    ON uioli_nota(email, creato_il DESC);

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

-- Una ricevuta non è una nota modificabile: il documento originale, il suo
-- hash e il collegamento all'XML ACER restano immutabili dopo l'import.
-- ``contenuto`` è un BLOB: il JSON usa base64 solo per il trasporto HTTP.
CREATE TABLE IF NOT EXISTS pdr_ricevuta (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    report_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    fonte TEXT NOT NULL CHECK (fonte IN ('pdr', 'acer')),
    esito TEXT NOT NULL CHECK (esito IN (
        'pdr_rejected', 'pdr_accepted', 'pdr_partial', 'acer_transmitted',
        'acer_accepted', 'acer_rejected', 'technical_retry', 'unknown'
    )),
    codice_carico TEXT CHECK (
        codice_carico IS NULL OR codice_carico GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ),
    segnalata_il TEXT NOT NULL,
    nome_file TEXT NOT NULL,
    media_type TEXT NOT NULL,
    dettaglio_errore TEXT NOT NULL DEFAULT '',
    contenuto BLOB NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    parser_version TEXT,
    parser_metadata TEXT NOT NULL DEFAULT '{}',
    connettore_verificato INTEGER NOT NULL DEFAULT 0 CHECK (connettore_verificato = 0),
    importata_il TEXT NOT NULL,
    UNIQUE (email, report_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_pdr_ricevuta_email_report
    ON pdr_ricevuta(email, report_id, importata_il DESC);

CREATE TRIGGER IF NOT EXISTS pdr_ricevuta_no_update
BEFORE UPDATE ON pdr_ricevuta
BEGIN
    SELECT RAISE(ABORT, 'Le ricevute PDR sono immutabili');
END;

CREATE TRIGGER IF NOT EXISTS pdr_ricevuta_no_delete
BEFORE DELETE ON pdr_ricevuta
BEGIN
    SELECT RAISE(ABORT, 'Le ricevute PDR sono immutabili');
END;

-- Scadenze dell'Agenda regolatoria: promemoria locali dello shipper. Le voci
-- istanziate dal modello (modello_chiave NOT NULL) portano il riferimento
-- alla fonte che fissa la data; quelle personalizzate (modello_chiave NULL)
-- sono libere e correggibili. Nessuna di esse è una prova: si possono
-- modificare ed eliminare, a differenza di ricevute ed esiti.
CREATE TABLE IF NOT EXISTS agenda_scadenza (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    titolo TEXT NOT NULL,
    categoria TEXT NOT NULL CHECK (categoria IN (
        'trasporto', 'stoccaggio', 'regolatorio', 'remit', 'operativo', 'personale'
    )),
    data_scadenza TEXT NOT NULL,
    ricorrenza TEXT NOT NULL CHECK (ricorrenza IN (
        'una_tantum', 'annuale', 'mensile', 'trimestrale', 'settimanale', 'giorno_gas'
    )),
    stato TEXT NOT NULL CHECK (stato IN ('aperta', 'adempiuta', 'saltata')),
    riferimento TEXT NOT NULL DEFAULT '',
    nota TEXT NOT NULL DEFAULT '',
    modello_chiave TEXT,
    modello_anno INTEGER,
    creato_il TEXT NOT NULL,
    aggiornata_il TEXT NOT NULL,
    UNIQUE (email, modello_chiave, modello_anno)
);
CREATE INDEX IF NOT EXISTS idx_agenda_scadenza_email_data
    ON agenda_scadenza(email, data_scadenza);

-- Accesso dell'operatore a SIICloud (WebDAV di Acquirente Unico): le
-- credenziali restano solo nel database locale dell'app e servono alla
-- sincronizzazione giornaliera dei file di misura. ``ultima_sync`` e
-- ``errore_sync`` fotografano l'esito dell'ultimo collegamento.
CREATE TABLE IF NOT EXISTS sii_accesso (
    email TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    utente TEXT NOT NULL,
    password TEXT NOT NULL,
    percorso TEXT NOT NULL DEFAULT '',
    attivo INTEGER NOT NULL DEFAULT 1,
    ultima_sync TEXT,
    errore_sync TEXT NOT NULL DEFAULT '',
    aggiornato_il TEXT NOT NULL DEFAULT (datetime('now'))
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


def crea_nomina_edigas(
    conn: sqlite3.Connection,
    *,
    nomina_id: str,
    email: str,
    identificativo: str,
    versione: int,
    tipo_documento: str,
    giorno_gas: str,
    punto: str,
    periodi: int,
    avvisi: str,
    xml: str,
    sha256: str,
) -> None:
    conn.execute(
        "INSERT INTO edigas_nomina (id, email, identificativo, versione, tipo_documento, giorno_gas, "
        "punto, periodi, avvisi, xml, sha256, creato_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            nomina_id,
            email,
            identificativo,
            versione,
            tipo_documento,
            giorno_gas,
            punto,
            periodi,
            avvisi,
            xml,
            sha256,
        ),
    )


def _json_o_righe(grezzo: str | None) -> list[str]:
    """Legge gli avvisi come JSON, con ricaduta sul vecchio formato a righe."""

    testo = (grezzo or "").strip()
    if not testo:
        return []
    try:
        letto = json.loads(testo)
    except json.JSONDecodeError:
        return [r for r in testo.split("\n") if r]
    return [str(x) for x in letto] if isinstance(letto, list) else []


def _riga_nomina(row: sqlite3.Row, *, con_xml: bool = False) -> dict:
    dati = {
        "id": row["id"],
        "identificativo": row["identificativo"],
        "versione": row["versione"],
        "tipo_documento": row["tipo_documento"],
        "giorno_gas": row["giorno_gas"],
        "punto": row["punto"],
        "periodi": row["periodi"],
        # JSON e non split("\n"): un valore che contenga un a capo spezzerebbe
        # un avviso in due, mostrandone uno monco.
        "avvisi": _json_o_righe(row["avvisi"]),
        "sha256": row["sha256"],
        "creato_il": row["creato_il"],
    }
    if con_xml:
        dati["xml"] = row["xml"]
    return dati


CAMPI_NOMINA = (
    "id, identificativo, versione, tipo_documento, giorno_gas, punto, periodi, avvisi, sha256, creato_il"
)


def elenca_nomine_edigas(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_NOMINA} FROM edigas_nomina WHERE email = ? "
        "ORDER BY creato_il DESC, rowid DESC LIMIT 500",
        (email,),
    ).fetchall()
    return [_riga_nomina(r) for r in righe]


def leggi_nomina_edigas(conn: sqlite3.Connection, email: str, nomina_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {CAMPI_NOMINA}, xml FROM edigas_nomina WHERE email = ? AND id = ?",
        (email, nomina_id),
    ).fetchone()
    return _riga_nomina(row, con_xml=True) if row else None


def trova_nomina_edigas(
    conn: sqlite3.Connection, email: str, identificativo: str, versione: str | int
) -> dict | None:
    """Ritrova la nomina citata da una risposta del trasportatore.

    La versione arriva dal file del trasportatore, quindi è testo libero: un
    refuso come "1.0" non deve far cadere la richiesta.
    """

    testo = str(versione or "").strip()
    # `isdecimal()` è vero anche per una stringa di diecimila cifre, che poi
    # sfonda il limite di conversione di int(). La versione EDIG@S sta in tre
    # cifre (Version_Integer, maxInclusive 999): tutto il resto non può
    # riferirsi a una nomina esistente.
    if not testo.isdecimal() or len(testo) > 3:
        return None
    numero = int(testo)
    if not 0 < numero <= 999:
        return None
    row = conn.execute(
        f"SELECT {CAMPI_NOMINA}, xml FROM edigas_nomina "
        # La prima: è quella che lo shipper ha effettivamente inviato e che
        # il trasportatore cita nella risposta.
        "WHERE email = ? AND identificativo = ? AND versione = ? ORDER BY rowid ASC LIMIT 1",
        (email, identificativo, numero),
    ).fetchone()
    return _riga_nomina(row, con_xml=True) if row else None


def crea_riscontro_edigas(
    conn: sqlite3.Connection,
    *,
    riscontro_id: str,
    email: str,
    nomina_id: str | None,
    identificativo: str,
    tipo_documento: str,
    riferimento: str,
    accettato: bool,
    esito: str,
    motivazioni: str,
    creato_il: str,
    xml: str,
    sha256: str,
) -> None:
    conn.execute(
        "INSERT INTO edigas_riscontro (id, email, nomina_id, identificativo, tipo_documento, riferimento, "
        "accettato, esito, motivazioni, creato_il, xml, sha256, importato_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            riscontro_id, email, nomina_id, identificativo, tipo_documento, riferimento,
            1 if accettato else 0, esito, motivazioni, creato_il, xml, sha256,
        ),
    )


def _riga_riscontro(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "nomina_id": row["nomina_id"],
        "identificativo": row["identificativo"],
        "tipo_documento": row["tipo_documento"],
        "riferimento": row["riferimento"],
        "accettato": bool(row["accettato"]),
        "esito": row["esito"],
        "motivazioni": _json_o_righe(row["motivazioni"]),
        "creato_il": row["creato_il"],
        "sha256": row["sha256"],
        "importato_il": row["importato_il"],
    }


CAMPI_RISCONTRO = (
    "id, nomina_id, identificativo, tipo_documento, riferimento, accettato, esito, motivazioni, "
    "creato_il, sha256, importato_il"
)


def elenca_riscontri_edigas(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_RISCONTRO} FROM edigas_riscontro WHERE email = ? "
        "ORDER BY importato_il DESC, rowid DESC LIMIT 500",
        (email,),
    ).fetchall()
    return [_riga_riscontro(r) for r in righe]


def riscontro_edigas_per_impronta(conn: sqlite3.Connection, email: str, sha256: str) -> dict | None:
    """Un riscontro già importato non va duplicato: si riconosce dall'impronta."""

    row = conn.execute(
        f"SELECT {CAMPI_RISCONTRO} FROM edigas_riscontro WHERE email = ? AND sha256 = ?",
        (email, sha256),
    ).fetchone()
    return _riga_riscontro(row) if row else None


# ------------------------------------------------------------------- EMIR

CAMPI_SEGNALAZIONE = (
    "id, uti, azione, sigla_azione, livello, controparte, avvisi, sha256, creato_il"
)


def _riga_segnalazione(row: sqlite3.Row, *, con_xml: bool = False) -> dict:
    dati = {
        "id": row["id"],
        "uti": row["uti"],
        "azione": row["azione"],
        "sigla_azione": row["sigla_azione"],
        "livello": row["livello"],
        "controparte": row["controparte"],
        "avvisi": _json_o_righe(row["avvisi"]),
        "sha256": row["sha256"],
        "creato_il": row["creato_il"],
    }
    if con_xml:
        dati["xml"] = row["xml"]
    return dati


def crea_segnalazione_emir(
    conn: sqlite3.Connection,
    *,
    segnalazione_id: str,
    email: str,
    uti: str,
    azione: str,
    sigla_azione: str,
    livello: str,
    controparte: str,
    avvisi: str,
    xml: str,
    sha256: str,
) -> None:
    conn.execute(
        "INSERT INTO emir_segnalazione (id, email, uti, azione, sigla_azione, livello, controparte, "
        "avvisi, xml, sha256, creato_il) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (segnalazione_id, email, uti, azione, sigla_azione, livello, controparte, avvisi, xml, sha256),
    )


def elenca_segnalazioni_emir(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_SEGNALAZIONE} FROM emir_segnalazione WHERE email = ? "
        "ORDER BY creato_il DESC, rowid DESC LIMIT 500",
        (email,),
    ).fetchall()
    return [_riga_segnalazione(r) for r in righe]


def leggi_segnalazione_emir(conn: sqlite3.Connection, email: str, segnalazione_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {CAMPI_SEGNALAZIONE}, xml FROM emir_segnalazione WHERE email = ? AND id = ?",
        (email, segnalazione_id),
    ).fetchone()
    return _riga_segnalazione(row, con_xml=True) if row else None


CAMPI_ESITO = "id, data_riferimento, accolte, respinte, righe, riepilogo, sha256, importato_il"


def _riga_esito(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "data_riferimento": row["data_riferimento"],
        "accolte": row["accolte"],
        "respinte": row["respinte"],
        "righe": json.loads(row["righe"]) if row["righe"] else [],
        "riepilogo": json.loads(row["riepilogo"]) if row["riepilogo"] else {},
        "sha256": row["sha256"],
        "importato_il": row["importato_il"],
    }


def crea_esito_emir(
    conn: sqlite3.Connection,
    *,
    esito_id: str,
    email: str,
    data_riferimento: str,
    accolte: int,
    respinte: int,
    righe: str,
    riepilogo: str,
    xml: str,
    sha256: str,
) -> None:
    conn.execute(
        "INSERT INTO emir_esito (id, email, data_riferimento, accolte, respinte, righe, riepilogo, "
        "xml, sha256, importato_il) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (esito_id, email, data_riferimento, accolte, respinte, righe, riepilogo, xml, sha256),
    )


def elenca_esiti_emir(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_ESITO} FROM emir_esito WHERE email = ? "
        "ORDER BY importato_il DESC, rowid DESC LIMIT 200",
        (email,),
    ).fetchall()
    return [_riga_esito(r) for r in righe]


CAMPI_INTERRUZIONE = (
    "id, punto, tipo, data_inizio, data_fine, giorni, capacita, preavviso_ore, "
    "riferimento, note, anno_termico, avvisi, creato_il"
)


def _riga_interruzione(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "punto": row["punto"],
        "tipo": row["tipo"],
        "data_inizio": row["data_inizio"],
        "data_fine": row["data_fine"],
        "giorni": row["giorni"],
        "capacita": row["capacita"],
        "preavviso_ore": row["preavviso_ore"],
        "riferimento": row["riferimento"],
        "note": row["note"],
        "anno_termico": row["anno_termico"],
        "avvisi": _json_o_righe(row["avvisi"]),
        "creato_il": row["creato_il"],
    }


def crea_interruzione(conn: sqlite3.Connection, *, interruzione_id: str, email: str,
                      record: dict) -> None:
    conn.execute(
        "INSERT INTO trasporto_interruzione (id, email, punto, tipo, data_inizio, data_fine, "
        "giorni, capacita, preavviso_ore, riferimento, note, anno_termico, avvisi, creato_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            interruzione_id, email, record["punto"], record["tipo"], record["data_inizio"],
            record["data_fine"], record["giorni"], record["capacita"], record["preavviso_ore"],
            record["riferimento"], record["note"], record["anno_termico"],
            json.dumps(record["avvisi"]),
        ),
    )


def elenca_interruzioni(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_INTERRUZIONE} FROM trasporto_interruzione WHERE email = ? "
        "ORDER BY data_inizio DESC, rowid DESC LIMIT 500",
        (email,),
    ).fetchall()
    return [_riga_interruzione(r) for r in righe]


def tutte_interruzioni(conn: sqlite3.Connection, email: str) -> list[dict]:
    """Elenco integrale, per i controlli del Codice: sovrapposizioni e 4 giorni.

    L'elenco da mostrare è limitato a 500 righe; i controlli invece devono
    vedere tutto, altrimenti la 501esima interruzione sfugge al confronto.
    """

    righe = conn.execute(
        f"SELECT {CAMPI_INTERRUZIONE} FROM trasporto_interruzione WHERE email = ? "
        "ORDER BY data_inizio",
        (email,),
    ).fetchall()
    return [_riga_interruzione(r) for r in righe]


def elimina_interruzione(conn: sqlite3.Connection, email: str, interruzione_id: str) -> bool:
    cursore = conn.execute(
        "DELETE FROM trasporto_interruzione WHERE email = ? AND id = ?",
        (email, interruzione_id),
    )
    return cursore.rowcount > 0


CAMPI_NOTA_UIOLI = (
    "id, punto, anno_termico, capacita_detrazione, durata, motivazioni, mittente, "
    "scadenza, fuori_termine, sha256, creato_il"
)


def _riga_nota_uioli(row: sqlite3.Row, *, con_testo: bool = False) -> dict:
    dati = {
        "id": row["id"],
        "punto": row["punto"],
        "anno_termico": row["anno_termico"],
        "capacita_detrazione": row["capacita_detrazione"],
        "durata": row["durata"],
        "motivazioni": row["motivazioni"],
        "mittente": row["mittente"],
        "scadenza": row["scadenza"],
        "fuori_termine": bool(row["fuori_termine"]),
        "sha256": row["sha256"],
        "creato_il": row["creato_il"],
    }
    if con_testo:
        dati["testo"] = row["testo"]
    return dati


def crea_nota_uioli(conn: sqlite3.Connection, *, nota_id: str, email: str, record: dict) -> None:
    conn.execute(
        "INSERT INTO uioli_nota (id, email, punto, anno_termico, capacita_detrazione, durata, "
        "motivazioni, mittente, scadenza, fuori_termine, testo, sha256, creato_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            nota_id, email, record["punto"], record["anno_termico"],
            record["capacita_detrazione"], record["durata"], record["motivazioni"],
            record["mittente"], record["scadenza"], int(record["fuori_termine"]),
            record["testo"], record["sha256"],
        ),
    )


def elenca_note_uioli(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_NOTA_UIOLI} FROM uioli_nota WHERE email = ? "
        "ORDER BY creato_il DESC, rowid DESC LIMIT 200",
        (email,),
    ).fetchall()
    return [_riga_nota_uioli(r) for r in righe]


def leggi_nota_uioli(conn: sqlite3.Connection, email: str, nota_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {CAMPI_NOTA_UIOLI}, testo FROM uioli_nota WHERE email = ? AND id = ?",
        (email, nota_id),
    ).fetchone()
    return _riga_nota_uioli(row, con_testo=True) if row else None


def esito_emir_per_impronta(conn: sqlite3.Connection, email: str, sha256: str) -> dict | None:
    """Un esito già importato non va duplicato: si riconosce dall'impronta."""

    row = conn.execute(
        f"SELECT {CAMPI_ESITO} FROM emir_esito WHERE email = ? AND sha256 = ?",
        (email, sha256),
    ).fetchone()
    return _riga_esito(row) if row else None


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
    """Vero se l'utente ha già importato il proprio registro legacy.

    L'esito NON dipende dall'impronta della lista: ancorarlo a quella
    significava rimigrare tutto a ogni modifica di ``remList``, duplicando le
    bozze già convertite. La migrazione è un evento che accade una volta per
    utente; le righe aggiunte dopo appartengono al registro nuovo.
    """

    return bool(
        conn.execute("SELECT 1 FROM remit_migrazione WHERE email = ?", (email,)).fetchone()
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
    if row is None:
        # Mai un assert qui: con `python -O` sparirebbe e un problema del
        # database diventerebbe un progressivo corrotto nel nome file PDR.
        raise RuntimeError(
            "Impossibile riservare il progressivo PDR "
            f"({data_file}/{schema_nome}/{schema_versione}/{codice_acer}): "
            "il database non ha restituito il valore atteso."
        )
    return int(row["progressivo"])


def crea_ricevuta_pdr(
    conn: sqlite3.Connection,
    *,
    receipt_id: str,
    email: str,
    report_id: str,
    artifact_id: str,
    artifact_sha256: str,
    source: str,
    outcome: str,
    load_code: str | None,
    reported_at: str,
    filename: str,
    media_type: str,
    error_detail: str,
    content: bytes,
    sha256: str,
    parser_version: str | None,
    parser_metadata: dict,
    imported_at: str,
) -> None:
    """Registra una ricevuta non modificabile associata a un XML ACER."""

    conn.execute(
        "INSERT INTO pdr_ricevuta "
        "(id, email, report_id, artifact_id, artifact_sha256, fonte, esito, codice_carico, "
        "segnalata_il, nome_file, media_type, dettaglio_errore, contenuto, sha256, "
        "parser_version, parser_metadata, connettore_verificato, importata_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            receipt_id,
            email,
            report_id,
            artifact_id,
            artifact_sha256,
            source,
            outcome,
            load_code,
            reported_at,
            filename,
            media_type,
            error_detail,
            sqlite3.Binary(content),
            sha256,
            parser_version,
            json.dumps(parser_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            imported_at,
        ),
    )


def _deserializza_ricevuta(row: sqlite3.Row) -> dict:
    try:
        parser_metadata = json.loads(row["parser_metadata"])
    except (TypeError, json.JSONDecodeError):
        parser_metadata = {}
    if not isinstance(parser_metadata, dict):
        parser_metadata = {}
    content = row["contenuto"]
    return {
        "id": row["id"],
        "report_id": row["report_id"],
        "artifact_id": row["artifact_id"],
        "artifact_sha256": row["artifact_sha256"],
        "source": row["fonte"],
        "outcome": row["esito"],
        "load_code": row["codice_carico"],
        "reported_at": row["segnalata_il"],
        "filename": row["nome_file"],
        "mime_type": row["media_type"],
        "error_detail": row["dettaglio_errore"],
        "content": bytes(content),
        "sha256": row["sha256"],
        "parser_version": row["parser_version"],
        "parser_metadata": parser_metadata,
        "connector_verified": bool(row["connettore_verificato"]),
        "imported_at": row["importata_il"],
    }


_RICEVUTA_COLUMNS = (
    "id, report_id, artifact_id, artifact_sha256, fonte, esito, codice_carico, segnalata_il, "
    "nome_file, media_type, dettaglio_errore, contenuto, sha256, parser_version, parser_metadata, "
    "connettore_verificato, importata_il"
)


def leggi_ricevuta_pdr(conn: sqlite3.Connection, email: str, receipt_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {_RICEVUTA_COLUMNS} FROM pdr_ricevuta WHERE email = ? AND id = ?",
        (email, receipt_id),
    ).fetchone()
    return _deserializza_ricevuta(row) if row else None


def leggi_ricevuta_pdr_per_report_hash(
    conn: sqlite3.Connection, email: str, report_id: str, sha256: str
) -> dict | None:
    row = conn.execute(
        f"SELECT {_RICEVUTA_COLUMNS} FROM pdr_ricevuta "
        "WHERE email = ? AND report_id = ? AND sha256 = ?",
        (email, report_id, sha256),
    ).fetchone()
    return _deserializza_ricevuta(row) if row else None


def lista_ricevute_pdr(
    conn: sqlite3.Connection, email: str, report_id: str | None = None
) -> list[dict]:
    if report_id:
        rows = conn.execute(
            f"SELECT {_RICEVUTA_COLUMNS} FROM pdr_ricevuta "
            "WHERE email = ? AND report_id = ? ORDER BY importata_il DESC, id DESC",
            (email, report_id),
        )
    else:
        rows = conn.execute(
            f"SELECT {_RICEVUTA_COLUMNS} FROM pdr_ricevuta "
            "WHERE email = ? ORDER BY importata_il DESC, id DESC",
            (email,),
        )
    return [_deserializza_ricevuta(row) for row in rows]


CAMPI_SCADENZA = (
    "id, titolo, categoria, data_scadenza, ricorrenza, stato, riferimento, nota, "
    "modello_chiave, modello_anno, creato_il, aggiornata_il"
)


def _riga_scadenza(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "titolo": row["titolo"],
        "categoria": row["categoria"],
        "data_scadenza": row["data_scadenza"],
        "ricorrenza": row["ricorrenza"],
        "stato": row["stato"],
        "riferimento": row["riferimento"],
        "nota": row["nota"],
        "modello_chiave": row["modello_chiave"],
        "modello_anno": row["modello_anno"],
        "creato_il": row["creato_il"],
        "aggiornata_il": row["aggiornata_il"],
    }


def elenca_scadenze(conn: sqlite3.Connection, email: str) -> list[dict]:
    righe = conn.execute(
        f"SELECT {CAMPI_SCADENZA} FROM agenda_scadenza WHERE email = ? "
        "ORDER BY data_scadenza, creato_il DESC",
        (email,),
    ).fetchall()
    return [_riga_scadenza(r) for r in righe]


def crea_scadenza(conn: sqlite3.Connection, *, scadenza_id: str, email: str, record: dict) -> None:
    conn.execute(
        "INSERT INTO agenda_scadenza (id, email, titolo, categoria, data_scadenza, ricorrenza, "
        "stato, riferimento, nota, modello_chiave, modello_anno, creato_il, aggiornata_il) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            scadenza_id, email, record["titolo"], record["categoria"], record["data_scadenza"],
            record["ricorrenza"], record["stato"], record["riferimento"], record["nota"],
            record.get("modello_chiave"), record.get("modello_anno"),
        ),
    )


def aggiorna_scadenza(conn: sqlite3.Connection, *, email: str, scadenza_id: str, record: dict) -> bool:
    cursore = conn.execute(
        "UPDATE agenda_scadenza SET titolo = ?, categoria = ?, data_scadenza = ?, ricorrenza = ?, "
        "stato = ?, riferimento = ?, nota = ?, aggiornata_il = datetime('now') "
        "WHERE email = ? AND id = ?",
        (
            record["titolo"], record["categoria"], record["data_scadenza"],
            record["ricorrenza"], record["stato"], record["riferimento"], record["nota"],
            email, scadenza_id,
        ),
    )
    return cursore.rowcount > 0


def elimina_scadenza(conn: sqlite3.Connection, email: str, scadenza_id: str) -> bool:
    cursore = conn.execute(
        "DELETE FROM agenda_scadenza WHERE email = ? AND id = ?",
        (email, scadenza_id),
    )
    return cursore.rowcount > 0


def leggi_accesso_sii(conn: sqlite3.Connection, email: str) -> dict | None:
    riga = conn.execute(
        "SELECT url, utente, password, percorso, attivo, ultima_sync, errore_sync "
        "FROM sii_accesso WHERE email = ?",
        (email,),
    ).fetchone()
    if not riga:
        return None
    return {
        "url": riga["url"],
        "utente": riga["utente"],
        "password": riga["password"],
        "percorso": riga["percorso"],
        "attivo": bool(riga["attivo"]),
        "ultima_sync": riga["ultima_sync"],
        "errore_sync": riga["errore_sync"],
    }


def scrivi_accesso_sii(conn: sqlite3.Connection, email: str, record: dict) -> None:
    conn.execute(
        "INSERT INTO sii_accesso (email, url, utente, password, percorso, attivo, aggiornato_il) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT (email) DO UPDATE SET url = excluded.url, utente = excluded.utente, "
        "password = excluded.password, percorso = excluded.percorso, attivo = excluded.attivo, "
        "aggiornato_il = datetime('now')",
        (
            email,
            record["url"],
            record["utente"],
            record["password"],
            record.get("percorso", ""),
            1 if record.get("attivo", True) else 0,
        ),
    )


def registra_esito_sync_sii(
    conn: sqlite3.Connection, email: str, *, quando: str, errore: str = ""
) -> None:
    conn.execute(
        "UPDATE sii_accesso SET ultima_sync = ?, errore_sync = ? WHERE email = ?",
        (quando, errore, email),
    )


def elimina_accesso_sii(conn: sqlite3.Connection, email: str) -> bool:
    cursore = conn.execute("DELETE FROM sii_accesso WHERE email = ?", (email,))
    return cursore.rowcount > 0


def accessi_sii_attivi(conn: sqlite3.Connection) -> list[dict]:
    righe = conn.execute(
        "SELECT email, url, utente, password, percorso, ultima_sync FROM sii_accesso "
        "WHERE attivo = 1"
    ).fetchall()
    return [
        {
            "email": riga["email"],
            "url": riga["url"],
            "utente": riga["utente"],
            "password": riga["password"],
            "percorso": riga["percorso"],
            "ultima_sync": riga["ultima_sync"],
        }
        for riga in righe
    ]
