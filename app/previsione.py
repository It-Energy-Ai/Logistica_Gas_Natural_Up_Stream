"""Previsione della domanda per preparare le nomine, con il metodo dichiarato.

Uno shipper prevede per nominare: la domanda attesa dei prossimi giorni è la
base del NOMINT. Questo modulo prende lo storico giornaliero incollato come
CSV e produce tre cose, tutte verificabili:

* un **backtest** sugli ultimi giorni noti — il modello viene addestrato
  senza vederli e confrontato con la realtà (MAE, RMSE, MAPE): è la misura
  onesta di quanto fidarsi;
* la **previsione del futuro vero** — giorni successivi all'ultimo dato,
  mai una riproiezione del passato spacciata per previsione;
* una **banda indicativa** costruita sui residui del modello (quantili
  empirici 10°-90°, allargati con la radice dell'orizzonte), dichiarata per
  quello che è: non un intervallo di confidenza parametrico.

Il metodo è Holt-Winters additivo con stagionalità settimanale, implementato
qui in puro Python: niente pandas, statsmodels o pmdarima nei requisiti — il
portale viaggia anche come eseguibile e cento megabyte di dipendenze
scientifiche non valgono un'etichetta più altisonante sullo stesso lavoro.
I tre coefficienti di lisciamento si scelgono con una piccola griglia
deterministica minimizzando l'errore a un passo: stesso input, stessa
previsione, sempre.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

PERIODO = 7  # stagionalità settimanale: il gas civile respira sui giorni della settimana

MIN_GIORNI_ADDESTRAMENTO = 28   # quattro settimane piene per stimare la stagionalità
ORIZZONTI = (7, 14, 28)
MAX_RIGHE = 20_000
MAX_CORPO_BYTES = 1024 * 1024
MAX_BUCO_GIORNI = 7

AGGREGAZIONI = {
    "somma": "Somma dei valori dello stesso giorno",
    "media": "Media dei valori dello stesso giorno",
}

COLONNE_DATA = {"data", "date", "giorno", "giorno_gas", "timestamp"}
COLONNE_VALORE = {"valore", "value", "domanda", "volume", "quantita", "quantità", "kwh", "smc"}

NUMERO_RE = re.compile(r"^-?[0-9]{1,15}([.,][0-9]{1,6})?$")


class PrevisioneError(ValueError):
    """Errore strutturato, traducibile in una risposta HTTP controllata."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.errors = errors or []


# ------------------------------------------------------------- lettura CSV


def _data_da_testo(testo: str) -> date | None:
    testo = testo.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", testo)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", testo)
    if m:
        # gg/mm/aaaa: il formato italiano. Un 03/04 americano qui sarebbe il
        # 3 aprile, e va bene così: il portale parla italiano.
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _numero_da_testo(testo: str) -> float | None:
    testo = testo.strip().replace(" ", "").replace(" ", "")
    if not testo:
        return None
    # 1.234.567,89 → 1234567.89 ; 1234.5 resta un decimale
    if "," in testo and "." in testo:
        if testo.rfind(",") > testo.rfind("."):
            testo = testo.replace(".", "").replace(",", ".")
        else:
            testo = testo.replace(",", "")
    elif "," in testo:
        testo = testo.replace(",", ".")
    elif re.fullmatch(r"-?[1-9][0-9]{0,2}(\.[0-9]{3})+", testo):
        testo = testo.replace(".", "")
    # Un valore che non rispetta il formato numerico atteso non deve entrare:
    # il fallback float() accetterebbe "nan", "inf" e la notazione esponenziale
    # ("1e5"), che non sono domande valide e produrrebbero un output JSON non
    # valido (NaN/Infinity non sono JSON). Meglio segnalare la riga.
    if not NUMERO_RE.fullmatch(testo):
        return None
    try:
        return float(testo)
    except ValueError:
        return None


def leggi_csv(contenuto: str) -> tuple[list[tuple[date, float]], list[str]]:
    """Interpreta il CSV incollato: separatore, intestazione e formati italiani.

    Restituisce le coppie (giorno, valore) così come lette — l'aggregazione
    dei duplicati e il riempimento dei buchi sono passi successivi e
    dichiarati, non magia del parser.
    """

    if not isinstance(contenuto, str) or not contenuto.strip():
        raise PrevisioneError("Incolla il CSV dello storico: una riga per giorno, data e valore.")

    righe = [r for r in contenuto.splitlines() if r.strip()]
    if len(righe) > MAX_RIGHE:
        raise PrevisioneError(f"Troppe righe: il limite è {MAX_RIGHE:,}.".replace(",", "."))

    # separatore: quello che produce più colonne sulla prima riga utile
    separatore = max((";", ",", "\t"), key=lambda s: righe[0].count(s))
    if righe[0].count(separatore) == 0:
        raise PrevisioneError(
            "Nel CSV manca un separatore riconoscibile: usa punto e virgola, virgola o tabulazione "
            "fra la data e il valore."
        )

    celle = [c.strip().strip('"') for c in righe[0].split(separatore)]
    indice_data, indice_valore = 0, 1
    inizio = 0
    intestazione = [c.lower() for c in celle]
    if any(c in COLONNE_DATA for c in intestazione) or any(c in COLONNE_VALORE for c in intestazione):
        indice_data = next((i for i, c in enumerate(intestazione) if c in COLONNE_DATA), 0)
        indice_valore = next((i for i, c in enumerate(intestazione) if c in COLONNE_VALORE), 1)
        inizio = 1
    elif _data_da_testo(celle[0]) is None:
        # prima riga né intestazione nota né data: è un'intestazione qualsiasi
        inizio = 1

    punti: list[tuple[date, float]] = []
    errori: list[dict[str, str]] = []
    for numero_riga, riga in enumerate(righe[inizio:], start=inizio + 1):
        celle = [c.strip().strip('"') for c in riga.split(separatore)]
        if len(celle) <= max(indice_data, indice_valore):
            errori.append({"field": f"riga {numero_riga}", "message": "colonne mancanti"})
            continue
        giorno = _data_da_testo(celle[indice_data])
        valore = _numero_da_testo(celle[indice_valore])
        if giorno is None:
            errori.append({"field": f"riga {numero_riga}", "message": f"data non riconosciuta: «{celle[indice_data][:30]}»"})
            continue
        if valore is None:
            errori.append({"field": f"riga {numero_riga}", "message": f"valore non numerico: «{celle[indice_valore][:30]}»"})
            continue
        if valore < 0:
            errori.append({"field": f"riga {numero_riga}", "message": "valore negativo: la domanda non può esserlo"})
            continue
        punti.append((giorno, valore))
        if len(errori) >= 20:
            break

    if errori and not punti:
        raise PrevisioneError("Nessuna riga del CSV è leggibile: correggi i punti segnalati.", errori[:20])
    if errori:
        raise PrevisioneError(
            f"{len(errori)} righe del CSV non sono leggibili: correggile o rimuovile.", errori[:20]
        )
    if not punti:
        raise PrevisioneError("Il CSV non contiene righe di dati.")
    return punti, []


def serie_giornaliera(
    punti: list[tuple[date, float]], aggregazione: str
) -> tuple[date, list[float], list[str]]:
    """Costruisce il continuo giornaliero: duplicati aggregati, buchi colmati.

    Ogni intervento sui dati viene contato e riferito: chi legge la
    previsione deve sapere quanta parte dello storico è stata ricostruita.
    """

    if aggregazione not in AGGREGAZIONI:
        raise PrevisioneError(f"Aggregazione non prevista: ammesse {', '.join(AGGREGAZIONI)}.")

    per_giorno: dict[date, list[float]] = {}
    for giorno, valore in punti:
        per_giorno.setdefault(giorno, []).append(valore)

    giorni = sorted(per_giorno)
    duplicati = sum(1 for v in per_giorno.values() if len(v) > 1)
    avvisi: list[str] = []
    if duplicati:
        avvisi.append(
            f"{duplicati} giorni comparivano più volte: valori uniti con «{aggregazione}»."
        )

    inizio, fine = giorni[0], giorni[-1]
    valori: list[float] = []
    buchi = 0
    buco_massimo = 0
    corrente = inizio
    ultimo_noto: float | None = None
    in_attesa: int = 0
    while corrente <= fine:
        if corrente in per_giorno:
            grezzi = per_giorno[corrente]
            valore = sum(grezzi) if aggregazione == "somma" else sum(grezzi) / len(grezzi)
            if in_attesa and ultimo_noto is not None:
                # interpolazione lineare sui giorni mancanti appena chiusi
                passo = (valore - ultimo_noto) / (in_attesa + 1)
                for k in range(in_attesa):
                    valori.append(ultimo_noto + passo * (k + 1))
                buchi += in_attesa
                buco_massimo = max(buco_massimo, in_attesa)
                in_attesa = 0
            valori.append(valore)
            ultimo_noto = valore
        else:
            in_attesa += 1
            if in_attesa > MAX_BUCO_GIORNI:
                raise PrevisioneError(
                    f"Lo storico ha un buco di oltre {MAX_BUCO_GIORNI} giorni consecutivi "
                    f"(intorno al {corrente.strftime('%d/%m/%Y')}): con un vuoto così largo "
                    "l'interpolazione inventerebbe una settimana intera. Completa i dati."
                )
        corrente += timedelta(days=1)

    if buchi:
        avvisi.append(
            f"{buchi} giorni mancanti colmati per interpolazione lineare "
            f"(il buco più largo: {buco_massimo} giorni)."
        )
    return inizio, valori, avvisi


# ----------------------------------------------------- Holt-Winters additivo


def _adatta_holt_winters(valori: list[float], alfa: float, beta: float, gamma: float):
    """Una passata del lisciamento; restituisce stato finale e residui a un passo."""

    settimane = len(valori) // PERIODO
    medie = [sum(valori[i * PERIODO:(i + 1) * PERIODO]) / PERIODO for i in range(settimane)]
    livello = medie[0]
    trend = (medie[-1] - medie[0]) / ((settimane - 1) * PERIODO) if settimane > 1 else 0.0
    stagione = [0.0] * PERIODO
    for j in range(PERIODO):
        scarti = [valori[i * PERIODO + j] - medie[i] for i in range(settimane)]
        stagione[j] = sum(scarti) / len(scarti)

    residui: list[float] = []
    for t, osservato in enumerate(valori):
        indice = t % PERIODO
        previsto = livello + trend + stagione[indice]
        if t >= PERIODO:
            residui.append(osservato - previsto)
        stag_vecchia = stagione[indice]
        livello_nuovo = alfa * (osservato - stag_vecchia) + (1 - alfa) * (livello + trend)
        trend = beta * (livello_nuovo - livello) + (1 - beta) * trend
        stagione[indice] = gamma * (osservato - livello_nuovo) + (1 - gamma) * stag_vecchia
        livello = livello_nuovo
    return livello, trend, stagione, residui


def _previsione_da_stato(livello: float, trend: float, stagione: list[float],
                         partenza: int, orizzonte: int) -> list[float]:
    return [
        livello + (h + 1) * trend + stagione[(partenza + h) % PERIODO]
        for h in range(orizzonte)
    ]


GRIGLIA_ALFA = (0.1, 0.3, 0.5, 0.7, 0.9)
GRIGLIA_BETA = (0.01, 0.05, 0.15)
GRIGLIA_GAMMA = (0.05, 0.15, 0.35)


def _migliori_coefficienti(valori: list[float]) -> tuple[float, float, float]:
    """Griglia deterministica: vince l'errore quadratico a un passo più basso."""

    migliore = None
    scelta = (GRIGLIA_ALFA[1], GRIGLIA_BETA[1], GRIGLIA_GAMMA[1])
    for alfa in GRIGLIA_ALFA:
        for beta in GRIGLIA_BETA:
            for gamma in GRIGLIA_GAMMA:
                _, _, _, residui = _adatta_holt_winters(valori, alfa, beta, gamma)
                errore = sum(r * r for r in residui)
                if migliore is None or errore < migliore - 1e-9:
                    migliore = errore
                    scelta = (alfa, beta, gamma)
    return scelta


def _quantile(ordinati: list[float], q: float) -> float:
    if not ordinati:
        return 0.0
    posizione = q * (len(ordinati) - 1)
    basso = int(math.floor(posizione))
    alto = min(basso + 1, len(ordinati) - 1)
    peso = posizione - basso
    return ordinati[basso] * (1 - peso) + ordinati[alto] * peso


# ------------------------------------------------------------------ calcolo


def prevedi(dati: dict[str, Any]) -> dict[str, Any]:
    """Dal CSV incollato alla previsione: backtest, futuro vero, banda dichiarata."""

    if not isinstance(dati, dict):
        raise PrevisioneError("Dati non validi: atteso un oggetto.")

    orizzonte_grezzo = str(dati.get("orizzonte", "7")).strip()
    if not orizzonte_grezzo.isdigit() or int(orizzonte_grezzo) not in ORIZZONTI:
        raise PrevisioneError(
            f"Orizzonte non previsto: ammessi {', '.join(str(o) for o in ORIZZONTI)} giorni."
        )
    orizzonte = int(orizzonte_grezzo)
    aggregazione = str(dati.get("aggregazione", "somma")).strip() or "somma"

    punti, _ = leggi_csv(str(dati.get("csv", "")))
    inizio, valori, avvisi = serie_giornaliera(punti, aggregazione)

    minimo = MIN_GIORNI_ADDESTRAMENTO + orizzonte
    if len(valori) < minimo:
        raise PrevisioneError(
            f"Servono almeno {minimo} giorni di storico per un orizzonte di {orizzonte} "
            f"({MIN_GIORNI_ADDESTRAMENTO} di addestramento più i {orizzonte} del backtest): "
            f"nel CSV ce ne sono {len(valori)}."
        )

    ultimo_giorno = inizio + timedelta(days=len(valori) - 1)

    # ---- backtest: il modello non vede gli ultimi `orizzonte` giorni
    addestramento = valori[:-orizzonte]
    reali = valori[-orizzonte:]
    alfa, beta, gamma = _migliori_coefficienti(addestramento)
    livello, trend, stagione, _ = _adatta_holt_winters(addestramento, alfa, beta, gamma)
    stimati = _previsione_da_stato(livello, trend, stagione, len(addestramento), orizzonte)

    def _metriche(previsti: list[float]) -> dict[str, float | None]:
        scarti = [reale - stimato for reale, stimato in zip(reali, previsti)]
        mae = sum(abs(s) for s in scarti) / len(scarti)
        rmse = math.sqrt(sum(s * s for s in scarti) / len(scarti))
        non_nulli = [(reale, scarto) for reale, scarto in zip(reali, scarti) if abs(reale) > 1e-9]
        mape = (
            sum(abs(scarto / reale) for reale, scarto in non_nulli) / len(non_nulli) * 100
            if non_nulli else None
        )
        return {"mae": mae, "rmse": rmse, "mape": mape}

    del_modello = _metriche(stimati)
    # Il riferimento minimo di ogni previsione: l'ultima settimana osservata,
    # ripetuta. Un modello che non batte «stesso giorno della settimana
    # scorsa» non merita fiducia, e qui lo si dice invece di nasconderlo.
    ultima_settimana = addestramento[-PERIODO:]
    naive = [ultima_settimana[h % PERIODO] for h in range(orizzonte)]
    del_naive = _metriche(naive)
    if del_naive["mae"] > 1e-9:
        vantaggio = round((1 - del_modello["mae"] / del_naive["mae"]) * 100, 1)
    else:
        vantaggio = 0.0
    batte_il_naive = del_modello["mae"] <= del_naive["mae"] + 1e-9

    # ---- previsione vera: riaddestra su TUTTO e guarda oltre l'ultimo giorno
    alfa2, beta2, gamma2 = _migliori_coefficienti(valori)
    livello, trend, stagione, residui = _adatta_holt_winters(valori, alfa2, beta2, gamma2)
    futuri = _previsione_da_stato(livello, trend, stagione, len(valori), orizzonte)

    ordinati = sorted(residui)
    q10, q90 = _quantile(ordinati, 0.10), _quantile(ordinati, 0.90)

    previsione = []
    for h, valore in enumerate(futuri):
        giorno = ultimo_giorno + timedelta(days=h + 1)
        scala = math.sqrt(h + 1)
        previsione.append({
            "data": giorno.isoformat(),
            "valore": round(max(0.0, valore), 2),
            "minimo": round(max(0.0, valore + q10 * scala), 2),
            "massimo": round(max(0.0, valore + q90 * scala), 2),
        })

    storico_recente = [
        {"data": (inizio + timedelta(days=len(valori) - k)).isoformat(), "valore": round(valori[-k], 2)}
        for k in range(min(28, len(valori)), 0, -1)
    ]

    return {
        "metodo": (
            "Holt-Winters additivo con stagionalità settimanale, coefficienti scelti su griglia "
            f"deterministica (α={alfa2}, β={beta2}, γ={gamma2})"
        ),
        "aggregazione": aggregazione,
        "giorni_storico": len(valori),
        "dal": inizio.isoformat(),
        "al": ultimo_giorno.isoformat(),
        "backtest": {
            "giorni": orizzonte,
            "mae": round(del_modello["mae"], 2),
            "rmse": round(del_modello["rmse"], 2),
            "mape": round(del_modello["mape"], 1) if del_modello["mape"] is not None else None,
            "naive": {
                "mae": round(del_naive["mae"], 2),
                "rmse": round(del_naive["rmse"], 2),
                "mape": round(del_naive["mape"], 1) if del_naive["mape"] is not None else None,
            },
            "batte_il_naive": batte_il_naive,
            "vantaggio_percentuale": vantaggio,
        },
        "storico_recente": storico_recente,
        "previsione": previsione,
        "avvisi": avvisi,
        "nota": (
            "La banda è indicativa: quantili 10°-90° dei residui del modello, allargati con la "
            "radice dell'orizzonte. Il backtest dice quanto il metodo ha sbagliato sugli ultimi "
            "giorni noti: è quella la misura da guardare prima di fidarsi."
        ),
    }
