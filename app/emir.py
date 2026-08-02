"""Segnalazione EMIR REFIT: auth.030.001.03 validata contro lo XSD ESMA.

EMIR e REMIT sono due obblighi distinti e vanno tenuti separati: REMIT
segnala il contratto all'ACER tramite un RRM, EMIR segnala il derivato a un
Trade Repository registrato.  Lo stesso forward sul gas può ricadere in
entrambi, ma i tracciati, gli identificativi e i destinatari non hanno nulla
in comune.  Per questo il modulo non riusa nulla di ``app/remit.py``.

Il modulo produce soltanto ciò che uno schema ufficiale ESMA sa validare.
Tre confini dichiarati, che valgono anche come limiti d'uso:

* **Non invia.**  La trasmissione al Trade Repository passa dal canale del TR
  scelto (portale, SFTP, API): credenziali e abilitazioni restano fuori.
* **Non impacchetta.**  ESMA pubblica lo schema del messaggio
  (``auth.030.001.03``) e quello dell'intestazione (``head.001.001.01``), ma
  *non* pubblica l'involucro che li unisce in un unico file, né la regola di
  denominazione: sono definiti da ciascun TR.  Qui i due documenti si
  generano e si validano separatamente, e si dice esplicitamente che il
  confezionamento finale è a carico del TR.
* **Non inventa codici.**  Ogni enumerazione esposta all'interfaccia viene
  letta a runtime dai facet dello XSD (``codici_ammessi``).  Le etichette
  italiane sono tradotte dalla documentazione incorporata nello schema, e un
  test verifica che l'insieme delle chiavi tradotte coincida con quello dei
  valori ammessi: un codice inventato non può sopravvivere alla suite.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # pragma: no cover - assenza di lxml gestita dal chiamante
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None  # type: ignore[assignment]


SCHEMA_DIR = Path(__file__).with_name("schemas") / "emir"

PACCHETTO = {
    "versione": "EMIR REFIT 1.1.0",
    "fonte": "ESMA · EMIR Refit Incoming/Outgoing Messages (XML schemas)",
    "scaricato_il": "2026-08-02",
}

SCHEMI: dict[str, dict[str, str]] = {
    "segnalazione": {
        "codice": "segnalazione",
        "etichetta": "Segnalazione del derivato (auth.030)",
        "filename": "auth.030.001.03_ESMAUG_DATTAR_1.1.0.xsd",
        "radice": "auth.030.001.03",
        "namespace": "urn:iso:std:iso:20022:tech:xsd:auth.030.001.03",
        "sha256": "cdff94d4b4de8dc5b6981190c2130622b68a77c26cda0bf813fd4c1f2fd06a7a",
    },
    "intestazione": {
        "codice": "intestazione",
        "etichetta": "Intestazione applicativa (head.001)",
        "filename": "head.001.001.01_ESMA_restricted.xsd",
        "radice": "head.001.001.01",
        "namespace": "urn:iso:std:iso:20022:tech:xsd:head.001.001.01",
        "sha256": "3e3f399b056c70f9a9d9a00b52a8f742f8d0371ad96fb4a1f4b1c78e8a24c2f9",
    },
    "esito": {
        "codice": "esito",
        "etichetta": "Esito del Trade Repository (auth.092)",
        "filename": "auth.092.001.04_ESMAUG_DATREJ_1.0.0.xsd",
        "radice": "auth.092.001.04",
        "namespace": "urn:iso:std:iso:20022:tech:xsd:auth.092.001.04",
        "sha256": "cacaddfed923914b8e81b9cad672cd939a1d1de535a573a504678bddfa0a6de9",
    },
}


# ------------------------------------------------------------------- azioni

# Nel tracciato EMIR REFIT il "tipo di azione" non è un campo: è il nome
# dell'elemento che avvolge la segnalazione.  Un <New> è una NEWT, un
# <Termntn> è una TERM.  Chi cerca un elemento <ActnTp> non lo trova, e chi lo
# scrive produce un file che nessun TR accetta.
AZIONI: dict[str, dict[str, Any]] = {
    "nuovo": {
        "elemento": "New",
        "sigla": "NEWT",
        "etichetta": "Nuova operazione",
        "descrizione": "Primo invio del derivato al Trade Repository.",
        "profilo": "completo",
        "evento": "obbligatorio",
    },
    "modifica": {
        "elemento": "Mod",
        "sigla": "MODI",
        "etichetta": "Modifica",
        "descrizione": "Cambiano le condizioni di un derivato già segnalato.",
        "profilo": "completo",
        "evento": "facoltativo",
    },
    "correzione": {
        "elemento": "Crrctn",
        "sigla": "CORR",
        "etichetta": "Correzione",
        "descrizione": "Si corregge un dato sbagliato in un invio precedente.",
        "profilo": "completo",
        "evento": "assente",
    },
    "componente_posizione": {
        "elemento": "PosCmpnt",
        "sigla": "POSC",
        "etichetta": "Componente di posizione",
        "descrizione": "L'operazione confluisce in una posizione segnalata a parte.",
        "profilo": "posizione",
        "evento": "assente",
    },
    "riattivazione": {
        "elemento": "Rvv",
        "sigla": "REVI",
        "etichetta": "Riattivazione",
        "descrizione": "Si riapre un derivato chiuso o annullato per errore.",
        "profilo": "completo",
        "evento": "assente",
    },
    "cessazione": {
        "elemento": "Termntn",
        "sigla": "TERM",
        "etichetta": "Cessazione anticipata",
        "descrizione": "Il derivato si chiude prima della scadenza contrattuale.",
        "profilo": "cessazione",
        "evento": "obbligatorio",
    },
    "valutazione": {
        "elemento": "ValtnUpd",
        "sigla": "VALU",
        "etichetta": "Aggiornamento della valutazione",
        "descrizione": "Aggiorna il valore del contratto senza toccarne le condizioni.",
        "profilo": "valutazione",
        "evento": "assente",
    },
    "errore": {
        "elemento": "Err",
        "sigla": "EROR",
        "etichetta": "Annullamento per errore",
        "descrizione": "Il derivato non esisteva o non andava segnalato.",
        "profilo": "annullamento",
        "evento": "assente",
    },
}

# Quali blocchi servono davvero, per profilo.  Le cardinalità vengono dallo
# XSD: una cessazione non porta né controparte estesa né dati di contratto,
# e pretenderli renderebbe il modulo più severo dello schema.
PROFILI = {
    "completo": {
        "controparte_estesa": True,
        "contratto": True,
        "operazione": "completa",
        "valutazione": "facoltativa",
    },
    "posizione": {
        "controparte_estesa": True,
        "contratto": True,
        "operazione": "completa",
        "valutazione": "facoltativa",
    },
    "cessazione": {
        "controparte_estesa": False,
        "contratto": False,
        "operazione": "cessazione",
        "valutazione": "assente",
    },
    "valutazione": {
        "controparte_estesa": False,
        "contratto": False,
        "operazione": "minima",
        "valutazione": "obbligatoria",
    },
    "annullamento": {
        "controparte_estesa": False,
        "contratto": False,
        "operazione": "minima",
        "valutazione": "assente",
    },
}


# --------------------------------------------------------- codici tradotti

# Le chiavi devono coincidere con le enumerazioni dello XSD: lo verifica
# `tests/test_emir.py::test_le_etichette_coprono_esattamente_lo_schema`.
ETICHETTE: dict[str, dict[str, str]] = {
    "FinancialInstrumentContractType2Code": {
        "CFDS": "Contratto per differenza (CFD)",
        "FRAS": "Forward rate agreement",
        "FUTR": "Future",
        "FORW": "Contratto a termine (forward)",
        "OPTN": "Opzione",
        "SPDB": "Spread betting",
        "SWAP": "Swap",
        "SWPT": "Swaption",
        "OTHR": "Altro tipo di contratto",
    },
    "ProductType4Code__1": {
        "CRDT": "Credito",
        "CURR": "Valuta",
        "EQUI": "Azionario",
        "INTR": "Tasso d'interesse",
        "COMM": "Merci (commodity)",
    },
    "PhysicalTransferType4Code": {
        "PHYS": "Consegna fisica",
        "OPTL": "Opzionale o decisa da un terzo",
        "CASH": "Regolamento per contanti",
    },
    "MasterAgreementType2Code": {
        "BIAG": "Accordo bilaterale",
        "CDEA": "FIA-ISDA Cleared Derivatives Execution Agreement",
        "CHMA": "Swiss Master Agreement",
        "CMOP": "Contrato Marco de Operaciones Financieras",
        "DERV": "Deutscher Rahmenvertrag für Finanztermingeschäfte (DRV)",
        "EFMA": "EFET Master Agreement",
        "EUMA": "European Master Agreement",
        "FMAT": "FBF Master Agreement (strumenti finanziari a termine)",
        "FPCA": "FOA Professional Client Agreement",
        "GMRA": "GMRA",
        "GMSL": "GMSLA",
        "IDMA": "Islamic Derivative Master Agreement",
        "ISDA": "ISDA Master Agreement",
        "OTHR": "Altro accordo quadro",
    },
    "DerivativeEventType3Code__1": {
        "ALOC": "Allocazione a controparti diverse",
        "CLRG": "Compensazione presso una CCP",
        "COMP": "Compressione o riduzione del rischio post-negoziazione",
        "CORP": "Operazione societaria",
        "CREV": "Evento di credito (solo derivati creditizi)",
        "ETRM": "Cessazione anticipata",
        "EXER": "Esercizio di un'opzione o di una swaption",
        "INCP": "Inclusione in una posizione",
        "NOVA": "Novazione (sostituzione di una parte)",
        "TRAD": "Conclusione o rinegoziazione del contratto",
        "UPDT": "Adeguamento ai requisiti di segnalazione rivisti",
    },
    "ClearingObligationType1Code": {
        "FLSE": "No: la classe non è soggetta all'obbligo di compensazione",
        "UKWN": "Non noto",
        "TRUE": "Sì: la classe è soggetta all'obbligo di compensazione",
    },
    "ValuationType1Code": {
        "CCPV": "Valutazione della CCP",
        "MTMA": "Mark to market",
        "MTMO": "Mark to model",
    },
    "ModificationLevel1Code": {
        "PSTN": "Posizione",
        "TCTN": "Operazione singola",
    },
    "OptionParty1Code": {
        "BYER": "Acquirente",
        "SLLR": "Venditore",
    },
    "OptionParty3Code": {
        "MAKE": "Maker (chi riceve l'operazione)",
        "TAKE": "Taker (chi la inizia)",
    },
    "FinancialPartySectorType3Code__1": {
        "AIFD": "Fondo d'investimento alternativo (gestito da un GEFIA)",
        "CSDS": "Depositario centrale di titoli",
        "CDTI": "Ente creditizio",
        "INUN": "Impresa di assicurazione",
        "ORPI": "Ente pensionistico aziendale o professionale",
        "INVF": "Impresa di investimento",
        "UCIT": "OICVM e relativa società di gestione",
    },
    "EnergyLoadType1Code": {
        "BSLD": "Carico di base (base load)",
        "GASD": "Giorno gas",
        "HABH": "Ora e blocchi orari",
        "OFFP": "Fuori punta (off-peak)",
        "PKLD": "Punta (peak load)",
        "SHPD": "Profilo sagomato (shaped)",
        "OTHR": "Altro profilo",
    },
    "AssetClassDetailedSubProductType31Code": {
        "GASP": "GASPOOL",
        "LNGG": "GNL (gas naturale liquefatto)",
        "NCGG": "NCG · NetConnect Germany",
        "TTFG": "TTF · Title Transfer Facility",
        "NBPG": "NBP · National Balancing Point",
        "OTHR": "Altro punto o indice del gas",
    },
    "AssetClassDetailedSubProductType5Code": {
        "BSLD": "Carico di base",
        "FITR": "Diritti finanziari di trasmissione",
        "PKLD": "Punta",
        "OFFP": "Fuori punta",
        "OTHR": "Altro",
    },
    "EnergyQuantityUnit2Code": {
        "BTUD": "BTU al giorno",
        "CMPD": "m³ al giorno",
        "GJDD": "GJ al giorno",
        "GWAT": "GW",
        "GWHD": "GWh al giorno",
        "GWHH": "GWh all'ora",
        "HMJD": "100 MJ al giorno",
        "KTMD": "kTherm al giorno",
        "KWAT": "kW",
        "KWHD": "kWh al giorno",
        "KWHH": "kWh all'ora",
        "MCMD": "Mm³ al giorno",
        "MJDD": "MJ al giorno",
        "MBTD": "MBTU al giorno",
        "MMJD": "milioni di MJ al giorno",
        "MTMD": "MTherm al giorno",
        "MWAT": "MW",
        "MWHD": "MWh al giorno",
        "MWHH": "MWh all'ora",
        "THMD": "Therm al giorno",
    },
    "DurationType1Code": {
        "YEAR": "Anno",
        "SEAS": "Semestre (stagione)",
        "QURT": "Trimestre",
        "MNTH": "Mese",
        "WEEK": "Settimana",
        "DASD": "Giorno",
        "HOUR": "Ora",
        "MNUT": "Minuto",
        "OTHR": "Altra durata",
    },
    "WeekDay3Code__1": {
        "MOND": "Lunedì",
        "TUED": "Martedì",
        "WEDD": "Mercoledì",
        "THUD": "Giovedì",
        "FRID": "Venerdì",
        "SATD": "Sabato",
        "SUND": "Domenica",
        "WDAY": "Giorni feriali",
        "WEND": "Fine settimana",
        "XBHL": "Giorni esclusi i festivi bancari",
        "IBHL": "Giorni inclusi i festivi bancari",
    },
}

# Esiti che il TR restituisce in auth.092.  Vivono in un altro schema, quindi
# il test di copertura li confronta con quello.
ETICHETTE_ESITO: dict[str, dict[str, str]] = {
    "ReportingMessageStatus2Code": {
        "ACPT": "Accettato",
        "RJCT": "Respinto",
        "INCF": "Nome del file non corretto",
        "CRPT": "File danneggiato",
        "NAUT": "Non autorizzato",
    },
    "TransactionOperationType10Code__1": {
        "NEWT": "Nuova operazione",
        "MODI": "Modifica",
        "CORR": "Correzione",
        "TERM": "Cessazione",
        "EROR": "Annullamento per errore",
        "REVI": "Riattivazione",
        "POSC": "Componente di posizione",
        "VALU": "Aggiornamento della valutazione",
        "MARU": "Aggiornamento dei margini",
        "COMP": "Compressione",
    },
}

ESITI_ACCETTAZIONE = {"ACPT"}

# Natura della controparte: nello XSD è una scelta fra quattro rami, non un
# codice.  La mappa tiene insieme il nome che usa l'operatore e il ramo.
NATURE = {
    "NFC": {"ramo": "NFI", "etichetta": "Controparte non finanziaria (NFC)"},
    "FC": {"ramo": "FI", "etichetta": "Controparte finanziaria (FC)"},
    "CCP": {"ramo": "CntrlCntrPty", "etichetta": "Controparte centrale (CCP)"},
    "ALTRO": {"ramo": "Othr", "etichetta": "Altra natura"},
}

# Il settore di una NFC è la lettera della sezione NACE (A…U): lo XSD accetta
# `[A-U]{1}` più tre caratteri liberi, il valore usato in pratica è la sola
# lettera.  Un fornitore di gas sta in D.
SEZIONI_NACE = {
    "A": "A · Agricoltura, silvicoltura e pesca",
    "B": "B · Estrazione di minerali da cave e miniere",
    "C": "C · Attività manifatturiere",
    "D": "D · Fornitura di energia elettrica, gas, vapore e aria condizionata",
    "E": "E · Fornitura di acqua, reti fognarie, gestione dei rifiuti",
    "F": "F · Costruzioni",
    "G": "G · Commercio all'ingrosso e al dettaglio",
    "H": "H · Trasporto e magazzinaggio",
    "I": "I · Alloggio e ristorazione",
    "J": "J · Servizi di informazione e comunicazione",
    "K": "K · Attività finanziarie e assicurative",
    "L": "L · Attività immobiliari",
    "M": "M · Attività professionali, scientifiche e tecniche",
    "N": "N · Noleggio, agenzie di viaggio, servizi alle imprese",
    "O": "O · Amministrazione pubblica e difesa",
    "P": "P · Istruzione",
    "Q": "Q · Sanità e assistenza sociale",
    "R": "R · Attività artistiche, sportive e di intrattenimento",
    "S": "S · Altre attività di servizi",
    "T": "T · Attività di famiglie e convivenze",
    "U": "U · Organizzazioni ed organismi extraterritoriali",
}

# Prodotti energia coperti dal modulo: sono i due rami dello XSD che un
# operatore del gas usa davvero.  Gli altri (carbone, petrolio, rinnovabili…)
# esistono nello schema ma non hanno un modulo dedicato qui: dichiararlo è
# meglio che generarli male.
PRODOTTI = {
    "gas": {
        "elemento": "NtrlGas",
        "base": "NRGY",
        "sotto": "NGAS",
        "dettaglio_tipo": "AssetClassDetailedSubProductType31Code",
        "etichetta": "Gas naturale",
    },
    "elettricita": {
        "elemento": "Elctrcty",
        "base": "NRGY",
        "sotto": "ELEC",
        "dettaglio_tipo": "AssetClassDetailedSubProductType5Code",
        "etichetta": "Energia elettrica",
    },
}


# ------------------------------------------------------------------- limiti

MAX_TESTO = 140
MAX_IDENT = 52
MAX_NUMERO = 30
MAX_PUNTI = 20
MAX_INTERVALLI = 200
MAX_ESITO_BYTES = 20 * 1024 * 1024
MAX_RIGHE_ESITO = 5000
MAX_ERRORI = 50

# Caratteri che l'XML 1.0 non ammette e surrogati spaiati: intercettarli qui
# evita che il serializzatore fallisca a metà documento.
CARATTERI_VIETATI = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")

LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
UTI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}[A-Z0-9]{0,32}$")
EIC_RE = re.compile(r"^[A-Z0-9\-]{16}$")
CFI_RE = re.compile(r"^[A-Z]{6}$")
VALUTA_RE = re.compile(r"^[A-Z]{3}$")
MIC_RE = re.compile(r"^[A-Z0-9]{4}$")
ORA_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?$")
ALFABETO_UTI = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class EmirError(ValueError):
    """Errore strutturato della preparazione del documento EMIR."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


@dataclass(frozen=True)
class DocumentoEmir:
    """Artefatto XML con l'esito della validazione dello schema incluso."""

    codice: str
    radice: str
    azione: str
    sigla_azione: str
    livello: str
    uti: str
    controparte: str
    schema_sha256: str
    xml: str
    xml_sha256: str
    size_bytes: int
    avvisi: tuple[str, ...] = ()


# ------------------------------------------------------- lettura dello XSD

XSD_NS = "{http://www.w3.org/2001/XMLSchema}"
_cache_definizioni: dict[str, dict[str, Any]] = {}
_cache_schemi: dict[str, Any] = {}
_cache_enumerazioni: dict[tuple[str, str], list[str]] = {}


def _byte_schema(codice: str) -> bytes:
    """Legge lo XSD e ne verifica l'impronta prima di qualunque parse.

    Vale per entrambe le strade che leggono lo schema: la compilazione che
    valida i documenti e l'indice dei tipi che alimenta le tendine.  Senza il
    controllo anche qui, uno schema sostituito su disco bloccherebbe la
    generazione ma continuerebbe a riempire il catalogo: due verità diverse
    sullo stesso file.  I byte verificati sono anche ciò che viene parsato,
    così non c'è finestra fra il controllo e l'uso.
    """

    meta = SCHEMI[codice]
    percorso = SCHEMA_DIR / meta["filename"]
    try:
        contenuto = percorso.read_bytes()
    except OSError as exc:
        raise EmirError(f"Schema EMIR non leggibile ({meta['radice']}): {exc}") from exc
    if hashlib.sha256(contenuto).hexdigest() != meta["sha256"]:
        raise EmirError(
            f"Integrità dello schema EMIR non verificata ({meta['radice']}): "
            "l'impronta del file non corrisponde a quella dichiarata."
        )
    return contenuto


def _definizioni(codice: str) -> dict[str, Any]:
    """Indice dei tipi dichiarati nello schema, compilato una volta sola."""

    if codice in _cache_definizioni:
        return _cache_definizioni[codice]
    if etree is None:  # pragma: no cover
        raise EmirError("lxml non è installato: impossibile leggere lo schema EMIR.")
    contenuto = _byte_schema(codice)
    try:
        # Il parser esplicito è la convenzione del modulo: gli XSD sono nostri
        # e verificati per impronta, ma la sicurezza non deve dipendere dai
        # default della libreria installata.
        radice = etree.fromstring(contenuto, _parser_sicuro())
    except etree.XMLSyntaxError as exc:
        raise EmirError(f"Schema EMIR non leggibile ({SCHEMI[codice]['radice']}): {exc}") from exc
    indice = {t.get("name"): t for t in radice if t.get("name")}
    _cache_definizioni[codice] = indice
    return indice


def codici_ammessi(tipo: str, codice: str = "segnalazione") -> list[str]:
    """Valori ammessi da un tipo dello XSD, seguendo le restrizioni annidate.

    Nessuna enumerazione è scritta a mano nel modulo: se ESMA cambia lo
    schema, cambiano le tendine, e il test di copertura segnala le etichette
    rimaste orfane invece di lasciarle passare.
    """

    chiave = (codice, tipo)
    if chiave in _cache_enumerazioni:
        return _cache_enumerazioni[chiave]
    valori = _enumerazione(_definizioni(codice), tipo, 0)
    _cache_enumerazioni[chiave] = valori
    return valori


def _enumerazione(indice: dict[str, Any], tipo: str, profondita: int) -> list[str]:
    nodo = indice.get(tipo)
    if nodo is None or profondita > 6:
        return []
    valori = [e.get("value") for e in nodo.iter(XSD_NS + "enumeration") if e.get("value")]
    if valori:
        return valori
    for restrizione in nodo.iter(XSD_NS + "restriction"):
        base = (restrizione.get("base") or "").split(":")[-1]
        if base and base in indice:
            ereditati = _enumerazione(indice, base, profondita + 1)
            if ereditati:
                return ereditati
    return []


def catalogo() -> dict[str, Any]:
    """Metadati e tendine per l'interfaccia, senza percorsi locali interni."""

    def tendina(tipo: str, codice: str = "segnalazione") -> list[dict[str, str]]:
        tabella = ETICHETTE.get(tipo) or ETICHETTE_ESITO.get(tipo) or {}
        # L'ordine è quello delle etichette quando c'è: mette in cima i valori
        # che un operatore del gas usa davvero, invece dell'ordine dello XSD.
        ammessi = codici_ammessi(tipo, codice)
        ordinati = [c for c in tabella if c in ammessi] + [c for c in ammessi if c not in tabella]
        return [{"codice": c, "etichetta": tabella.get(c, c)} for c in ordinati]

    return {
        "pacchetto": PACCHETTO,
        "schemi": [
            {
                "codice": meta["codice"],
                "etichetta": meta["etichetta"],
                "radice": meta["radice"],
                "namespace": meta["namespace"],
                "sha256": meta["sha256"],
            }
            for meta in SCHEMI.values()
        ],
        "azioni": [
            {
                "codice": chiave,
                "sigla": voce["sigla"],
                "etichetta": voce["etichetta"],
                "descrizione": voce["descrizione"],
                "profilo": voce["profilo"],
                "elemento": voce["elemento"],
            }
            for chiave, voce in AZIONI.items()
        ],
        "nature": [{"codice": c, "etichetta": v["etichetta"]} for c, v in NATURE.items()],
        "sezioni_nace": [{"codice": c, "etichetta": e} for c, e in SEZIONI_NACE.items()],
        "prodotti": [{"codice": c, "etichetta": v["etichetta"]} for c, v in PRODOTTI.items()],
        "contratti": tendina("FinancialInstrumentContractType2Code"),
        "classi_attivo": tendina("ProductType4Code__1"),
        "consegne": tendina("PhysicalTransferType4Code"),
        "accordi": tendina("MasterAgreementType2Code"),
        "eventi": tendina("DerivativeEventType3Code__1"),
        "obblighi_compensazione": tendina("ClearingObligationType1Code"),
        "valutazioni": tendina("ValuationType1Code"),
        "livelli": tendina("ModificationLevel1Code"),
        "lati": tendina("OptionParty1Code"),
        "settori_finanziari": tendina("FinancialPartySectorType3Code__1"),
        "carichi": tendina("EnergyLoadType1Code"),
        "dettagli_gas": tendina("AssetClassDetailedSubProductType31Code"),
        "dettagli_elettricita": tendina("AssetClassDetailedSubProductType5Code"),
        "unita_energia": tendina("EnergyQuantityUnit2Code"),
        "durate": tendina("DurationType1Code"),
        "giorni_settimana": tendina("WeekDay3Code__1"),
        "esiti": tendina("ReportingMessageStatus2Code", "esito"),
        "operazioni_esito": tendina("TransactionOperationType10Code__1", "esito"),
    }


# ------------------------------------------------------------- validazioni

def _errore(errors: list[dict[str, str]], campo: str, messaggio: str) -> None:
    if len(errors) < MAX_ERRORI:
        errors.append({"field": campo, "message": messaggio})


def _testo(
    dati: dict[str, Any],
    chiave: str,
    errors: list[dict[str, str]],
    etichetta: str,
    *,
    obbligatorio: bool = True,
    max_len: int = MAX_TESTO,
) -> str:
    grezzo = dati.get(chiave)
    if grezzo is None or isinstance(grezzo, bool):
        valore = ""
    elif isinstance(grezzo, (int, float)):
        valore = str(grezzo)
    elif isinstance(grezzo, str):
        valore = grezzo.strip()
    else:
        _errore(errors, chiave, f"{etichetta}: atteso un valore testuale.")
        return ""
    if CARATTERI_VIETATI.search(valore):
        _errore(errors, chiave, f"{etichetta}: contiene caratteri non ammessi in un documento XML.")
        return ""
    if len(valore) > max_len:
        _errore(errors, chiave, f"{etichetta}: massimo {max_len} caratteri (ricevuti {len(valore)}).")
        return ""
    if not valore and obbligatorio:
        _errore(errors, chiave, f"{etichetta}: campo obbligatorio.")
    return valore


def _booleano(dati: dict[str, Any], chiave: str) -> str:
    valore = dati.get(chiave)
    if isinstance(valore, str):
        return "true" if valore.strip().lower() in {"true", "1", "si", "sì", "vero"} else "false"
    return "true" if bool(valore) else "false"


def _codice(
    valore: str,
    tipo: str,
    campo: str,
    etichetta: str,
    errors: list[dict[str, str]],
    *,
    codice_schema: str = "segnalazione",
) -> str:
    if not valore:
        return ""
    ammessi = codici_ammessi(tipo, codice_schema)
    if valore not in ammessi:
        tabella = ETICHETTE.get(tipo) or ETICHETTE_ESITO.get(tipo) or {}
        elenco = ", ".join(f"{c} ({tabella[c]})" if c in tabella else c for c in ammessi[:12])
        _errore(errors, campo, f"{etichetta}: valore non previsto dallo schema. Ammessi: {elenco}.")
        return ""
    return valore


def cifra_di_controllo_lei(lei: str) -> bool:
    """Verifica le due cifre finali del LEI con ISO/IEC 7064 MOD 97-10.

    È la stessa aritmetica dell'IBAN: lettere convertite in numeri (A=10…
    Z=35), il numero intero che ne risulta deve dare resto 1 diviso 97.  Un
    LEI con una cifra sbagliata passa la regex ma non questo controllo, e
    andrebbe a sbattere contro le regole di validazione del TR giorni dopo.
    """

    if not LEI_RE.fullmatch(lei):
        return False
    numerico = "".join(str(int(c, 36)) for c in lei)
    return int(numerico) % 97 == 1


def _lei(valore: str, campo: str, etichetta: str, errors: list[dict[str, str]]) -> str:
    if not valore:
        return ""
    if not LEI_RE.fullmatch(valore):
        _errore(
            errors,
            campo,
            f"{etichetta}: un LEI ha 20 caratteri, 18 alfanumerici maiuscoli più 2 cifre di controllo.",
        )
        return ""
    if not cifra_di_controllo_lei(valore):
        _errore(
            errors,
            campo,
            f"{etichetta}: le due cifre finali non superano il controllo ISO 17442 (MOD 97-10). "
            "Ricontrolla il codice sul registro GLEIF.",
        )
        return ""
    return valore


def _uti(valore: str, campo: str, errors: list[dict[str, str]]) -> str:
    if not valore:
        return ""
    if not UTI_RE.fullmatch(valore):
        _errore(
            errors,
            campo,
            "UTI: da 20 a 52 caratteri alfanumerici maiuscoli; i primi 20 sono il LEI "
            "di chi lo ha generato (ISO 23897).",
        )
        return ""
    return valore


def genera_uti(lei: str, chiave: str) -> str:
    """Costruisce un UTI conforme al formato ISO 23897 a partire da una chiave.

    Lo standard fissa la *forma* — primi 20 caratteri uguali al LEI di chi
    genera il codice, coda alfanumerica maiuscola fino a 52 in tutto — ma non
    impone come derivare la coda: la sceglie chi genera.  Qui la coda è
    deterministica sulla chiave dell'operazione, così due invii dello stesso
    contratto producono lo stesso UTI invece di duplicarlo nel registro.
    Non è un algoritmo ESMA e non va spacciato per tale: è la nostra regola,
    dichiarata.
    """

    impronta = hashlib.sha256(f"{lei}|{chiave}".encode("utf-8")).digest()
    numero = int.from_bytes(impronta, "big")
    coda = []
    for _ in range(22):
        numero, resto = divmod(numero, len(ALFABETO_UTI))
        coda.append(ALFABETO_UTI[resto])
    return lei + "".join(coda)


def _decimale(
    valore: Any,
    campo: str,
    etichetta: str,
    errors: list[dict[str, str]],
    *,
    obbligatorio: bool = True,
    negativo: bool = False,
) -> str:
    testo = "" if valore is None or isinstance(valore, bool) else str(valore).strip()
    # Accetta sia 1.234,50 sia 1234.50 senza togliere ciecamente i punti,
    # altrimenti 33.50 diventerebbe 3350 in silenzio.
    if "," in testo and "." in testo:
        if testo.rfind(",") > testo.rfind("."):
            testo = testo.replace(".", "").replace(",", ".")
        else:
            testo = testo.replace(",", "")
    elif "," in testo:
        testo = testo.replace(",", ".")
    if not testo:
        if obbligatorio:
            _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return ""
    if len(testo) > MAX_NUMERO:
        _errore(errors, campo, f"{etichetta}: numero troppo lungo.")
        return ""
    schema = r"^-?[0-9]{1,20}(\.[0-9]{1,5})?$" if negativo else r"^[0-9]{1,20}(\.[0-9]{1,5})?$"
    if not re.fullmatch(schema, testo):
        segno = " (il segno meno è ammesso)" if negativo else " (nessun segno, nessun separatore di migliaia)"
        _errore(errors, campo, f"{etichetta}: atteso un numero con al massimo 5 decimali{segno}.")
        return ""
    return testo


def _data(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]], *, obbligatorio: bool = True) -> str:
    testo = "" if valore is None else str(valore).strip()
    if not testo:
        if obbligatorio:
            _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return ""
    try:
        return date.fromisoformat(testo).isoformat()
    except ValueError:
        _errore(errors, campo, f"{etichetta}: data non valida, attesa nella forma AAAA-MM-GG.")
        return ""


def _istante(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]], *, obbligatorio: bool = True) -> str:
    testo = "" if valore is None else str(valore).strip()
    if not testo:
        if obbligatorio:
            _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return ""
    normalizzato = testo[:-1] + "+00:00" if testo.endswith("Z") else testo
    try:
        momento = datetime.fromisoformat(normalizzato)
    except ValueError:
        _errore(
            errors,
            campo,
            f"{etichetta}: momento non valido. Atteso AAAA-MM-GGTHH:MM:SSZ (orario UTC).",
        )
        return ""
    if momento.tzinfo is None:
        # Un istante senza fuso è ambiguo di due ore in Italia: piuttosto che
        # indovinare, si dichiara UTC e lo si scrive nel documento.
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ora(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]]) -> str:
    testo = "" if valore is None else str(valore).strip()
    if not testo:
        return ""
    if not ORA_RE.fullmatch(testo):
        _errore(errors, campo, f"{etichetta}: orario non valido, atteso HH:MM o HH:MM:SS.")
        return ""
    return testo if len(testo) == 8 else f"{testo}:00"


def ora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------- costruzione XML

def _figlio(padre, ns: str, nome: str, testo: str | None = None, **attributi):
    elemento = etree.SubElement(padre, f"{{{ns}}}{nome}")
    if testo is not None:
        elemento.text = testo
    for chiave, valore in attributi.items():
        if valore:
            elemento.set(chiave, valore)
    return elemento


def _parte(padre, ns: str, nome: str, lei: str):
    """Blocco <…><LEI>…</LEI></…>, la forma con cui EMIR nomina un soggetto."""

    nodo = _figlio(padre, ns, nome)
    _figlio(nodo, ns, "LEI", lei)
    return nodo


def _natura(padre, ns: str, natura: str, settore: str, sopra_soglia: str, collegata: str | None) -> None:
    """Ramo <Ntr>: quattro alternative, non un codice."""

    nodo = _figlio(padre, ns, "Ntr")
    ramo = NATURE[natura]["ramo"]
    if ramo == "NFI":
        nfi = _figlio(nodo, ns, "NFI")
        settore_el = _figlio(nfi, ns, "Sctr")
        _figlio(settore_el, ns, "Id", settore)
        _figlio(nfi, ns, "ClrThrshld", sopra_soglia)
        if collegata is not None:
            _figlio(nfi, ns, "DrctlyLkdActvty", collegata)
    elif ramo == "FI":
        fi = _figlio(nodo, ns, "FI")
        settore_el = _figlio(fi, ns, "Sctr")
        _figlio(settore_el, ns, "Cd", settore)
        _figlio(fi, ns, "ClrThrshld", sopra_soglia)
    else:
        _figlio(nodo, ns, ramo, "NORE")


def genera_segnalazione(dati: dict[str, Any]) -> DocumentoEmir:
    """Costruisce la segnalazione auth.030 e la valida contro l'XSD ESMA."""

    if etree is None:  # pragma: no cover
        raise EmirError("lxml non è installato: impossibile validare il documento EMIR.")
    if not isinstance(dati, dict):
        raise EmirError("Dati della segnalazione non validi: atteso un oggetto.")

    errors: list[dict[str, str]] = []
    avvisi: list[str] = []
    meta = SCHEMI["segnalazione"]
    ns = meta["namespace"]

    azione_chiave = _testo(dati, "azione", errors, "Tipo di azione", max_len=40)
    azione = AZIONI.get(azione_chiave)
    if azione_chiave and azione is None:
        elenco = ", ".join(f"{c} ({v['etichetta']})" for c, v in AZIONI.items())
        _errore(errors, "azione", f"Tipo di azione non gestito. Ammessi: {elenco}.")
    if azione is None:
        raise EmirError("La segnalazione EMIR non è stata generata: correggi i campi segnalati.", errors)
    profilo = PROFILI[azione["profilo"]]

    livello = _testo(dati, "livello", errors, "Livello", obbligatorio=False, max_len=8) or "TCTN"
    livello = _codice(livello, "ModificationLevel1Code", "livello", "Livello", errors) or "TCTN"
    if azione["profilo"] == "posizione" and livello != "TCTN":
        # Lo schema stringe la scelta a TCTN per il solo PosCmpnt: un PSTN qui
        # verrebbe respinto dallo XSD con un messaggio incomprensibile.
        _errore(errors, "livello", "Un componente di posizione si segnala sempre a livello di operazione (TCTN).")

    segnalante = _lei(_testo(dati, "segnalante_lei", errors, "LEI della controparte segnalante", max_len=20),
                      "segnalante_lei", "LEI della controparte segnalante", errors)
    controparte = _lei(_testo(dati, "controparte_lei", errors, "LEI dell'altra controparte", max_len=20),
                       "controparte_lei", "LEI dell'altra controparte", errors)
    mittente = _lei(_testo(dati, "mittente_lei", errors, "LEI del soggetto che trasmette", max_len=20),
                    "mittente_lei", "LEI del soggetto che trasmette", errors)
    responsabile = _lei(_testo(dati, "responsabile_lei", errors, "LEI del responsabile della segnalazione",
                               obbligatorio=False, max_len=20),
                        "responsabile_lei", "LEI del responsabile della segnalazione", errors)
    intermediario = _lei(_testo(dati, "intermediario_lei", errors, "LEI dell'intermediario",
                                obbligatorio=False, max_len=20),
                         "intermediario_lei", "LEI dell'intermediario", errors)
    if segnalante and controparte and segnalante == controparte:
        _errore(errors, "controparte_lei", "Le due controparti non possono avere lo stesso LEI.")

    uti = _uti(_testo(dati, "uti", errors, "UTI", obbligatorio=False, max_len=MAX_IDENT), "uti", errors)
    if not uti and segnalante:
        # Senza UTI il documento non è generabile: se manca lo si costruisce
        # in forma conforme e lo si dichiara, invece di respingere l'operatore.
        chiave = "|".join(
            str(dati.get(c, "")) for c in ("controparte_lei", "data_efficacia", "momento_esecuzione", "nozionale")
        )
        uti = genera_uti(segnalante, chiave)
        avvisi.append(
            f"UTI assente: generato {uti} in forma ISO 23897 (LEI del segnalante + coda deterministica). "
            "Se la controparte ne ha già assegnato uno, sostituiscilo."
        )
    elif not uti:
        _errore(errors, "uti", "UTI: campo obbligatorio quando manca il LEI del segnalante.")

    # ---- controparti
    natura_segnalante = _testo(dati, "segnalante_natura", errors, "Natura del segnalante",
                               obbligatorio=profilo["controparte_estesa"], max_len=8)
    natura_controparte = _testo(dati, "controparte_natura", errors, "Natura dell'altra controparte",
                                obbligatorio=False, max_len=8)
    for campo, valore in (("segnalante_natura", natura_segnalante), ("controparte_natura", natura_controparte)):
        if valore and valore not in NATURE:
            _errore(errors, campo, f"Natura non prevista. Ammesse: {', '.join(NATURE)}.")

    settore_segnalante = _settore(dati, "segnalante", natura_segnalante, errors,
                                  obbligatorio=profilo["controparte_estesa"])
    settore_controparte = _settore(dati, "controparte", natura_controparte, errors, obbligatorio=False)

    lato = _testo(dati, "segnalante_lato", errors, "Lato dell'operazione",
                  obbligatorio=profilo["controparte_estesa"], max_len=8)
    lato = _codice(lato, "OptionParty1Code", "segnalante_lato", "Lato dell'operazione", errors)

    # ---- contratto e operazione
    contratto_tipo = classe = cfi = valuta_regolamento = ""
    if profilo["contratto"]:
        contratto_tipo = _codice(
            _testo(dati, "contratto_tipo", errors, "Tipo di contratto", max_len=8),
            "FinancialInstrumentContractType2Code", "contratto_tipo", "Tipo di contratto", errors,
        )
        classe = _codice(
            _testo(dati, "classe_attivo", errors, "Classe di attività", max_len=8),
            "ProductType4Code__1", "classe_attivo", "Classe di attività", errors,
        )
        cfi = _testo(dati, "cfi", errors, "Classificazione del prodotto (CFI)", max_len=6)
        if cfi and not CFI_RE.fullmatch(cfi):
            _errore(errors, "cfi", "Classificazione del prodotto: codice CFI ISO 10962, 6 lettere maiuscole "
                                   "(per un forward su merce inizia con JC).")
            cfi = ""
        valuta_regolamento = _valuta(dati, "valuta_regolamento", "Valuta di regolamento", errors, obbligatorio=False)

    operazione = profilo["operazione"]
    portafoglio = _testo(dati, "portafoglio", errors, "Codice del portafoglio", obbligatorio=False, max_len=MAX_IDENT)
    sede = _testo(dati, "sede", errors, "Sede di negoziazione", obbligatorio=False, max_len=4)
    if sede and not MIC_RE.fullmatch(sede):
        _errore(errors, "sede", "Sede di negoziazione: codice MIC di 4 caratteri (XOFF se fuori sede).")
        sede = ""
    riferimento = _testo(dati, "riferimento_interno", errors, "Riferimento interno",
                         obbligatorio=False, max_len=MAX_IDENT)

    nozionale = valuta_nozionale = quantita = consegna = ""
    esecuzione = efficacia = scadenza = regolamento = ""
    accordo = accordo_anno = accordo_altro = ""
    prezzo = valuta_prezzo = ""
    if operazione == "completa":
        prezzo = _decimale(dati.get("prezzo"), "prezzo", "Prezzo", errors, obbligatorio=False)
        valuta_prezzo = _valuta(dati, "valuta_prezzo", "Valuta del prezzo", errors, obbligatorio=bool(prezzo))
        nozionale = _decimale(dati.get("nozionale"), "nozionale", "Importo nozionale", errors)
        valuta_nozionale = _valuta(dati, "valuta_nozionale", "Valuta del nozionale", errors)
        quantita = _decimale(dati.get("quantita"), "quantita", "Quantità nozionale", errors, obbligatorio=False)
        consegna = _codice(
            _testo(dati, "consegna", errors, "Tipo di consegna", max_len=8),
            "PhysicalTransferType4Code", "consegna", "Tipo di consegna", errors,
        )
        esecuzione = _istante(dati.get("momento_esecuzione"), "momento_esecuzione", "Momento dell'esecuzione", errors)
        efficacia = _data(dati.get("data_efficacia"), "data_efficacia", "Data di efficacia", errors)
        scadenza = _data(dati.get("data_scadenza"), "data_scadenza", "Data di scadenza", errors, obbligatorio=False)
        regolamento = _data(dati.get("data_regolamento"), "data_regolamento", "Data di regolamento", errors,
                            obbligatorio=False)
        if efficacia and scadenza and scadenza < efficacia:
            _errore(errors, "data_scadenza", "La data di scadenza precede quella di efficacia.")
        if esecuzione and scadenza:
            # Un contratto non può scadere prima di essere stato concluso. La
            # tolleranza di un giorno però è necessaria: un within-day sul
            # giorno gas D negoziato dopo la mezzanotte ha l'esecuzione con
            # data di calendario D+1 e la scadenza ancora a D — legittimo,
            # perché il giorno gas finisce alle 06:00 del giorno dopo.
            giorno_esecuzione = date.fromisoformat(esecuzione[:10])
            if date.fromisoformat(scadenza) < giorno_esecuzione - timedelta(days=1):
                _errore(
                    errors,
                    "data_scadenza",
                    "La data di scadenza precede il giorno dell'esecuzione: un contratto "
                    "non può scadere prima di essere stato concluso.",
                )
            elif date.fromisoformat(scadenza) < giorno_esecuzione:
                avvisi.append(
                    "La scadenza cade il giorno di calendario precedente all'esecuzione: "
                    "è coerente solo con un prodotto a giorno gas negoziato dopo la mezzanotte "
                    "(within-day). Se non è questo il caso, ricontrolla le date."
                )
        accordo = _codice(
            _testo(dati, "accordo_tipo", errors, "Accordo quadro", max_len=8),
            "MasterAgreementType2Code", "accordo_tipo", "Accordo quadro", errors,
        )
        accordo_anno = _anno(dati, errors)
        accordo_altro = _testo(dati, "accordo_altro", errors, "Altro accordo quadro", obbligatorio=False, max_len=50)
        if accordo == "OTHR" and not accordo_altro:
            _errore(errors, "accordo_altro", "Con accordo quadro «Altro» va indicato di quale accordo si tratta.")

    cessazione = ""
    if operazione == "cessazione":
        cessazione = _data(dati.get("data_cessazione"), "data_cessazione", "Data di cessazione anticipata", errors)

    successivo = ""
    if azione["profilo"] == "posizione":
        successivo = _uti(
            _testo(dati, "uti_posizione", errors, "UTI della posizione", max_len=MAX_IDENT),
            "uti_posizione", errors,
        )

    # Il tipo di evento non c'è in tutte le azioni, e non è una scelta di
    # stile: lo XSD usa una variante diversa di DerivativeEvent per ciascun
    # elemento contenitore.  Una correzione, una riattivazione, un componente
    # di posizione e un annullamento *non ammettono* <Tp>; una nuova
    # operazione e una cessazione lo pretendono.  Scriverlo dove non va
    # produce un documento che lo schema respinge alla riga giusta ma con un
    # messaggio incomprensibile, quindi il caso si intercetta qui.
    regola_evento = azione["evento"]
    evento_tipo = _codice(
        _testo(dati, "evento_tipo", errors, "Tipo di evento",
               obbligatorio=regola_evento == "obbligatorio", max_len=8),
        "DerivativeEventType3Code__1", "evento_tipo", "Tipo di evento", errors,
    )
    if evento_tipo and regola_evento == "assente":
        evento_tipo = ""
        avvisi.append(
            f"Il tipo di evento non è previsto per l'azione «{azione['etichetta']}»: "
            "il campo è stato ignorato."
        )
    evento_data = _data(dati.get("evento_data"), "evento_data", "Data dell'evento", errors)

    obbligo = compensato = ccp = momento_compensazione = ""
    if operazione == "completa":
        obbligo = _codice(
            _testo(dati, "obbligo_compensazione", errors, "Obbligo di compensazione",
                   obbligatorio=False, max_len=8),
            "ClearingObligationType1Code", "obbligo_compensazione", "Obbligo di compensazione", errors,
        )
        compensato = _booleano(dati, "compensato")
        if compensato == "true":
            ccp = _lei(_testo(dati, "ccp_lei", errors, "LEI della controparte centrale",
                              obbligatorio=False, max_len=20),
                       "ccp_lei", "LEI della controparte centrale", errors)
            momento_compensazione = _istante(dati.get("momento_compensazione"), "momento_compensazione",
                                             "Momento della compensazione", errors, obbligatorio=False)

    # ---- valutazione
    valore = valuta_valutazione = momento_valutazione = tipo_valutazione = delta = ""
    richiesta = profilo["valutazione"]
    if richiesta != "assente":
        obbligatoria = richiesta == "obbligatoria"
        fornita = any(dati.get(c) for c in ("valutazione_valore", "valutazione_momento", "valutazione_tipo"))
        if obbligatoria or fornita:
            valore = _decimale(dati.get("valutazione_valore"), "valutazione_valore", "Valore del contratto",
                               errors, negativo=True)
            valuta_valutazione = _valuta(dati, "valutazione_valuta", "Valuta della valutazione", errors)
            momento_valutazione = _istante(dati.get("valutazione_momento"), "valutazione_momento",
                                           "Momento della valutazione", errors)
            tipo_valutazione = _codice(
                _testo(dati, "valutazione_tipo", errors, "Tipo di valutazione", max_len=8),
                "ValuationType1Code", "valutazione_tipo", "Tipo di valutazione", errors,
            )
            delta = _decimale(dati.get("valutazione_delta"), "valutazione_delta", "Delta", errors,
                              obbligatorio=False, negativo=True)

    # ---- energia
    energia = _energia(dati, errors) if operazione == "completa" else None

    momento_segnalazione = _istante(dati.get("momento_segnalazione"), "momento_segnalazione",
                                    "Momento della segnalazione", errors, obbligatorio=False) or ora_iso()

    if errors:
        raise EmirError("La segnalazione EMIR non è stata generata: correggi i campi segnalati.", errors)

    # ------------------------------------------------------- serializzazione
    radice = etree.Element(f"{{{ns}}}Document", nsmap={None: ns})
    rapporto = _figlio(radice, ns, "DerivsTradRpt")
    intestazione = _figlio(rapporto, ns, "RptHdr")
    # Un documento, una segnalazione: il numero di record è quindi sempre 1.
    # Gli invii massivi si compongono lato TR, non qui.
    _figlio(intestazione, ns, "NbRcrds", "1")
    dati_operazione = _figlio(rapporto, ns, "TradData")
    segnalazione = _figlio(_figlio(dati_operazione, ns, "Rpt"), ns, azione["elemento"])

    specifici = _figlio(segnalazione, ns, "CtrPtySpcfcData")
    controparti = _figlio(specifici, ns, "CtrPty")

    segnalante_el = _figlio(controparti, ns, "RptgCtrPty")
    _parte(_figlio(_figlio(segnalante_el, ns, "Id"), ns, "Lgl"), ns, "Id", segnalante)
    if profilo["controparte_estesa"]:
        _natura(segnalante_el, ns, natura_segnalante, settore_segnalante,
                _booleano(dati, "segnalante_sopra_soglia"),
                _booleano(dati, "segnalante_attivita_collegata") if natura_segnalante == "NFC" else None)
        _figlio(_figlio(segnalante_el, ns, "DrctnOrSd"), ns, "CtrPtySd", lato)

    altra = _figlio(controparti, ns, "OthrCtrPty")
    _parte(_figlio(_figlio(altra, ns, "IdTp"), ns, "Lgl"), ns, "Id", controparte)
    if profilo["controparte_estesa"]:
        if natura_controparte:
            _natura(altra, ns, natura_controparte, settore_controparte,
                    _booleano(dati, "controparte_sopra_soglia"), None)
        _figlio(altra, ns, "RptgOblgtn", _booleano(dati, "controparte_obbligo"))

    if intermediario and profilo["controparte_estesa"]:
        _figlio(_figlio(controparti, ns, "Brkr"), ns, "LEI", intermediario)
    _figlio(_figlio(controparti, ns, "SubmitgAgt"), ns, "LEI", mittente)
    if responsabile:
        _figlio(_figlio(controparti, ns, "NttyRspnsblForRpt"), ns, "LEI", responsabile)

    if valore:
        valutazione_el = _figlio(specifici, ns, "Valtn")
        importo = _figlio(valutazione_el, ns, "CtrctVal")
        segno = valore.startswith("-")
        _figlio(importo, ns, "Amt", valore.lstrip("-"), Ccy=valuta_valutazione)
        # Il segno viaggia in un campo suo: l'importo è sempre positivo.
        _figlio(importo, ns, "Sgn", "false" if segno else "true")
        _figlio(valutazione_el, ns, "TmStmp", momento_valutazione)
        _figlio(valutazione_el, ns, "Tp", tipo_valutazione)
        if delta:
            _figlio(valutazione_el, ns, "Dlta", delta)
    _figlio(specifici, ns, "RptgTmStmp", momento_segnalazione)

    comuni = _figlio(segnalazione, ns, "CmonTradData")
    if profilo["contratto"]:
        contratto_el = _figlio(comuni, ns, "CtrctData")
        _figlio(contratto_el, ns, "CtrctTp", contratto_tipo)
        _figlio(contratto_el, ns, "AsstClss", classe)
        _figlio(contratto_el, ns, "PdctClssfctn", cfi)
        if valuta_regolamento:
            _figlio(_figlio(contratto_el, ns, "SttlmCcy"), ns, "Ccy", valuta_regolamento)
        _figlio(contratto_el, ns, "DerivBasedOnCrptAsst", _booleano(dati, "su_cripto"))

    transazione = _figlio(comuni, ns, "TxData")
    _figlio(_figlio(transazione, ns, "TxId"), ns, "UnqTxIdr", uti)
    if successivo:
        _figlio(_figlio(transazione, ns, "SbsqntTxId"), ns, "UnqTxIdr", successivo)

    portafoglio_el = _figlio(_figlio(transazione, ns, "CollPrtflCd"), ns, "Prtfl")
    if portafoglio:
        _figlio(portafoglio_el, ns, "Cd", portafoglio)
    else:
        _figlio(portafoglio_el, ns, "NoPrtfl", "NOAP")

    if operazione == "completa":
        if riferimento:
            _figlio(transazione, ns, "RptTrckgNb", riferimento)
        if sede:
            _figlio(transazione, ns, "PltfmIdr", sede)
        if prezzo:
            valore_el = _figlio(_figlio(_figlio(transazione, ns, "TxPric"), ns, "Pric"), ns, "MntryVal")
            _figlio(valore_el, ns, "Amt", prezzo, Ccy=valuta_prezzo)
        importo = _figlio(_figlio(_figlio(transazione, ns, "NtnlAmt"), ns, "FrstLeg"), ns, "Amt")
        _figlio(importo, ns, "Amt", nozionale, Ccy=valuta_nozionale)
        if quantita:
            _figlio(_figlio(_figlio(transazione, ns, "NtnlQty"), ns, "FrstLeg"), ns, "TtlQty", quantita)
        _figlio(transazione, ns, "DlvryTp", consegna)
        _figlio(transazione, ns, "ExctnTmStmp", esecuzione)
        _figlio(transazione, ns, "FctvDt", efficacia)
        if scadenza:
            _figlio(transazione, ns, "XprtnDt", scadenza)
        if regolamento:
            _figlio(transazione, ns, "SttlmDt", regolamento)
        accordo_el = _figlio(transazione, ns, "MstrAgrmt")
        _figlio(_figlio(accordo_el, ns, "Tp"), ns, "Tp", accordo)
        if accordo_anno:
            _figlio(accordo_el, ns, "Vrsn", accordo_anno)
        if accordo_altro:
            _figlio(accordo_el, ns, "OthrMstrAgrmtDtls", accordo_altro)
    elif operazione == "cessazione":
        _figlio(transazione, ns, "EarlyTermntnDt", cessazione)

    evento_el = _figlio(transazione, ns, "DerivEvt")
    if evento_tipo:
        _figlio(evento_el, ns, "Tp", evento_tipo)
    _figlio(_figlio(evento_el, ns, "TmStmp"), ns, "Dt", evento_data)

    if operazione == "completa":
        compensazione = _figlio(transazione, ns, "TradClr")
        if obbligo:
            _figlio(compensazione, ns, "ClrOblgtn", obbligo)
        stato = _figlio(compensazione, ns, "ClrSts")
        if compensato == "true":
            dettagli = _figlio(_figlio(stato, ns, "Clrd"), ns, "Dtls")
            if ccp:
                _figlio(_figlio(dettagli, ns, "CCP"), ns, "LEI", ccp)
            if momento_compensazione:
                _figlio(dettagli, ns, "ClrDtTm", momento_compensazione)
        else:
            _figlio(_figlio(stato, ns, "NonClrd"), ns, "Rsn", "NORE")
        _figlio(compensazione, ns, "IntraGrp", _booleano(dati, "infragruppo"))

        if energia:
            _merce(transazione, ns, energia)

    _figlio(segnalazione, ns, "Lvl", livello)

    xml = etree.tostring(radice, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    _valida(xml, "segnalazione")
    return DocumentoEmir(
        codice="segnalazione",
        radice=meta["radice"],
        azione=azione_chiave,
        sigla_azione=azione["sigla"],
        livello=livello,
        uti=uti,
        controparte=controparte,
        schema_sha256=meta["sha256"],
        xml=xml.decode("utf-8"),
        xml_sha256=hashlib.sha256(xml).hexdigest(),
        size_bytes=len(xml),
        avvisi=tuple(avvisi),
    )


def _settore(
    dati: dict[str, Any],
    prefisso: str,
    natura: str,
    errors: list[dict[str, str]],
    *,
    obbligatorio: bool,
) -> str:
    """Il settore cambia dominio con la natura: NACE per le NFC, codice ESMA per le FC."""

    campo = f"{prefisso}_settore"
    etichetta = "Settore" if prefisso == "segnalante" else "Settore dell'altra controparte"
    valore = _testo(dati, campo, errors, etichetta, obbligatorio=obbligatorio and natura in {"NFC", "FC"}, max_len=4)
    if not valore:
        return ""
    if natura == "NFC":
        if valore not in SEZIONI_NACE:
            _errore(errors, campo, f"{etichetta}: attesa la lettera della sezione NACE, da A a U.")
            return ""
        return valore
    if natura == "FC":
        return _codice(valore, "FinancialPartySectorType3Code__1", campo, etichetta, errors)
    # CCP e "altro" non portano settore: se arriva lo si ignora, senza errore.
    return ""


def _valuta(dati: dict[str, Any], campo: str, etichetta: str, errors: list[dict[str, str]], *,
            obbligatorio: bool = True) -> str:
    valore = _testo(dati, campo, errors, etichetta, obbligatorio=obbligatorio, max_len=3)
    if valore and not VALUTA_RE.fullmatch(valore):
        _errore(errors, campo, f"{etichetta}: codice ISO 4217 di 3 lettere maiuscole (es. EUR).")
        return ""
    return valore


def _anno(dati: dict[str, Any], errors: list[dict[str, str]]) -> str:
    grezzo = _testo(dati, "accordo_anno", errors, "Anno dell'accordo quadro", obbligatorio=False, max_len=4)
    if not grezzo:
        return ""
    if not re.fullmatch(r"(19|20)[0-9]{2}", grezzo):
        _errore(errors, "accordo_anno", "Anno dell'accordo quadro: quattro cifre fra 1900 e 2099.")
        return ""
    return grezzo


def _energia(dati: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any] | None:
    """Blocco merce + attributi energetici: è la parte che rende gas un gas."""

    prodotto = _testo(dati, "prodotto", errors, "Prodotto energetico", obbligatorio=False, max_len=20)
    if not prodotto:
        return None
    if prodotto not in PRODOTTI:
        _errore(errors, "prodotto", f"Prodotto energetico non gestito. Ammessi: {', '.join(PRODOTTI)}.")
        return None
    voce = PRODOTTI[prodotto]
    dettaglio = _codice(
        _testo(dati, "dettaglio_prodotto", errors, "Dettaglio del prodotto", obbligatorio=False, max_len=8),
        voce["dettaglio_tipo"], "dettaglio_prodotto", "Dettaglio del prodotto", errors,
    )

    punti: list[str] = []
    grezzi = dati.get("punti_consegna")
    if isinstance(grezzi, str):
        grezzi = [p for p in re.split(r"[\s,;]+", grezzi) if p]
    if isinstance(grezzi, list):
        if len(grezzi) > MAX_PUNTI:
            _errore(errors, "punti_consegna", f"Punti di consegna: massimo {MAX_PUNTI}.")
            grezzi = grezzi[:MAX_PUNTI]
        for indice, punto in enumerate(grezzi):
            testo = str(punto or "").strip().upper()
            if not testo:
                continue
            if not EIC_RE.fullmatch(testo):
                _errore(errors, "punti_consegna",
                        f"Punto di consegna #{indice + 1}: atteso un codice EIC di 16 caratteri.")
                continue
            punti.append(testo)
    elif grezzi is not None:
        _errore(errors, "punti_consegna", "Punti di consegna: atteso un elenco di codici EIC.")

    interconnessione = _testo(dati, "punto_interconnessione", errors, "Punto di interconnessione",
                              obbligatorio=False, max_len=16).upper()
    if interconnessione and not EIC_RE.fullmatch(interconnessione):
        _errore(errors, "punto_interconnessione", "Punto di interconnessione: atteso un codice EIC di 16 caratteri.")
        interconnessione = ""

    carico = _codice(
        _testo(dati, "tipo_carico", errors, "Tipo di carico", obbligatorio=False, max_len=8),
        "EnergyLoadType1Code", "tipo_carico", "Tipo di carico", errors,
    )

    intervalli: list[dict[str, Any]] = []
    grezzi_intervalli = dati.get("intervalli")
    if isinstance(grezzi_intervalli, list):
        if len(grezzi_intervalli) > MAX_INTERVALLI:
            _errore(errors, "intervalli", f"Intervalli di consegna: massimo {MAX_INTERVALLI}.")
            grezzi_intervalli = grezzi_intervalli[:MAX_INTERVALLI]
        for indice, grezzo in enumerate(grezzi_intervalli):
            if not isinstance(grezzo, dict):
                _errore(errors, "intervalli", f"Intervallo #{indice + 1}: atteso un oggetto.")
                continue
            voce_intervallo = {
                "da": _ora(grezzo.get("da"), f"intervalli.{indice}.da", f"Intervallo #{indice + 1} · ora iniziale",
                           errors),
                "a": _ora(grezzo.get("a"), f"intervalli.{indice}.a", f"Intervallo #{indice + 1} · ora finale", errors),
                "data_da": _data(grezzo.get("data_da"), f"intervalli.{indice}.data_da",
                                 f"Intervallo #{indice + 1} · dal", errors, obbligatorio=False),
                "data_a": _data(grezzo.get("data_a"), f"intervalli.{indice}.data_a",
                                f"Intervallo #{indice + 1} · al", errors, obbligatorio=False),
                "durata": _codice(str(grezzo.get("durata") or "").strip(), "DurationType1Code",
                                  f"intervalli.{indice}.durata", f"Intervallo #{indice + 1} · durata", errors),
                "capacita": _decimale(grezzo.get("capacita"), f"intervalli.{indice}.capacita",
                                      f"Intervallo #{indice + 1} · capacità", errors, obbligatorio=False),
                "unita": _codice(str(grezzo.get("unita") or "").strip(), "EnergyQuantityUnit2Code",
                                 f"intervalli.{indice}.unita", f"Intervallo #{indice + 1} · unità", errors),
                "giorni": [],
            }
            giorni = grezzo.get("giorni")
            if isinstance(giorni, list):
                for giorno in giorni[:11]:
                    valore = _codice(str(giorno or "").strip(), "WeekDay3Code__1",
                                     f"intervalli.{indice}.giorni", f"Intervallo #{indice + 1} · giorni", errors)
                    if valore:
                        voce_intervallo["giorni"].append(valore)
            if voce_intervallo["data_da"] and not voce_intervallo["data_a"]:
                # Lo XSD rende obbligatorio solo il termine finale: una data
                # iniziale senza finale non è rappresentabile.
                _errore(errors, f"intervalli.{indice}.data_a",
                        f"Intervallo #{indice + 1}: indicata la data iniziale, manca quella finale.")
            if voce_intervallo["capacita"] and not voce_intervallo["unita"]:
                _errore(errors, f"intervalli.{indice}.unita",
                        f"Intervallo #{indice + 1}: la capacità richiede l'unità di misura.")
            intervalli.append(voce_intervallo)
    elif grezzi_intervalli is not None:
        _errore(errors, "intervalli", "Intervalli di consegna: atteso un elenco.")

    return {
        "prodotto": voce,
        "dettaglio": dettaglio,
        "punti": punti,
        "interconnessione": interconnessione,
        "carico": carico,
        "intervalli": intervalli,
    }


def _merce(transazione, ns: str, energia: dict[str, Any]) -> None:
    voce = energia["prodotto"]
    merce = _figlio(_figlio(_figlio(transazione, ns, "Cmmdty"), ns, "Nrgy"), ns, voce["elemento"])
    _figlio(merce, ns, "BasePdct", voce["base"])
    _figlio(merce, ns, "SubPdct", voce["sotto"])
    if energia["dettaglio"]:
        _figlio(merce, ns, "AddtlSubPdct", energia["dettaglio"])

    if not (energia["punti"] or energia["interconnessione"] or energia["carico"] or energia["intervalli"]):
        return
    attributi = _figlio(transazione, ns, "NrgySpcfcAttrbts")
    for punto in energia["punti"]:
        _figlio(_figlio(attributi, ns, "DlvryPtOrZone"), ns, "Cd", punto)
    if energia["interconnessione"]:
        _figlio(_figlio(attributi, ns, "IntrCnnctnPt"), ns, "Cd", energia["interconnessione"])
    if energia["carico"]:
        _figlio(attributi, ns, "LdTp", energia["carico"])
    for intervallo in energia["intervalli"]:
        consegna = _figlio(attributi, ns, "DlvryAttr")
        if intervallo["da"]:
            orario = _figlio(consegna, ns, "DlvryIntrvl")
            _figlio(orario, ns, "FrTm", intervallo["da"])
            if intervallo["a"]:
                _figlio(orario, ns, "ToTm", intervallo["a"])
        if intervallo["data_a"]:
            periodo = _figlio(consegna, ns, "DlvryDt")
            if intervallo["data_da"]:
                _figlio(periodo, ns, "FrDt", intervallo["data_da"])
            _figlio(periodo, ns, "ToDt", intervallo["data_a"])
        if intervallo["durata"]:
            _figlio(consegna, ns, "Drtn", intervallo["durata"])
        for giorno in intervallo["giorni"]:
            _figlio(consegna, ns, "WkDay", giorno)
        if intervallo["capacita"]:
            _figlio(_figlio(consegna, ns, "DlvryCpcty"), ns, "Qty", intervallo["capacita"])
        if intervallo["unita"]:
            _figlio(_figlio(consegna, ns, "QtyUnit"), ns, "Cd", intervallo["unita"])


# ------------------------------------------------------------ intestazione

def genera_intestazione(dati: dict[str, Any]) -> DocumentoEmir:
    """Costruisce l'AppHdr head.001 che accompagna la segnalazione.

    ESMA pubblica lo schema di questa intestazione ma non l'involucro che la
    unisce al messaggio: quello lo definisce il Trade Repository.  Il file qui
    prodotto è quindi valido come documento a sé, e va consegnato al TR
    seguendo le sue istruzioni di confezionamento.
    """

    if etree is None:  # pragma: no cover
        raise EmirError("lxml non è installato: impossibile validare l'intestazione EMIR.")

    errors: list[dict[str, str]] = []
    meta = SCHEMI["intestazione"]
    ns = meta["namespace"]

    mittente = _lei(_testo(dati, "mittente_lei", errors, "LEI del mittente", max_len=20),
                    "mittente_lei", "LEI del mittente", errors)
    destinatario = _lei(_testo(dati, "destinatario_lei", errors, "LEI del Trade Repository", max_len=20),
                        "destinatario_lei", "LEI del Trade Repository", errors)
    identificativo = _testo(dati, "identificativo", errors, "Identificativo del messaggio", max_len=35)
    creazione = _istante(dati.get("creato_il"), "creato_il", "Momento di creazione", errors,
                         obbligatorio=False) or ora_iso()
    if errors:
        raise EmirError("L'intestazione EMIR non è stata generata: correggi i campi segnalati.", errors)

    radice = etree.Element(f"{{{ns}}}AppHdr", nsmap={None: ns})
    _soggetto_intestazione(radice, ns, "Fr", mittente)
    _soggetto_intestazione(radice, ns, "To", destinatario)
    _figlio(radice, ns, "BizMsgIdr", identificativo)
    _figlio(radice, ns, "MsgDefIdr", SCHEMI["segnalazione"]["radice"])
    _figlio(radice, ns, "CreDt", creazione)

    xml = etree.tostring(radice, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    _valida(xml, "intestazione")
    return DocumentoEmir(
        codice="intestazione",
        radice=meta["radice"],
        azione="intestazione",
        sigla_azione="HEAD",
        livello="",
        uti="",
        controparte=destinatario,
        schema_sha256=meta["sha256"],
        xml=xml.decode("utf-8"),
        xml_sha256=hashlib.sha256(xml).hexdigest(),
        size_bytes=len(xml),
        avvisi=(
            "ESMA non pubblica l'involucro che unisce intestazione e messaggio in un unico file: "
            "il confezionamento e il nome del file sono definiti dal Trade Repository destinatario.",
        ),
    )


def _soggetto_intestazione(radice, ns: str, nome: str, lei: str) -> None:
    """Fr/To dell'AppHdr: qui il LEI non ha un elemento proprio.

    A differenza del messaggio, l'intestazione ESMA non prevede un tag
    ``<LEI>``: il codice va in un identificativo generico
    ``OrgId/Id/OrgId/Othr/Id``.  Scriverlo come nel messaggio produce un
    documento che lo schema rifiuta.
    """

    nodo = _figlio(_figlio(radice, ns, nome), ns, "OrgId")
    generico = _figlio(_figlio(_figlio(nodo, ns, "Id"), ns, "OrgId"), ns, "Othr")
    _figlio(generico, ns, "Id", lei)


# -------------------------------------------------------- esito dal TR

def leggi_esito(contenuto: bytes | str) -> dict[str, Any]:
    """Legge l'auth.092 con cui il Trade Repository dichiara accolti e respinti.

    È l'equivalente EMIR del NOMRES: l'unico documento che dice davvero se la
    segnalazione è passata.  Ogni riga porta l'UTI, lo stato e le regole di
    validazione violate, così l'operatore vede *cosa* correggere e non solo
    *che* qualcosa è andato storto.
    """

    if etree is None:  # pragma: no cover
        raise EmirError("lxml non è installato: impossibile leggere l'esito EMIR.")

    grezzo = contenuto.encode("utf-8") if isinstance(contenuto, str) else contenuto
    if not grezzo or not grezzo.strip():
        raise EmirError("L'esito è vuoto: incolla il documento auth.092 ricevuto dal Trade Repository.")
    if len(grezzo) > MAX_ESITO_BYTES:
        raise EmirError(f"L'esito supera il limite di {MAX_ESITO_BYTES // (1024 * 1024)} MB.")

    try:
        documento = etree.fromstring(grezzo, _parser_sicuro())
    except etree.XMLSyntaxError as exc:
        raise EmirError(f"L'esito non è un XML leggibile: {exc}") from exc

    meta = SCHEMI["esito"]
    ns = meta["namespace"]
    attesa = f"{{{ns}}}Document"
    if documento.tag != attesa:
        raise EmirError(
            "Il documento non è un esito EMIR auth.092. "
            f"Radice attesa {attesa}, trovata {documento.tag}."
        )

    valido, errori_schema = _esito_validazione("esito", documento)

    def testo(nodo, percorso: str) -> str:
        trovato = nodo.find(percorso, namespaces={"e": ns})
        return (trovato.text or "").strip() if trovato is not None and trovato.text else ""

    righe: list[dict[str, Any]] = []
    troncato = False
    for motivo in documento.iter(f"{{{ns}}}TxsRjctnsRsn"):
        if len(righe) >= MAX_RIGHE_ESITO:
            troncato = True
            break
        stato = testo(motivo, "e:Sts")
        regole = []
        for regola in motivo.findall("e:DtldVldtnRule", namespaces={"e": ns}):
            regole.append({
                "id": (regola.findtext("e:Id", namespaces={"e": ns}) or "").strip(),
                "descrizione": (regola.findtext("e:Desc", namespaces={"e": ns}) or "").strip(),
            })
        righe.append({
            "uti": testo(motivo, "e:TxId/e:UnqIdr/e:UnqTxIdr"),
            "azione": testo(motivo, "e:TxId/e:ActnTp"),
            "azione_etichetta": ETICHETTE_ESITO["TransactionOperationType10Code__1"].get(
                testo(motivo, "e:TxId/e:ActnTp"), ""
            ),
            "momento": testo(motivo, "e:TxId/e:RptgTmStmp"),
            "controparte": testo(motivo, "e:TxId/e:OthrCtrPty/e:Lgl/e:Id/e:LEI"),
            "stato": stato,
            "stato_etichetta": ETICHETTE_ESITO["ReportingMessageStatus2Code"].get(stato, stato),
            "accolto": stato in ESITI_ACCETTAZIONE,
            "regole": regole,
        })

    rapporto = documento.find(f".//{{{ns}}}RjctnSttstcs/{{{ns}}}Rpt")
    riepilogo = {"data": "", "segnalazioni": "", "accolte": "", "respinte": "",
                 "operazioni": "", "operazioni_accolte": "", "operazioni_respinte": ""}
    if rapporto is not None:
        campi = (
            ("data", "RefDt"),
            ("segnalazioni", "TtlNbOfRpts"),
            ("accolte", "TtlNbOfRptsAccptd"),
            ("respinte", "TtlNbOfRptsRjctd"),
            ("operazioni", "TtlNbOfTxs"),
            ("operazioni_accolte", "TtlNbOfTxsAccptd"),
            ("operazioni_respinte", "TtlNbOfTxsRjctd"),
        )
        for chiave, tag in campi:
            trovato = rapporto.find(f"{{{ns}}}{tag}")
            riepilogo[chiave] = (trovato.text or "").strip() if trovato is not None and trovato.text else ""

    return {
        "radice": meta["radice"],
        "schema_sha256": meta["sha256"],
        "valido_xsd": valido,
        "errori_schema": errori_schema,
        "riepilogo": riepilogo,
        "righe": righe,
        "troncato": troncato,
        "sha256": hashlib.sha256(grezzo).hexdigest(),
        "letto_il": ora_iso(),
    }


def abbina_esito(esito: dict[str, Any], uti_inviati: list[str]) -> list[dict[str, Any]]:
    """Accosta le righe dell'esito agli UTI che risultano inviati da noi.

    Un esito arriva per l'intero flusso della giornata e può contenere righe
    di altri: senza questo filtro l'operatore leggerebbe rifiuti che non sono
    suoi e ne cercherebbe la causa nei propri file.
    """

    nostri = {u for u in uti_inviati if u}
    abbinate = []
    for riga in esito.get("righe", []):
        abbinate.append({**riga, "nostro": riga.get("uti") in nostri})
    return abbinate


# ------------------------------------------------------------ validazione

def _parser_sicuro():
    """Parser senza entità esterne, rete e DTD: un esito arriva da fuori."""

    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def _schema(codice: str):
    """Compila lo schema dopo averne verificato l'impronta dichiarata.

    L'hash torna al client come prova di quale schema ha validato il
    documento: va quindi controllato davvero, non soltanto dichiarato.
    """

    if codice in _cache_schemi:
        return _cache_schemi[codice]
    meta = SCHEMI[codice]
    contenuto = _byte_schema(codice)
    try:
        # Si compila dai byte appena verificati, non rileggendo il percorso:
        # rileggere aprirebbe una finestra fra il controllo dell'impronta e
        # l'uso del file.  Funziona perché gli XSD ESMA sono autonomi, senza
        # xs:import né xs:include da risolvere.
        schema = etree.XMLSchema(etree.fromstring(contenuto, _parser_sicuro()))
    except (etree.XMLSyntaxError, etree.XMLSchemaParseError) as exc:
        raise EmirError(f"Schema EMIR non compilabile ({meta['radice']}): {exc}") from exc
    _cache_schemi[codice] = schema
    return schema


def _valida(xml: bytes, codice: str) -> None:
    meta = SCHEMI[codice]
    schema = _schema(codice)
    documento = etree.fromstring(xml, _parser_sicuro())
    if schema.validate(documento):
        return
    errori = [
        {"field": meta["radice"], "message": f"Riga {e.line}: {e.message}"}
        for e in list(schema.error_log)[:12]
    ]
    raise EmirError(
        f"Il documento non supera la validazione contro lo schema ESMA {meta['radice']}.",
        errori,
    )


def _esito_validazione(codice: str, documento) -> tuple[bool, list[str]]:
    """Valida senza far esplodere il chiamante su documenti ostili.

    Un file con entità dichiarate ma non risolte — il caso XXE, che il parser
    blocca correttamente — manda in errore interno il validatore di libxml2:
    va tradotto in «non valido», non in un 500.
    """

    schema = _schema(codice)
    try:
        valido = bool(schema.validate(documento))
    except etree.XMLSchemaValidateError as exc:
        return False, [f"Validazione non eseguibile: {exc}"]
    if valido:
        return True, []
    return False, [f"Riga {e.line}: {e.message}" for e in list(schema.error_log)[:12]]
