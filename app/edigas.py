"""Nomine di trasporto gas nel protocollo EDIG@S 6.1.

EDIG@S è lo standard europeo di scambio dati per il gas naturale pubblicato da
EASEE-gas (edigas.org).  La versione 6.1 conta 128 schemi divisi in sette
famiglie: qui è implementata solo la famiglia che serve davvero a uno shipper
nel ciclo quotidiano di nomina, e in modo esplicito.

Copertura:

* ``Nomination_Document`` (NOMINT): la nomina che lo shipper invia al
  trasportatore, generata e validata contro l'XSD ufficiale;
* ``NominationResponse_Document`` (NOMRES): la risposta del trasportatore,
  letta e riassunta per confrontare quantità nominate e confermate.

Fuori copertura, e non simulato: allocazione capacità, trading, bilanciamento,
matching bilaterale e i documenti REMIT/Transparency, che hanno obbligati e
tracciati distinti.

Il giorno gas segue la convenzione italiana (06:00 → 06:00 ora locale) ed è
calcolato su ``Europe/Rome``, quindi i giorni di cambio ora hanno 23 o 25 ore
invece di 24: un profilo orario viene accettato solo se rispetta la durata
reale di quel giorno.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

try:  # Il requisito applicativo rende lxml disponibile; il messaggio resta chiaro per installazioni difettose.
    from lxml import etree
except ImportError:  # pragma: no cover - esercitato solo in installazioni incomplete
    etree = None  # type: ignore[assignment]


SCHEMA_DIR = Path(__file__).with_name("schemas") / "edigas"
FUSO_GAS = ZoneInfo("Europe/Rome")
ORA_AVVIO_GIORNO_GAS = 6

# Pacchetto "Edigas 6.1 full" scaricato da edigas.org il 31/07/2026.
PACCHETTO = {
    "versione": "6.1",
    "fonte": "https://edigas.org/_files/downloads/9_Edigas_6.1_full_2026-07-31.zip",
    "sha256": "70c0bf6f6081649c52edb2f00add42640b3dbc7bf244b7d881e6b78e86f26bc7",
    "scaricato_il": "2026-07-31",
}

SCHEMI: dict[str, dict[str, str]] = {
    "nomina": {
        "codice": "nomina",
        "etichetta": "Nomina di trasporto · NOMINT",
        "radice": "Nomination_Document",
        "namespace": "urn:easee-gas.eu:edigas:BrpNominationAndMatching:NominationDocument:6:1",
        "filename": "nomination/urn-easee-gas-eu-edigas-BrpNominationAndMatching-NominationDocument-6-1.xsd",
        "sha256": "0d5dd1a2b6f76709500e377bb5f2e278852d93bb1880d483fc2489508f61d4b6",
    },
    "risposta": {
        "codice": "risposta",
        "etichetta": "Risposta del trasportatore · NOMRES",
        "radice": "NominationResponse_Document",
        "namespace": "urn:easee-gas.eu:edigas:BrpNominationAndMatching:NominationResponseDocument:6:1",
        "filename": "nomination/urn-easee-gas-eu-edigas-BrpNominationAndMatching-NominationResponseDocument-6-1.xsd",
        "sha256": "1831468ac7218989d88a63dc6393ccc715fc0ade1b21d7191e217bb08d063b42",
    },
}

# Schema di codifica dell'identificativo: 305 è il registro EIC, ZSO un codice
# assegnato dal trasportatore.  Sono gli unici due ammessi dal tracciato.
CODIFICA_EIC = "305"
CODIFICA_TSO = "ZSO"

# Le direzioni viste dal punto di connessione.
DIREZIONI = {"Z02": "Entrata (immissione in rete)", "Z03": "Uscita (prelievo dalla rete)"}
UNITA = {"KW1": "kWh/h", "KW2": "kWh/g", "A15": "kg/h"}
RUOLI = {
    "ZSH": "Balance Responsible Party (shipper)",
    "ZSO": "System Operator (trasportatore)",
    "ZUK": "Area Coordinator",
    "ZUM": "Clearing Responsible",
}
TIPI_NOMINA = {"A01": "Nomina single-sided", "A02": "Nomina double-sided"}

# Decision table del MIG "BRP Nomination and Matching" v6r0 (§3.2): per ogni
# tipo di documento sono fissati i ruoli delle due parti, se serve il blocco
# NominationType e dove vanno le quantità.  Sono regole di processo che l'XSD
# non esprime, quindi vanno verificate qui prima di produrre il file.
TIPI_DOCUMENTO: dict[str, dict[str, Any]] = {
    "01G": {
        "etichetta": "Nomina al punto di connessione",
        "emittente": "ZSH",
        "destinatario": "ZSO",
        "tipi_nomina": ("A01", "A02"),
        "con_controparti": True,
    },
    "02G": {
        "etichetta": "Nomina al punto di scambio virtuale · OTC",
        "emittente": "ZSH",
        "destinatario": "ZUK",
        "tipi_nomina": ("A02",),
        "con_controparti": True,
    },
    "03G": {
        "etichetta": "Nomina al punto di scambio virtuale · borsa",
        "emittente": "ZUM",
        "destinatario": "ZUK",
        "tipi_nomina": (),
        "con_controparti": True,
    },
    "04G": {
        "etichetta": "Nomina non-matching (cliente finale)",
        "emittente": "ZSH",
        "destinatario": "ZSO",
        "tipi_nomina": (),
        "con_controparti": False,
    },
}

MAX_PERIODI = 2000
MAX_RISPOSTA_BYTES = 5 * 1024 * 1024
# Oltre questa soglia l'elenco degli errori non aiuta più nessuno e la
# risposta diventa un peso: si riporta il resto come conteggio.
MAX_ERRORI = 50
# Anni plausibili per un giorno gas: fuori da qui le operazioni su date
# sfondano i limiti di datetime invece di produrre un errore leggibile.
ANNO_MIN, ANNO_MAX = 2000, 2100
# Caratteri che nessun file XML 1.0 può contenere, surrogati compresi.
CARATTERI_VIETATI = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")
# Serie di un NOMRES: solo la 16G porta le quantità confermate allo shipper.
ORIGINE_CONFERMATA = "16G"
ORIGINI = {
    "14G": "Elaborata dal trasportatore",
    "15G": "Elaborata dal trasportatore adiacente",
    "16G": "Confermata",
    "18G": "Nomina della controparte",
}
# EIC: 2 cifre dell'ufficio emittente, 1 lettera per il tipo di oggetto
# (X parte, Y area, Z punto di misura, V location, W risorsa, T tie-line),
# 12 caratteri alfanumerici e 1 carattere di controllo: 16 in tutto.
EIC_RE = re.compile(r"^[0-9]{2}[XYZVWT][A-Z0-9\-]{12}[A-Z0-9]$")


class EdigasError(ValueError):
    """Errore strutturato della preparazione del documento EDIG@S."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


@dataclass(frozen=True)
class DocumentoEdigas:
    """Artefatto XML con l'esito della validazione dello schema incluso."""

    codice: str
    radice: str
    tipo_documento: str
    identificativo: str
    versione: int
    giorno_gas: str
    punto: str
    versione_edigas: str
    schema_sha256: str
    xml: str
    xml_sha256: str
    size_bytes: int
    periodi: int
    avvisi: tuple[str, ...] = ()


def catalogo_schemi() -> list[dict[str, Any]]:
    """Metadati esponibili alla UI senza i percorsi locali interni."""

    return [
        {
            "codice": meta["codice"],
            "etichetta": meta["etichetta"],
            "radice": meta["radice"],
            "namespace": meta["namespace"],
            "sha256": meta["sha256"],
            "versione_edigas": PACCHETTO["versione"],
            "fonte": PACCHETTO["fonte"],
            "scaricato_il": PACCHETTO["scaricato_il"],
        }
        for meta in SCHEMI.values()
    ]


# ---------------------------------------------------------------- giorno gas


def confini_giorno_gas(giorno: date) -> tuple[datetime, datetime]:
    """Inizio e fine del giorno gas in UTC, con l'offset reale di quel giorno.

    Il giorno gas italiano va dalle 06:00 alle 06:00 locali: in UTC cade alle
    05:00 con l'ora solare e alle 04:00 con l'ora legale.  Calcolarlo sulla
    zona invece che su un offset fisso è ciò che rende corretti anche i due
    giorni di transizione.
    """

    inizio = datetime.combine(giorno, time(ORA_AVVIO_GIORNO_GAS), tzinfo=FUSO_GAS)
    fine = datetime.combine(giorno + timedelta(days=1), time(ORA_AVVIO_GIORNO_GAS), tzinfo=FUSO_GAS)
    return inizio.astimezone(timezone.utc), fine.astimezone(timezone.utc)


def ore_giorno_gas(giorno: date) -> list[tuple[datetime, datetime]]:
    """Slot orari del giorno gas: 24 di norma, 23 o 25 al cambio dell'ora."""

    inizio, fine = confini_giorno_gas(giorno)
    slot, cursore = [], inizio
    while cursore < fine:
        successivo = min(cursore + timedelta(hours=1), fine)
        slot.append((cursore, successivo))
        cursore = successivo
    return slot


def _istante(momento: datetime) -> str:
    """Formato EDIG@S degli istanti: UTC, minuti, suffisso Z e niente secondi."""

    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _intervallo(inizio: datetime, fine: datetime) -> str:
    return f"{_istante(inizio)}/{_istante(fine)}"


# ------------------------------------------------------------- validazione


XSD_NS = "{http://www.w3.org/2001/XMLSchema}"


@lru_cache(maxsize=None)
def _definizioni(codice: str) -> dict[str, Any]:
    """Indicizza per nome i simpleType dello schema e delle sue restrizioni."""

    meta = SCHEMI[codice]
    principale = SCHEMA_DIR / meta["filename"]
    base = principale.name.removesuffix(".xsd")
    percorsi = [
        principale,
        principale.parent / f"{base}-restricted-codes.xsd",
        principale.parent / f"{base}-local-restrictions.xsd",
        # I tipi la cui lista ristretta è vuota ereditano dalla code list
        # generale: senza questo file le enumerazioni si perderebbero.
        SCHEMA_DIR / "cclib-v6" / "urn-easee-gas-eu-edigas-codelistsV6.xsd",
    ]
    indice: dict[str, Any] = {}
    for percorso in percorsi:
        if not percorso.exists():
            continue
        for st in ElementTree.parse(percorso).getroot().iter(f"{XSD_NS}simpleType"):
            nome = st.get("name")
            if nome and nome not in indice:
                indice[nome] = st
    return indice


@lru_cache(maxsize=None)
def _facet(codice: str, tipo: str) -> dict[str, Any]:
    """Vincoli di un simpleType, con la catena di derivazione risolta."""

    return _facet_ricorsivo(codice, tipo, 0)


def _facet_ricorsivo(codice: str, tipo: str, _profondita: int) -> dict[str, Any]:
    """Legge i vincoli di un simpleType risolvendo union e tipi derivati.

    I codici ammessi non stanno mai sul tipo usato dall'elemento: quello
    deriva da una ``*CodeTypeCodeList`` che è l'unione di una lista standard e
    di una lista locale del trasportatore, ciascuna in un file diverso.
    Seguire la catena a runtime tiene la validazione allineata se il pacchetto
    EDIG@S viene aggiornato, invece di ricopiare i valori a mano.
    """

    if codice not in SCHEMI or _profondita > 6:
        return {}
    st = _definizioni(codice).get(tipo)
    if st is None:
        return {}

    unione = st.find(f"{XSD_NS}union")
    if unione is not None:
        valori: list[str] = []
        for membro in (unione.get("memberTypes") or "").split():
            for valore in _facet_ricorsivo(codice, membro.split(":")[-1], _profondita + 1).get("enum") or []:
                if valore not in valori:
                    valori.append(valore)
        return {"enum": valori, "pattern": []}

    res = st.find(f"{XSD_NS}restriction")
    if res is None:
        return {}
    out: dict[str, Any] = {
        "enum": [e.get("value") for e in res.findall(f"{XSD_NS}enumeration")],
        "pattern": [pt.get("value") for pt in res.findall(f"{XSD_NS}pattern")],
    }
    for facet in ("minLength", "maxLength", "length", "maxInclusive"):
        el = res.find(f"{XSD_NS}{facet}")
        if el is not None:
            out[facet] = int(el.get("value"))

    ereditato = _facet_ricorsivo(codice, (res.get("base") or "").split(":")[-1], _profondita + 1)
    if ereditato:
        # Una restrizione senza facet propri eredita quelli del tipo base.
        out["enum"] = out["enum"] or ereditato.get("enum") or []
        out["pattern"] = out["pattern"] or ereditato.get("pattern") or []
        for facet in ("minLength", "maxLength", "length", "maxInclusive"):
            if facet not in out and facet in ereditato:
                out[facet] = ereditato[facet]
    return out


def codici_ammessi(tipo: str) -> list[str]:
    """Valori ammessi per un tipo del tracciato nomina, letti dall'XSD."""

    return list(_facet("nomina", tipo).get("enum") or [])


def _errore(errors: list[dict[str, str]], campo: str, messaggio: str) -> None:
    """Accoda un errore, fermandosi al tetto ma dichiarando quanti ne restano.

    Troncare in silenzio farebbe credere all'operatore di aver corretto tutto
    dopo aver sistemato i primi cinquanta.
    """

    if len(errors) < MAX_ERRORI:
        errors.append({"field": campo, "message": messaggio})
    elif len(errors) == MAX_ERRORI:
        errors.append({
            "field": "_altri",
            "message": f"Altri errori non elencati: correggi i primi {MAX_ERRORI} e rigenera.",
        })


def _testo(
    dati: dict[str, Any],
    campo: str,
    errors: list[dict[str, str]],
    etichetta: str,
    *,
    obbligatorio: bool = True,
) -> str:
    """Legge un campo come testo, rifiutando strutture e caratteri illegali.

    Senza questo controllo un dict inviato per sbaglio dal client finirebbe
    nel documento come ``{'a': 1}``, e un carattere di controllo farebbe
    fallire la serializzazione con un errore incomprensibile invece che con
    un messaggio sul campo.
    """

    valore = dati.get(campo)
    if valore is None or valore == "":
        grezzo = ""
    elif isinstance(valore, bool) or not isinstance(valore, (str, int, float)):
        _errore(errors, campo, f"{etichetta}: atteso un valore semplice, ricevuto {type(valore).__name__}.")
        return ""
    else:
        grezzo = str(valore).strip()
    if CARATTERI_VIETATI.search(grezzo):
        _errore(errors, campo, f"{etichetta}: contiene caratteri non ammessi in un documento XML.")
        return ""
    if obbligatorio and not grezzo:
        _errore(errors, campo, f"{etichetta} obbligatorio per il tracciato EDIG@S.")
    return grezzo


def _controlla(
    errors: list[dict[str, str]],
    campo: str,
    valore: str,
    tipo: str,
    etichetta: str,
) -> None:
    """Verifica un valore contro i facet XSD del suo tipo, in italiano."""

    if not valore:
        return
    f = _facet("nomina", tipo)
    if not f:
        return
    ammessi = f.get("enum") or []
    if ammessi and valore not in ammessi:
        _errore(
            errors,
            campo,
            f"{etichetta}: valore non ammesso dal tracciato. Valori validi: {', '.join(ammessi)}.",
        )
        return
    massimo = f.get("maxLength")
    if massimo is not None and len(valore) > massimo:
        _errore(errors, campo, f"{etichetta}: massimo {massimo} caratteri (ne hai {len(valore)}).")
        return
    for pattern in f.get("pattern") or []:
        if not re.fullmatch(pattern, valore):
            _errore(errors, campo, f"{etichetta}: formato non conforme al tracciato EDIG@S.")
            return


def _quantita(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]]) -> str | None:
    """Normalizza una quantità: non negativa, al massimo 12 decimali."""

    if isinstance(valore, bool) or not isinstance(valore, (str, int, float, type(None))):
        _errore(errors, campo, f"{etichetta}: attesa una quantità, ricevuto {type(valore).__name__}.")
        return None
    testo = str(valore).strip() if valore is not None else ""
    if not testo:
        _errore(errors, campo, f"{etichetta}: quantità obbligatoria.")
        return None
    # Il tipo XSD è xs:decimal: niente notazione esponenziale, nan, inf o
    # separatori.  float() li accetterebbe e l'errore arriverebbe più tardi
    # dallo schema, in una forma illeggibile per l'operatore.
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", testo):
        _errore(errors, campo, f"{etichetta}: quantità non numerica ({testo!r}). Attese solo cifre, con punto decimale.")
        return None
    if testo.startswith("-") and float(testo) != 0:
        _errore(errors, campo, f"{etichetta}: la quantità non può essere negativa.")
        return None
    testo = testo.lstrip("+")
    if testo.startswith("-"):  # -0 e -0.0 sono zero: si normalizzano senza segno
        testo = testo[1:]
    intera, _, frazione = testo.partition(".")
    if len(frazione) > 12:
        _errore(errors, campo, f"{etichetta}: al massimo 12 decimali.")
        return None
    if len(intera) + len(frazione) > 40:
        _errore(errors, campo, f"{etichetta}: al massimo 40 cifre complessive.")
        return None
    return testo


# -------------------------------------------------------------- generazione


def _periodi_controparte(
    controparte: dict[str, Any],
    prefisso: str,
    nome: str,
    giorno: date,
    errors: list[dict[str, str]],
) -> list[tuple[str, str, str]]:
    """Espande una partita in periodi (intervallo, direzione, quantità).

    Sono accettate tre forme, dalla più sintetica alla più esplicita: una
    quantità costante sull'intero giorno gas, un profilo orario, oppure i
    periodi già scritti dall'operatore.  ``prefisso`` e ``nome`` servono a
    scrivere errori comprensibili anche per la nomina senza controparte,
    dove le quantità pendono dal punto di connessione.
    """

    direzione_base = str(controparte.get("direzione") or "").strip()
    slot = ore_giorno_gas(giorno)
    fuori: list[tuple[str, str, str]] = []

    espliciti = controparte.get("periodi")
    profilo = controparte.get("profilo_orario")
    costante = controparte.get("quantita_giornaliera")
    forme = [n for n, v in (("periodi", espliciti), ("profilo_orario", profilo), ("quantita_giornaliera", costante)) if v not in (None, "", [])]
    if len(forme) > 1:
        _errore(errors, prefisso, f"{nome}: indica una sola forma tra {', '.join(forme)}.")
        return fuori
    if not forme:
        _errore(errors, prefisso, f"{nome}: manca la quantità (giornaliera, profilo orario o periodi).")
        return fuori

    if costante not in (None, ""):
        _controlla(errors, f"{prefisso}.direzione", direzione_base, "GasDirectionCodeType", f"{nome} · direzione")
        if not direzione_base:
            _errore(errors, f"{prefisso}.direzione", f"{nome}: direzione obbligatoria.")
        quantita = _quantita(costante, f"{prefisso}.quantita_giornaliera", nome, errors)
        inizio, fine = confini_giorno_gas(giorno)
        if quantita is not None and direzione_base:
            fuori.append((_intervallo(inizio, fine), direzione_base, quantita))
        return fuori

    if profilo:
        if not isinstance(profilo, list):
            _errore(errors, f"{prefisso}.profilo_orario", f"{nome}: il profilo orario dev'essere una lista.")
            return fuori
        if not direzione_base:
            _errore(errors, f"{prefisso}.direzione", f"{nome}: direzione obbligatoria.")
        _controlla(errors, f"{prefisso}.direzione", direzione_base, "GasDirectionCodeType", f"{nome} · direzione")
        if len(profilo) != len(slot):
            _errore(
                errors,
                f"{prefisso}.profilo_orario",
                f"{nome}: il {giorno.isoformat()} è un giorno gas di {len(slot)} ore, "
                f"il profilo ne contiene {len(profilo)}.",
            )
            return fuori
        for ora, valore in enumerate(profilo):
            quantita = _quantita(valore, f"{prefisso}.profilo_orario[{ora}]", f"{nome} · ora {ora + 1}", errors)
            if quantita is not None and direzione_base:
                fuori.append((_intervallo(*slot[ora]), direzione_base, quantita))
        return fuori

    if not isinstance(espliciti, list):
        _errore(errors, f"{prefisso}.periodi", f"{nome}: i periodi devono essere una lista.")
        return fuori
    limiti = confini_giorno_gas(giorno)
    for n, periodo in enumerate(espliciti):
        etichetta = f"{nome} · periodo {n + 1}"
        campo = f"{prefisso}.periodi[{n}]"
        if not isinstance(periodo, dict):
            _errore(errors, campo, f"{etichetta}: formato non valido.")
            continue
        direzione = str(periodo.get("direzione") or direzione_base or "").strip()
        if not direzione:
            _errore(errors, f"{campo}.direzione", f"{etichetta}: direzione obbligatoria.")
        _controlla(errors, f"{campo}.direzione", direzione, "GasDirectionCodeType", f"{etichetta} · direzione")
        inizio = _momento(periodo.get("inizio"), f"{campo}.inizio", f"{etichetta} · inizio", errors)
        fine = _momento(periodo.get("fine"), f"{campo}.fine", f"{etichetta} · fine", errors)
        quantita = _quantita(periodo.get("quantita"), f"{campo}.quantita", etichetta, errors)
        if inizio is None or fine is None or quantita is None or not direzione:
            continue
        if fine <= inizio:
            _errore(errors, campo, f"{etichetta}: la fine deve seguire l'inizio.")
            continue
        if inizio < limiti[0] or fine > limiti[1]:
            _errore(
                errors,
                campo,
                f"{etichetta}: fuori dal giorno gas {giorno.isoformat()} "
                f"({_intervallo(*limiti)}).",
            )
            continue
        fuori.append((_intervallo(inizio, fine), direzione, quantita))
    return fuori


def _momento(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]]) -> datetime | None:
    """Interpreta un istante ISO 8601 e lo riporta in UTC, al minuto.

    Il troncamento avviene qui e non al momento di scrivere il file: gli
    intervalli EDIG@S hanno la precisione del minuto, quindi i controlli di
    ordine e sovrapposizione devono già lavorare sui valori troncati, o
    finirebbero per approvare periodi che nel file risultano a durata zero.
    """

    if isinstance(valore, bool) or not isinstance(valore, (str, type(None))):
        _errore(errors, campo, f"{etichetta}: atteso un istante ISO 8601, ricevuto {type(valore).__name__}.")
        return None
    testo = str(valore or "").strip()
    if not testo:
        _errore(errors, campo, f"{etichetta}: istante obbligatorio.")
        return None
    try:
        momento = datetime.fromisoformat(testo.replace("Z", "+00:00"))
    except ValueError:
        _errore(errors, campo, f"{etichetta}: istante non in formato ISO 8601 ({testo!r}).")
        return None
    if not ANNO_MIN <= momento.year <= ANNO_MAX:
        _errore(errors, campo, f"{etichetta}: anno fuori dall'intervallo gestito ({ANNO_MIN}-{ANNO_MAX}).")
        return None
    if momento.tzinfo is None:
        # Un istante senza fuso è inteso come ora italiana, coerentemente con
        # il giorno gas; fold=0 sceglie la prima delle due ore ripetute nella
        # notte del ritorno all'ora solare.
        momento = momento.replace(tzinfo=FUSO_GAS)
    return momento.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _lacune(periodi: list[tuple[str, str, str]], inizio: datetime, fine: datetime) -> list[str]:
    """Tratti del giorno gas non coperti dagli intervalli indicati.

    Le Basic Ground Rules chiedono che gli intervalli coprano l'intero periodo
    di validità, ma una nomina parziale (poche ore acquistate da una
    controparte) è pratica corrente: la lacuna è quindi un avviso, non un
    errore che impedisce di produrre il file.
    """

    intervalli = sorted({(i.partition("/")[0], i.partition("/")[2]) for i, _, _ in periodi})
    if not intervalli:
        return []
    mancanti, cursore = [], _istante(inizio)
    for partenza, arrivo in intervalli:
        if partenza > cursore:
            mancanti.append(f"{cursore}/{partenza}")
        cursore = max(cursore, arrivo)
    if cursore < _istante(fine):
        mancanti.append(f"{cursore}/{_istante(fine)}")
    return mancanti


def _sovrapposizioni(periodi: list[tuple[str, str, str]]) -> list[str]:
    """Intervalli che si accavallano nella stessa direzione, per controparte."""

    per_direzione: dict[str, list[tuple[str, str]]] = {}
    for intervallo, direzione, _ in periodi:
        inizio, _, fine = intervallo.partition("/")
        per_direzione.setdefault(direzione, []).append((inizio, fine))
    conflitti = []
    for direzione, elenco in per_direzione.items():
        ordinati = sorted(elenco)
        for (i1, f1), (i2, f2) in zip(ordinati, ordinati[1:]):
            if i2 < f1:
                conflitti.append(f"{direzione}: {i1}/{f1} si sovrappone a {i2}/{f2}")
    return conflitti


def genera_nomina(dati: dict[str, Any]) -> DocumentoEdigas:
    """Costruisce il Nomination_Document e lo valida contro l'XSD ufficiale."""

    if etree is None:  # pragma: no cover
        raise EdigasError("lxml non è installato: impossibile validare il documento EDIG@S.")

    errors: list[dict[str, str]] = []
    meta = SCHEMI["nomina"]

    identificativo = _testo(dati, "identificativo", errors, "Identificativo del documento")
    _controlla(errors, "identificativo", identificativo, "IdentificationType", "Identificativo del documento")

    # Non usare `or "1"`: la versione 0 è falsy e verrebbe corretta in
    # silenzio, mentre è un valore che l'operatore deve vedere respinto.
    grezza = _testo(dati, "versione", errors, "Versione", obbligatorio=False)
    versione = grezza or "1"
    # `isdigit()` è vero anche per apici e frazioni unicode, che poi int()
    # rifiuta: la regex evita che l'eccezione arrivi fino al chiamante.
    if not re.fullmatch(r"[0-9]{1,3}", versione) or not 1 <= int(versione) <= 999:
        _errore(errors, "versione", "Versione: numero intero da 1 a 999 (si incrementa a ogni rinomina).")
        versione = "1"

    tipo_documento = _testo(dati, "tipo_documento", errors, "Tipo di documento")
    _controlla(errors, "tipo_documento", tipo_documento, "DocumentCodeType", "Tipo di documento")
    regole = TIPI_DOCUMENTO.get(tipo_documento)
    if tipo_documento and regole is None:
        elenco = ", ".join(f"{c} ({r['etichetta']})" for c, r in TIPI_DOCUMENTO.items())
        _errore(errors, "tipo_documento", f"Tipo di documento non gestito per le nomine. Ammessi: {elenco}.")

    emittente = _testo(dati, "emittente_eic", errors, "EIC dell'emittente")
    _controlla(errors, "emittente_eic", emittente, "PartyType-base", "EIC dell'emittente")
    _eic(errors, "emittente_eic", emittente, "EIC dell'emittente")
    emittente_ruolo = _testo(dati, "emittente_ruolo", errors, "Ruolo dell'emittente")
    _controlla(errors, "emittente_ruolo", emittente_ruolo, "RoleCodeType", "Ruolo dell'emittente")

    destinatario = _testo(dati, "destinatario_eic", errors, "EIC del destinatario")
    _controlla(errors, "destinatario_eic", destinatario, "PartyType-base", "EIC del destinatario")
    _eic(errors, "destinatario_eic", destinatario, "EIC del destinatario")
    destinatario_ruolo = _testo(dati, "destinatario_ruolo", errors, "Ruolo del destinatario")
    _controlla(errors, "destinatario_ruolo", destinatario_ruolo, "RoleCodeType", "Ruolo del destinatario")

    if emittente and emittente == destinatario:
        _errore(errors, "destinatario_eic", "Emittente e destinatario non possono avere lo stesso EIC.")

    if regole:
        atteso_emittente, atteso_destinatario = regole["emittente"], regole["destinatario"]
        if emittente_ruolo and emittente_ruolo != atteso_emittente:
            _errore(
                errors,
                "emittente_ruolo",
                f"Con il documento {tipo_documento} ({regole['etichetta']}) l'emittente dev'essere "
                f"{atteso_emittente} — {RUOLI[atteso_emittente]}.",
            )
        if destinatario_ruolo and destinatario_ruolo != atteso_destinatario:
            _errore(
                errors,
                "destinatario_ruolo",
                f"Con il documento {tipo_documento} ({regole['etichetta']}) il destinatario dev'essere "
                f"{atteso_destinatario} — {RUOLI[atteso_destinatario]}.",
            )

    tipo_nomina = str(dati.get("tipo_nomina") or "").strip()
    if regole:
        ammessi = regole["tipi_nomina"]
        if ammessi and not tipo_nomina:
            elenco = " oppure ".join(f"{c} ({TIPI_NOMINA[c]})" for c in ammessi)
            _errore(errors, "tipo_nomina", f"Il documento {tipo_documento} richiede il tipo di nomina: {elenco}.")
        elif ammessi and tipo_nomina not in ammessi:
            elenco = " oppure ".join(f"{c} ({TIPI_NOMINA[c]})" for c in ammessi)
            _errore(errors, "tipo_nomina", f"Con il documento {tipo_documento} il tipo di nomina ammesso è {elenco}.")
        elif not ammessi and tipo_nomina:
            _errore(
                errors,
                "tipo_nomina",
                f"Il documento {tipo_documento} ({regole['etichetta']}) non prevede il tipo di nomina.",
            )

    conto = _testo(dati, "conto_interno", errors, "Conto interno (shipper account)")
    _controlla(errors, "conto_interno", conto, "AccountType-base", "Conto interno")

    punto = _testo(dati, "punto_eic", errors, "Punto di connessione")
    _controlla(errors, "punto_eic", punto, "MeasurementPointType-base", "Punto di connessione")
    # Il tracciato ammette due codifiche per il punto: EIC oppure un codice
    # assegnato dal trasportatore.  Dichiarare 305 su un codice che EIC non è
    # produce un documento formalmente valido ma falso.
    codifica_punto = _testo(dati, "codifica_punto", errors, "Codifica del punto", obbligatorio=False) or CODIFICA_EIC
    if codifica_punto not in (CODIFICA_EIC, CODIFICA_TSO):
        _errore(
            errors,
            "codifica_punto",
            f"Codifica del punto: ammessi {CODIFICA_EIC} (codice EIC) o {CODIFICA_TSO} (codice del trasportatore).",
        )
    elif codifica_punto == CODIFICA_EIC:
        _eic(errors, "punto_eic", punto, "Punto di connessione", tipi="YZ")

    unita = str(dati.get("unita") or "KW1").strip()
    _controlla(errors, "unita", unita, "UnitOfMeasureCodeType", "Unità di misura")

    testo_giorno = _testo(dati, "giorno_gas", errors, "Giorno gas")
    giorno: date | None = None
    if testo_giorno:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", testo_giorno):
            _errore(errors, "giorno_gas", f"Giorno gas: attesa una data AAAA-MM-GG, ricevuto {testo_giorno!r}.")
        else:
            try:
                giorno = date.fromisoformat(testo_giorno)
            except ValueError:
                _errore(errors, "giorno_gas", f"Giorno gas: data inesistente ({testo_giorno!r}).")
            else:
                if not ANNO_MIN <= giorno.year <= ANNO_MAX:
                    _errore(errors, "giorno_gas", f"Giorno gas: anno fuori dall'intervallo gestito ({ANNO_MIN}-{ANNO_MAX}).")
                    giorno = None

    # Il documento 04G è una nomina verso cliente finale: non ha controparte
    # da abbinare, quindi le quantità stanno direttamente sul punto di
    # connessione.  Tutti gli altri tipi richiedono almeno una controparte.
    non_matching = bool(regole) and not regole["con_controparti"]
    controparti = dati.get("controparti")
    if non_matching and controparti:
        _errore(
            errors,
            "controparti",
            f"Il documento {tipo_documento} non prevede controparti: indica le quantità sul punto di connessione.",
        )
    if regole and not non_matching and (not isinstance(controparti, list) or not controparti):
        _errore(errors, "controparti", "Serve almeno una controparte con le quantità nominate.")
    if not isinstance(controparti, list):
        controparti = []

    partite: list[tuple[str, list[tuple[str, str, str]]]] = []
    # I conti già incontrati stanno in un insieme costruito una volta sola:
    # ricavarli dalle partite a ogni giro rendeva quadratico il costo, e una
    # nomina con molte controparti teneva occupato il server per minuti.
    conti_visti: set[str] = set()
    senza_controparte: list[tuple[str, str, str]] = []
    totale_periodi = 0
    if giorno is not None and non_matching:
        senza_controparte = _periodi_controparte(dati, "punto", "Punto di connessione", giorno, errors)
        for conflitto in _sovrapposizioni(senza_controparte):
            _errore(errors, "periodi", f"Periodi sovrapposti — {conflitto}.")
        totale_periodi = len(senza_controparte)
    elif giorno is not None:
        for indice, controparte in enumerate(controparti):
            if not isinstance(controparte, dict):
                _errore(errors, f"controparti[{indice}]", f"Controparte {indice + 1}: formato non valido.")
                continue
            conto_esterno = _testo(
                controparte,
                "conto",
                errors,
                f"Controparte {indice + 1} · conto",
            )
            _controlla(
                errors,
                f"controparti[{indice}].conto",
                conto_esterno,
                "AccountType-base",
                f"Controparte {indice + 1} · conto",
            )
            if conto_esterno and conto_esterno in conti_visti:
                _errore(
                    errors,
                    f"controparti[{indice}].conto",
                    f"Controparte {indice + 1}: {conto_esterno} compare più volte. "
                    "Riunisci i suoi periodi in una sola voce.",
                )
                continue
            periodi = _periodi_controparte(
                controparte, f"controparti[{indice}]", f"Controparte {indice + 1}", giorno, errors
            )
            for conflitto in _sovrapposizioni(periodi):
                _errore(errors, f"controparti[{indice}]", f"Controparte {indice + 1}: periodi sovrapposti — {conflitto}.")
            totale_periodi += len(periodi)
            if conto_esterno:
                conti_visti.add(conto_esterno)
            if conto_esterno and periodi:
                partite.append((conto_esterno, periodi))
            if totale_periodi > MAX_PERIODI:
                # Si interrompe subito: continuare a espandere periodi che
                # verranno comunque rifiutati è solo lavoro sprecato.
                break

    if totale_periodi > MAX_PERIODI:
        _errore(errors, "controparti", f"Troppi periodi ({totale_periodi}): il limite applicativo è {MAX_PERIODI}.")

    if errors:
        raise EdigasError("Il documento EDIG@S non è conforme: correggi i campi segnalati.", errors)

    assert giorno is not None  # garantito dai controlli sopra
    inizio, fine = confini_giorno_gas(giorno)

    avvisi: list[str] = []
    for etichetta, periodi in [*partite, ("il punto di connessione", senza_controparte)]:
        if not periodi:
            continue
        mancanti = _lacune(periodi, inizio, fine)
        if mancanti:
            avvisi.append(
                f"{etichetta}: il giorno gas non è coperto per intero, mancano {', '.join(mancanti)}."
            )
    creato = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if dati.get("creato_il"):
        indicato = _momento(dati.get("creato_il"), "creato_il", "Data di creazione", errors)
        if indicato is None:
            raise EdigasError("Il documento EDIG@S non è conforme: correggi i campi segnalati.", errors)
        creato = indicato

    ns = meta["namespace"]
    radice = etree.Element(f"{{{ns}}}{meta['radice']}", nsmap={None: ns})
    radice.set("schemaVersion", "1")

    def figlio(padre, nome: str, testo: str, **attrs: str):
        el = etree.SubElement(padre, f"{{{ns}}}{nome}")
        el.text = testo
        for chiave, valore in attrs.items():
            el.set(chiave, valore)
        return el

    figlio(radice, "identification", identificativo)
    figlio(radice, "version", versione)
    figlio(radice, "documentCode", tipo_documento)
    figlio(radice, "creationDateTime", creato.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    figlio(radice, "validityPeriod", _intervallo(inizio, fine))
    figlio(radice, "issuer_MarketParticipant.identification", emittente, codingScheme=CODIFICA_EIC)
    figlio(radice, "issuer_MarketParticipant.marketRole.roleCode", emittente_ruolo)
    figlio(radice, "recipient_MarketParticipant.identification", destinatario, codingScheme=CODIFICA_EIC)
    figlio(radice, "recipient_MarketParticipant.marketRole.roleCode", destinatario_ruolo)

    interno = etree.SubElement(radice, f"{{{ns}}}Internal_Account")
    figlio(interno, "internalAccount", conto, codingScheme=CODIFICA_TSO)
    punto_el = etree.SubElement(interno, f"{{{ns}}}ConnectionPoint")
    figlio(punto_el, "identification", punto, codingScheme=codifica_punto)
    figlio(punto_el, "measureUnit.unitOfMeasureCode", unita)

    def scrivi_periodi(padre, periodi: list[tuple[str, str, str]]) -> None:
        for intervallo, direzione, quantita in periodi:
            periodo_el = etree.SubElement(padre, f"{{{ns}}}Period")
            figlio(periodo_el, "timeInterval", intervallo)
            figlio(periodo_el, "direction.gasDirectionCode", direzione)
            figlio(periodo_el, "quantity.amount", quantita)

    # Con 01G e 02G le controparti stanno dentro NominationType, che porta il
    # tipo di nomina; con 03G stanno direttamente sul punto; con 04G non ci
    # sono controparti e i periodi pendono dal punto di connessione.
    contenitore = punto_el
    if tipo_nomina:
        contenitore = etree.SubElement(punto_el, f"{{{ns}}}NominationType")
        figlio(contenitore, "nominationCode", tipo_nomina)
    for conto_esterno, periodi in partite:
        esterno = etree.SubElement(contenitore, f"{{{ns}}}External_Account")
        figlio(esterno, "externalAccount", conto_esterno, codingScheme=CODIFICA_TSO)
        scrivi_periodi(esterno, periodi)
    scrivi_periodi(punto_el, senza_controparte)

    xml = etree.tostring(radice, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    _valida(xml, "nomina")
    return DocumentoEdigas(
        codice="nomina",
        radice=meta["radice"],
        tipo_documento=tipo_documento,
        identificativo=identificativo,
        versione=int(versione),
        giorno_gas=giorno.isoformat(),
        punto=punto,
        versione_edigas=PACCHETTO["versione"],
        schema_sha256=meta["sha256"],
        xml=xml.decode("utf-8"),
        xml_sha256=hashlib.sha256(xml).hexdigest(),
        size_bytes=len(xml),
        periodi=totale_periodi,
        avvisi=tuple(avvisi),
    )


def _eic(errors: list[dict[str, str]], campo: str, valore: str, etichetta: str, *, tipi: str = "X") -> None:
    """Il tracciato accetta 16 caratteri qualsiasi; l'EIC ha una forma precisa.

    La terza posizione dichiara il tipo di oggetto: ``X`` per le parti, ``Y``
    o ``Z`` per aree e punti di misura.  Un EIC di parte usato come punto è
    un errore che lo schema non intercetta.
    """

    if not valore:
        return
    if not EIC_RE.fullmatch(valore):
        _errore(errors, campo, f"{etichetta}: non è un codice EIC valido (16 caratteri, es. 21X000000001234A).")
        return
    if valore[2] not in tipi:
        atteso = " o ".join(tipi)
        _errore(
            errors,
            campo,
            f"{etichetta}: la terza lettera dell'EIC indica il tipo di oggetto e qui dev'essere {atteso} "
            f"(ricevuto {valore[2]}).",
        )


def _valida(xml: bytes, codice: str) -> None:
    meta = SCHEMI[codice]
    schema = _schema(codice)
    documento = etree.fromstring(xml)
    if schema.validate(documento):
        return
    errori = [
        {"field": meta["radice"], "message": f"Riga {e.line}: {e.message}"}
        for e in list(schema.error_log)[:12]
    ]
    raise EdigasError(
        f"Il documento non supera la validazione contro lo schema EDIG@S {meta['radice']}.",
        errori,
    )


def _parser_sicuro():
    """Parser per i file di terzi: niente entità esterne, DTD o rete.

    I default di libxml2 oggi bloccano già XXE ed espansione di entità, ma
    la garanzia non deve dipendere dalla versione installata.
    """

    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


@lru_cache(maxsize=None)
def _schema(codice: str):
    """Compila lo schema dopo averne verificato l'impronta dichiarata.

    L'hash viene restituito al client come prova di quale schema ha validato
    il documento: va quindi controllato davvero, non solo dichiarato.
    """

    meta = SCHEMI[codice]
    percorso = SCHEMA_DIR / meta["filename"]
    try:
        contenuto = percorso.read_bytes()
    except OSError as exc:
        raise EdigasError(f"Schema EDIG@S non leggibile ({meta['radice']}): {exc}") from exc
    if hashlib.sha256(contenuto).hexdigest() != meta["sha256"]:
        raise EdigasError(
            f"Integrità dello schema EDIG@S non verificata ({meta['radice']}): "
            "l'impronta del file non corrisponde a quella dichiarata."
        )
    try:
        return etree.XMLSchema(etree.parse(str(percorso)))
    except etree.XMLSchemaParseError as exc:
        raise EdigasError(f"Schema EDIG@S non compilabile ({meta['radice']}): {exc}") from exc


# --------------------------------------------------- risposta del trasportatore


def leggi_risposta(contenuto: bytes | str) -> dict[str, Any]:
    """Legge un NOMRES e riassume quantità confermate contro quantità nominate.

    Il confronto per intervallo è ciò che interessa allo shipper: se il
    trasportatore riduce una nomina, la differenza va vista subito, non
    dedotta a mano dal file.
    """

    if etree is None:  # pragma: no cover
        raise EdigasError("lxml non è installato: impossibile leggere la risposta EDIG@S.")

    grezzo = contenuto.encode("utf-8") if isinstance(contenuto, str) else contenuto
    try:
        documento = etree.fromstring(grezzo, _parser_sicuro())
    except etree.XMLSyntaxError as exc:
        raise EdigasError(f"Il file non è XML valido: {exc}") from exc

    meta = SCHEMI["risposta"]
    atteso = f"{{{meta['namespace']}}}{meta['radice']}"
    if documento.tag != atteso:
        raise EdigasError(
            "Il file non è una risposta di nomina EDIG@S 6.1 "
            f"(radice attesa {meta['radice']}, trovata {etree.QName(documento).localname})."
        )

    schema = _schema("risposta")
    valido = bool(schema.validate(documento))
    errori_schema = [f"Riga {e.line}: {e.message}" for e in list(schema.error_log)[:12]]

    ns = {"e": meta["namespace"]}

    def testo(percorso: str, radice=documento) -> str:
        trovato = radice.find(percorso, ns)
        return (trovato.text or "").strip() if trovato is not None else ""

    righe: list[dict[str, Any]] = []
    # Le serie stanno sotto External_Account quando c'è una controparte, e
    # direttamente sotto ConnectionPoint nelle nomine non-matching (cliente
    # finale): entrambe le forme sono legittime e vanno lette.
    for serie in documento.iterfind(".//e:InformationOrigin_TimeSeries", ns):
        genitore = serie.getparent()
        controparte = (
            testo("e:externalAccount", genitore)
            if genitore is not None and etree.QName(genitore).localname == "External_Account"
            else ""
        )
        origine = testo("e:businessCode", serie)
        for periodo in serie.iterfind("e:Period", ns):
            stati = [
                {
                    "codice": testo("e:statusCode", stato),
                    "nota": testo("e:complementaryText.text", stato),
                }
                for stato in periodo.iterfind("e:Status", ns)
            ]
            righe.append(
                {
                    "controparte": controparte,
                    "origine": origine,
                    "intervallo": testo("e:timeInterval", periodo),
                    "direzione": testo("e:direction.gasDirectionCode", periodo),
                    "quantita": testo("e:quantity.amount", periodo),
                    "stati": stati,
                }
            )

    return {
        "identificativo": testo("e:identification"),
        "versione": testo("e:version"),
        "tipo_documento": testo("e:documentCode"),
        "creato_il": testo("e:creationDateTime"),
        "periodo_validita": testo("e:validityPeriod"),
        "emittente": testo("e:issuer_MarketParticipant.identification"),
        "destinatario": testo("e:recipient_MarketParticipant.identification"),
        "nomina_riferita": {
            "identificativo": testo("e:nomination_Document.identification"),
            "versione": testo("e:nomination_Document.version"),
            "tipo_documento": testo("e:nomination_Document.documentCode"),
        },
        "punto": testo(".//e:ConnectionPoint/e:identification"),
        "unita": testo(".//e:measureUnit.unitOfMeasureCode"),
        "righe": righe,
        "valido_xsd": valido,
        "errori_schema": [] if valido else errori_schema,
        "sha256": hashlib.sha256(grezzo).hexdigest(),
    }


def righe_confermate(risposta: dict[str, Any]) -> list[dict[str, Any]]:
    """Seleziona la serie che porta le quantità confermate allo shipper.

    Un NOMRES può contenere fino a quattro serie per lo stesso intervallo, e
    solo ``16G`` è la nomina confermata: ``14G`` e ``15G`` descrivono
    l'elaborazione dei trasportatori, ``18G`` è la nomina della controparte e
    per specifica ha la direzione speculare.  Confrontarle tutte insieme
    produrrebbe scostamenti inesistenti.
    """

    if not isinstance(risposta, dict):
        return []
    righe = [r for r in (risposta.get("righe") or []) if isinstance(r, dict)]
    confermate = [r for r in righe if (r.get("origine") or "") == ORIGINE_CONFERMATA]
    if confermate:
        return confermate
    # Risposte che non qualificano le serie: si confronta ciò che c'è.
    return [r for r in righe if not (r.get("origine") or "")]


def confronta_con_nomina(nomina_xml: bytes | str, risposta: dict[str, Any]) -> list[dict[str, Any]]:
    """Differenze fra quantità nominate e quantità confermate, per intervallo."""

    grezzo = nomina_xml.encode("utf-8") if isinstance(nomina_xml, str) else nomina_xml
    ns = {"e": SCHEMI["nomina"]["namespace"]}
    documento = etree.fromstring(grezzo)

    # I Period pendono da External_Account quando c'è una controparte e dal
    # punto di connessione nelle nomine non-matching: vanno letti entrambi,
    # altrimenti su una 04G non risulterebbe nominato niente.
    nominato: dict[tuple[str, str, str], str] = {}
    for periodo in documento.iterfind(".//e:Period", ns):
        genitore = periodo.getparent()
        conto = ""
        if genitore is not None and etree.QName(genitore).localname == "External_Account":
            conto_el = genitore.find("e:externalAccount", ns)
            conto = (conto_el.text or "").strip() if conto_el is not None else ""

        def valore(nome: str) -> str:
            el = periodo.find(f"e:{nome}", ns)
            return (el.text or "").strip() if el is not None else ""

        nominato[(conto, valore("timeInterval"), valore("direction.gasDirectionCode"))] = valore("quantity.amount")

    scostamenti = []
    visti = set()
    for riga in righe_confermate(risposta):
        chiave = (
            str(riga.get("controparte") or ""),
            str(riga.get("intervallo") or ""),
            str(riga.get("direzione") or ""),
        )
        visti.add(chiave)
        atteso = nominato.get(chiave)
        confermato = riga.get("quantita")
        if atteso is None:
            scostamenti.append({**riga, "nominato": None, "esito": "non nominato"})
        elif _diverso(atteso, confermato):
            scostamenti.append({**riga, "nominato": atteso, "esito": "ridotto" if _minore(confermato, atteso) else "aumentato"})
    for chiave, atteso in nominato.items():
        if chiave not in visti:
            scostamenti.append(
                {
                    "controparte": chiave[0],
                    "intervallo": chiave[1],
                    "direzione": chiave[2],
                    "quantita": None,
                    "nominato": atteso,
                    "stati": [],
                    "esito": "senza risposta",
                }
            )
    return scostamenti


def _diverso(a: Any, b: Any) -> bool:
    try:
        return float(a) != float(b)
    except (TypeError, ValueError):
        return a != b


def _minore(a: Any, b: Any) -> bool:
    try:
        return float(a) < float(b)
    except (TypeError, ValueError):
        return False
