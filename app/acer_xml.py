"""Generazione deterministica e validazione XSD di un sottoinsieme REMIT.

Il modulo tratta i file XML come artefatti regolatori, non come semplici
serializzazioni di campi della UI.  Per ogni formato supportato conserva la
versione esatta dello schema ACER, la sua impronta e la validazione XSD che
precede l'export PDR.

Copertura intenzionalmente esplicita:

* ``REMITTable1 / V3``: una TradeReport che riferisce un contratto già
  identificato;
* ``REMITTable2 / V1``: una nonStandardContractReport a prezzo fisso.

Il trasporto gas usa un tracciato EDIG@S distinto (GasCapacity) e non viene
mai trasformato in un XML Table 1/2 approssimativo.  Rimane bloccato fino alla
raccolta dei suoi dati specifici e alla validazione del relativo bundle XSD.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

try:  # Il requisito applicativo rende lxml disponibile; il messaggio resta chiaro per installazioni difettose.
    from lxml import etree
except ImportError:  # pragma: no cover - esercitato solo in installazioni incomplete
    etree = None  # type: ignore[assignment]


SCHEMA_DIR = Path(__file__).with_name("schemas")
PDR_MAX_FILE_BYTES = 10 * 1024 * 1024

# URL e impronte del contenuto distribuito dall'ACER il 04/07/2024.  Il sito
# ACER segnala che parte della documentazione è in aggiornamento dopo il quadro
# REMIT 2026: l'operatore deve confermare il bundle prima dell'uso produttivo.
SCHEMAS: dict[str, dict[str, str]] = {
    "gas_standard": {
        "kind": "gas_standard",
        "label": "Contratto standard gas · Table 1",
        "schema_name": "REMITTable1",
        "schema_version": "V3",
        "namespace": "http://www.acer.europa.eu/REMIT/REMITTable1_V3.xsd",
        "filename": "REMITTable1_V3.xsd",
        "sha256": "68315256f29bccdc916377d0213a40fdaa743595819f9e942ceec9cf716d8a4c",
        "source_url": "https://www.acer.europa.eu/sites/default/files/REMIT/REMIT%20Reporting%20Guidance/Manual%20of%20Procedures%20%28MoP%29%20on%20Data%20Reporting/standard-contract-schema.zip",
        "published_at": "2024-07-04",
    },
    "gas_nonstandard": {
        "kind": "gas_nonstandard",
        "label": "Contratto non-standard gas · Table 2",
        "schema_name": "REMITTable2",
        "schema_version": "V1",
        "namespace": "http://www.acer.europa.eu/REMIT/REMITTable2_V1.xsd",
        "filename": "REMITTable2_V1.xsd",
        "sha256": "f59a872f68a357dcf05b43aa9a6724f2ed18a3d3b15153586cffa23415653886",
        "source_url": "https://www.acer.europa.eu/sites/default/files/REMIT/REMIT%20Reporting%20Guidance/Manual%20of%20Procedures%20%28MoP%29%20on%20Data%20Reporting/nonstandard-schema.zip",
        "published_at": "2024-07-04",
    },
}

PDR_DECLARED_BUT_NOT_IMPLEMENTED = {
    "gas_transport": {
        "kind": "gas_transport",
        "label": "Trasporto gas · GasCapacity",
        "schema_name": "GasCapacity",
        "schema_version": "V2",
        "reason": (
            "Il tracciato GasCapacity/EDIG@S richiede dati di asta, TSO, punto di "
            "connessione, capacità e periodi che il form Table 1/2 non raccoglie."
        ),
    }
}

IDENTIFIER_SCHEMES = {"ace", "lei", "bic", "eic", "gln"}
MARKETPLACE_SCHEMES = {"ace", "lei", "mic", "bil"}
ACE_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Z]{2}$")


class AcerXmlError(ValueError):
    """Errore strutturato della preparazione del file ACER."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


@dataclass(frozen=True)
class AcerXmlDocument:
    """Artefatto XML con esito della validazione dello schema incluso."""

    kind: str
    schema_name: str
    schema_version: str
    schema_sha256: str
    xml: str
    xml_sha256: str
    size_bytes: int


def catalogo_schemi() -> list[dict[str, Any]]:
    """Metadati esponibili alla UI senza i percorsi locali interni."""

    enabled = [
        {
            "kind": meta["kind"],
            "label": meta["label"],
            "schema_name": meta["schema_name"],
            "schema_version": meta["schema_version"],
            "sha256": meta["sha256"],
            "source_url": meta["source_url"],
            "published_at": meta["published_at"],
            "enabled": True,
        }
        for meta in SCHEMAS.values()
    ]
    deferred = [{**meta, "enabled": False} for meta in PDR_DECLARED_BUT_NOT_IMPLEMENTED.values()]
    return enabled + deferred


def schema_per_tipo(kind: str) -> dict[str, str] | None:
    return SCHEMAS.get(kind)


def _error(errors: list[dict[str, str]], field: str, message: str) -> None:
    errors.append({"field": field, "message": message})


def _require(data: dict[str, Any], field: str, errors: list[dict[str, str]], label: str) -> str:
    value = str(data.get(field) or "").strip()
    if not value:
        _error(errors, field, f"{label} obbligatorio per il tracciato ACER.")
    return value


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_iso_datetime(value: str) -> bool:
    # La forma con Z è quella più pratica per il form; fromisoformat accetta
    # gli offset espliciti e rifiuta date senza ora.
    try:
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return "T" in value and parsed.tzinfo is not None


def _is_future_iso_datetime(value: str) -> bool:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate).astimezone(timezone.utc) > datetime.now(timezone.utc)


def _valid_ace(value: str) -> bool:
    return len(value) == 12 and bool(ACE_RE.fullmatch(value))


def errori_dati(record: dict[str, Any]) -> list[dict[str, str]]:
    """Controlli leggibili prima della validazione XSD.

    L'XSD rimane l'autorità finale per enumerazioni e pattern.  Questi
    controlli permettono però di indicare all'operatore il campo mancante
    invece di consegnargli solo un errore XPath tecnico.
    """

    errors: list[dict[str, str]] = []
    kind = str(record.get("report_kind") or "")
    if kind in PDR_DECLARED_BUT_NOT_IMPLEMENTED:
        _error(errors, "report_kind", PDR_DECLARED_BUT_NOT_IMPLEMENTED[kind]["reason"])
        return errors
    meta = schema_per_tipo(kind)
    if not meta:
        _error(errors, "report_kind", "Scegli un tracciato ACER supportato.")
        return errors

    acer_code = _require(record, "acer_code", errors, "Codice ACER/CEREMP")
    if acer_code and not _valid_ace(acer_code):
        _error(errors, "acer_code", "Il codice ACE deve avere 12 caratteri e forma esempio A0045821W.IT.")
    counterparty = _require(record, "counterparty", errors, "Identificativo controparte")
    counterparty_scheme = _require(record, "counterparty_scheme", errors, "Tipo identificativo controparte").lower()
    if counterparty_scheme and counterparty_scheme not in IDENTIFIER_SCHEMES:
        _error(errors, "counterparty_scheme", "Usa ace, lei, bic, eic o gln.")
    if not counterparty:
        # Evita un secondo errore XSD poco utile per l'identificativo vuoto.
        return errors

    quantity = _require(record, "quantity_mwh", errors, "Quantità")
    _require(record, "quantity_unit", errors, "Unità quantità")
    if quantity and not re.fullmatch(r"-?\d+(?:\.\d+)?", quantity):
        _error(errors, "quantity_mwh", "La quantità deve essere un decimale normalizzato.")

    action = _require(record, "action", errors, "Azione")
    if action and action not in {"new", "modify", "cancel", "error"}:
        _error(errors, "action", "Azione non supportata: usa new, modify, cancel o error.")
    side = _require(record, "side", errors, "Lato")
    if side and side not in {"buy", "sell", "cross"}:
        _error(errors, "side", "Lato non supportato: usa buy, sell o cross.")
    _require(record, "trading_capacity", errors, "Capacità di negoziazione")

    price = _require(record, "price_eur_mwh", errors, "Prezzo")
    _require(record, "price_currency", errors, "Valuta prezzo")
    if price and not re.fullmatch(r"-?\d+(?:\.\d+)?", price):
        _error(errors, "price_eur_mwh", "Il prezzo deve essere un decimale normalizzato.")

    if kind == "gas_standard":
        _require(record, "contract_id", errors, "Contract ID")
        transaction_id = _require(record, "transaction_id", errors, "UTI/identificativo transazione")
        transaction_at = _require(record, "transaction_at", errors, "Data-ora transazione")
        if transaction_at and not _is_iso_datetime(transaction_at):
            _error(errors, "transaction_at", "Usa ISO 8601 con fuso, ad esempio 2026-08-01T10:15:00Z.")
        elif transaction_at and _is_future_iso_datetime(transaction_at):
            _error(errors, "transaction_at", "La data-ora della transazione non può essere futura.")
        _require(record, "marketplace_id", errors, "Identificativo mercato organizzato")
        market_scheme = _require(record, "marketplace_scheme", errors, "Tipo identificativo mercato").lower()
        if market_scheme and market_scheme not in MARKETPLACE_SCHEMES:
            _error(errors, "marketplace_scheme", "Usa ace, lei, mic o bil per il mercato organizzato.")
        if transaction_id and len(transaction_id) > 100:
            _error(errors, "transaction_id", "L'identificativo transazione supera 100 caratteri.")
    elif kind == "gas_nonstandard":
        _require(record, "contract_id", errors, "Contract ID")
        contract_date = _require(record, "contract_date", errors, "Data contratto")
        delivery_start = _require(record, "delivery_start_date", errors, "Inizio consegna")
        delivery_end = _require(record, "delivery_end_date", errors, "Fine consegna")
        _require(record, "contract_type", errors, "Tipo contratto")
        _require(record, "energy_commodity", errors, "Commodity")
        delivery_point = _require(record, "delivery_point", errors, "EIC punto/zona di consegna")
        for field, value in (
            ("contract_date", contract_date),
            ("delivery_start_date", delivery_start),
            ("delivery_end_date", delivery_end),
        ):
            if value and not _is_iso_date(value):
                _error(errors, field, "Usa il formato AAAA-MM-GG.")
        if contract_date and _is_iso_date(contract_date) and date.fromisoformat(contract_date) > date.today():
            _error(errors, "contract_date", "La data del contratto non può essere futura.")
        if delivery_start and delivery_end and _is_iso_date(delivery_start) and _is_iso_date(delivery_end):
            if delivery_end < delivery_start:
                _error(errors, "delivery_end_date", "La fine consegna non può precedere l'inizio.")
        if delivery_point and len(delivery_point) != 16:
            _error(errors, "delivery_point", "Il punto/zona Table 2 deve essere un EIC di 16 caratteri.")
    return errors


def _q(meta: dict[str, str], name: str):
    assert etree is not None
    return etree.QName(meta["namespace"], name)


def _element(meta: dict[str, str], name: str, value: Any | None = None):
    assert etree is not None
    node = etree.Element(_q(meta, name))
    if value is not None:
        node.text = str(value)
    return node


def _root(meta: dict[str, str], name: str):
    assert etree is not None
    return etree.Element(_q(meta, name), nsmap={None: meta["namespace"]})


def _append(parent, meta: dict[str, str], name: str, value: Any | None = None):
    node = _element(meta, name, value)
    parent.append(node)
    return node


def _identifier(parent, meta: dict[str, str], container: str, scheme: str, value: str):
    wrapper = _append(parent, meta, container)
    _append(wrapper, meta, scheme, value)
    return wrapper


def _action_code(action: str) -> str:
    return {"new": "N", "modify": "M", "error": "E", "cancel": "C"}[action]


def _side_code(side: str) -> str:
    return {"buy": "B", "sell": "S", "cross": "C"}[side]


def _build_table_1(record: dict[str, Any], meta: dict[str, str]):
    assert etree is not None
    root = _root(meta, "REMITTable1")
    _identifier(root, meta, "reportingEntityID", "ace", str(record["acer_code"]))
    trades = _append(root, meta, "TradeList")
    trade = _append(trades, meta, "TradeReport")
    _append(trade, meta, "RecordSeqNumber", "1")
    _identifier(trade, meta, "idOfMarketParticipant", "ace", str(record["acer_code"]))
    _identifier(
        trade,
        meta,
        "otherMarketParticipant",
        str(record["counterparty_scheme"]).lower(),
        str(record["counterparty"]),
    )
    _append(trade, meta, "tradingCapacity", str(record["trading_capacity"]))
    _append(trade, meta, "buySellIndicator", _side_code(str(record["side"])))
    contract_info = _append(trade, meta, "contractInfo")
    _append(contract_info, meta, "contractId", str(record["contract_id"]))
    market = _append(trade, meta, "organisedMarketPlaceIdentifier")
    _append(market, meta, str(record["marketplace_scheme"]).lower(), str(record["marketplace_id"]))
    _append(trade, meta, "transactionTime", str(record["transaction_at"]))
    uti = _append(trade, meta, "uniqueTransactionIdentifier")
    _append(uti, meta, "uniqueTransactionIdentifier", str(record["transaction_id"]))
    price = _append(trade, meta, "priceDetails")
    _append(price, meta, "price", str(record["price_eur_mwh"]))
    _append(price, meta, "priceCurrency", str(record["price_currency"]))
    total = _append(trade, meta, "totalNotionalContractQuantity")
    _append(total, meta, "value", str(record["quantity_mwh"]))
    _append(total, meta, "unit", str(record["quantity_unit"]))
    _append(trade, meta, "actionType", _action_code(str(record["action"])))
    return root


def _build_table_2(record: dict[str, Any], meta: dict[str, str]):
    assert etree is not None
    root = _root(meta, "REMITTable2")
    _identifier(root, meta, "reportingEntityID", "ace", str(record["acer_code"]))
    trades = _append(root, meta, "TradeList")
    trade = _append(trades, meta, "nonStandardContractReport")
    _append(trade, meta, "RecordSeqNumber", "1")
    _identifier(trade, meta, "idOfMarketParticipant", "ace", str(record["acer_code"]))
    _identifier(
        trade,
        meta,
        "otherMarketParticipant",
        str(record["counterparty_scheme"]).lower(),
        str(record["counterparty"]),
    )
    _append(trade, meta, "tradingCapacity", str(record["trading_capacity"]))
    _append(trade, meta, "buySellIndicator", _side_code(str(record["side"])))
    _append(trade, meta, "contractId", str(record["contract_id"]))
    _append(trade, meta, "contractDate", str(record["contract_date"]))
    _append(trade, meta, "contractType", str(record["contract_type"]))
    _append(trade, meta, "energyCommodity", str(record["energy_commodity"]))
    price = _append(trade, meta, "priceOrPriceFormula")
    fixed = _append(price, meta, "price")
    _append(fixed, meta, "value", str(record["price_eur_mwh"]))
    _append(fixed, meta, "currency", str(record["price_currency"]))
    total = _append(trade, meta, "totalNotionalContractQuantity")
    _append(total, meta, "value", str(record["quantity_mwh"]))
    _append(total, meta, "unit", str(record["quantity_unit"]))
    _append(trade, meta, "settlementMethod", str(record.get("settlement_method") or "P"))
    _append(trade, meta, "deliveryPointOrZone", str(record["delivery_point"]))
    _append(trade, meta, "deliveryStartDate", str(record["delivery_start_date"]))
    _append(trade, meta, "deliveryEndDate", str(record["delivery_end_date"]))
    _append(trade, meta, "actionType", _action_code(str(record["action"])))
    return root


@lru_cache(maxsize=None)
def _compiled_schema(kind: str):
    if etree is None:
        raise AcerXmlError("Dipendenza lxml mancante: impossibile validare il file XSD.")
    meta = schema_per_tipo(kind)
    if not meta:
        raise AcerXmlError("Tracciato ACER non supportato.")
    path = SCHEMA_DIR / meta["filename"]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AcerXmlError(f"Schema ACER non disponibile: {path.name}.") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != meta["sha256"]:
        raise AcerXmlError("Integrità dello schema ACER non verificata: export bloccato.")
    try:
        return etree.XMLSchema(etree.parse(str(path)))
    except (OSError, etree.XMLSchemaParseError) as exc:
        raise AcerXmlError("Schema ACER non caricabile: export bloccato.") from exc


def _xsd_errors(kind: str, root) -> list[dict[str, str]]:
    schema = _compiled_schema(kind)
    if schema.validate(root):
        return []
    return [
        {"field": "xsd", "message": f"Riga {entry.line}: {entry.message}"}
        for entry in schema.error_log
    ]


def genera_xml(record: dict[str, Any]) -> AcerXmlDocument:
    """Costruisce un file XML e lo valida con lo XSD ACER fissato nel repo."""

    errors = errori_dati(record)
    if errors:
        raise AcerXmlError("Dati incompleti o incompatibili con il tracciato ACER.", errors)
    kind = str(record["report_kind"])
    meta = SCHEMAS[kind]
    root = _build_table_1(record, meta) if kind == "gas_standard" else _build_table_2(record, meta)
    errors = _xsd_errors(kind, root)
    if errors:
        raise AcerXmlError("Il file generato non supera la validazione XSD ACER.", errors)
    assert etree is not None
    xml_bytes = etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
    if len(xml_bytes) > PDR_MAX_FILE_BYTES:
        raise AcerXmlError("Il file supera il limite PDR di 10 MB.")
    return AcerXmlDocument(
        kind=kind,
        schema_name=meta["schema_name"],
        schema_version=meta["schema_version"],
        schema_sha256=meta["sha256"],
        xml=xml_bytes.decode("utf-8"),
        xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        size_bytes=len(xml_bytes),
    )


def verifica_xml_archiviato(kind: str, xml: str, expected_sha256: str) -> None:
    """Riverifica hash e XSD prima di esporre o preflightare un artefatto.

    Lo stato di export non è una garanzia sufficiente: la base locale può
    essere corrotta o alterata dopo la generazione. Il parser disabilita DTD e
    risoluzione di entità anche se l'XML proviene in origine dall'app stessa.
    """

    if etree is None:
        raise AcerXmlError("Dipendenza lxml mancante: impossibile verificare l'artefatto XML.")
    if not schema_per_tipo(kind):
        raise AcerXmlError("Tracciato dell'artefatto XML non supportato.")
    if not isinstance(xml, str):
        raise AcerXmlError("Contenuto dell'artefatto XML non disponibile.")
    raw = xml.encode("utf-8")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise AcerXmlError(
            "Hash SHA-256 dell'artefatto XML non corrispondente.",
            [{"field": "sha256", "message": "Il contenuto XML non coincide con l'impronta registrata."}],
        )
    try:
        root = etree.fromstring(
            raw,
            parser=etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False),
        )
    except etree.XMLSyntaxError as exc:
        raise AcerXmlError(
            "Artefatto XML non leggibile.",
            [{"field": "xml", "message": str(exc)}],
        ) from exc
    errors = _xsd_errors(kind, root)
    if errors:
        raise AcerXmlError("Artefatto XML non più valido contro lo XSD ACER.", errors)


def nome_file_pdr(document: AcerXmlDocument, acer_code: str, progressivo: int, created_on: date) -> str:
    """Applica la convenzione PDR: data_schema_versione_CEREMP_progressivo.XML."""

    if progressivo < 1:
        raise AcerXmlError("Progressivo PDR non valido.")
    if not _valid_ace(acer_code):
        raise AcerXmlError("Codice ACE non valido per il nome file PDR.")
    return (
        f"{created_on:%Y%m%d}_{document.schema_name}_{document.schema_version}_"
        f"{acer_code}_{progressivo}.XML"
    )
