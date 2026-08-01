"""Registro REMIT, XML ACER validato e audit locale.

Il modulo può produrre soltanto i formati ACER inclusi e validati con XSD. Non
simula mai una ricevuta o un invio: quello è un passo PDR/GME distinto, con
abilitazione e credenziali esterne.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from . import acer_xml, db


WORKSPACE_SCHEMA = "vettore-remit-pdr/1.0"
WORKSPACE_RULESET = "acer-xsd-precheck/2026-08"

REPORT_KINDS = {
    "gas_standard": "Contratto standard · gas",
    "gas_nonstandard": "Contratto non-standard · gas",
    "gas_transport": "Trasporto gas · pre-registrazione",
}

SIDES = {"buy", "sell", "cross"}
ACTION_TYPES = {"new", "modify", "cancel", "error"}
STATUSES = {
    "bozza",
    "validata_localmente",
    "xml_validato_xsd",
    "annullata",
}

MAX_TEXT = 160
MAX_ACER_CODE = 12


class RemitError(ValueError):
    """Errore di dominio traducibile in una risposta HTTP controllata."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


class ConflittoVersione(RemitError):
    """Il record è stato modificato da un'altra sessione."""


def ora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonico(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _testo(value: Any, *, max_len: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:max_len]


def _numero(value: Any, field: str, errors: list[dict[str, str]], *, required: bool = True) -> str:
    testo = _testo(value, max_len=40)
    # Accetta sia la forma italiana 1.234,50 sia la forma tecnica 1234.50;
    # non rimuove ciecamente i punti, altrimenti 33.50 diventerebbe 3350.
    if "," in testo and "." in testo:
        if testo.rfind(",") > testo.rfind("."):
            testo = testo.replace(".", "").replace(",", ".")
        else:
            testo = testo.replace(",", "")
    elif "," in testo:
        testo = testo.replace(",", ".")
    if not testo:
        if required:
            errors.append({"field": field, "message": "Campo obbligatorio."})
        return ""
    try:
        valore = Decimal(testo)
    except InvalidOperation:
        errors.append({"field": field, "message": "Inserisci un numero valido."})
        return ""
    if not valore.is_finite():
        errors.append({"field": field, "message": "Inserisci un numero finito."})
        return ""
    if field == "quantity_mwh" and valore <= 0:
        errors.append({"field": field, "message": "La quantità deve essere maggiore di zero."})
    if field == "price_eur_mwh" and valore < 0:
        errors.append({"field": field, "message": "Il prezzo non può essere negativo."})
    # Decimal normalizzato, senza notazione esponenziale: ripetibile nei file
    # esportati e nei relativi hash.
    return format(valore.normalize(), "f")


def _data(value: Any, field: str, errors: list[dict[str, str]]) -> str:
    testo = _testo(value, max_len=10)
    if not testo:
        errors.append({"field": field, "message": "Campo obbligatorio."})
        return ""
    try:
        parsed = date.fromisoformat(testo)
    except ValueError:
        errors.append({"field": field, "message": "Usa il formato AAAA-MM-GG."})
        return ""
    if parsed > date.today():
        errors.append({"field": field, "message": "La data dell'evento non può essere futura."})
    return parsed.isoformat()


def _required(value: Any, field: str, errors: list[dict[str, str]], label: str, *, max_len: int = MAX_TEXT) -> str:
    testo = _testo(value, max_len=max_len)
    if not testo:
        errors.append({"field": field, "message": f"{label} obbligatorio."})
    return testo


def normalizza_draft(payload: dict[str, Any], *, precedente: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalizza i dati salvabili di una bozza senza promuoverla a valida.

    Una bozza incompleta resta conservabile, così l'utente non perde il lavoro.
    I controlli bloccanti sono svolti da :func:`valida` prima dell'export.
    """

    precedente = precedente or {}
    report_kind = _testo(payload.get("report_kind", precedente.get("report_kind")), max_len=40)
    if report_kind not in REPORT_KINDS:
        report_kind = ""
    side = _testo(payload.get("side", precedente.get("side")), max_len=12)
    if side not in SIDES:
        side = ""
    action = _testo(payload.get("action", precedente.get("action", "new")), max_len=12)
    if action not in ACTION_TYPES:
        action = "new"
    # I valori in questa mappa sono l'unica superficie modificabile dal
    # browser. ID, stati, hash, ricevute e artefatti sono sempre server-side.
    return {
        "report_kind": report_kind,
        "action": action,
        "source_ref": _testo(payload.get("source_ref", precedente.get("source_ref"))),
        "event_at": _testo(payload.get("event_at", precedente.get("event_at")), max_len=10),
        "delivery_point": _testo(payload.get("delivery_point", precedente.get("delivery_point"))),
        "counterparty": _testo(payload.get("counterparty", precedente.get("counterparty"))),
        "counterparty_scheme": _testo(
            payload.get("counterparty_scheme", precedente.get("counterparty_scheme")), max_len=8
        ).lower(),
        "side": side,
        "quantity_mwh": _testo(payload.get("quantity_mwh", precedente.get("quantity_mwh")), max_len=40),
        "quantity_unit": _testo(payload.get("quantity_unit", precedente.get("quantity_unit")), max_len=16),
        "price_eur_mwh": _testo(payload.get("price_eur_mwh", precedente.get("price_eur_mwh")), max_len=40),
        "price_currency": _testo(payload.get("price_currency", precedente.get("price_currency")), max_len=8),
        "acer_code": _testo(payload.get("acer_code", precedente.get("acer_code")), max_len=MAX_ACER_CODE),
        "trading_capacity": _testo(
            payload.get("trading_capacity", precedente.get("trading_capacity")), max_len=4
        ),
        "contract_id": _testo(payload.get("contract_id", precedente.get("contract_id")), max_len=50),
        "contract_date": _testo(payload.get("contract_date", precedente.get("contract_date")), max_len=10),
        "contract_type": _testo(payload.get("contract_type", precedente.get("contract_type")), max_len=20),
        "energy_commodity": _testo(
            payload.get("energy_commodity", precedente.get("energy_commodity")), max_len=4
        ),
        "delivery_start_date": _testo(
            payload.get("delivery_start_date", precedente.get("delivery_start_date")), max_len=10
        ),
        "delivery_end_date": _testo(
            payload.get("delivery_end_date", precedente.get("delivery_end_date")), max_len=10
        ),
        "settlement_method": _testo(
            payload.get("settlement_method", precedente.get("settlement_method")), max_len=4
        ),
        "marketplace_scheme": _testo(
            payload.get("marketplace_scheme", precedente.get("marketplace_scheme")), max_len=8
        ).lower(),
        "marketplace_id": _testo(payload.get("marketplace_id", precedente.get("marketplace_id")), max_len=32),
        "transaction_at": _testo(payload.get("transaction_at", precedente.get("transaction_at")), max_len=40),
        "transaction_id": _testo(payload.get("transaction_id", precedente.get("transaction_id")), max_len=100),
    }


def valida(record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Applica controlli locali di completezza e normalizzazione.

    Non sono le ARIS Data Validation Rules e non certificano la conformità
    regolatoria: la versione delle regole e dello schema ACER va scelta e
    validata dall'RRM prima di un invio reale.
    """

    errors: list[dict[str, str]] = []
    out = normalizza_draft(record, precedente=record)
    if not out["report_kind"]:
        errors.append({"field": "report_kind", "message": "Scegli il tipo di segnalazione."})
    _required(out["source_ref"], "source_ref", errors, "Riferimento contratto")
    out["event_at"] = _data(out["event_at"], "event_at", errors)
    if not out["side"]:
        errors.append({"field": "side", "message": "Scegli acquisto, vendita o cross."})
    out["quantity_mwh"] = _numero(out["quantity_mwh"], "quantity_mwh", errors)
    # Il trasporto gas ha un tracciato distinto: per questo il prezzo resta
    # facoltativo qui e l'export locale lo evidenzia come pre-registrazione.
    out["price_eur_mwh"] = _numero(
        out["price_eur_mwh"],
        "price_eur_mwh",
        errors,
        required=out["report_kind"] != "gas_transport",
    )
    # Oltre ai controlli di base, ogni export Table 1/2 deve avere i campi
    # richiesti dal proprio tracciato. La validazione XSD completa avviene al
    # momento della generazione dell'artefatto XML.
    errors.extend(acer_xml.errori_dati(out))
    unique_errors: list[dict[str, str]] = []
    seen = set()
    for error in errors:
        key = (error.get("field"), error.get("message"))
        if key not in seen:
            unique_errors.append(error)
            seen.add(key)
    return out, unique_errors


def _evento_hash(prev_hash: str, evento: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonico(evento)).encode("utf-8")).hexdigest()


def _crea_evento(
    conn,
    *,
    email: str,
    report_id: str,
    actor: str,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    detail: dict[str, Any],
) -> dict[str, Any]:
    precedente = db.ultimo_hash_evento_remit(conn, email, report_id)
    occurred_at = ora_iso()
    evento = {
        "report_id": report_id,
        "occurred_at": occurred_at,
        "actor": actor,
        "event_type": event_type,
        "from_status": from_status,
        "to_status": to_status,
        "detail": detail,
    }
    event_hash = _evento_hash(precedente, evento)
    db.aggiungi_evento_remit(
        conn,
        email=email,
        report_id=report_id,
        occurred_at=occurred_at,
        actor=actor,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        detail=detail,
        prev_hash=precedente,
        event_hash=event_hash,
    )
    return {**evento, "prev_hash": precedente, "hash": event_hash}


def registra_ricevuta_pdr_importata(
    conn,
    email: str,
    report_id: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Aggiunge all'audit il solo fatto dell'import manuale della ricevuta.

    L'evento non promuove lo stato della segnalazione e non equivale a una
    prova di upload, di consegna PDR o di accettazione ACER: tali evidenze
    richiedono un connettore autorizzato e relative credenziali/risposte.
    Il dettaglio libero non viene duplicato nella catena audit; se necessario
    resta consultabile nell'artefatto ricevuta, indicizzato dalla sua impronta.
    """

    report = db.leggi_report_remit(conn, email, report_id)
    if not report:
        raise RemitError("Segnalazione non trovata.")
    error_detail = str(receipt.get("error_detail") or "")
    return _crea_evento(
        conn,
        email=email,
        report_id=report_id,
        actor=email,
        event_type="PDR_RECEIPT_IMPORTED",
        from_status=report.get("status"),
        to_status=report.get("status"),
        detail={
            "receipt_id": receipt["id"],
            "artifact_id": receipt["artifact_id"],
            "artifact_sha256": receipt["artifact_sha256"],
            "receipt_sha256": receipt["sha256"],
            "source": receipt["source"],
            "outcome": receipt["outcome"],
            "load_code": receipt.get("load_code"),
            "reported_at": receipt["reported_at"],
            "filename": receipt["filename"],
            "mime_type": receipt["mime_type"],
            "error_detail_sha256": hashlib.sha256(error_detail.encode("utf-8")).hexdigest(),
            "connector_verified": False,
            "verification_state": "manual_import_unverified",
            "parser_version": receipt.get("parser_version"),
        },
    )


def _presenta(record: dict[str, Any]) -> dict[str, Any]:
    validato, errors = valida(record)
    return {
        **record,
        "data": validato,
        "validation_errors": errors,
        "is_complete": not errors,
        "workspace_schema": WORKSPACE_SCHEMA,
        "ruleset_version": WORKSPACE_RULESET,
    }


def _preserva_dati(record: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    # id, stato, versione, date e metadati interni non provengono mai dal
    # client; i soli dati modificabili sono quelli del payload locale.
    return {
        **record,
        **normalized,
        "schema_version": WORKSPACE_SCHEMA,
        "ruleset_version": WORKSPACE_RULESET,
    }


def crea_report(conn, email: str, payload: dict[str, Any]) -> dict[str, Any]:
    draft = normalizza_draft(payload)
    now = ora_iso()
    record = {
        "id": str(uuid.uuid4()),
        **draft,
        "status": "bozza",
        "schema_version": WORKSPACE_SCHEMA,
        "ruleset_version": WORKSPACE_RULESET,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    db.crea_report_remit(conn, email, record)
    _crea_evento(
        conn,
        email=email,
        report_id=record["id"],
        actor=email,
        event_type="CREATED",
        from_status=None,
        to_status="bozza",
        detail={"schema_version": WORKSPACE_SCHEMA, "ruleset_version": WORKSPACE_RULESET},
    )
    return _presenta(record)


def lista_report(conn, email: str) -> list[dict[str, Any]]:
    return [_presenta(record) for record in db.leggi_report_remit_tutti(conn, email)]


def leggi_report(conn, email: str, report_id: str) -> dict[str, Any] | None:
    record = db.leggi_report_remit(conn, email, report_id)
    return _presenta(record) if record else None


def aggiorna_bozza(
    conn,
    email: str,
    report_id: str,
    payload: dict[str, Any],
    expected_version: int | None,
) -> dict[str, Any]:
    record = db.leggi_report_remit(conn, email, report_id)
    if not record:
        raise RemitError("Segnalazione non trovata.")
    if record["status"] not in {"bozza", "validata_localmente"}:
        raise RemitError("L'XML esportato non può essere sovrascritto: crea una correzione.")
    if expected_version is not None and record["version"] != expected_version:
        raise ConflittoVersione("La segnalazione è cambiata in un'altra sessione. Ricarica il registro.")
    previous_status = record["status"]
    updated = _preserva_dati(record, normalizza_draft(payload, precedente=record))
    updated["status"] = "bozza"
    updated["updated_at"] = ora_iso()
    try:
        version = db.aggiorna_report_remit(conn, email, report_id, updated, expected_version=record["version"])
    except db.ConflittoStato as exc:
        raise ConflittoVersione(str(exc)) from exc
    updated["version"] = version
    _crea_evento(
        conn,
        email=email,
        report_id=report_id,
        actor=email,
        event_type="UPDATED",
        from_status=previous_status,
        to_status="bozza",
        detail={"reason": "draft_updated"},
    )
    return _presenta(updated)


def valida_report(
    conn,
    email: str,
    report_id: str,
    payload: dict[str, Any],
    expected_version: int | None,
) -> dict[str, Any]:
    record = db.leggi_report_remit(conn, email, report_id)
    if not record:
        raise RemitError("Segnalazione non trovata.")
    if record["status"] not in {"bozza", "validata_localmente"}:
        raise RemitError("L'XML esportato non può essere validato di nuovo: crea una correzione.")
    if expected_version is not None and record["version"] != expected_version:
        raise ConflittoVersione("La segnalazione è cambiata in un'altra sessione. Ricarica il registro.")
    updated = _preserva_dati(record, normalizza_draft(payload, precedente=record))
    normalized, errors = valida(updated)
    previous_status = record["status"]
    updated = _preserva_dati(updated, normalized)
    updated["status"] = "validata_localmente" if not errors else "bozza"
    updated["updated_at"] = ora_iso()
    try:
        version = db.aggiorna_report_remit(conn, email, report_id, updated, expected_version=record["version"])
    except db.ConflittoStato as exc:
        raise ConflittoVersione(str(exc)) from exc
    updated["version"] = version
    _crea_evento(
        conn,
        email=email,
        report_id=report_id,
        actor=email,
        event_type="VALIDATED" if not errors else "VALIDATION_FAILED",
        from_status=previous_status,
        to_status=updated["status"],
        detail={"errors": errors},
    )
    return _presenta(updated)


@contextmanager
def _transazione_export(conn):
    """Rende atomici artefatto, progressivo, record ed evento di export.

    Gli handler HTTP trasformano gli errori di dominio in risposte 4xx dentro
    il blocco di connessione SQLite. Senza un savepoint, una collisione di
    versione dopo l'inserimento dell'artefatto potrebbe quindi essere
    confermata dal context manager esterno. Il savepoint elimina anche file e
    progressivi parziali prima che l'errore raggiunga l'handler.
    """

    conn.execute("SAVEPOINT remit_export")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT remit_export")
        conn.execute("RELEASE SAVEPOINT remit_export")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT remit_export")


def esporta_report(
    conn,
    email: str,
    report_id: str,
    expected_version: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _transazione_export(conn):
        return _esporta_report_atomico(conn, email, report_id, expected_version)


def _esporta_report_atomico(
    conn,
    email: str,
    report_id: str,
    expected_version: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = db.leggi_report_remit(conn, email, report_id)
    if not record:
        raise RemitError("Segnalazione non trovata.")
    if record["status"] != "validata_localmente":
        raise RemitError("Convalida localmente la segnalazione prima dell'export.")
    if expected_version is not None and record["version"] != expected_version:
        raise ConflittoVersione("La segnalazione è cambiata in un'altra sessione. Ricarica il registro.")
    payload, errors = valida(record)
    if errors:
        raise RemitError("La segnalazione non supera i controlli richiesti per il tracciato ACER.", errors)
    try:
        document = acer_xml.genera_xml(payload)
    except acer_xml.AcerXmlError as error:
        raise RemitError(str(error), error.errors) from error
    file_day = datetime.now(ZoneInfo("Europe/Rome")).date()
    progressivo = db.prossimo_progressivo_pdr(
        conn,
        data_file=file_day.isoformat(),
        schema_nome=document.schema_name,
        schema_versione=document.schema_version,
        codice_acer=payload["acer_code"],
    )
    filename = acer_xml.nome_file_pdr(document, payload["acer_code"], progressivo, file_day)
    artifact_id = str(uuid.uuid4())
    db.crea_artifact_remit(
        conn,
        artifact_id=artifact_id,
        email=email,
        report_id=report_id,
        kind="acer_xml_xsd_validated",
        media_type="application/xml",
        filename=filename,
        content=document.xml,
        sha256=document.xml_sha256,
    )
    updated = {
        **record,
        **payload,
        "status": "xml_validato_xsd",
        "xml_artifact_id": artifact_id,
        "xml_schema_name": document.schema_name,
        "xml_schema_version": document.schema_version,
        "xml_schema_sha256": document.schema_sha256,
        "xml_filename": filename,
        "xml_progressive": progressivo,
        "updated_at": ora_iso(),
    }
    try:
        version = db.aggiorna_report_remit(conn, email, report_id, updated, expected_version=record["version"])
    except db.ConflittoStato as exc:
        raise ConflittoVersione(str(exc)) from exc
    updated["version"] = version
    _crea_evento(
        conn,
        email=email,
        report_id=report_id,
        actor=email,
        event_type="ACER_XML_EXPORTED_XSD_VALID",
        from_status="validata_localmente",
        to_status="xml_validato_xsd",
        detail={
            "artifact_id": artifact_id,
            "sha256": document.xml_sha256,
            "filename": filename,
            "size_bytes": document.size_bytes,
            "schema_name": document.schema_name,
            "schema_version": document.schema_version,
            "schema_sha256": document.schema_sha256,
            "pdr_progressive": progressivo,
        },
    )
    artifact = {
        "id": artifact_id,
        "kind": "acer_xml_xsd_validated",
        "media_type": "application/xml",
        "filename": filename,
        "sha256": document.xml_sha256,
        "size_bytes": document.size_bytes,
        "schema_name": document.schema_name,
        "schema_version": document.schema_version,
        "schema_sha256": document.schema_sha256,
        "xsd_valid": True,
    }
    return _presenta(updated), artifact


def eventi_report(conn, email: str, report_id: str) -> list[dict[str, Any]]:
    if not db.leggi_report_remit(conn, email, report_id):
        raise RemitError("Segnalazione non trovata.")
    return db.leggi_eventi_remit(conn, email, report_id)


def leggi_artifact(conn, email: str, artifact_id: str) -> dict[str, Any] | None:
    artifact = db.leggi_artifact_remit(conn, email, artifact_id)
    if not artifact:
        return None
    return artifact


def verifica_integrita_artifact(conn, email: str, artifact: dict[str, Any]) -> None:
    """Verifica l'artefatto rispetto a record, hash e XSD ancora presenti."""

    report = db.leggi_report_remit(conn, email, artifact.get("report_id", ""))
    if not report:
        raise RemitError("Il report associato all'artefatto XML non è disponibile.")
    if artifact.get("kind") != "acer_xml_xsd_validated" or artifact.get("media_type") != "application/xml":
        raise RemitError("L'artefatto non ha il tipo XML ACER validato atteso.")
    try:
        acer_xml.verifica_xml_archiviato(
            str(report.get("report_kind") or ""),
            artifact.get("content"),
            str(artifact.get("sha256") or ""),
        )
    except acer_xml.AcerXmlError as error:
        raise RemitError(str(error), error.errors) from error


def richiesta_invio_reale() -> None:
    """Blocco esplicito: non è lecito simulare un invio RRM/ACER riuscito."""

    raise RemitError(
        "Invio reale non configurato: il file XML è un artefatto validato XSD, non una ricevuta. "
        "Per PDR/GME servono contratto o abilitazione, credenziali a due livelli e test PDR superati."
    )


def importa_legacy_se_necessario(conn, email: str) -> int:
    """Importa una sola volta il vecchio ``remList`` senza fidarsi dei suoi stati.

    Il registro precedente era controllabile dal browser e non conservava
    ricevute: ogni riga storica diventa perciò una bozza da verificare. Il raw
    originale è conservato nell'evento di importazione, non reinterpretato come
    "inviato" o "accettato".
    """

    old_list = db.leggi_stato(conn, email).get("remList")
    if not isinstance(old_list, list):
        return 0
    source_hash = hashlib.sha256(_canonico(old_list).encode("utf-8")).hexdigest()
    if db.migrazione_remit_eseguita(conn, email, source_hash):
        return 0
    imported = 0
    for index, raw in enumerate(old_list):
        if not isinstance(raw, dict):
            continue
        tipo = _testo(raw.get("tipo"))
        kind = "gas_standard" if tipo.lower().startswith("standard") else "gas_nonstandard"
        now = ora_iso()
        record = {
            "id": str(uuid.uuid4()),
            **normalizza_draft(
                {
                    "report_kind": kind,
                    "source_ref": raw.get("rif", ""),
                    "quantity_mwh": raw.get("qta", ""),
                    "price_eur_mwh": raw.get("prezzo", ""),
                }
            ),
            "status": "bozza",
            "schema_version": WORKSPACE_SCHEMA,
            "ruleset_version": WORKSPACE_RULESET,
            "created_at": now,
            "updated_at": now,
            "legacy_unverified": True,
        }
        db.crea_report_remit(conn, email, record)
        _crea_evento(
            conn,
            email=email,
            report_id=record["id"],
            actor=email,
            event_type="LEGACY_IMPORTED_UNVERIFIED",
            from_status=None,
            to_status="bozza",
            detail={"legacy_index": index, "legacy_record": raw},
        )
        imported += 1
    db.registra_migrazione_remit(conn, email, source_hash)
    return imported
