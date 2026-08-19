"""Agenda regolatoria e scadenze, dal lato dello shipper.

Le date operative del gas italiano stanno in documenti pubblici che il
portale non riscrive: le richiama.  Il modello regolatorio contiene solo
voci la cui data è fissata da una fonte verificabile — Codice di Stoccaggio
Stogit (allegato alle delibere ARERA), Codice di Rete Snam Rete Gas,
comunicazioni ARERA — e ogni voce porta il riferimento.  Dove la fonte non
fissa una data (consultazioni ARERA, aste Snam pubblicate su Jarvis,
segnalazioni REMIT legate alla transazione), il modello tace: quelle
scadenze l'operatore le crea come voci personalizzate, senza date inventate.

L'agenda è un promemoria locale, non una prova: le voci si modificano e si
eliminano, a differenza di ricevute ed esiti.  Non sostituisce le
pubblicazioni ufficiali: la data mostrata va verificata sulla fonte prima
di ogni adempimento.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .trasporto import scadenza_nota

CATEGORIE = {
    "trasporto": "Trasporto · Snam",
    "stoccaggio": "Stoccaggio · Stogit",
    "regolatorio": "Regolatorio · ARERA",
    "remit": "REMIT · ACER",
    "operativo": "Operativo · giorno gas",
    "personale": "Personale",
}

RICORRENZE = {
    "una_tantum": "Una tantum",
    "annuale": "Annuale",
    "mensile": "Mensile",
    "trimestrale": "Trimestrale",
    "settimanale": "Settimanale",
    "giorno_gas": "Ogni giorno gas",
}

STATI = {"aperta", "adempiuta", "saltata"}

FONTI = {
    "stoccaggio": {
        "nome": "Codice di Stoccaggio Stogit (testo vigente, allegato alle delibere ARERA)",
        "url": "https://www.stogit.it/",
    },
    "trasporto": {
        "nome": "Codice di Rete Snam Rete Gas (testo vigente)",
        "url": "https://www.snam.it/it/i-nostri-business/trasporto/codice-di-rete-tariffe-area-comitato-e-consultazioni/codice-di-rete.html",
    },
    "regolatorio": {
        "nome": "ARERA · area operatori gas e comunicati per operatori",
        "url": "https://www.arera.it/area-operatori/gas",
    },
    "remit": {
        "nome": "ACER · REMIT Reporting Guidance (TRUM, MoP)",
        "url": "https://www.acer.europa.eu/remit-documents",
    },
}

# ------------------------------------------------------------------ modello
#
# Ogni voce ha una data fissata dalla fonte: mese/giorno + regola rispetto
# all'Anno Termico scelto.  L'Anno Termico di istanziazione è quello dello
# stoccaggio (avvio 1 aprile, come il Codice di Stoccaggio); le voci del
# trasporto si riferiscono all'Anno Termico con avvio nello stesso anno
# civile (1 ottobre): i due calendari convivono, e il riferimento della voce
# dice sempre a quale dei due appartiene.

# (chiave, categoria, titolo, mese, giorno, offset_anni, riferimento)
# La data è: date(anno + offset_anni, mese, giorno), con anno = avvio AT
# stoccaggio; per le voci trasporto l'etichetta dell'AT è quella propria.
_MODELLO: list[tuple[str, str, str, int, int, int, str]] = [
    # Anno Termico stoccaggio: 1 aprile – 31 marzo.
    ("stoccaggio.fase_iniezione_inizio", "stoccaggio", "Inizio Fase di Iniezione", 4, 1, 0,
     "Codice di Stoccaggio · definizione Fase di Iniezione (1/4 – 31/10)"),
    ("stoccaggio.fase_iniezione_fine", "stoccaggio", "Fine Fase di Iniezione", 10, 31, 0,
     "Codice di Stoccaggio · definizione Fase di Iniezione (1/4 – 31/10)"),
    ("stoccaggio.fase_erogazione_inizio", "stoccaggio", "Inizio Fase di Erogazione", 11, 1, 0,
     "Codice di Stoccaggio · definizione Fase di Erogazione (1/11 – 31/3)"),
    ("stoccaggio.fase_erogazione_fine", "stoccaggio", "Fine Fase di Erogazione", 3, 31, 1,
     "Codice di Stoccaggio · definizione Fase di Erogazione (1/11 – 31/3)"),
    ("stoccaggio.programma_erogazione", "stoccaggio",
     "Programma stagionale di erogazione da inserire in SAMPEI", 10, 23, 0,
     "Codice di Stoccaggio, §6.3.2: inserimento entro e non oltre il 23 ottobre"),
    ("stoccaggio.accettazione_erogazione", "stoccaggio",
     "Accettazione del programma stagionale di erogazione da Stogit", 10, 31, 0,
     "Codice di Stoccaggio, §6.3.2: comunicazione entro il 31 ottobre"),
    ("stoccaggio.accettazione_iniezione", "stoccaggio",
     "Accettazione del programma stagionale di iniezione da Stogit", 3, 31, 1,
     "Codice di Stoccaggio, §6.3.1: comunicazione entro e non oltre il 31 marzo"),
    ("stoccaggio.calendario_conferimento", "stoccaggio",
     "Pubblicazione del calendario di conferimento capacità dei Servizi Base", 2, 1, 0,
     "Codice di Stoccaggio, capitolo 5: pubblicazione sul sito Stogit entro il 1° febbraio"),
    ("stoccaggio.fattura_stogit_iniezione", "stoccaggio",
     "Fattura Stogit: riaddebito costi, periodo iniezione (1/4 – 31/10)", 3, 31, 1,
     "Codice di Stoccaggio, Cap. 7 Allegato 1: entro la fine dell'Anno Termico"),
    ("stoccaggio.fattura_stogit_erogazione", "stoccaggio",
     "Fattura Stogit: riaddebito costi, periodo erogazione (1/11 – 31/3)", 5, 31, 1,
     "Codice di Stoccaggio, Cap. 7 Allegato 1: entro il 31 maggio successivo"),
    ("stoccaggio.fattura_utente_iniezione", "stoccaggio",
     "Fattura dell'utente a Stogit: energia elettrica, periodo iniezione", 4, 30, 1,
     "Codice di Stoccaggio, Cap. 7 Allegato 1: entro il 30 aprile"),
    ("stoccaggio.fattura_utente_erogazione", "stoccaggio",
     "Fattura dell'utente a Stogit: energia elettrica, periodo erogazione", 6, 30, 1,
     "Codice di Stoccaggio, Cap. 7 Allegato 1: entro il 30 giugno"),
    # Anno Termico trasporto: 1 ottobre – 30 settembre (avvio nello stesso
    # anno civile dell'avvio dell'AT stoccaggio scelto).
    ("trasporto.anno_termico_avvio", "trasporto", "Inizio Anno Termico di trasporto", 10, 1, 0,
     "Codice di Rete Snam · Anno Termico dal 1° ottobre"),
    ("trasporto.uioli_nota", "trasporto", "Termine nota giustificativa UIOLI", 9, 30, 1,
     "Codice di Rete, Cap. 7 §4.3: sette giorni lavorativi dal termine dell'Anno Termico"),
]

MAX_TESTO = 160
MAX_NOTA = 500
MAX_CORPO_BYTES = 64 * 1024

CARATTERI_VIETATI = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


class AgendaError(ValueError):
    """Errore strutturato, traducibile in una risposta HTTP controllata."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


def ora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


FUSO_AGENDA = ZoneInfo("Europe/Rome")


def oggi_roma() -> date:
    """La data corrente sul calendario italiano (le scadenze valgono sul giorno italiano)."""

    return datetime.now(FUSO_AGENDA).date()


def etichetta_at_stoccaggio(avvio: int) -> str:
    return f"{avvio}/{avvio + 1}"


def etichetta_at_trasporto(avvio: int) -> str:
    return f"{avvio}/{avvio + 1}"


# ------------------------------------------------------------- modello puro


def date_modello(chiave: str, anno: int) -> date:
    """Data fissata dalla fonte per una voce del modello, dato l'avvio AT stoccaggio.

    Per la voce UIOLI la data non è un giorno fisso: sono i sette giorni
    lavorativi dal 30 settembre, come già calcolato dal modulo Trasporto.
    """

    if chiave == "trasporto.uioli_nota":
        return scadenza_nota(anno)
    for voce, _, _, mese, giorno, offset, _ in _MODELLO:
        if voce == chiave:
            return date(anno + offset, mese, giorno)
    raise AgendaError(f"Voce del modello sconosciuta: {chiave}.")


def modello_per_at(anno: int) -> list[dict[str, Any]]:
    """Le voci del modello con le date calcolate per l'AT stoccaggio avviato ad ``anno``."""

    voci = []
    for chiave, categoria, titolo, mese, giorno, offset, riferimento in _MODELLO:
        if chiave == "trasporto.uioli_nota":
            data = scadenza_nota(anno)
            at = etichetta_at_trasporto(anno)
        else:
            data = date(anno + offset, mese, giorno)
            at = etichetta_at_stoccaggio(anno) if categoria == "stoccaggio" else etichetta_at_trasporto(anno)
        voci.append({
            "chiave": chiave,
            "categoria": categoria,
            "titolo": titolo,
            "data": data.isoformat(),
            "riferimento": riferimento,
            "anno_termico": at,
        })
    return voci


def prossima_occorrenza(data: date, ricorrenza: str) -> date:
    """La prossima scadenza dopo l'adempimento di una voce ricorrente.

    L'adempimento di una voce che ricorre genera la prossima occorrenza: il
    conteggio riparte dalla data adempiuta, non dal calendario (un adempimento
    in ritardo non recupera le occorrenze perse, che restano nella cronologia).
    """

    if ricorrenza == "giorno_gas":
        return data + timedelta(days=1)
    if ricorrenza == "settimanale":
        return data + timedelta(days=7)
    if ricorrenza == "mensile" or ricorrenza == "trimestrale":
        passi = 1 if ricorrenza == "mensile" else 3
        mese = data.month - 1 + passi
        anno = data.year + mese // 12
        mese = mese % 12 + 1
        giorno = min(data.day, _giorni_mese(anno, mese))
        return date(anno, mese, giorno)
    if ricorrenza == "annuale":
        anno = data.year + 1
        giorno = min(data.day, _giorni_mese(anno, data.month))
        return date(anno, data.month, giorno)
    return data


def _giorni_mese(anno: int, mese: int) -> int:
    if mese == 2:
        return 29 if anno % 4 == 0 and (anno % 100 != 0 or anno % 400 == 0) else 28
    return 30 if mese in (4, 6, 9, 11) else 31


def _termine_scadenza(data: date, categoria: str) -> datetime:
    """Il momento in cui la scadenza è davvero passata, sul calendario italiano.

    Una voce operativa vale sul giorno gas (06:00 → 06:00 locali): «entro il
    giorno gas X» resta aperta fino alle 06:00 di X+1, non a mezzanotte.
    Le altre voci hanno scadenze di calendario e valgono fino alla
    mezzanotte del giorno.
    """

    domani = data + timedelta(days=1)
    ora = time(6) if categoria == "operativo" else time(0)
    return datetime.combine(domani, ora, tzinfo=FUSO_AGENDA)


def _adesso(adesso: datetime | None) -> datetime:
    """L'istante di riferimento, sempre sul calendario italiano.

    Gli istanti senza fuso sono interpretati come ora italiana: è la stessa
    regola degli istanti del giorno gas, dove un valore nudo segue il fuso
    locale.
    """

    if adesso is None:
        return datetime.now(FUSO_AGENDA)
    return adesso if adesso.tzinfo is not None else adesso.replace(tzinfo=FUSO_AGENDA)


def stato_effettivo(riga: dict[str, Any], adesso: datetime | None = None) -> str:
    """Lo stato mostrato: una voce aperta oltre la sua scadenza è scaduta.

    La scaduta è uno stato derivato, non scritto: lasciare la riga «aperta»
    permette all'operatore di decidere dopo — adempierla (generando la
    prossima occorrenza se ricorrente) o dichiararla saltata.  Il termine
    dipende dalla categoria: le voci operative valgono sul giorno gas e
    scadono alle 06:00 del giorno dopo (vedi ``_termine_scadenza``).
    """

    if riga.get("stato") != "aperta":
        return str(riga.get("stato") or "aperta")
    termine = _termine_scadenza(date.fromisoformat(riga["data_scadenza"]), str(riga.get("categoria") or ""))
    if _adesso(adesso) >= termine:
        return "scaduta"
    return "aperta"


def contatori(scadenze: list[dict[str, Any]], adesso: datetime | None = None) -> dict[str, int]:
    """Quante cose chiedono attenzione oggi, nei prossimi 7 e 30 giorni.

    Conta solo le voci aperte (incluse quelle già scadute, che restano il
    problema più urgente): adempiute e saltate non chiedono nulla.  Le
    finestre misurano il tempo che manca al termine della voce (l'ora del
    giorno gas per le voci operative), quindi una voce operativa resta
    «oggi» fino alle 06:00 del giorno dopo, non a mezzanotte.
    """

    adesso = _adesso(adesso)
    oggi_n, sette_n, trenta_n, scadute_n = 0, 0, 0, 0
    adempiute_mese = 0
    for riga in scadenze:
        effettivo = stato_effettivo(riga, adesso)
        if effettivo not in ("aperta", "scaduta"):
            if effettivo == "adempiuta" and _stesso_mese(date.fromisoformat(riga["data_scadenza"]), adesso.date()):
                adempiute_mese += 1
            continue
        restante = _termine_scadenza(date.fromisoformat(riga["data_scadenza"]), str(riga.get("categoria") or "")) - adesso
        if restante <= timedelta(0):
            scadute_n += 1
            oggi_n += 1
            sette_n += 1
            trenta_n += 1
        else:
            if restante <= timedelta(days=1):
                oggi_n += 1
            if restante <= timedelta(days=7):
                sette_n += 1
            if restante <= timedelta(days=30):
                trenta_n += 1
    return {
        "oggi": oggi_n,
        "sette": sette_n,
        "trenta": trenta_n,
        "scadute": scadute_n,
        "adempiute_mese": adempiute_mese,
    }


def _stesso_mese(a: date, b: date) -> bool:
    return a.year == b.year and a.month == b.month


# ------------------------------------------------------------- validazioni


def _errore(errors: list[dict[str, str]], campo: str, messaggio: str) -> None:
    errors.append({"field": campo, "message": messaggio})


def _testo(
    dati: dict[str, Any],
    chiave: str,
    errors: list[dict[str, str]],
    etichetta: str,
    *,
    obbligatorio: bool = True,
    max_len: int = MAX_TESTO,
    multilinea: bool = False,
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
        _errore(errors, chiave, f"{etichetta}: contiene caratteri non ammessi.")
        return ""
    if not multilinea and re.search(r"[\t\n\r]", valore):
        _errore(errors, chiave, f"{etichetta}: niente a-capo o tabulazioni in questo campo.")
        return ""
    if len(valore) > max_len:
        _errore(errors, chiave, f"{etichetta}: massimo {max_len} caratteri (ricevuti {len(valore)}).")
        return ""
    if not valore and obbligatorio:
        _errore(errors, chiave, f"{etichetta}: campo obbligatorio.")
    return valore


def _data(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]]) -> date | None:
    testo = "" if valore is None else str(valore).strip()
    if not testo:
        _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return None
    try:
        data = date.fromisoformat(testo)
    except ValueError:
        _errore(errors, campo, f"{etichetta}: data non valida, attesa nella forma AAAA-MM-GG.")
        return None
    if not 2000 <= data.year <= 2100:
        _errore(errors, campo, f"{etichetta}: anno fuori dall'intervallo 2000-2100.")
        return None
    return data


def prepara_scadenza(dati: dict[str, Any]) -> dict[str, Any]:
    """Valida una scadenza (nuova o aggiornata) e la normalizza per il salvataggio.

    Lo stato è accettato solo fra quelli dichiarati; «scaduta» non è uno
    stato scrivibile perché è derivato dalla data, non una decisione.
    """

    if not isinstance(dati, dict):
        raise AgendaError("Dati della scadenza non validi: atteso un oggetto.")
    errors: list[dict[str, str]] = []

    titolo = _testo(dati, "titolo", errors, "Titolo")
    if titolo and len(titolo) < 3:
        _errore(errors, "titolo", "Titolo: almeno 3 caratteri.")

    categoria = _testo(dati, "categoria", errors, "Categoria", max_len=16)
    if categoria and categoria not in CATEGORIE:
        _errore(errors, "categoria", f"Categoria: ammesse {', '.join(CATEGORIE)}.")

    data = _data(dati.get("data_scadenza"), "data_scadenza", "Data di scadenza", errors)

    ricorrenza = _testo(dati, "ricorrenza", errors, "Ricorrenza", obbligatorio=False, max_len=16) or "una_tantum"
    if ricorrenza not in RICORRENZE:
        _errore(errors, "ricorrenza", f"Ricorrenza: ammesse {', '.join(RICORRENZE)}.")

    stato = _testo(dati, "stato", errors, "Stato", obbligatorio=False, max_len=16) or "aperta"
    if stato not in STATI:
        _errore(errors, "stato", f"Stato: ammessi {', '.join(sorted(STATI))}.")

    riferimento = _testo(dati, "riferimento", errors, "Riferimento", obbligatorio=False)
    nota = _testo(dati, "nota", errors, "Nota", obbligatorio=False, max_len=MAX_NOTA, multilinea=True)

    if errors:
        raise AgendaError("La scadenza non è stata salvata: correggi i campi segnalati.", errors)

    return {
        "titolo": titolo,
        "categoria": categoria,
        "data_scadenza": data.isoformat(),
        "ricorrenza": ricorrenza,
        "stato": stato,
        "riferimento": riferimento,
        "nota": nota,
    }


def istanzia_modello(anno: int, esistenti: list[dict[str, Any]]) -> dict[str, Any]:
    """Istanzia il modello per l'AT stoccaggio avviato ad ``anno``.

    Le voci già presenti per lo stesso Anno Termico non vengono duplicati:
    l'operazione è idempotente per singola voce.  Se tutte le voci esistono
    già, è un errore: l'operatore ha davanti l'elenco e non deve ripeterla.
    """

    if not 2000 <= anno <= 2100:
        raise AgendaError(
            "Anno Termico non valido: atteso un anno da 2000 a 2100.",
            [{"field": "anno", "message": "Anno fuori dall'intervallo 2000-2100."}],
        )
    presenti = {
        (str(esistente.get("modello_chiave")), esistente.get("modello_anno"))
        for esistente in esistenti
        if esistente.get("modello_chiave")
    }
    da_creare = []
    gia_presenti = []
    for voce in modello_per_at(anno):
        if (voce["chiave"], anno) in presenti:
            gia_presenti.append(voce)
            continue
        da_creare.append({
            **voce,
            "stato": "aperta",
            "ricorrenza": "annuale" if voce["categoria"] in ("stoccaggio", "trasporto") else "una_tantum",
            "nota": "",
        })
    if not da_creare:
        raise AgendaError(
            f"Il modello per l'Anno Termico {etichetta_at_stoccaggio(anno)} è già istanziato: "
            "nessuna voce nuova da creare.",
            [{"field": "anno", "message": "Modello già istanziato per questo Anno Termico."}],
        )
    return {"da_creare": da_creare, "gia_presenti": gia_presenti}


def catalogo() -> dict[str, Any]:
    """Fonti, categorie e modello calcolato per gli AT correnti, per l'interfaccia."""

    oggi = oggi_roma()
    # AT stoccaggio corrente: quello in corso oggi (avvio = anno del 1° aprile
    # più recente); AT successivo: quello che inizia il prossimo 1° aprile.
    corrente = oggi.year if oggi >= date(oggi.year, 4, 1) else oggi.year - 1
    return {
        "fonti": FONTI,
        "categorie": CATEGORIE,
        "ricorrenze": RICORRENZE,
        "stati": {stato: stato for stato in sorted(STATI)},
        "oggi": oggi.isoformat(),
        "anno_termico_corrente": corrente,
        "etichetta_corrente": etichetta_at_stoccaggio(corrente),
        "anno_termico_successivo": corrente + 1,
        "etichetta_successiva": etichetta_at_stoccaggio(corrente + 1),
        "modello_corrente": modello_per_at(corrente),
        "modello_successivo": modello_per_at(corrente + 1),
    }