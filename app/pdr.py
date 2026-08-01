"""Preflight e confine di integrazione per la PDR del GME.

La PDR riceve file già nel formato ACER; perciò questo modulo controlla sia la
readiness operativa sia l'esistenza di un artefatto XML validato XSD. Non
conserva password/PIN/OTP e non finge mai di aver eseguito un upload.
"""

from __future__ import annotations

import re
from typing import Any

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


class PdrError(ValueError):
    """Errore controllato del connettore PDR."""


def _text(value: Any, max_len: int = 120) -> str:
    return str(value or "").strip()[:max_len]


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
    return {
        "environment": environment if environment in PDR_ENDPOINTS else "test",
        "channel": channel if channel in {"portal", "web_service"} else "portal",
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
