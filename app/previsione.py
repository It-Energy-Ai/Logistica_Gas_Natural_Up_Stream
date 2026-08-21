"""Previsione della domanda per preparare le nomine, con il metodo dichiarato.

Uno shipper prevede per nominare: la domanda attesa dei prossimi giorni è la
base del NOMINT. Questo modulo prende lo storico giornaliero incollato come
CSV e produce tre cose, tutte verificabili:

* un **backtest a finestre scorrevoli** — l'ensemble viene addestrato più
  volte (quante lo storico consente, fino a quattro) senza vedere gli ultimi
  giorni di ogni finestra e confrontato con la realtà (MAE, RMSE, MAPE,
  MASE, sMAPE): è la misura onesta di quanto fidarsi;
* la **previsione del futuro vero** — giorni successivi all'ultimo dato,
  mai una riproiezione del passato spacciata per previsione;
* una **banda indicativa** costruita sui residui out-of-sample dell'ensemble
  (quantili empirici 10°-90° misurati sulle finestre del backtest, allargati
  con la radice dell'orizzonte), dichiarata per quello che è: non un
  intervallo di confidenza parametrico.

Il metodo è un **ensemble** dei tre approcci che le competizioni M3/M4 di
Makridakis indicano come i più affidabili sulle serie brevi con
stagionalità: Holt-Winters additivo con **trend smorzato** (il trend non
viene più estrapolato all'infinito), **metodo Theta** (Assimakopoulos &
Nikolopoulos 2000, il più accurato dell'M3) e **naive stagionale**
(l'ultima settimana ripetuta, il riferimento che ogni modello deve battere).
I pesi sono proporzionali all'inverso dell'errore di ciascun membro sul
backtest: stesso input, stessa previsione, sempre.

Tutto in puro Python: niente pandas, statsmodels o pmdarima nei requisiti —
il portale viaggia anche come eseguibile e cento megabyte di dipendenze
scientifiche non valgono un'etichetta più altisonante sullo stesso lavoro.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

PERIODO = 7  # stagionalità settimanale: il gas civile respira sui giorni della settimana

MIN_GIORNI_ADDESTRAMENTO = 28   # quattro settimane piene per stimare la stagionalità
ORIZZONTI = (7, 14, 28)
FINESTRE_BACKTEST = 4           # origini scorrevoli: più finestre, stima d'errore più robusta
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


# ----------------------------------------------------- Holt-Winters smorzato


def _adatta_holt_winters(valori: list[float], alfa: float, beta: float, gamma: float,
                         phi: float = 1.0):
    """Una passata del lisciamento; restituisce stato finale e residui a un passo.

    Con ``phi`` < 1 il trend è smorzato: invece di crescere linearmente
    all'infinito, il suo contributo si attenua passo dopo passo (ETS(A,Ad,A)
    nella tassonomia di Hyndman). Sulle serie brevi di domanda è la scelta
    prudente: il livello della domanda non tira dritto per mesi.
    """

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
        previsto = livello + phi * trend + stagione[indice]
        if t >= PERIODO:
            residui.append(osservato - previsto)
        stag_vecchia = stagione[indice]
        livello_nuovo = alfa * (osservato - stag_vecchia) + (1 - alfa) * (livello + phi * trend)
        trend = beta * (livello_nuovo - livello) + (1 - beta) * phi * trend
        stagione[indice] = gamma * (osservato - livello_nuovo) + (1 - gamma) * stag_vecchia
        livello = livello_nuovo
    return livello, trend, stagione, residui


def _previsione_da_stato(livello: float, trend: float, stagione: list[float],
                         partenza: int, orizzonte: int, phi: float = 1.0) -> list[float]:
    """Previsione h passi avanti con trend smorzato: somma φ+φ²+…+φʰ."""

    previsti = []
    smorzamento = 0.0
    for h in range(orizzonte):
        smorzamento += phi ** (h + 1)
        previsti.append(livello + smorzamento * trend + stagione[(partenza + h) % PERIODO])
    return previsti


GRIGLIA_ALFA = (0.1, 0.3, 0.5, 0.7, 0.9)
GRIGLIA_BETA = (0.01, 0.05, 0.15)
GRIGLIA_GAMMA = (0.05, 0.15, 0.35)
GRIGLIA_PHI = (0.80, 0.90, 0.98, 1.00)


def _migliori_coefficienti(valori: list[float]) -> tuple[float, float, float, float]:
    """Griglia deterministica: vince l'errore quadratico a un passo più basso."""

    migliore = None
    scelta = (GRIGLIA_ALFA[1], GRIGLIA_BETA[1], GRIGLIA_GAMMA[1], 1.0)
    for alfa in GRIGLIA_ALFA:
        for beta in GRIGLIA_BETA:
            for gamma in GRIGLIA_GAMMA:
                for phi in GRIGLIA_PHI:
                    _, _, _, residui = _adatta_holt_winters(valori, alfa, beta, gamma, phi)
                    errore = sum(r * r for r in residui)
                    if migliore is None or errore < migliore - 1e-9:
                        migliore = errore
                        scelta = (alfa, beta, gamma, phi)
    return scelta


def _holt_winters(valori: list[float], orizzonte: int) -> list[float]:
    alfa, beta, gamma, phi = _migliori_coefficienti(valori)
    livello, trend, stagione, _ = _adatta_holt_winters(valori, alfa, beta, gamma, phi)
    return _previsione_da_stato(livello, trend, stagione, len(valori), orizzonte, phi)


# ------------------------------------------------------------- metodo Theta


GRIGLIA_THETA = (0.1, 0.3, 0.5, 0.7, 0.9)


def _theta(valori: list[float], orizzonte: int) -> list[float]:
    """Metodo Theta (Assimakopoulos & Nikolopoulos 2000), il più accurato dell'M3.

    La serie «theta» con θ=2 raddoppia la curvatura locale; la pratica
    standard la scompone in una regressione lineare sul tempo — che cattura
    il trend — più un lisciamento esponenziale semplice, che cattura il
    livello. Il trend è stimato sulle medie settimanali, non sulla serie
    grezza: su un profilo settimanale asimmetrico la regressione grezza
    vedrebbe una pendenza spuria, mentre le medie di settimane intere la
    annullano. La stagionalità rientra come aggiustamento medio per giorno
    della settimana sugli scarti dal trend.
    """

    n = len(valori)
    settimane = n // PERIODO
    medie = [sum(valori[i * PERIODO:(i + 1) * PERIODO]) / PERIODO for i in range(settimane)]
    centri = [i * PERIODO + (PERIODO - 1) / 2 for i in range(settimane)]
    if settimane > 1:
        intercetta, pendenza = _ols_medie(medie, centri)
    else:
        intercetta, pendenza = medie[0], 0.0

    stagionali = [0.0] * PERIODO
    for j in range(PERIODO):
        scarti = [valori[t] - (intercetta + pendenza * t) for t in range(j, n, PERIODO)]
        stagionali[j] = sum(scarti) / len(scarti)

    residui = [valori[t] - stagionali[t % PERIODO] - (intercetta + pendenza * t) for t in range(n)]

    migliore, livello = None, residui[0]
    for alfa in GRIGLIA_THETA:
        liv, errore = residui[0], 0.0
        for t in range(1, n):
            errore += (residui[t] - liv) ** 2
            liv = alfa * residui[t] + (1 - alfa) * liv
        if migliore is None or errore < migliore - 1e-9:
            migliore, livello = errore, liv

    return [
        intercetta + pendenza * (n + h) + livello + stagionali[(n + h) % PERIODO]
        for h in range(orizzonte)
    ]


def _ols_medie(medie: list[float], centri: list[float]) -> tuple[float, float]:
    """Regressione lineare delle medie settimanali sul tempo: (intercetta, pendenza)."""

    n = len(medie)
    media_y = sum(medie) / n
    media_x = sum(centri) / n
    var_x = sum((x - media_x) ** 2 for x in centri)
    cov = sum((x - media_x) * (y - media_y) for x, y in zip(centri, medie))
    pendenza = cov / var_x if var_x > 0 else 0.0
    return media_y - pendenza * media_x, pendenza


# ------------------------------------------------------- naive stagionale


def _naive_stagionale(valori: list[float], orizzonte: int) -> list[float]:
    """L'ultima settimana osservata, ripetuta: il riferimento minimo di ogni
    previsione. Un modello che non la batte non merita fiducia — e qui,
    invece di nasconderlo, la si mette nell'ensemble e la si dichiara."""

    ultima = valori[-PERIODO:]
    return [ultima[h % PERIODO] for h in range(orizzonte)]


MEMBRI = (
    ("holt_winters", "Holt-Winters additivo con trend smorzato", _holt_winters),
    ("theta", "Metodo Theta", _theta),
    ("naive_stagionale", "Naive stagionale (ultima settimana ripetuta)", _naive_stagionale),
)


def _pesi_da_errori(errori: list[float]) -> list[float]:
    """Pesi proporzionali all'inverso dell'errore; i membri perfetti si
    dividono il peso in parti uguali, gli altri restano a zero."""

    inversi = [1.0 / e if e > 1e-9 else None for e in errori]
    if any(inv is None for inv in inversi):
        perfetti = [i for i, inv in enumerate(inversi) if inv is None]
        return [1.0 / len(perfetti) if i in perfetti else 0.0 for i in range(len(errori))]
    totale = sum(inversi)
    return [inv / totale for inv in inversi]


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
    """Dal CSV incollato alla previsione: backtest a finestre scorrevoli,
    futuro vero, banda sui residui out-of-sample dell'ensemble."""

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

    # ---- backtest a origini scorrevoli: tante finestre quante lo storico ne
    # consente (almeno una, al massimo FINESTRE_BACKTEST), così lo storico
    # minimo non cresce e chi ha più dati ottiene una stima d'errore più
    # robusta. Ogni membro non vede mai la coda di ogni finestra.
    finestre_disponibili = (len(valori) - MIN_GIORNI_ADDESTRAMENTO) // orizzonte
    finestre = max(1, min(FINESTRE_BACKTEST, finestre_disponibili))
    origini = [len(valori) - orizzonte * (finestre - k) for k in range(finestre)]
    per_finestra: list[tuple[list[list[float]], list[float]]] = []
    scarti_naive: list[float] = []
    for origine in origini:
        addestramento, reali = valori[:origine], valori[origine:origine + orizzonte]
        previsioni = [metodo(addestramento, orizzonte) for _, _, metodo in MEMBRI]
        per_finestra.append((previsioni, reali))
        for reale, stimato in zip(reali, _naive_stagionale(addestramento, orizzonte)):
            scarti_naive.append(reale - stimato)

    # I pesi nascono una volta sola, dalle medie degli errori out-of-sample
    # dei membri su tutte le finestre: gli stessi pesi valutano l'ensemble e
    # producono la previsione finale.
    mae_membri = [
        sum(
            sum(abs(r - s) for r, s in zip(reali, previsioni[i])) / len(reali)
            for previsioni, reali in per_finestra
        ) / len(per_finestra)
        for i in range(len(MEMBRI))
    ]
    pesi = _pesi_da_errori(mae_membri)

    scarti_ensemble: list[float] = []
    for previsioni, reali in per_finestra:
        for h, reale in enumerate(reali):
            stimato = sum(p * previsioni[i][h] for i, p in enumerate(pesi))
            scarti_ensemble.append(reale - stimato)

    def _metriche(scarti: list[float], reali_tutti: list[float]) -> dict[str, float | None]:
        n = len(scarti)
        mae = sum(abs(s) for s in scarti) / n
        rmse = math.sqrt(sum(s * s for s in scarti) / n)
        non_nulli = [(r, s) for r, s in zip(reali_tutti, scarti) if abs(r) > 1e-9]
        mape = (
            sum(abs(s / r) for r, s in non_nulli) / len(non_nulli) * 100
            if non_nulli else None
        )
        # MASE: l'errore diviso quello del naive a un passo sullo storico di
        # addestramento — insensibile alla scala, confrontabile fra serie.
        denominatori = []
        for origine in origini:
            addestramento = valori[:origine]
            passo = sum(
                abs(addestramento[t] - addestramento[t - PERIODO])
                for t in range(PERIODO, len(addestramento))
            ) / (len(addestramento) - PERIODO)
            denominatori.append(passo)
        mase = mae / (sum(denominatori) / len(denominatori)) if sum(denominatori) > 1e-9 else None
        smape = sum(
            2 * abs(s) / (abs(r) + abs(r - s))
            for r, s in zip(reali_tutti, scarti)
            if abs(r) + abs(r - s) > 1e-9
        ) / n * 100
        return {"mae": mae, "rmse": rmse, "mape": mape, "mase": mase, "smape": smape}

    reali_tutti = [valori[o + h] for o in origini for h in range(orizzonte)]
    del_ensemble = _metriche(scarti_ensemble, reali_tutti)
    del_naive = _metriche(scarti_naive, reali_tutti)
    if del_naive["mae"] > 1e-9:
        vantaggio = round((1 - del_ensemble["mae"] / del_naive["mae"]) * 100, 1)
    else:
        vantaggio = 0.0
    batte_il_naive = del_ensemble["mae"] <= del_naive["mae"] + 1e-9

    # ---- previsione vera: ciascun membro riaddestrato su TUTTO lo storico,
    # combinato con i pesi derivati dal backtest — non dai dati che i membri
    # hanno già visto, altrimenti i pesi sarebbero truccati.
    pesi = _pesi_da_errori(mae_membri)
    ensemble = [0.0] * orizzonte
    membri_finali = []
    for i, (nome, descrizione, metodo) in enumerate(MEMBRI):
        previsti = metodo(valori, orizzonte)
        for h in range(orizzonte):
            ensemble[h] += pesi[i] * previsti[h]
        membri_finali.append({
            "nome": nome, "descrizione": descrizione,
            "peso": round(pesi[i], 3), "mae_backtest": round(mae_membri[i], 2),
        })

    # Banda indicativa: quantili 10°-90° dei residui out-of-sample del
    # backtest (pooled su tutte le finestre), allargati con la radice
    # dell'orizzonte. Non è un intervallo di confidenza parametrico.
    ordinati = sorted(scarti_ensemble)
    q10, q90 = _quantile(ordinati, 0.10), _quantile(ordinati, 0.90)

    # ---- aggancio Wkr (facoltativo): il fattore di correzione climatica
    # ufficiale pubblicato da Snam, mostrato accanto a ciascun giorno previsto
    # e — solo se l'operatore lo chiede — applicato come semplice
    # moltiplicazione. Non è una stima del modello: è il fattore ufficiale.
    wkr_csv = dati.get("wkr_csv")
    wkr_zona = str(dati.get("wkr_zona") or "").strip()
    wkr_applica = bool(dati.get("wkr_applica"))
    fattori_wkr: dict[date, float] | None = None
    tipi_wkr: dict[date, str] = {}
    if isinstance(wkr_csv, str) and wkr_csv.strip():
        from . import wkr as _wkr

        try:
            record_wkr = _wkr.leggi_csv_wkr(wkr_csv)
            _wkr._verifica_griglia(record_wkr)
        except _wkr.WkrError as errore:
            raise PrevisioneError(f"CSV Wkr non leggibile: {errore}", errore.errors) from errore
        zone_disponibili = sorted({r["zona"] for r in record_wkr}, key=int)
        if not wkr_zona:
            raise PrevisioneError(
                "Indica la zona climatica del CSV Wkr da usare: disponibili "
                + ", ".join(zone_disponibili) + "."
            )
        if wkr_zona not in zone_disponibili:
            raise PrevisioneError(
                f"La zona climatica «{wkr_zona}» non è nel CSV Wkr: disponibili "
                + ", ".join(zone_disponibili) + "."
            )
        fattori_wkr = _wkr.fattori_per_zona(record_wkr, wkr_zona)
        tipi_wkr = {r["giorno"]: r["tipo"] for r in record_wkr if r["zona"] == wkr_zona}

    previsione = []
    giorni_coperti = 0
    for h, valore in enumerate(ensemble):
        giorno = ultimo_giorno + timedelta(days=h + 1)
        scala = math.sqrt(h + 1)
        punto = {
            "data": giorno.isoformat(),
            "valore": round(max(0.0, valore), 2),
            "minimo": round(max(0.0, valore + q10 * scala), 2),
            "massimo": round(max(0.0, valore + q90 * scala), 2),
        }
        if fattori_wkr is not None:
            fattore = fattori_wkr.get(giorno)
            punto["wkr"] = None if fattore is None else round(fattore, 6)
            punto["wkr_tipo"] = tipi_wkr.get(giorno)
            if fattore is not None:
                giorni_coperti += 1
                if wkr_applica:
                    punto["valore_modello"] = punto["valore"]
                    punto["valore"] = round(max(0.0, valore * fattore), 2)
                    punto["minimo"] = round(max(0.0, (valore + q10 * scala) * fattore), 2)
                    punto["massimo"] = round(max(0.0, (valore + q90 * scala) * fattore), 2)
                    punto["wkr_applicato"] = True
        previsione.append(punto)

    blocco_wkr = None
    if fattori_wkr is not None:
        giorni_scoperti = orizzonte - giorni_coperti
        if giorni_scoperti > 0:
            avvisi.append(
                f"{giorni_scoperti} giorni previsti su {orizzonte} non sono coperti dalla "
                "finestra Wkr pubblicata (che arriva a G+5): per quei giorni il fattore non "
                "è applicato."
            )
        blocco_wkr = {
            "zona": wkr_zona,
            "applica": wkr_applica,
            "giorni_coperti": giorni_coperti,
            "giorni_scoperti": giorni_scoperti,
            "nota": (
                "Il Wkr è il fattore di correzione climatica ufficiale pubblicato ogni giorno "
                "da Snam per la zona scelta. Se «applica» è attivo, i valori previsti sono "
                "moltiplicati per il fattore del giorno: è una semplice moltiplicazione per il "
                "fattore ufficiale, non una stima del modello. Verifica che la direzione "
                "(moltiplicare o dividere) corrisponda al tuo uso: la normalizzazione di "
                "settlement e la previsione della domanda sono operazioni diverse."
            ),
        }

    storico_recente = [
        {"data": (inizio + timedelta(days=len(valori) - k)).isoformat(), "valore": round(valori[-k], 2)}
        for k in range(min(28, len(valori)), 0, -1)
    ]

    return {
        "metodo": (
            "Ensemble pesato di Holt-Winters additivo con trend smorzato, metodo Theta e naive "
            "stagionale; pesi proporzionali all'inverso dell'errore di ciascun membro sul "
            f"backtest a {FINESTRE_BACKTEST} finestre scorrevoli"
        ),
        "aggregazione": aggregazione,
        "giorni_storico": len(valori),
        "dal": inizio.isoformat(),
        "al": ultimo_giorno.isoformat(),
        "backtest": {
            "giorni": orizzonte,
            "finestre": len(origini),
            "mae": round(del_ensemble["mae"], 2),
            "rmse": round(del_ensemble["rmse"], 2),
            "mape": round(del_ensemble["mape"], 1) if del_ensemble["mape"] is not None else None,
            "mase": round(del_ensemble["mase"], 2) if del_ensemble["mase"] is not None else None,
            "smape": round(del_ensemble["smape"], 1),
            "naive": {
                "mae": round(del_naive["mae"], 2),
                "rmse": round(del_naive["rmse"], 2),
                "mape": round(del_naive["mape"], 1) if del_naive["mape"] is not None else None,
                "mase": round(del_naive["mase"], 2) if del_naive["mase"] is not None else None,
                "smape": round(del_naive["smape"], 1),
            },
            "batte_il_naive": batte_il_naive,
            "vantaggio_percentuale": vantaggio,
        },
        "membri": membri_finali,
        "storico_recente": storico_recente,
        "previsione": previsione,
        "wkr": blocco_wkr,
        "avvisi": avvisi,
        "nota": (
            "La banda è indicativa: quantili 10°-90° dei residui dell'ensemble misurati fuori "
            "campione sulle finestre del backtest, allargati con la radice dell'orizzonte. Il "
            "backtest a finestre scorrevoli dice quanto il metodo ha sbagliato sugli ultimi "
            "giorni noti: è quella la misura da guardare prima di fidarsi."
        ),
    }
