"""Generazione di UTI e Contract ID secondo l'algoritmo ufficiale ACER.

Per le operazioni concluse su un mercato organizzato l'UTI è assegnato dal
mercato. Per i contratti **bilaterali** le due controparti devono riportare lo
stesso identificativo: ACER pubblica un algoritmo che permette a entrambe di
ricavarlo dai soli termini economici, senza scambiarsi nulla.

L'implementazione riproduce il generatore ufficiale (TRUM Annex IV, *UTI
Generator* v2.3, aggiornamento 2025), la cui macro esegue:

1. concatenazione dei termini economici, ciascuno ripulito dagli spazi ai bordi
   e senza separatori;
2. ``SHA-256`` del testo in UTF-8;
3. codifica base64 con quattro sostituzioni che rendono il risultato solo
   alfanumerico: ``+``→``A``, ``/``→``B``, ``=``→``C``, ``-``→``D``
   (nel foglio: *"Only alphanumeric characters are allowed in this string"*);
4. i primi **42** caratteri dell'impronta, riempiti con ``E`` se più corta;
5. in coda il **progressivo a 3 cifre**, che distingue operazioni identiche
   concluse nella stessa giornata.

L'identificativo finale è quindi lungo 45 caratteri e rispetta il pattern XSD
``uniqueTransactionIdentifierType``.

I due tracciati usano insiemi di campi diversi:

* **UTI** (Table 1): 13 campi, prezzo e quantità inclusi;
* **Contract ID** (Table 2): 9 campi, senza prezzo né quantità.

La conformità è verificata contro i 181 casi calcolati dal foglio ufficiale,
conservati in ``tests/dati/uti_acer.json``.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

LUNGHEZZA_IMPRONTA = 42
LUNGHEZZA_ID = LUNGHEZZA_IMPRONTA + 3
RIEMPIMENTO = "E"
MAX_PROGRESSIVO = 999

# Ordine dei campi: è parte dell'accordo con la controparte, cambiarlo
# produrrebbe identificativi che non si appaiano.
CAMPI_UTI = (
    "buyer_acer",
    "seller_acer",
    "contract_type",
    "energy_commodity",
    "settlement_method",
    "trade_date",
    "price",
    "price_currency",
    "quantity",
    "quantity_unit",
    "delivery_point",
    "delivery_start_date",
    "delivery_end_date",
)
CAMPI_CONTRACT_ID = (
    "buyer_acer",
    "seller_acer",
    "contract_type",
    "energy_commodity",
    "settlement_method",
    "trade_date",
    "delivery_point",
    "delivery_start_date",
    "delivery_end_date",
)


class UtiError(ValueError):
    """Dati insufficienti o incoerenti per un identificativo condivisibile."""


def impronta_acer(testo: str) -> str:
    """SHA-256 in base64 con le sostituzioni previste dal generatore ACER."""

    grezzo = base64.b64encode(hashlib.sha256(testo.encode("utf-8")).digest()).decode("ascii")
    return grezzo.replace("+", "A").replace("/", "B").replace("=", "C").replace("-", "D")


def _componi(impronta: str, progressivo: int) -> str:
    if not isinstance(progressivo, int) or isinstance(progressivo, bool):
        raise UtiError("Il progressivo deve essere un numero intero.")
    if not 1 <= progressivo <= MAX_PROGRESSIVO:
        raise UtiError(f"Il progressivo deve essere compreso fra 1 e {MAX_PROGRESSIVO}.")
    riempita = (impronta + RIEMPIMENTO * LUNGHEZZA_IMPRONTA)[:LUNGHEZZA_IMPRONTA]
    return f"{riempita}{progressivo:03d}"


def da_concatenazione(concatenato: str, progressivo: int = 1) -> str:
    """Identificativo a partire dalla stringa già concatenata.

    È la forma usata dai vettori ufficiali ACER e utile a chi ha già i valori
    nell'ordine previsto.
    """

    return _componi(impronta_acer(concatenato), progressivo)


def _lati(record: dict[str, Any]) -> tuple[str, str]:
    """Ruoli di compratore e venditore, uguali per entrambe le controparti.

    È il punto che rende l'algoritmo utilizzabile dalle due parti: chi compra e
    chi vende ottengono la stessa coppia e quindi lo stesso identificativo, pur
    avendo i propri codici invertiti nel record.
    """

    proprio = str(record.get("acer_code") or "").strip()
    controparte = str(record.get("counterparty") or "").strip()
    schema = str(record.get("counterparty_scheme") or "").strip().lower()
    lato = str(record.get("side") or "").strip().lower()

    if not proprio or not controparte:
        raise UtiError("Servono il codice ACER e l'identificativo della controparte.")
    if schema and schema != "ace":
        raise UtiError(
            "L'algoritmo ACER confronta i codici ACER delle due parti: "
            "imposta lo schema della controparte su ACE."
        )
    if len(proprio) != 12 or len(controparte) != 12:
        raise UtiError("I codici ACER devono essere lunghi 12 caratteri.")
    if proprio.upper() == controparte.upper():
        raise UtiError("Compratore e venditore devono essere soggetti diversi.")
    if lato == "buy":
        return proprio, controparte
    if lato == "sell":
        return controparte, proprio
    raise UtiError("Indica se l'operazione è un acquisto o una vendita.")


def dati_identificativo(record: dict[str, Any]) -> dict[str, str]:
    """Termini economici usati dall'algoritmo, ripuliti come fa il foglio ACER."""

    compratore, venditore = _lati(record)
    # Table 1 porta la data-ora della transazione, Table 2 la data del contratto.
    data = str(record.get("transaction_at") or record.get("contract_date") or "").strip()
    if "T" in data:
        data = data.split("T", 1)[0]
    elif " " in data:
        data = data.split(" ", 1)[0]
    campo = lambda nome: str(record.get(nome) or "").strip()  # noqa: E731
    return {
        "buyer_acer": compratore,
        "seller_acer": venditore,
        "contract_type": campo("contract_type"),
        "energy_commodity": campo("energy_commodity"),
        "settlement_method": campo("settlement_method"),
        "trade_date": data,
        "price": campo("price_eur_mwh"),
        "price_currency": campo("price_currency"),
        "quantity": campo("quantity_mwh"),
        "quantity_unit": campo("quantity_unit"),
        "delivery_point": campo("delivery_point"),
        "delivery_start_date": campo("delivery_start_date"),
        "delivery_end_date": campo("delivery_end_date"),
    }


def _concatena(record: dict[str, Any], campi: tuple[str, ...]) -> str:
    dati = dati_identificativo(record)
    mancanti = [c for c in ("contract_type", "energy_commodity", "settlement_method", "trade_date") if not dati[c]]
    if mancanti:
        raise UtiError(
            "Per un identificativo condivisibile servono tipo contratto, commodity, "
            "metodo di regolamento e data dell'operazione."
        )
    # I campi vuoti restano vuoti nella concatenazione, come nel foglio ACER.
    return "".join(dati[c] for c in campi)


def stringa_uti(record: dict[str, Any]) -> str:
    """Concatenazione dei 13 campi usata per l'UTI (Table 1)."""

    return _concatena(record, CAMPI_UTI)


def stringa_contract_id(record: dict[str, Any]) -> str:
    """Concatenazione dei 9 campi usata per il Contract ID (Table 2)."""

    return _concatena(record, CAMPI_CONTRACT_ID)


def genera_uti(record: dict[str, Any], progressivo: int = 1) -> str:
    """UTI di 45 caratteri per una transazione bilaterale (Table 1)."""

    return _componi(impronta_acer(stringa_uti(record)), progressivo)


def genera_contract_id(record: dict[str, Any], progressivo: int = 1) -> str:
    """Contract ID di 45 caratteri per un contratto non-standard (Table 2)."""

    return _componi(impronta_acer(stringa_contract_id(record)), progressivo)
