"""Coefficienti di correzione climatica Wkr pubblicati da Snam Rete Gas.

Il fattore Wkr, determinato per ciascuna zona climatica, è pubblicato ogni
giorno su Jarvis (il portale dati pubblici di Snam). Per uno shipper che
prevede la domanda per nominare è il tassello ufficiale che collega la
previsione «grezza» alla correzione climatica usata nel settlement.

Questo modulo fa tre cose, tutte dichiarate:

* **legge il CSV di Jarvis** — l'operatore lo scarica dalla pagina pubblica e
  lo incolla qui (oppure il portale lo scarica live, vedi sotto). Il parser
  valida la griglia zona × giorno senza inventare nulla;
* **lo sistema in una tabella** — 18 zone climatiche per la finestra di sette
  giorni pubblicata (ieri consuntivo, oggi in corso, i prossimi cinque
  provvisori), con il tipo di ciascun giorno e la fonte dichiarata;
* **espone il fattore per zona e data** — così il modulo Previsione può
  mostrare (e, se l'operatore lo chiede, applicare) il Wkr ufficiale accanto
  ai giorni previsti.

Due onestà dovute:

* i dati sono pubblici ma Snam vieta la redistribuzione a terzi; qui sono
  mostrati all'operatore che li ha richiesti, **non conservati né
  ritrasmessi** — la stessa posizione di chi li apre nel browser;
* l'API di Jarvis non è un contratto pubblico: il portale la legge dalla
  configurazione pubblica del sito Snam (user_key e indirizzo compresi), così
  se Snam li aggiorna il modulo continua a funzionare; se la chiamata fallisce
  l'operatore può sempre incollare il CSV a mano.

Tutto in puro Python: il fetch usa ``urllib`` della libreria standard, nessuna
dipendenza nuova.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

# La finestra pubblicata: G−1 (consuntivo), G (in corso), G+1…G+5 (provvisori).
TIPI_AMMESSI = ("C", "I", "P", "P2", "P3", "P4", "P5")
ETICHETTE_TIPO = {
    "C": "Consuntivo (G−1)",
    "I": "In corso (giorno gas)",
    "P": "Provvisorio (G+1)",
    "P2": "Provvisorio (G+2)",
    "P3": "Provvisorio (G+3)",
    "P4": "Provvisorio (G+4)",
    "P5": "Provvisorio (G+5)",
}

INTESTAZIONE_ATTESA = ["zona_climatica", "giorno", "data_wkr", "wkr", "tipo", "data_hdd"]

MAX_RIGHE = 5_000
MAX_CORPO_BYTES = 512 * 1024

# Il Wkr è un fattore attorno a 1 (in inverno tipicamente 0.7–1.4): un valore
# fuori da questi limiti non è un fattore climatico plausibile e va fermato.
WKR_MIN = 0.1
WKR_MAX = 5.0

JARVIS_CONFIG_URL = "https://jarvis.snam.it/config/portal-public-config.json"
JARVIS_PAGINA_URL = (
    "https://jarvis.snam.it/public-data?pubblicazione=Coefficienti%20WKR"
    "&periodo={anno}&lang=it"
)
TIPOLOGIA_WKR = "Coefficienti WKR"
USER_AGENT = "Vettore (portale shipper; lettura operativa dei dati pubblici Snam)"
TIMEOUT_SECONDS = 15


class WkrError(ValueError):
    """Errore strutturato, traducibile in una risposta HTTP controllata."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


# ------------------------------------------------------------- lettura CSV


def _data_da_aaaammgg(testo: str) -> date | None:
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", testo.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _wkr_da_testo(testo: str) -> float | None:
    testo = testo.strip().replace(",", ".")
    try:
        valore = float(testo)
    except ValueError:
        return None
    if valore != valore or valore in (float("inf"), float("-inf")):
        return None
    return valore


def leggi_csv_wkr(contenuto: str) -> list[dict[str, Any]]:
    """Interpreta il CSV dei coefficienti Wkr pubblicato da Jarvis.

    Restituisce una lista di record ``{zona, giorno, wkr, tipo, data_wkr,
    data_hdd}``. Ogni riga non leggibile è indicata per numero; la griglia
    deve essere rettangolare (stesse zone per ogni giorno, stessi giorni per
    ogni zona) altrimenti il dato non è affidabile e ci si ferma.
    """

    if not isinstance(contenuto, str) or not contenuto.strip():
        raise WkrError(
            "Incolla il CSV dei coefficienti Wkr scaricato da Jarvis, "
            "oppure usa «Scarica da Jarvis»."
        )

    righe = [r for r in contenuto.splitlines() if r.strip()]
    if len(righe) > MAX_RIGHE:
        raise WkrError(f"Troppe righe: il limite è {MAX_RIGHE:,}.".replace(",", "."))

    separatore = max((";", ",", "\t"), key=lambda s: righe[0].count(s))
    intestazione = [c.strip().strip('"').lower() for c in righe[0].split(separatore)]
    if intestazione[: len(INTESTAZIONE_ATTESA)] != INTESTAZIONE_ATTESA:
        raise WkrError(
            "L'intestazione non è quella del CSV Wkr di Jarvis: attese le colonne "
            "ZONA_CLIMATICA;GIORNO;DATA_WKR;Wkr;TIPO;DATA_HDD."
        )

    record: list[dict[str, Any]] = []
    errori: list[dict[str, str]] = []
    for numero_riga, riga in enumerate(righe[1:], start=2):
        celle = [c.strip().strip('"') for c in riga.split(separatore)]
        if len(celle) < len(INTESTAZIONE_ATTESA):
            errori.append({"field": f"riga {numero_riga}", "message": "colonne mancanti"})
            continue
        zona_testo, giorno_testo, data_wkr, wkr_testo, tipo, data_hdd = celle[:6]
        if not zona_testo.isdigit():
            errori.append({"field": f"riga {numero_riga}", "message": f"zona non numerica: «{zona_testo[:20]}»"})
            continue
        giorno = _data_da_aaaammgg(giorno_testo)
        if giorno is None:
            errori.append({"field": f"riga {numero_riga}", "message": f"giorno non riconosciuto: «{giorno_testo[:20]}»"})
            continue
        wkr = _wkr_da_testo(wkr_testo)
        if wkr is None:
            errori.append({"field": f"riga {numero_riga}", "message": f"Wkr non numerico: «{wkr_testo[:20]}»"})
            continue
        if not (WKR_MIN <= wkr <= WKR_MAX):
            errori.append({"field": f"riga {numero_riga}", "message": f"Wkr fuori dall'intervallo plausibile ({WKR_MIN}–{WKR_MAX}): {wkr}"})
            continue
        if tipo not in TIPI_AMMESSI:
            errori.append({"field": f"riga {numero_riga}", "message": f"tipo non previsto: «{tipo[:10]}»"})
            continue
        record.append({
            "zona": zona_testo, "giorno": giorno, "wkr": wkr, "tipo": tipo,
            "data_wkr": data_wkr, "data_hdd": data_hdd,
        })
        if len(errori) >= 20:
            break

    if errori and not record:
        raise WkrError("Nessuna riga del CSV è leggibile: correggi i punti segnalati.", errori[:20])
    if errori:
        raise WkrError(
            f"{len(errori)} righe del CSV non sono leggibili: correggile o rimuovile.", errori[:20]
        )
    if not record:
        raise WkrError("Il CSV non contiene righe di dati.")
    return record


def _verifica_griglia(record: list[dict[str, Any]]) -> None:
    """La griglia deve essere rettangolare e coerente, altrimenti il dato è inaffidabile."""

    zone = sorted({r["zona"] for r in record}, key=int)
    giorni = sorted({r["giorno"] for r in record})
    attesi = len(zone) * len(giorni)
    if len(record) != attesi:
        raise WkrError(
            "La griglia zona × giorno non è completa: ogni zona deve avere lo stesso "
            "numero di giorni. Riscarica il CSV da Jarvis."
        )
    visti = {(r["zona"], r["giorno"]) for r in record}
    if len(visti) != len(record):
        raise WkrError("Il CSV contiene coppie zona/giorno duplicate: dato non affidabile.")
    # il tipo di un giorno è unico: tutte le zone condividono lo stesso tipo
    per_giorno: dict[date, set[str]] = {}
    for r in record:
        per_giorno.setdefault(r["giorno"], set()).add(r["tipo"])
    for giorno, tipi in per_giorno.items():
        if len(tipi) > 1:
            raise WkrError(
                f"Il giorno {giorno.strftime('%d/%m/%Y')} compare con più tipi "
                f"({'/'.join(sorted(tipi))}): dato non coerente."
            )


# ------------------------------------------------------------------ sistema


def sistema(dati: dict[str, Any]) -> dict[str, Any]:
    """Dal CSV (incollato o scaricato live) alla tabella dei coefficienti."""

    if not isinstance(dati, dict):
        raise WkrError("Dati non validi: atteso un oggetto.")

    fonte_file = "CSV incollato"
    aggiornato_il = None
    if dati.get("scarica"):
        anno_grezzo = str(dati.get("anno") or "").strip()
        anno = int(anno_grezzo) if anno_grezzo.isdigit() else datetime.now().year
        testo_csv, meta = scarica_da_jarvis(anno)
        fonte_file = meta["file"]
        aggiornato_il = meta.get("aggiornato_il")
        contenuto = testo_csv
    else:
        contenuto = str(dati.get("csv", ""))

    record = leggi_csv_wkr(contenuto)
    _verifica_griglia(record)

    zone = sorted({r["zona"] for r in record}, key=int)
    giorni = sorted({r["giorno"] for r in record})
    tipo_per_giorno = {r["giorno"]: r["tipo"] for r in record}

    valori: dict[tuple[str, date], float] = {(r["zona"], r["giorno"]): r["wkr"] for r in record}
    righe = [
        {"zona": zona, "valori": [valori[(zona, giorno)] for giorno in giorni]}
        for zona in zone
    ]

    giorni_out = [
        {
            "data": giorno.isoformat(),
            "tipo": tipo_per_giorno[giorno],
            "etichetta": ETICHETTE_TIPO.get(tipo_per_giorno[giorno], tipo_per_giorno[giorno]),
        }
        for giorno in giorni
    ]

    non_unitari = sum(1 for v in valori.values() if abs(v - 1.0) > 1e-9)
    avvisi: list[str] = []
    if non_unitari == 0:
        avvisi.append(
            "Tutti i coefficienti sono 1: in questo periodo Snam non applica "
            "correzioni climatiche (nessun riscaldamento in corso)."
        )

    data_wkr = max((r["data_wkr"] for r in record if r["data_wkr"]), default="")
    data_hdd = max((r["data_hdd"] for r in record if r["data_hdd"]), default="")
    anno = giorni[-1].year

    return {
        "fonte": {
            "pubblicazione": "Coefficienti WKR · Snam Rete Gas (Jarvis)",
            "url": JARVIS_PAGINA_URL.format(anno=anno),
            "file": fonte_file,
            "aggiornato_il": aggiornato_il,
            "data_wkr": data_wkr,
            "data_hdd": data_hdd,
        },
        "giorni": giorni_out,
        "zone": zone,
        "righe": righe,
        "non_unitari": non_unitari,
        "avvisi": avvisi,
        "nota": (
            "Il Wkr è il fattore di correzione climatica ufficiale pubblicato ogni giorno "
            "da Snam per ciascuna zona climatica. I dati sono mostrati a te che li hai "
            "richiesti e non vengono conservati né ritrasmessi. Fonte: Jarvis, pagina "
            "pubblica «Coefficienti WKR»."
        ),
    }


def fattori_per_zona(record: list[dict[str, Any]], zona: str) -> dict[date, float]:
    """Mappa data → Wkr per una zona: il gancio usato dal modulo Previsione."""

    return {r["giorno"]: r["wkr"] for r in record if r["zona"] == zona}


# ------------------------------------------------------- fetch live (Jarvis)


def _http_json(url: str, payload: Any = None, headers: dict[str, str] | None = None) -> Any:
    intestazioni = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        intestazioni.update(headers)
    corpo = json.dumps(payload).encode("utf-8") if payload is not None else None
    if corpo is not None:
        intestazioni["Content-Type"] = "application/json"
    richiesta = urllib.request.Request(url, data=corpo, headers=intestazioni)
    with urllib.request.urlopen(richiesta, timeout=TIMEOUT_SECONDS) as risposta:
        return json.loads(risposta.read().decode("utf-8"))


def _http_bytes(url: str, headers: dict[str, str] | None = None) -> bytes:
    intestazioni = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    if headers:
        intestazioni.update(headers)
    richiesta = urllib.request.Request(url, headers=intestazioni)
    with urllib.request.urlopen(richiesta, timeout=TIMEOUT_SECONDS) as risposta:
        return risposta.read()


def scarica_da_jarvis(anno: int) -> tuple[str, dict[str, str]]:
    """Scarica live il CSV dei coefficienti Wkr per l'anno dato.

    Legge la configurazione pubblica del sito Snam per ricavare user_key e
    indirizzo dell'API (così non sono hardcoded), chiede l'elenco delle
    pubblicazioni «Coefficienti WKR» e scarica il CSV più recente. Restituisce
    ``(testo_csv, metadati)``. Ogni fallimento diventa un ``WkrError`` con
    l'invito a incollare il CSV a mano: il portale non dipende dalla rete.
    """

    try:
        config = _http_json(JARVIS_CONFIG_URL)
        user_key = config["key_config"]["user_key"]
        base = config["ms_config"]["pubblicazioni_public"]
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError, OSError) as errore:
        raise WkrError(
            "Non riesco a raggiungere la configurazione pubblica di Jarvis. "
            "Scarica il CSV dalla pagina Snam e incollalo qui. "
            f"(dettaglio: {errore})"
        ) from errore

    intestazioni = {
        "X-jarvis-multiCompany": "SNM",
        "Accept": "application/jarvis.pubblicazioni_smart.v2+json",
        "Origin": "https://jarvis.snam.it",
        "Referer": "https://jarvis.snam.it/",
    }
    elenco_url = f"{base}/pubblicazioni/getPublications?user_key={urllib.parse.quote(user_key)}"
    payload = [
        {"tag": "tipologia", "value": TIPOLOGIA_WKR},
        {"tag": "anno_pubblicazione", "value": str(anno)},
    ]
    try:
        elenco = _http_json(elenco_url, payload=payload, headers=intestazioni)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as errore:
        raise WkrError(
            "La chiamata all'API pubblica di Jarvis non è andata a buon fine. "
            "Scarica il CSV dalla pagina Snam e incollalo qui. "
            f"(dettaglio: {errore})"
        ) from errore

    if not isinstance(elenco, list) or not elenco:
        raise WkrError(
            f"Jarvis non ha pubblicazioni «Coefficienti WKR» per l'anno {anno}. "
            "Controlla l'anno o incolla il CSV a mano."
        )

    # Scegli il CSV più recente (ore18 è l'aggiornamento serale, ore11 il
    # mattutino): fra i file CSV prendiamo quello con aggiornato_il massimo.
    candidati = [
        voce for voce in elenco
        if voce.get("formato_file") == "csv"
        and str(voce.get("nome_file_ITA", "")).startswith("CoefficientiWkr_ore")
    ]
    if not candidati:
        raise WkrError(
            "Nessun CSV dei coefficienti WKR trovato nella pubblicazione di Jarvis. "
            "Scarica il file dalla pagina Snam e incollalo qui."
        )
    prescelto = max(candidati, key=lambda v: str(v.get("aggiornato_il", "")))
    nome_file = f"{prescelto['nome_file_ITA']}.csv"
    download_id = next(
        (d["value"] for d in prescelto.get("download_id", []) if d.get("lang") == "ITA"),
        None,
    )
    if not download_id:
        raise WkrError("Il file scelto non ha un identificativo di download: riprova o incolla il CSV.")

    download_url = (
        f"{base}/document/get?d={urllib.parse.quote(download_id)}"
        f"&fileName={urllib.parse.quote(nome_file)}&user_key={urllib.parse.quote(user_key)}"
    )
    try:
        contenuto = _http_bytes(download_url, headers={"X-jarvis-multiCompany": "SNM"})
    except (urllib.error.URLError, TimeoutError, OSError) as errore:
        raise WkrError(
            "Il download del CSV da Jarvis non è riuscito. Scaricalo dalla pagina "
            f"Snam e incollalo qui. (dettaglio: {errore})"
        ) from errore

    return contenuto.decode("utf-8", errors="replace"), {
        "file": nome_file,
        "aggiornato_il": str(prescelto.get("aggiornato_il", "")),
    }
