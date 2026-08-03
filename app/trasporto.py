"""Trasporto interrompibile e UIOLI, dal lato in cui sta davvero lo shipper.

Nel Codice di Rete questi due processi sono di Snam: è il Trasportatore che
«ha facoltà di interrompere» la capacità interrompibile (Cap. 3, §2.2.1) ed è
Snam che verifica le condizioni del use-it-or-lose-it e comunica all'Autorità
(Cap. 7, §4.3).  Un portale per shipper non li esegue: li subisce, li
controlla e vi risponde.  Questo modulo copre esattamente quel perimetro:

* **registrare le interruzioni comunicate da Snam** e verificare ciò che il
  Codice fissa nel proprio testo: almeno 4 giorni fra il termine di
  un'interruzione e l'inizio della successiva (Cap. 3, §2.2.1, lettera b) —
  controllato sempre — e il tetto del 20% della capacità conferita per le
  interruzioni parziali aggiuntive in T1max (§2.2.1, lettera a), che il
  registro può solo segnalare perché richiede la capacità conferita e il
  T1max pubblicato.  I VALORI di Tmax, T1max, Dmax e Pmin non sono nel
  Codice: li pubblica Snam per ciascun punto e durata, e questo modulo non
  li ricopia né li inventa;
* **calcolare l'Utilizzo Medio per semestre** con la definizione vera del
  §4.3.1: rapporto fra i quantitativi immessi/prelevati dai bilanci
  definitivi e la capacità conferita giornaliera, con il denominatore
  diminuito delle tre voci previste (capacità messa a disposizione, capacità
  non disponibile per riduzioni/interruzioni ex Capp. 14 e 21, quantitativi
  attestati).  La soglia dell'80% va verificata in CIASCUNO dei due semestri
  (1/10–31/3 e 1/4–30/9), non su una media annuale;
* **preparare la nota giustificativa** che il §4.3 riconosce all'Utente:
  l'unico atto attivo dello shipper nel processo, da trasmettere «entro
  sette giorni lavorativi dal termine dell'Anno Termico di Riferimento» con
  i quantitativi da portare a detrazione e le motivazioni documentate.  Il
  portale la prepara e la fa scaricare: NON la invia, perché le modalità di
  trasmissione le pubblica Snam e una spedizione finta varrebbe zero.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Cap. 3, §2.2.1, lettera b): «Il Trasportatore potrà esercitare nuovamente la
# propria facoltà di interruzione non prima che siano trascorsi almeno 4
# giorni dal termine della precedente interruzione». L'altro valore che il
# Codice fissa nel testo è il tetto del 20% per le parziali in T1max
# (lettera a), verificabile solo conoscendo capacità conferita e T1max.
INTERVALLO_MINIMO_GIORNI = 4

# Soglia dell'Utilizzo Medio sotto la quale scatta la condizione b) del
# ritiro UIOLI (Cap. 7, §4.2, lettera b).
SOGLIA_UTILIZZO = 0.80

# I tre punti a cui si applica il use-it-or-lose-it di lungo termine
# (Cap. 7, §4.1: Passo Gries, Tarvisio e Gorizia).
PUNTI_UIOLI = ("Passo Gries", "Tarvisio", "Gorizia")

SEMESTRI = {
    "invernale": "1 ottobre – 31 marzo",
    "estivo": "1 aprile – 30 settembre",
}

TIPI_INTERRUZIONE = {
    "totale": "Interruzione totale",
    "parziale": "Interruzione parziale",
}

FONTI = {
    "interruzioni": "Codice di Rete Snam Rete Gas, Capitolo 3, §2.2 (rev. XCI)",
    "uioli": "Codice di Rete Snam Rete Gas, Capitolo 7, §4 (art. 14ter Delibera 137/02)",
    "parametri": (
        "I parametri Tmax, T1max, Dmax e Pmin sono pubblicati da Snam sul proprio "
        "sito per ciascun Punto di Entrata interconnesso con l'estero e per durata "
        "di conferimento: il Codice non ne fissa i valori."
    ),
}

MAX_TESTO = 160
MAX_MOTIVAZIONI = 4000
MAX_GIORNI = 366
MAX_NUMERO = 30
# Tetto sul corpo delle POST: le motivazioni arrivano a 4000 caratteri, tutto
# il resto è corto. 64 KiB coprono ogni payload legittimo con ampio margine.
MAX_CORPO_BYTES = 64 * 1024

CARATTERI_VIETATI = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")
NUMERO_RE = re.compile(r"^[0-9]{1,15}(\.[0-9]{1,3})?$")


class TrasportoError(ValueError):
    """Errore strutturato, traducibile in una risposta HTTP controllata."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


def ora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------ anno termico


def anno_termico(giorno: date) -> int:
    """L'Anno Termico inizia il 1 ottobre: si identifica con l'anno di avvio."""

    return giorno.year if giorno.month >= 10 else giorno.year - 1


def etichetta_anno_termico(avvio: int) -> str:
    return f"{avvio}/{avvio + 1}"


def scadenza_nota(anno_termico_riferimento: int) -> date:
    """Termine per la nota giustificativa: 7 giorni lavorativi dal 30 settembre.

    Il Codice dice «entro sette giorni lavorativi dal termine dell'Anno
    Termico di Riferimento» (Cap. 7, §4.3): l'Anno Termico termina il 30
    settembre dell'anno successivo all'avvio.  Qui i giorni lavorativi sono
    lunedì-venerdì; un festivo infrasettimanale accorcerebbe di fatto il
    termine calcolato, mai il contrario, quindi la data mostrata è prudente.
    """

    corrente = date(anno_termico_riferimento + 1, 9, 30)
    lavorativi = 0
    while lavorativi < 7:
        corrente += timedelta(days=1)
        if corrente.weekday() < 5:
            lavorativi += 1
    return corrente


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
        # I campi a riga singola finiscono interpolati nel testo della nota:
        # un a-capo permetterebbe di fabbricare righe che sembrano del modulo.
        _errore(errors, chiave, f"{etichetta}: niente a-capo o tabulazioni in questo campo.")
        return ""
    if len(valore) > max_len:
        _errore(errors, chiave, f"{etichetta}: massimo {max_len} caratteri (ricevuti {len(valore)}).")
        return ""
    if not valore and obbligatorio:
        _errore(errors, chiave, f"{etichetta}: campo obbligatorio.")
    return valore


def _numero(
    valore: Any,
    campo: str,
    etichetta: str,
    errors: list[dict[str, str]],
    *,
    obbligatorio: bool = True,
) -> float | None:
    testo = "" if valore is None or isinstance(valore, bool) else str(valore).strip()
    if "," in testo and "." in testo:
        if testo.rfind(",") > testo.rfind("."):
            testo = testo.replace(".", "").replace(",", ".")
        else:
            testo = testo.replace(",", "")
    elif "," in testo:
        testo = testo.replace(",", ".")
    elif re.fullmatch(r"[1-9][0-9]{0,2}(\.[0-9]{3})+", testo):
        # "45.000.000" senza virgola è la forma italiana dei separatori di
        # migliaia: si riconosce dai gruppi di tre cifre esatte, così "33.50"
        # resta un decimale e non diventa 3350 in silenzio.
        testo = testo.replace(".", "")
    if not testo:
        if obbligatorio:
            _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return None
    if len(testo) > MAX_NUMERO or not NUMERO_RE.fullmatch(testo):
        _errore(errors, campo, f"{etichetta}: atteso un numero non negativo (es. 1.250.000 kWh).")
        return None
    return float(testo)


def _data(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]]) -> date | None:
    testo = "" if valore is None else str(valore).strip()
    if not testo:
        _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return None
    try:
        valore = date.fromisoformat(testo)
    except ValueError:
        _errore(errors, campo, f"{etichetta}: data non valida, attesa nella forma AAAA-MM-GG.")
        return None
    if not 2000 <= valore.year <= 2100:
        # Oltre questi limiti l'aritmetica delle date può traboccare e nessuna
        # comunicazione reale di Snam può cadere lì: meglio un errore chiaro.
        _errore(errors, campo, f"{etichetta}: anno fuori dall'intervallo 2000-2100.")
        return None
    return valore


def _intero(valore: Any, campo: str, etichetta: str, errors: list[dict[str, str]], *,
            minimo: int, massimo: int, obbligatorio: bool = True) -> int | None:
    testo = "" if valore is None or isinstance(valore, bool) else str(valore).strip()
    if not testo:
        if obbligatorio:
            _errore(errors, campo, f"{etichetta}: campo obbligatorio.")
        return None
    if not re.fullmatch(r"[0-9]{1,5}", testo) or not minimo <= int(testo) <= massimo:
        _errore(errors, campo, f"{etichetta}: numero intero da {minimo} a {massimo}.")
        return None
    return int(testo)


def _chiave_punto(punto: str) -> str:
    """Chiave di confronto: «Tarvisio», «TARVISIO» e «tarvisio » sono lo stesso punto."""

    return " ".join(punto.split()).casefold()


def _formato_it(numero: float) -> str:
    """1250000.75 → «1.250.000,75»: il documento attesta il numero esatto."""

    testo = f"{numero:,.3f}".rstrip("0").rstrip(".")
    return testo.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


# ----------------------------------------------------- interruzioni ricevute


def registra_interruzione(dati: dict[str, Any], esistenti: list[dict[str, Any]]) -> dict[str, Any]:
    """Valida un'interruzione comunicata da Snam e la confronta con il registro.

    Le ``esistenti`` sono le interruzioni già registrate dallo stesso account:
    servono per l'unico controllo che il Codice consente di fare in autonomia
    — i 4 giorni minimi fra due interruzioni sullo stesso punto — e per
    intercettare sovrapposizioni, che nel registro di uno shipper possono
    solo essere errori di trascrizione o comunicazioni da contestare.
    """

    if not isinstance(dati, dict):
        raise TrasportoError("Dati dell'interruzione non validi: atteso un oggetto.")
    errors: list[dict[str, str]] = []
    avvisi: list[str] = []

    punto = _testo(dati, "punto", errors, "Punto di entrata")
    if punto and len(punto) < 3:
        _errore(errors, "punto", "Punto di entrata: almeno 3 caratteri (nome o codice EIC).")

    tipo = _testo(dati, "tipo", errors, "Tipo di interruzione", obbligatorio=False, max_len=16) or "totale"
    if tipo not in TIPI_INTERRUZIONE:
        _errore(errors, "tipo", f"Tipo di interruzione: ammessi {', '.join(TIPI_INTERRUZIONE)}.")

    inizio = _data(dati.get("data_inizio"), "data_inizio", "Data di inizio", errors)
    giorni = _intero(dati.get("giorni"), "giorni", "Giorni di interruzione", errors,
                     minimo=1, massimo=MAX_GIORNI)
    capacita = _numero(dati.get("capacita"), "capacita", "Capacità interrotta (kWh/g)",
                       errors, obbligatorio=False)
    preavviso = _intero(dati.get("preavviso_ore"), "preavviso_ore", "Preavviso ricevuto (ore)",
                        errors, minimo=0, massimo=8760, obbligatorio=False)
    riferimento = _testo(dati, "riferimento", errors, "Riferimento della comunicazione",
                         obbligatorio=False)
    note = _testo(dati, "note", errors, "Note", obbligatorio=False, max_len=500)

    if tipo == "parziale" and capacita is None:
        _errore(errors, "capacita", "Un'interruzione parziale richiede la capacità interrotta.")

    if errors:
        raise TrasportoError("L'interruzione non è stata registrata: correggi i campi segnalati.", errors)

    fine = inizio + timedelta(days=giorni - 1)

    chiave_punto = _chiave_punto(punto)
    for altra in esistenti:
        if _chiave_punto(str(altra.get("punto", ""))) != chiave_punto:
            continue
        altro_inizio = date.fromisoformat(altra["data_inizio"])
        altro_fine = altro_inizio + timedelta(days=int(altra["giorni"]) - 1)
        if inizio <= altro_fine and altro_inizio <= fine:
            raise TrasportoError(
                f"Su {punto} è già registrata un'interruzione che copre lo stesso periodo "
                f"({altra['data_inizio']}, {altra['giorni']} gg). Se Snam ha comunicato due "
                "interruzioni sovrapposte, è la comunicazione a essere anomala: verificala "
                "prima di registrarla.",
                [{"field": "data_inizio", "message": "Periodo sovrapposto a un'interruzione già registrata."}],
            )
        # Il vincolo dei 4 giorni vale in entrambe le direzioni: conta la
        # distanza fra la fine di una e l'inizio dell'altra, comunque ordinate.
        if altro_fine < inizio:
            distanza = (inizio - altro_fine).days
        else:
            distanza = (altro_inizio - fine).days
        if 0 < distanza <= INTERVALLO_MINIMO_GIORNI:
            avvisi.append(
                f"Solo {distanza - 1 if distanza > 1 else 0} giorni pieni dall'interruzione "
                f"del {altra['data_inizio']} sullo stesso punto: il Codice di Rete impone "
                f"almeno {INTERVALLO_MINIMO_GIORNI} giorni dal termine della precedente "
                "(Cap. 3, §2.2.1-b). Elemento da contestare a Snam."
            )

    avvio = anno_termico(inizio)
    if anno_termico(fine) != avvio:
        avvisi.append(
            "L'interruzione attraversa il cambio di Anno Termico (30 settembre): i giorni "
            "si conteggiano sull'anno in cui cadono, registra le due parti separatamente "
            "se serve il riparto esatto."
        )

    record = {
        "punto": punto,
        "tipo": tipo,
        "data_inizio": inizio.isoformat(),
        "data_fine": fine.isoformat(),
        "giorni": giorni,
        "capacita": capacita,
        "preavviso_ore": preavviso,
        "riferimento": riferimento,
        "note": note,
        "anno_termico": avvio,
        "avvisi": avvisi,
    }
    record["sha256"] = hashlib.sha256(
        "|".join(str(record[c]) for c in ("punto", "data_inizio", "giorni", "tipo")).encode("utf-8")
    ).hexdigest()
    return record


def riepilogo_interruzioni(righe: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Totali per punto e Anno Termico.

    I giorni si ripartiscono per tipo, perché il Codice distingue i plafond:
    Tmax copre le interruzioni totali o parziali, T1max è un periodo
    aggiuntivo riservato alle parziali entro il 20% della capacità conferita
    (Cap. 3, §2.2.1-a) — sommarli in un numero solo direbbe che il Tmax è più
    eroso di quanto sia.  I giorni consecutivi si misurano fondendo i periodi
    adiacenti: due interruzioni attaccate sono un'unica sequenza.
    """

    gruppi: dict[tuple[str, int], dict[str, Any]] = {}
    periodi: dict[tuple[str, int], list[tuple[date, date]]] = {}
    for riga in righe:
        chiave = (_chiave_punto(str(riga["punto"])), int(riga["anno_termico"]))
        gruppo = gruppi.setdefault(chiave, {
            "punto": riga["punto"],
            "anno_termico": int(riga["anno_termico"]),
            "etichetta": etichetta_anno_termico(int(riga["anno_termico"])),
            "interruzioni": 0,
            "giorni_totali": 0,
            "giorni_parziali": 0,
            "giorni_massimi_consecutivi": 0,
            "con_avvisi": 0,
        })
        gruppo["interruzioni"] += 1
        gruppo["giorni_totali"] += int(riga["giorni"])
        if riga.get("tipo") == "parziale":
            gruppo["giorni_parziali"] += int(riga["giorni"])
        if riga.get("avvisi"):
            gruppo["con_avvisi"] += 1
        periodi.setdefault(chiave, []).append((
            date.fromisoformat(riga["data_inizio"]),
            date.fromisoformat(riga["data_fine"]),
        ))
    for chiave, intervalli in periodi.items():
        intervalli.sort()
        massimo = corrente_inizio, corrente_fine = intervalli[0]
        massimo = (corrente_fine - corrente_inizio).days + 1
        for inizio, fine in intervalli[1:]:
            if inizio <= corrente_fine + timedelta(days=1):
                corrente_fine = max(corrente_fine, fine)
            else:
                corrente_inizio, corrente_fine = inizio, fine
            massimo = max(massimo, (corrente_fine - corrente_inizio).days + 1)
        gruppi[chiave]["giorni_massimi_consecutivi"] = massimo
    return sorted(gruppi.values(), key=lambda g: (-g["anno_termico"], _chiave_punto(str(g["punto"]))))


# ------------------------------------------------------------ utilizzo medio


def calcola_utilizzo_medio(dati: dict[str, Any]) -> dict[str, Any]:
    """Utilizzo Medio di un semestre secondo il §4.3.1, con le tre detrazioni.

    Numeratore: somma dei quantitativi immessi/prelevati nel semestre, dai
    bilanci di trasporto definitivi.  Denominatore: somma della capacità
    conferita giornaliera, diminuita della capacità messa a disposizione, di
    quella non disponibile per riduzioni/interruzioni (Capp. 14 e 21) e dei
    quantitativi attestati con la nota giustificativa.  Ignorare le
    detrazioni gonfia il denominatore e fa sembrare sotto soglia chi non lo è.
    """

    if not isinstance(dati, dict):
        raise TrasportoError("Dati del calcolo non validi: atteso un oggetto.")
    errors: list[dict[str, str]] = []

    semestre = _testo(dati, "semestre", errors, "Semestre", max_len=16)
    if semestre and semestre not in SEMESTRI:
        _errore(errors, "semestre", f"Semestre: ammessi {', '.join(SEMESTRI)}.")

    immessi = _numero(dati.get("immessi"), "immessi",
                      "Quantitativi immessi/prelevati nel semestre (kWh)", errors)
    conferita = _numero(dati.get("capacita_conferita"), "capacita_conferita",
                        "Capacità conferita nel semestre (kWh)", errors)
    disposizione = _numero(dati.get("messa_disposizione"), "messa_disposizione",
                           "Capacità messa a disposizione (kWh)", errors, obbligatorio=False) or 0.0
    indisponibile = _numero(dati.get("non_disponibile"), "non_disponibile",
                            "Capacità non disponibile per riduzioni/interruzioni (kWh)",
                            errors, obbligatorio=False) or 0.0
    attestati = _numero(dati.get("attestati"), "attestati",
                        "Quantitativi attestati con nota giustificativa (kWh)",
                        errors, obbligatorio=False) or 0.0

    if errors:
        raise TrasportoError("Utilizzo Medio non calcolabile: correggi i campi segnalati.", errors)

    denominatore = conferita - disposizione - indisponibile - attestati
    if denominatore <= 0:
        raise TrasportoError(
            "Utilizzo Medio non calcolabile: le detrazioni superano o azzerano la capacità "
            "conferita. Ricontrolla i valori: il denominatore del §4.3.1 deve restare positivo.",
            [{"field": "capacita_conferita", "message": "Denominatore nullo o negativo."}],
        )

    utilizzo = immessi / denominatore
    return {
        "semestre": semestre,
        "periodo": SEMESTRI[semestre],
        "immessi": immessi,
        "capacita_conferita": conferita,
        "detrazioni": {
            "messa_disposizione": disposizione,
            "non_disponibile": indisponibile,
            "attestati": attestati,
        },
        "denominatore": denominatore,
        "utilizzo_medio": round(utilizzo, 6),
        "percentuale": round(utilizzo * 100, 2),
        "sotto_soglia": utilizzo < SOGLIA_UTILIZZO,
        "soglia": SOGLIA_UTILIZZO,
        # La condizione b) del §4.2 richiede la soglia violata in ENTRAMBI i
        # semestri: un solo semestre sotto l'80% non basta al ritiro, e il
        # chiamante deve saperlo prima di allarmarsi.
        "nota": (
            "La condizione b) del ritiro richiede l'Utilizzo Medio sotto l'80% in ciascuno "
            "dei due semestri; le altre condizioni (titolarità pluriennale, mancata messa a "
            "disposizione, capacità interamente conferita) le verifica Snam Rete Gas."
        ),
    }


# ------------------------------------------------------- nota giustificativa


def prepara_nota(dati: dict[str, Any]) -> dict[str, Any]:
    """Prepara la nota giustificativa del §4.3, pronta da scaricare.

    Il contenuto segue ciò che il Codice prescrive: l'attestazione dei
    quantitativi da portare a detrazione con la relativa durata, e le
    motivazioni documentate del mancato utilizzo e della mancata messa a
    disposizione.  La trasmissione resta fuori: le modalità le pubblica Snam
    sul proprio sito, e qui non si simula nessun invio.
    """

    if not isinstance(dati, dict):
        raise TrasportoError("Dati della nota non validi: atteso un oggetto.")
    errors: list[dict[str, str]] = []
    avvisi: list[str] = []

    punto = _testo(dati, "punto", errors, "Punto")
    if punto and punto not in PUNTI_UIOLI:
        avvisi.append(
            f"Il use-it-or-lose-it di lungo termine si applica a {', '.join(PUNTI_UIOLI)}: "
            f"per «{punto}» la nota potrebbe non avere una sede di destinazione."
        )

    avvio = _intero(dati.get("anno_termico"), "anno_termico", "Anno Termico di Riferimento",
                    errors, minimo=2000, massimo=2100)
    capacita = _numero(dati.get("capacita_detrazione"), "capacita_detrazione",
                       "Capacità da portare a detrazione (kWh/g)", errors)
    durata = _testo(dati, "durata", errors, "Durata della capacità da detrarre")
    motivazioni = _testo(dati, "motivazioni", errors, "Motivazioni documentate",
                         max_len=MAX_MOTIVAZIONI, multilinea=True)
    if motivazioni and len(motivazioni) < 40:
        _errore(errors, "motivazioni",
                "Motivazioni: il Codice chiede motivazioni documentate, non una riga. "
                "Descrivi l'evento e allega i riferimenti (almeno 40 caratteri).")
    mittente = _testo(dati, "mittente", errors, "Ragione sociale dell'Utente")

    if errors:
        raise TrasportoError("La nota non è stata preparata: correggi i campi segnalati.", errors)

    termine = scadenza_nota(avvio)
    oggi = datetime.now(timezone.utc).date()
    fuori_termine = oggi > termine
    if fuori_termine:
        avvisi.append(
            f"Il termine dei sette giorni lavorativi dal 30 settembre "
            f"({termine.isoformat()}) è già passato: la nota può non essere considerata."
        )

    testo = "\n".join([
        "NOTA GIUSTIFICATIVA AI SENSI DEL CODICE DI RETE, CAPITOLO 7, §4.3",
        "(articolo 14ter della Delibera n. 137/02 — use-it-or-lose-it di lungo termine)",
        "",
        f"Utente:                     {mittente}",
        f"Punto di Entrata/Uscita:    {punto}",
        f"Anno Termico di Riferimento: {etichetta_anno_termico(avvio)}",
        f"Preparata il:               {oggi.isoformat()}",
        f"Termine di trasmissione:    {termine.isoformat()} (7 giorni lavorativi dal 30/09)",
        "",
        "1. ATTESTAZIONE DEI QUANTITATIVI DA PORTARE A DETRAZIONE (§4.3.1, punto 2)",
        f"   Capacità: {_formato_it(capacita)} kWh/g",
        f"   Durata:   {durata}",
        "",
        "2. MOTIVAZIONI DEL MANCATO UTILIZZO E DELLA MANCATA MESSA A DISPOSIZIONE",
        "   " + "\n   ".join(motivazioni.splitlines()),
        "",
        "La presente nota è predisposta ai fini delle verifiche di cui al Capitolo 7, "
        "paragrafo 4.3 del Codice di Rete. Le modalità di trasmissione sono quelle "
        "pubblicate da Snam Rete Gas sul proprio sito Internet.",
    ])

    return {
        "punto": punto,
        "anno_termico": avvio,
        "etichetta": etichetta_anno_termico(avvio),
        "capacita_detrazione": capacita,
        "durata": durata,
        "motivazioni": motivazioni,
        "mittente": mittente,
        "scadenza": termine.isoformat(),
        "fuori_termine": fuori_termine,
        "avvisi": avvisi,
        "testo": testo,
        "sha256": hashlib.sha256(testo.encode("utf-8")).hexdigest(),
    }


def catalogo() -> dict[str, Any]:
    """Regole e riferimenti per l'interfaccia: solo ciò che sta nel Codice."""

    oggi = datetime.now(timezone.utc).date()
    corrente = anno_termico(oggi)
    # Finché il termine della nota dell'anno precedente non è passato, è
    # quello l'Anno Termico di Riferimento su cui l'operatore sta lavorando.
    riferimento = corrente - 1 if oggi <= scadenza_nota(corrente - 1) else corrente
    return {
        "fonti": FONTI,
        "intervallo_minimo_giorni": INTERVALLO_MINIMO_GIORNI,
        "soglia_utilizzo": SOGLIA_UTILIZZO,
        "punti_uioli": list(PUNTI_UIOLI),
        "semestri": SEMESTRI,
        "tipi_interruzione": TIPI_INTERRUZIONE,
        "anno_termico_corrente": corrente,
        "etichetta_corrente": etichetta_anno_termico(corrente),
        "anno_termico_riferimento": riferimento,
        "scadenza_nota_riferimento": scadenza_nota(riferimento).isoformat(),
    }
