"""Coefficienti Wkr: parsing onesto del CSV di Jarvis, griglia verificata, fetch live."""

from datetime import date

import pytest

from app import wkr


def csv_wkr(zone=("11", "13", "24"), giorni=None, wkr_fn=None, sep=";"):
    """Costruisce un CSV Wkr nella forma pubblicata da Jarvis.

    ``wkr_fn(zona, giorno)`` permette di dare valori invernali (diversi da 1)
    per i test che li richiedono; di default è tutto 1 come in agosto.
    """

    if giorni is None:
        giorni = [
            ("20260819", "C"), ("20260820", "I"), ("20260821", "P"),
            ("20260822", "P2"), ("20260823", "P3"), ("20260824", "P4"),
            ("20260825", "P5"),
        ]
    righe = ["ZONA_CLIMATICA;GIORNO;DATA_WKR;Wkr;TIPO;DATA_HDD"]
    for zona in zone:
        for giorno, tipo in giorni:
            valore = wkr_fn(zona, giorno) if wkr_fn else 1
            righe.append(f"{zona};{giorno};20260820 10:41:06;{valore};{tipo};20260820 11:00:00")
    return "\n".join(righe).replace(";", sep) if sep != ";" else "\n".join(righe)


# ------------------------------------------------------------------ parsing

def test_un_csv_valido_dà_la_griglia_completa():
    esito = wkr.sistema({"csv": csv_wkr()})
    assert esito["zone"] == ["11", "13", "24"]
    assert len(esito["giorni"]) == 7
    assert len(esito["righe"]) == 3
    assert all(len(r["valori"]) == 7 for r in esito["righe"])


def test_i_tipi_dei_giorni_seguono_la_finestra_pubblicata():
    esito = wkr.sistema({"csv": csv_wkr()})
    tipi = [g["tipo"] for g in esito["giorni"]]
    assert tipi == ["C", "I", "P", "P2", "P3", "P4", "P5"]
    assert esito["giorni"][0]["etichetta"] == "Consuntivo (G−1)"
    assert esito["giorni"][1]["etichetta"] == "In corso (giorno gas)"
    assert esito["giorni"][2]["etichetta"] == "Provvisorio (G+1)"


def test_la_fonte_è_dichiarata():
    esito = wkr.sistema({"csv": csv_wkr()})
    assert "Snam" in esito["fonte"]["pubblicazione"]
    assert "jarvis.snam.it" in esito["fonte"]["url"]
    assert esito["fonte"]["file"] == "CSV incollato"
    assert esito["fonte"]["data_wkr"] == "20260820 10:41:06"


def test_tutti_uno_dà_l_avviso_nessuna_correzione():
    esito = wkr.sistema({"csv": csv_wkr()})
    assert esito["non_unitari"] == 0
    assert any("Tutti i coefficienti sono 1" in a for a in esito["avvisi"])


def test_valori_invernali_sono_contati_senza_avviso():
    def inverno(zona, giorno):
        return 1.25 if zona == "24" and giorno == "20260824" else 1
    esito = wkr.sistema({"csv": csv_wkr(wkr_fn=inverno)})
    assert esito["non_unitari"] == 1
    assert esito["avvisi"] == []
    riga_24 = next(r for r in esito["righe"] if r["zona"] == "24")
    assert riga_24["valori"][5] == 1.25


def test_fattori_per_zona_espone_la_mappa_data_valore():
    def inverno(zona, giorno):
        return 0.9 if zona == "11" else 1.1
    record = wkr.leggi_csv_wkr(csv_wkr(wkr_fn=inverno))
    mappa = wkr.fattori_per_zona(record, "11")
    assert mappa[date(2026, 8, 19)] == 0.9
    assert mappa[date(2026, 8, 25)] == 0.9
    assert len(mappa) == 7
    assert wkr.fattori_per_zona(record, "99") == {}


def test_stesso_input_stesso_output():
    a = wkr.sistema({"csv": csv_wkr()})
    b = wkr.sistema({"csv": csv_wkr()})
    assert a == b


# --------------------------------------------------------------- validazione

def test_intestazione_sbagliata_ferma_il_calcolo():
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "data;valore\n01/01/2026;1"})
    assert "intestazione" in str(errore.value).lower()


def test_righe_illeggibili_indicate_per_numero():
    righe = csv_wkr().splitlines()
    righe[2] = "undici;20260819;20260820 10:41:06;1;C;20260820 11:00:00"
    righe[9] = "13;2026-08-19;20260820 10:41:06;1;C;20260820 11:00:00"
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "\n".join(righe)})
    campi = {e["field"] for e in errore.value.errors}
    assert campi == {"riga 3", "riga 10"}


def test_wkr_non_numerico_o_fuori_intervallo():
    righe = csv_wkr().splitlines()
    righe[2] = "11;20260819;20260820 10:41:06;alto;C;20260820 11:00:00"
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "\n".join(righe)})
    assert "non numerico" in errore.value.errors[0]["message"]

    righe = csv_wkr().splitlines()
    righe[2] = "11;20260819;20260820 10:41:06;9.9;C;20260820 11:00:00"
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "\n".join(righe)})
    assert "intervallo" in errore.value.errors[0]["message"]


def test_tipo_non_previsto_è_rifiutato():
    righe = csv_wkr().splitlines()
    righe[2] = "11;20260819;20260820 10:41:06;1;X;20260820 11:00:00"
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "\n".join(righe)})
    assert "tipo" in errore.value.errors[0]["message"]


def test_griglia_incompleta_ferma_il_calcolo():
    righe = csv_wkr().splitlines()
    del righe[-1]  # manca l'ultimo giorno dell'ultima zona
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "\n".join(righe)})
    assert "griglia" in str(errore.value).lower()


def test_coppie_duplicate_sono_rifiutate():
    # duplica la prima riga dati e toglie l'ultima: la lunghezza totale resta
    # quella della griglia completa, ma la coppia duplicata la rende inaffidabile
    righe = csv_wkr().splitlines()
    righe.append(righe[1])
    del righe[-2]
    with pytest.raises(wkr.WkrError) as errore:
        wkr.sistema({"csv": "\n".join(righe)})
    assert "duplicate" in str(errore.value)


def test_csv_vuoto_o_non_stringa():
    with pytest.raises(wkr.WkrError):
        wkr.sistema({"csv": "   "})
    with pytest.raises(wkr.WkrError):
        wkr.sistema(["lista"])


# ------------------------------------------------------------- fetch live

def test_scarica_da_jarvis_usa_la_config_pubblica(monkeypatch):
    chiamate = {}

    def finto_json(url, payload=None, headers=None):
        if url == wkr.JARVIS_CONFIG_URL:
            chiamate["config"] = True
            return {"key_config": {"user_key": "k"}, "ms_config": {"pubblicazioni_public": "https://api"}}
        chiamate["elenco"] = (url, payload)
        return [{
            "nome_file_ITA": "CoefficientiWkr_ore18", "formato_file": "csv",
            "aggiornato_il": "20260820154546",
            "download_id": [{"lang": "ITA", "value": "JPUBB006/2026/CoefficientiWkr_ore18.csv"}],
        }, {
            "nome_file_ITA": "CoefficientiWkr_20260820_G-0-IT", "formato_file": "xlsx",
            "aggiornato_il": "20260820154533",
            "download_id": [{"lang": "ITA", "value": "JPUBB006/2026/x.xlsx"}],
        }]

    def finto_bytes(url, headers=None):
        chiamate["download"] = url
        return csv_wkr().encode("utf-8")

    monkeypatch.setattr(wkr, "_http_json", finto_json)
    monkeypatch.setattr(wkr, "_http_bytes", finto_bytes)

    testo, meta = wkr.scarica_da_jarvis(2026)
    assert chiamate["config"] is True
    assert "getPublications" in chiamate["elenco"][0]
    assert {"tag": "tipologia", "value": "Coefficienti WKR"} in chiamate["elenco"][1]
    assert "CoefficientiWkr_ore18.csv" in chiamate["download"]
    assert meta["file"] == "CoefficientiWkr_ore18.csv"
    assert "11;20260819" in testo


def test_scarica_da_jarvis_sceglie_il_csv_più_recente(monkeypatch):
    def finto_json(url, payload=None, headers=None):
        if url == wkr.JARVIS_CONFIG_URL:
            return {"key_config": {"user_key": "k"}, "ms_config": {"pubblicazioni_public": "https://api"}}
        return [
            {"nome_file_ITA": "CoefficientiWkr_ore11", "formato_file": "csv",
             "aggiornato_il": "20260820084545",
             "download_id": [{"lang": "ITA", "value": "d/ore11.csv"}]},
            {"nome_file_ITA": "CoefficientiWkr_ore18", "formato_file": "csv",
             "aggiornato_il": "20260820154546",
             "download_id": [{"lang": "ITA", "value": "d/ore18.csv"}]},
        ]

    scaricati = []

    def finto_bytes(url, headers=None):
        scaricati.append(url)
        return csv_wkr().encode("utf-8")

    monkeypatch.setattr(wkr, "_http_json", finto_json)
    monkeypatch.setattr(wkr, "_http_bytes", finto_bytes)
    _, meta = wkr.scarica_da_jarvis(2026)
    assert meta["file"] == "CoefficientiWkr_ore18.csv"
    assert "ore18.csv" in scaricati[0]


def test_scarica_da_jarvis_rete_giù_invita_a_incollare(monkeypatch):
    import urllib.error

    def finto_json(url, payload=None, headers=None):
        raise urllib.error.URLError("rete assente")

    monkeypatch.setattr(wkr, "_http_json", finto_json)
    with pytest.raises(wkr.WkrError) as errore:
        wkr.scarica_da_jarvis(2026)
    assert "incollalo" in str(errore.value)


def test_scarica_da_jarvis_annata_vuota(monkeypatch):
    def finto_json(url, payload=None, headers=None):
        if url == wkr.JARVIS_CONFIG_URL:
            return {"key_config": {"user_key": "k"}, "ms_config": {"pubblicazioni_public": "https://api"}}
        return []

    monkeypatch.setattr(wkr, "_http_json", finto_json)
    with pytest.raises(wkr.WkrError) as errore:
        wkr.scarica_da_jarvis(1999)
    assert "1999" in str(errore.value)


def test_sistema_con_scarica_live(monkeypatch):
    monkeypatch.setattr(wkr, "scarica_da_jarvis", lambda anno: (csv_wkr(), {"file": "CoefficientiWkr_ore18.csv", "aggiornato_il": "20260820154546"}))
    esito = wkr.sistema({"scarica": True, "anno": "2026"})
    assert esito["fonte"]["file"] == "CoefficientiWkr_ore18.csv"
    assert esito["fonte"]["aggiornato_il"] == "20260820154546"
    assert len(esito["righe"]) == 3


# ------------------------------------------------------------- rotte HTTP

def test_la_rotta_richiede_una_sessione(client):
    assert client.post("/api/wkr", json={}).status_code == 401


def test_ciclo_completo_via_http(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post("/api/wkr", json={"csv": csv_wkr()})
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["zone"] == ["11", "13", "24"]
    assert corpo["giorni"][0]["tipo"] == "C"

    errore = client.post("/api/wkr", json={"csv": "a;b"})
    assert errore.status_code == 422
    assert client.post("/api/wkr", json=["lista"]).status_code == 400


def test_un_corpo_enorme_viene_rifiutato_prima_di_leggerlo(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post(
        "/api/wkr",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(wkr.MAX_CORPO_BYTES + 1)},
    )
    assert risposta.status_code == 413
