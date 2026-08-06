"""Previsione della domanda: parsing onesto, backtest vero, futuro vero."""

import math
import random
from datetime import date, timedelta

import pytest

from app import previsione


def csv_sintetico(giorni=112, *, sep=";", formato="it", rumore=20, seme=7, intestazione="data;valore"):
    random.seed(seme)
    inizio = date(2026, 4, 1)
    righe = [intestazione.replace(";", sep)] if intestazione else []
    for i in range(giorni):
        g = inizio + timedelta(days=i)
        v = 1000 + 2 * i + 180 * math.sin(2 * math.pi * (i % 7) / 7) + random.gauss(0, rumore)
        data = g.strftime("%d/%m/%Y") if formato == "it" else g.isoformat()
        valore = f"{v:.1f}".replace(".", ",") if formato == "it" else f"{v:.1f}"
        righe.append(f"{data}{sep}{valore}")
    return "\n".join(righe)


# ------------------------------------------------------------------ parsing

def test_formati_italiani_e_iso_danno_lo_stesso_risultato():
    it = previsione.prevedi({"csv": csv_sintetico(formato="it"), "orizzonte": 7})
    iso = previsione.prevedi({"csv": csv_sintetico(formato="iso"), "orizzonte": 7})
    assert it["previsione"] == iso["previsione"]
    assert it["backtest"] == iso["backtest"]


def test_separatori_diversi_sono_equivalenti():
    con_virgola = previsione.prevedi({"csv": csv_sintetico(sep=",", formato="iso"), "orizzonte": 7})
    con_tab = previsione.prevedi({"csv": csv_sintetico(sep="\t", formato="iso"), "orizzonte": 7})
    assert con_virgola["previsione"] == con_tab["previsione"]


def test_intestazione_riconosciuta_per_nome_anche_in_altro_ordine():
    base = csv_sintetico(intestazione="").splitlines()
    scambiate = ["volume;giorno"] + [";".join(reversed(r.split(";"))) for r in base]
    esito = previsione.prevedi({"csv": "\n".join(scambiate), "orizzonte": 7})
    assert esito["giorni_storico"] == 112


def test_le_righe_illeggibili_vengono_indicate_per_numero():
    rotte = csv_sintetico().splitlines()
    rotte[5] = "non-una-data;100"
    rotte[9] = "01/06/2026;cento"
    with pytest.raises(previsione.PrevisioneError) as errore:
        previsione.prevedi({"csv": "\n".join(rotte), "orizzonte": 7})
    campi = {e["field"] for e in errore.value.errors}
    assert campi == {"riga 6", "riga 10"}


def test_i_valori_negativi_sono_rifiutati():
    righe = csv_sintetico().splitlines()
    righe[3] = "03/04/2026;-50"
    with pytest.raises(previsione.PrevisioneError):
        previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7})


def test_csv_vuoto_o_senza_separatore():
    with pytest.raises(previsione.PrevisioneError):
        previsione.prevedi({"csv": "   ", "orizzonte": 7})
    with pytest.raises(previsione.PrevisioneError) as errore:
        previsione.prevedi({"csv": "solo testo\naltro testo", "orizzonte": 7})
    assert "separatore" in str(errore.value)


# --------------------------------------------------------- serie giornaliera

def test_i_buchi_vengono_colmati_e_dichiarati():
    righe = [r for i, r in enumerate(csv_sintetico(formato="iso").splitlines()) if i not in (40, 41, 42)]
    esito = previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7})
    assert any("3 giorni mancanti" in a for a in esito["avvisi"])
    assert esito["giorni_storico"] == 112  # il continuo resta intero


def test_un_buco_troppo_largo_ferma_il_calcolo():
    righe = [r for i, r in enumerate(csv_sintetico(formato="iso").splitlines()) if not 30 <= i <= 38]
    with pytest.raises(previsione.PrevisioneError) as errore:
        previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7})
    assert "buco" in str(errore.value)


def test_i_duplicati_si_aggregano_secondo_la_scelta():
    righe = csv_sintetico(formato="iso").splitlines() + ["2026-04-05;100"]
    somma = previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7, "aggregazione": "somma"})
    media = previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7, "aggregazione": "media"})
    assert any("uniti" in a for a in somma["avvisi"])
    assert somma["previsione"] != media["previsione"]


# ------------------------------------------------------------------ modello

def test_la_previsione_guarda_al_futuro_non_al_passato():
    """Il difetto della proposta respinta: qui il futuro è davvero futuro."""

    esito = previsione.prevedi({"csv": csv_sintetico(), "orizzonte": 7})
    assert esito["previsione"][0]["data"] > esito["al"]
    date_previste = [p["data"] for p in esito["previsione"]]
    assert date_previste == sorted(date_previste)
    assert len(date_previste) == 7


def test_su_una_serie_regolare_il_backtest_e_buono():
    esito = previsione.prevedi({"csv": csv_sintetico(rumore=10), "orizzonte": 7})
    assert esito["backtest"]["mape"] < 4


def test_la_stagionalita_settimanale_viene_imparata():
    """Su un profilo puramente settimanale la previsione ricalca il profilo."""

    inizio = date(2026, 4, 6)  # lunedì
    profilo = [100, 110, 120, 130, 120, 60, 40]  # feriali alti, weekend basso
    righe = ["data;valore"] + [
        f"{(inizio + timedelta(days=i)).isoformat()};{profilo[i % 7]}" for i in range(70)
    ]
    esito = previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7})
    previsti = [p["valore"] for p in esito["previsione"]]
    attesi = [profilo[(70 + h) % 7] for h in range(7)]
    for previsto, atteso in zip(previsti, attesi):
        assert abs(previsto - atteso) < 3, (previsti, attesi)


def test_la_banda_contiene_il_valore_e_si_allarga():
    esito = previsione.prevedi({"csv": csv_sintetico(), "orizzonte": 14})
    larghezze = []
    for punto in esito["previsione"]:
        assert punto["minimo"] <= punto["valore"] <= punto["massimo"]
        larghezze.append(punto["massimo"] - punto["minimo"])
    assert larghezze[-1] >= larghezze[0]


def test_stesso_input_stessa_previsione():
    a = previsione.prevedi({"csv": csv_sintetico(), "orizzonte": 28})
    b = previsione.prevedi({"csv": csv_sintetico(), "orizzonte": 28})
    assert a == b


def test_storico_troppo_corto_spiegato_con_i_numeri():
    with pytest.raises(previsione.PrevisioneError) as errore:
        previsione.prevedi({"csv": csv_sintetico(giorni=30), "orizzonte": 7})
    assert "almeno 35 giorni" in str(errore.value)


def test_orizzonte_fuori_lista():
    with pytest.raises(previsione.PrevisioneError):
        previsione.prevedi({"csv": csv_sintetico(), "orizzonte": 9})


def test_un_payload_non_oggetto_non_diventa_500():
    with pytest.raises(previsione.PrevisioneError):
        previsione.prevedi(["lista"])


# ------------------------------------------------------------ rotte HTTP

def test_la_rotta_richiede_una_sessione(client):
    assert client.post("/api/previsione", json={}).status_code == 401


def test_ciclo_completo_via_http(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post("/api/previsione", json={"csv": csv_sintetico(), "orizzonte": 7})
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["previsione"][0]["data"] > corpo["al"]
    assert "Holt-Winters" in corpo["metodo"]

    errore = client.post("/api/previsione", json={"csv": "a;b", "orizzonte": 7})
    assert errore.status_code == 422
    assert client.post("/api/previsione", json=["lista"]).status_code == 400


def test_un_corpo_enorme_viene_rifiutato_prima_di_leggerlo(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post(
        "/api/previsione",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(previsione.MAX_CORPO_BYTES + 1)},
    )
    assert risposta.status_code == 413


# --------------------------------------------- riferimento naive stagionale

def test_il_backtest_confronta_sempre_col_naive_stagionale():
    """Se il modello non batte «stessa settimana precedente», va detto."""

    esito = previsione.prevedi({"csv": csv_sintetico(), "orizzonte": 7})
    naive = esito["backtest"]["naive"]
    assert naive["mae"] > 0 and naive["rmse"] >= naive["mae"] * 0.9
    # con un trend nello storico il naive resta indietro: il modello vince
    assert esito["backtest"]["batte_il_naive"] is True
    assert esito["backtest"]["vantaggio_percentuale"] > 0


def test_il_naive_e_davvero_lultima_settimana_ripetuta():
    inizio = date(2026, 4, 6)
    profilo = [100, 110, 120, 130, 120, 60, 40]
    righe = ["data;valore"] + [
        f"{(inizio + timedelta(days=i)).isoformat()};{profilo[i % 7]}" for i in range(70)
    ]
    esito = previsione.prevedi({"csv": "\n".join(righe), "orizzonte": 7})
    # su un profilo perfettamente periodico il naive è perfetto: MAE 0 e
    # nessun vantaggio dichiarabile del modello
    assert esito["backtest"]["naive"]["mae"] == 0
    assert esito["backtest"]["vantaggio_percentuale"] == 0
