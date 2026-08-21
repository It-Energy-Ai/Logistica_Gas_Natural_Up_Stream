"""Preflight e confine di integrazione per la PDR del GME.

La PDR riceve file già nel formato ACER; perciò questo modulo controlla sia la
readiness operativa sia l'esistenza di un artefatto XML validato XSD. Non
conserva password/PIN/OTP e non finge mai di aver eseguito un upload.
"""

from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from lxml import etree

from . import acer_xml, db, remit


PDR_ENDPOINTS = {
    "test": "https://provepdr.ipex.it",
    "production": "https://pdr.ipex.it",
}

# Fonte: pagina GME "Corrispettivi e settlement", valida 01/01–31/12/2026.
# Il valore è mostrato come riferimento configurabile, non calcolato come
# importo dovuto né usato per alcuna fatturazione.
PDR_FEES_2026 = {
    "effective_period": "2026-01-01/2026-12-31",
    "external_upload_annual_eur": 300,
    "record_fee_tiers_eur": [
        {"min": 1, "max": 10, "eur": 250},
        {"min": 11, "max": 100, "eur": 500},
        {"min": 101, "max": 1_000, "eur": 1_000},
        {"min": 1_001, "max": 10_000, "eur": 2_000},
        {"min": 10_001, "max": 100_000, "eur": 4_000},
        {"min": 100_001, "max": 1_000_000, "eur": 8_000},
        {"min": 1_000_001, "max": 10_000_000, "eur": 16_000},
        {"min": 10_000_001, "max": None, "eur": 32_000},
    ],
}

PROFILE_KEYS = {
    "environment",
    "channel",
    "gme_operator_code",
    "pdr_contract_reference",
    "test_access_requested",
    "two_factor_ready",
    "registered_acer_code",
}

# Il contenuto è conservato come BLOB immutabile; base64 serve unicamente per
# trasportarlo nel JSON di import. Le ricevute PDR/ACER sono normalmente molto
# più piccole dell'XML regolatorio, quindi 2 MiB lascia margine senza rendere
# l'endpoint un canale di deposito file generico.
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BASE64_CHARS = ((MAX_RECEIPT_BYTES + 2) // 3) * 4 + 4
MAX_RECEIPT_DETAIL = 4_000
MAX_RECEIPT_FILENAME = 255
MAX_RECEIPT_MIME = 120

RECEIPT_SOURCES = {
    "pdr": "pdr",
    "pdr_portal": "pdr",
    "acer": "acer",
    "acer_archive": "acer",
}
RECEIPT_OUTCOMES = {
    "pdr_rejected",
    "pdr_accepted",
    "pdr_partial",
    "acer_transmitted",
    "acer_accepted",
    "acer_rejected",
    "technical_retry",
    "unknown",
}
MIME_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
# Il vincolo SQLite usa [0-9]; usare lo stesso alfabeto qui evita che cifre
# Unicode superino il controllo applicativo e causino un IntegrityError 500.
LOAD_CODE_RE = re.compile(r"^[0-9]{6}$")
PIPE_NAMESPACE = "urn:XML-PIPE"
PIPE_ACK_ROOT = f"{{{PIPE_NAMESPACE}}}PIPEFunctionalAcknowledgement"
PIPE_ACK_PARSER_VERSION = "gme-pdr-functional-ack/1"


class PdrError(ValueError):
    """Errore controllato del connettore PDR."""


class PdrNotFoundError(PdrError):
    """La risorsa non esiste oppure non appartiene all'utente in sessione."""


def _text(value: Any, max_len: int = 120) -> str:
    return str(value or "").strip()[:max_len]


def _receipt_text(value: Any, field: str, max_len: int, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise PdrError(f"{field} deve essere una stringa JSON.")
    text = value.strip()
    if required and not text:
        raise PdrError(f"{field} obbligatorio.")
    if len(text) > max_len:
        raise PdrError(f"{field} supera il limite di {max_len} caratteri.")
    return text


def _normalizza_reported_at(value: Any) -> str:
    raw = _receipt_text(value, "reported_at", 80)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise PdrError("reported_at deve essere ISO 8601 con data, ora e fuso orario.") from exc
    if "T" not in raw or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PdrError("reported_at deve includere il fuso orario, ad esempio 2026-08-01T10:15:00Z.")
    return parsed.isoformat().replace("+00:00", "Z")


def _normalizza_source(value: Any) -> str:
    raw = _receipt_text(value, "source", 32).lower()
    source = RECEIPT_SOURCES.get(raw)
    if not source:
        raise PdrError("source deve essere pdr o acer (sono accettati anche pdr_portal e acer_archive).")
    return source


def _normalizza_outcome(value: Any, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    raw = _receipt_text(value, "outcome", 32, required=required).lower()
    if not raw and not required:
        return None
    if raw not in RECEIPT_OUTCOMES:
        raise PdrError("outcome non supportato per una ricevuta PDR/ACER.")
    return raw


def _normalizza_load_code(value: Any) -> str | None:
    if value is None or value == "":
        return None
    code = _receipt_text(value, "load_code", 6)
    if not LOAD_CODE_RE.fullmatch(code):
        raise PdrError("load_code deve contenere esattamente 6 cifre.")
    return code


def _normalizza_filename(value: Any) -> str:
    filename = _receipt_text(value, "filename", MAX_RECEIPT_FILENAME)
    if any(ord(char) < 32 for char in filename) or "/" in filename or "\\" in filename:
        raise PdrError("filename deve essere un nome file semplice, senza percorsi o caratteri di controllo.")
    return filename


def _normalizza_mime(value: Any) -> str:
    mime = _receipt_text(value, "mime_type", MAX_RECEIPT_MIME).lower()
    if not MIME_RE.fullmatch(mime):
        raise PdrError("mime_type deve essere un media type valido, ad esempio application/xml.")
    return mime


def _normalizza_error_detail(payload: dict[str, Any]) -> str:
    """Accetta ``detail`` dalla UI e ``error_detail`` dall'API esplicita."""

    has_detail = "detail" in payload
    has_error_detail = "error_detail" in payload
    if has_detail and has_error_detail and payload["detail"] != payload["error_detail"]:
        raise PdrError("detail e error_detail non possono contenere valori diversi.")
    value = payload["detail"] if has_detail else payload.get("error_detail", "")
    return _receipt_text(value, "detail", MAX_RECEIPT_DETAIL, required=False)


def _decode_receipt_content(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise PdrError("content_base64 obbligatorio e non vuoto.")
    if len(value) > MAX_RECEIPT_BASE64_CHARS:
        raise PdrError(f"La ricevuta supera il limite di {MAX_RECEIPT_BYTES} byte.")
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise PdrError("content_base64 non è base64 valido.") from exc
    if not content:
        raise PdrError("La ricevuta importata non può essere vuota.")
    if len(content) > MAX_RECEIPT_BYTES:
        raise PdrError(f"La ricevuta supera il limite di {MAX_RECEIPT_BYTES} byte.")
    return content


def _parse_pdr_functional_ack(content: bytes) -> dict[str, Any] | None:
    """Riconosce prudentemente la ricevuta GME ``PIPEFunctionalAcknowledgement``.

    Il parser è volontariamente ristretto al namespace e alla root documentati
    da GME. Un file XML qualsiasi, una ricevuta ACER o uno ZIP non sono
    interpretati come esiti ufficiali dal progetto.
    """

    try:
        root = etree.fromstring(
            content,
            parser=etree.XMLParser(
                resolve_entities=False,
                no_network=True,
                load_dtd=False,
                dtd_validation=False,
                attribute_defaults=False,
                recover=False,
                huge_tree=False,
            ),
        )
    except (etree.XMLSyntaxError, ValueError):
        return None
    if root.tag != PIPE_ACK_ROOT:
        return None

    status = _text(root.get("Status"), 32)
    outcome = {
        "accept": "pdr_accepted",
        "reject": "pdr_rejected",
        "partial": "pdr_partial",
    }.get(status.lower(), "unknown")
    original_reference = _text(root.get("OriginalReferenceNumber"), MAX_RECEIPT_FILENAME)
    creation_date = _text(root.get("CreationDate"), 80)
    parsed_reported_at = None
    if creation_date:
        try:
            parsed_reported_at = _normalizza_reported_at(creation_date)
        except PdrError:
            # Il raw resta nel file immutabile; non inventiamo il fuso se GME
            # non avesse fornito un valore ISO con timezone riconoscibile.
            parsed_reported_at = None
    reasons = []
    for element in root.iter():
        if isinstance(element.tag, str) and etree.QName(element.tag).localname == "ReasonText":
            reason = " ".join("".join(element.itertext()).split())
            if reason:
                reasons.append(reason)
    reason_detail = "\n".join(reasons)[:MAX_RECEIPT_DETAIL]
    metadata: dict[str, Any] = {
        "status": status or None,
        "original_reference_number": original_reference or None,
        "creation_date_raw": creation_date or None,
    }
    if parsed_reported_at:
        metadata["reported_at_derived"] = parsed_reported_at
    if reason_detail:
        metadata["reason_text_detected"] = True
    return {
        "outcome": outcome,
        "reported_at": parsed_reported_at,
        "error_detail": reason_detail or None,
        "metadata": metadata,
    }


def _artifact_xml_del_report(
    conn,
    email: str,
    report_id: str,
    requested_artifact_id: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = remit.leggi_report(conn, email, report_id)
    if not report:
        raise PdrNotFoundError("Segnalazione REMIT non trovata.")
    artifact_id = report.get("xml_artifact_id")
    if not artifact_id:
        raise PdrError("La ricevuta deve essere associata a un XML ACER esportato del report.")
    if requested_artifact_id is not None:
        provided = _receipt_text(requested_artifact_id, "artifact_id", 80)
        if provided != artifact_id:
            raise PdrError("artifact_id deve coincidere con l'XML ACER esportato per il report indicato.")
    artifact = remit.leggi_artifact(conn, email, artifact_id)
    if not artifact:
        raise PdrError("L'artefatto XML ACER associato al report non è reperibile.")
    if artifact["report_id"] != report_id or artifact["kind"] != "acer_xml_xsd_validated":
        raise PdrError("La ricevuta può essere associata solo all'artefatto XML ACER validato del report.")
    try:
        remit.verifica_integrita_artifact(conn, email, artifact)
    except remit.RemitError as exc:
        raise PdrError(f"L'XML ACER associato non supera il controllo di integrità: {exc}") from exc
    return report, artifact


@contextmanager
def _transazione_ricevuta(conn):
    """Mantiene atomici inserimento immutabile e relativo evento audit."""

    # BEGIN IMMEDIATE e non SAVEPOINT: con WAL una transazione differita fissa
    # lo snapshot sulla SELECT di idempotenza e la INSERT successiva fallisce
    # con SQLITE_BUSY_SNAPSHOT, che busy_timeout non ritenta.
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def presenta_ricevuta(receipt: dict[str, Any]) -> dict[str, Any]:
    """Metadati JSON: il contenuto raw è scaricabile solo dall'endpoint dedicato."""

    return {
        "id": receipt["id"],
        "report_id": receipt["report_id"],
        "artifact_id": receipt["artifact_id"],
        "artifact_sha256": receipt["artifact_sha256"],
        "source": receipt["source"],
        "outcome": receipt["outcome"],
        "load_code": receipt["load_code"],
        "reported_at": receipt["reported_at"],
        "filename": receipt["filename"],
        "mime_type": receipt["mime_type"],
        "error_detail": receipt["error_detail"],
        "sha256": receipt["sha256"],
        "size_bytes": len(receipt["content"]),
        "parser_version": receipt["parser_version"],
        "parser_metadata": receipt["parser_metadata"],
        # L'import è manuale: anche un documento con source="pdr" non è una
        # prova che il connettore abbia effettuato upload o verifiche remote.
        "connector_verified": False,
        "verification_state": "manual_import_unverified",
        "imported_at": receipt["imported_at"],
    }


def importa_ricevuta(conn, email: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Importa una ricevuta PDR/ACER e la lega all'XML ACER del report.

    Restituisce ``(ricevuta, idempotente)``. Le importazioni identiche per lo
    stesso report non modificano mai la ricevuta già conservata né aggiungono
    un secondo evento audit.
    """

    allowed = {
        "report_id",
        "artifact_id",
        "source",
        "outcome",
        "load_code",
        "reported_at",
        "detail",
        "error_detail",
        "filename",
        "mime_type",
        "content_base64",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise PdrError("Campi ricevuta non supportati: " + ", ".join(unknown))
    report_id = _receipt_text(payload.get("report_id"), "report_id", 80)
    source = _normalizza_source(payload.get("source"))
    content = _decode_receipt_content(payload.get("content_base64"))
    content_sha256 = hashlib.sha256(content).hexdigest()
    manual_outcome = _normalizza_outcome(payload.get("outcome"), required=False)
    manual_reported_at = _normalizza_reported_at(payload.get("reported_at"))
    filename = _normalizza_filename(payload.get("filename"))
    mime_type = _normalizza_mime(payload.get("mime_type"))
    load_code = _normalizza_load_code(payload.get("load_code"))
    error_detail = _normalizza_error_detail(payload)
    # Il riconoscimento non dipende da come l'operatore dichiara la fonte: se
    # il file È un acknowledgement GME, il suo Status prevale comunque.
    # Legandolo a source="pdr" bastava dichiarare "acer" per far passare come
    # accettata una ricevuta che il GME aveva rifiutato.
    parsed = _parse_pdr_functional_ack(content)
    parser_version = None
    parser_metadata: dict[str, Any] = {}

    if parsed and source != "pdr":
        # Il documento è un ack GME ma è stato dichiarato di altra fonte:
        # l'incoerenza va segnalata, non risolta in silenzio.
        raise PdrError(
            "Il file è un acknowledgement funzionale GME/PDR: importalo dichiarando la fonte «pdr»."
        )

    if parsed:
        parser_version = PIPE_ACK_PARSER_VERSION
        parsed_outcome = parsed["outcome"]
        # Una ricevuta GME riconosciuta è più autorevole del campo manuale.
        # ``unknown`` è l'unico valore manuale che può essere promosso dal
        # parser senza nascondere un conflitto dichiarato dall'operatore.
        if manual_outcome not in {None, "unknown", parsed_outcome}:
            raise PdrError("L'outcome dichiarato non coincide con lo Status della ricevuta XML PDR.")
        outcome = parsed_outcome
        if parsed.get("reported_at"):
            manual_reported_at = parsed["reported_at"]
        if parsed.get("error_detail"):
            error_detail = parsed["error_detail"]
        parser_metadata = dict(parsed["metadata"])
        parser_metadata["outcome_derived"] = True
    else:
        outcome = manual_outcome
    if outcome is None:
        raise PdrError("outcome obbligatorio quando la ricevuta non contiene un acknowledgement PDR riconosciuto.")

    report, artifact = _artifact_xml_del_report(
        conn, email, report_id, payload.get("artifact_id")
    )
    # Il riferimento originale della ricevuta GME può essere utile nella
    # riconciliazione, ma non è trattato come prova se non coincide col file
    # esportato localmente e non modifica mai l'associazione già validata.
    original_reference = parser_metadata.get("original_reference_number")
    if original_reference:
        parser_metadata["original_reference_matches_artifact"] = (
            original_reference == artifact["filename"]
        )

    with _transazione_ricevuta(conn):
        existing = db.leggi_ricevuta_pdr_per_report_hash(conn, email, report_id, content_sha256)
        if existing:
            return presenta_ricevuta(existing), True
        receipt_id = str(uuid.uuid4())
        imported_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
        try:
            db.crea_ricevuta_pdr(
                conn,
                receipt_id=receipt_id,
                email=email,
                report_id=report_id,
                artifact_id=artifact["id"],
                artifact_sha256=artifact["sha256"],
                source=source,
                outcome=outcome,
                load_code=load_code,
                reported_at=manual_reported_at,
                filename=filename,
                media_type=mime_type,
                error_detail=error_detail,
                content=content,
                sha256=content_sha256,
                parser_version=parser_version,
                parser_metadata=parser_metadata,
                imported_at=imported_at,
            )
        except sqlite3.IntegrityError:
            # Due richieste simultanee con lo stesso documento possono
            # arrivare dopo il controllo precedente: la UNIQUE garantisce
            # comunque l'idempotenza senza duplicare l'evento audit.
            existing = db.leggi_ricevuta_pdr_per_report_hash(conn, email, report_id, content_sha256)
            if existing:
                return presenta_ricevuta(existing), True
            raise
        receipt = db.leggi_ricevuta_pdr(conn, email, receipt_id)
        if receipt is None:  # pragma: no cover - garantito dall'INSERT appena eseguito
            raise PdrError("Ricevuta PDR non trovata dopo l'inserimento.")
        remit.registra_ricevuta_pdr_importata(conn, email, report_id, receipt)
    return presenta_ricevuta(receipt), False


def lista_ricevute(conn, email: str, report_id: str | None = None) -> list[dict[str, Any]]:
    if report_id:
        report_id = _receipt_text(report_id, "report_id", 80)
        if not remit.leggi_report(conn, email, report_id):
            raise PdrNotFoundError("Segnalazione REMIT non trovata.")
    return [presenta_ricevuta(receipt) for receipt in db.lista_ricevute_pdr(conn, email, report_id)]


def leggi_ricevuta(conn, email: str, receipt_id: str) -> dict[str, Any] | None:
    return db.leggi_ricevuta_pdr(conn, email, receipt_id)


def _boolean(payload: dict[str, Any], field: str, previous: dict[str, Any]) -> bool:
    """Accetta solo booleani JSON, evitando che la stringa ``\"false\"`` diventi vera."""

    value = payload[field] if field in payload else previous.get(field, False)
    if not isinstance(value, bool):
        raise PdrError(f"{field} deve essere un booleano JSON.")
    return value


def default_profile() -> dict[str, Any]:
    return {
        "environment": "test",
        "channel": "portal",
        "gme_operator_code": "",
        "pdr_contract_reference": "",
        "test_access_requested": False,
        "two_factor_ready": False,
        "registered_acer_code": "",
    }


def normalizza_profile(payload: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or default_profile()
    environment = _text(payload.get("environment", previous.get("environment")), 20)
    channel = _text(payload.get("channel", previous.get("channel")), 20)
    # Sostituire in silenzio un valore fuori lista farebbe credere
    # all'operatore di aver salvato la produzione mentre resta sul test.
    if environment and environment not in PDR_ENDPOINTS:
        raise PdrError(f"Ambiente PDR non valido: ammessi {', '.join(sorted(PDR_ENDPOINTS))}.")
    if channel and channel not in {"portal", "web_service"}:
        raise PdrError("Canale PDR non valido: ammessi «portal» e «web_service».")
    return {
        "environment": environment or "test",
        "channel": channel or "portal",
        "gme_operator_code": _text(payload.get("gme_operator_code", previous.get("gme_operator_code")), 64),
        "pdr_contract_reference": _text(
            payload.get("pdr_contract_reference", previous.get("pdr_contract_reference")), 120
        ),
        "test_access_requested": _boolean(payload, "test_access_requested", previous),
        "two_factor_ready": _boolean(payload, "two_factor_ready", previous),
        "registered_acer_code": _text(
            payload.get("registered_acer_code", previous.get("registered_acer_code")), 12
        ),
    }


def profile(conn, email: str) -> dict[str, Any]:
    saved = db.leggi_profilo_pdr(conn, email)
    return normalizza_profile(saved or {})


def salva_profile(conn, email: str, payload: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(payload) - PROFILE_KEYS)
    if unknown:
        raise PdrError(
            "Il profilo PDR non conserva credenziali o campi non previsti: " + ", ".join(unknown)
        )
    saved = normalizza_profile(payload, previous=profile(conn, email))
    db.scrivi_profilo_pdr(conn, email, saved)
    return saved


def stato(conn, email: str) -> dict[str, Any]:
    saved = profile(conn, email)
    return {
        "mode": "xml-generation-and-pdr-preflight",
        "profile": saved,
        "endpoint": PDR_ENDPOINTS[saved["environment"]],
        "live_submission": False,
        "schemas": acer_xml.catalogo_schemi(),
        "fees": PDR_FEES_2026,
        "notice": (
            "Il progetto genera XML ACER Table 1 V3 e Table 2 V1 validati XSD; "
            "nessuna credenziale PDR è archiviata e nessun file viene inviato al GME."
        ),
    }


def preflight(conn, email: str, report_id: str) -> dict[str, Any]:
    report = remit.leggi_report(conn, email, report_id)
    if not report:
        raise PdrError("Segnalazione REMIT non trovata.")
    saved = profile(conn, email)
    issues: list[dict[str, str]] = []

    if not saved["gme_operator_code"]:
        issues.append({"code": "GME_OPERATOR", "message": "Inserisci l'identificativo dell'operatore GME."})
    if not report["data"].get("acer_code"):
        issues.append({"code": "CEREMP", "message": "Completa il codice ACER/CEREMP nella segnalazione."})
    if not saved["registered_acer_code"]:
        issues.append(
            {
                "code": "REGISTERED_ACER_CODE",
                "message": "Registra il codice ACER/CEREMP abilitato nel rapporto PDR/GME.",
            }
        )
    elif saved["registered_acer_code"] != report["data"].get("acer_code"):
        issues.append(
            {
                "code": "ACER_CODE_MISMATCH",
                "message": "Il codice ACER nel file deve coincidere con quello abilitato presso PDR/GME.",
            }
        )
    if saved["environment"] == "test":
        if not saved["test_access_requested"]:
            issues.append(
                {
                    "code": "TEST_ACCESS",
                    "message": "Richiedi le credenziali distinte per la PDR di test al GME.",
                }
            )
    else:
        if not saved["pdr_contract_reference"]:
            issues.append(
                {
                    "code": "PDR_CONTRACT",
                    "message": "Registra il riferimento del contratto di data reporting PDR.",
                }
            )
        if not saved["two_factor_ready"]:
            issues.append(
                {
                    "code": "TWO_FACTOR",
                    "message": "L'accesso PDR richiede autenticazione a due livelli: abilitala presso il GME.",
                }
            )
    if report["status"] != "xml_validato_xsd":
        issues.append(
            {
                "code": "ACER_XML",
                "message": "Genera l'artefatto XML ACER e supera la validazione XSD prima del passaggio PDR.",
            }
        )
    artifact = None
    artifact_id = report.get("xml_artifact_id")
    if artifact_id:
        artifact = remit.leggi_artifact(conn, email, artifact_id)
    if not artifact:
        issues.append({"code": "XML_ARTIFACT", "message": "Artefatto XML ACER non reperibile."})
    else:
        if artifact["kind"] != "acer_xml_xsd_validated" or artifact["media_type"] != "application/xml":
            issues.append({"code": "XML_ARTIFACT", "message": "L'artefatto non è un XML ACER validato XSD."})
        if len(artifact["content"].encode("utf-8")) > acer_xml.PDR_MAX_FILE_BYTES:
            issues.append({"code": "PDR_SIZE", "message": "Il file supera il limite PDR di 10 MB."})
        expected_schema = acer_xml.schema_per_tipo(report["data"].get("report_kind", ""))
        expected_name = (
            rf"\d{{8}}_{re.escape(expected_schema['schema_name'])}_{re.escape(expected_schema['schema_version'])}_"
            rf"{re.escape(report['data'].get('acer_code', ''))}_\d+\.XML"
        ) if expected_schema else ""
        if expected_schema and not re.fullmatch(expected_name, artifact["filename"]):
            issues.append({"code": "PDR_FILENAME", "message": "Il nome file non rispetta lo schema/versione PDR attesi."})
        try:
            remit.verifica_integrita_artifact(conn, email, artifact)
        except remit.RemitError as error:
            issues.append(
                {
                    "code": "XML_INTEGRITY",
                    "message": f"Integrità o validazione XSD dell'artefatto non verificata: {error}",
                }
            )

    # Le informazioni di profilo sono dichiarazioni dell'operatore, non una
    # prova di autenticazione GME, di upload o di ricezione ACER. Finché non
    # esisterà un connettore autorizzato che registri tale prova, il progetto
    # non può onestamente marcare alcun file come pronto all'upload PDR.
    manual_prerequisites_declared = not issues
    verification_issue = {
        "code": "PDR_ACCESS_UNVERIFIED",
        "message": (
            "Le condizioni dichiarate non dimostrano l'accesso PDR né un test GME riuscito; "
            "l'upload resta un'operazione manuale esterna finché il connettore autorizzato non è configurato."
        ),
    }
    return {
        "report_id": report_id,
        "environment": saved["environment"],
        "endpoint": PDR_ENDPOINTS[saved["environment"]],
        "channel": saved["channel"],
        "manual_prerequisites_declared": manual_prerequisites_declared,
        "xml_ready_for_manual_upload": manual_prerequisites_declared,
        "upload_ready": False,
        "issues": [*issues, verification_issue],
        "artifact": (
            {
                "id": artifact["id"],
                "filename": artifact["filename"],
                "sha256": artifact["sha256"],
                "size_bytes": len(artifact["content"].encode("utf-8")),
            }
            if artifact
            else None
        ),
        "next_step": (
            "Le condizioni locali sono coerenti, ma serve un test PDR/GME verificato e la relativa ricevuta "
            "prima di considerare disponibile l'upload."
            if manual_prerequisites_declared
            else "Correggi i controlli elencati, poi ripeti il preflight PDR."
        ),
    }


def invio_reale_non_configurato() -> None:
    raise PdrError(
        "Connettore PDR non configurato: il file XML è pronto al preflight ma non è stato caricato. "
        "Servono le specifiche web-service/endpoint abilitate dal GME, contratto o accesso test, "
        "autenticazione a due livelli e test PDR riusciti prima di qualunque invio reale."
    )
