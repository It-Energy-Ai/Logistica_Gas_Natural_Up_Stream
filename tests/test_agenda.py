"""Test del modulo Agenda regolatoria: modello, stati, contatori e API.

Le date del modello devono venire dalle fonti (Codice di Stoccaggio Stogit,
Codice di Rete Snam): i test fissano le date attese per un Anno Termico
noto, così un refuso nella tabella del modello non passa inosservato.
"""

from datetime import date

import pytest

from app import agenda


# ------------------------------------------------------------ modello puro


def test_modello_per_at_2026_ha_le_date_della_fonte():
    voci = {v["chiave"]: v for v in agenda.modello_per_at(2026)}
    # Fasi di iniezione/erogazione: definizioni del Codice di Stoccaggio.
    assert voci["stoccaggio.fase_iniezione_inizio"]["data"] == "2026-04-01"
    assert voci["stoccaggio.fase_iniezione_fine"]["data"] == "2026-10-31"
    assert voci["stoccaggio.fase_erogazione_inizio"]["data"] == "2026-11-01"
    assert voci["stoccaggio.fase_erogazione_fine"]["data"] == "2027-03-31"
    # §6.3.2: programma di erogazione entro il 23 ottobre, accettazione il 31.
    assert voci["stoccaggio.programma_erogazione"]["data"] == "2026-10-23"
    assert voci["stoccaggio.accettazione_erogazione"]["data"] == "2026-10-31"
    # §6.3.1: accettazione del programma di iniezione entro il 31 marzo.
    assert voci["stoccaggio.accettazione_iniezione"]["data"] == "2027-03-31"
    # Cap. 5: calendario di conferimento pubblicato entro il 1° febbraio.
    assert voci["stoccaggio.calendario_conferimento"]["data"] == "2026-02-01"
    # Cap. 7 Allegato 1: fatture di riaddebito.
    assert voci["stoccaggio.fattura_stogit_iniezione"]["data"] == "2027-03-31"
    assert voci["stoccaggio.fattura_stogit_erogazione"]["data"] == "2027-05-31"
    assert voci["stoccaggio.fattura_utente_iniezione"]["data"] == "2027-04-30"
    assert voci["stoccaggio.fattura_utente_erogazione"]["data"] == "2027-06-30"
    # Trasporto: Anno Termico dal 1° ottobre e nota UIOLI a 7 giorni lavorativi
    # dal 30 settembre (stessa regola del modulo Trasporto, già testata lì).
    assert voci["trasporto.anno_termico_avvio"]["data"] == "2026-10-01"
    assert voci["trasporto.uioli_nota"]["data"] == "2027-10-11"


def test_modello_date_modello_coerenti_con_voci():
    for voce in agenda.modello_per_at(2025):
        assert voce["data"] == agenda.date_modello(voce["chiave"], 2025).isoformat()
        assert voce["categoria"] in agenda.CATEGORIE
        assert voce["riferimento"]


def test_modello_ignora_date_non_fissate_dalla_fonte():
    # Il modello contiene solo voci con data certa: non deve esistere una
    # voce ARERA o REMIT con una data inventata.
    chiavi = [v["chiave"] for v in agenda.modello_per_at(2026)]
    assert not any(c.startswith("regolatorio") for c in chiavi)
    assert not any(c.startswith("remit") for c in chiavi)


def test_prossima_occorrenza_mensile_chiude_il_giorno():
    assert agenda.prossima_occorrenza(date(2026, 1, 31), "mensile") == date(2026, 2, 28)
    assert agenda.prossima_occorrenza(date(2024, 1, 31), "mensile") == date(2024, 2, 29)


def test_prossima_occorrenza_annuale_sul_29_febbraio():
    assert agenda.prossima_occorrenza(date(2024, 2, 29), "annuale") == date(2025, 2, 28)


def test_prossima_occorrenza_trimestrale_e_giorno_gas():
    assert agenda.prossima_occorrenza(date(2026, 11, 30), "trimestrale") == date(2027, 2, 28)
    assert agenda.prossima_occorrenza(date(2026, 8, 19), "giorno_gas") == date(2026, 8, 20)


def test_stato_effettivo_deriva_la_scaduta():
    oggi = date(2026, 8, 19)
    riga_aperta = {"stato": "aperta", "data_scadenza": "2026-08-19"}
    riga_scaduta = {"stato": "aperta", "data_scadenza": "2026-08-18"}
    assert agenda.stato_effettivo(riga_aperta, oggi) == "aperta"
    assert agenda.stato_effettivo(riga_scaduta, oggi) == "scaduta"
    assert agenda.stato_effettivo({**riga_scaduta, "stato": "saltata"}, oggi) == "saltata"
    assert agenda.stato_effettivo({**riga_scaduta, "stato": "adempiuta"}, oggi) == "adempiuta"


def test_contatori_distinguono_scadenze_e_adempiute():
    oggi = date(2026, 8, 19)
    scadenze = [
        {"stato": "aperta", "data_scadenza": "2026-08-10"},   # scaduta
        {"stato": "aperta", "data_scadenza": "2026-08-19"},   # oggi
        {"stato": "aperta", "data_scadenza": "2026-08-25"},   # entro 7
        {"stato": "aperta", "data_scadenza": "2026-09-10"},   # entro 30
        {"stato": "aperta", "data_scadenza": "2026-10-01"},   # oltre 30
        {"stato": "adempiuta", "data_scadenza": "2026-08-05"},  # nel mese
        {"stato": "saltata", "data_scadenza": "2026-08-01"},    # non conta
    ]
    assert agenda.contatori(scadenze, oggi) == {
        "oggi": 2, "sette": 3, "trenta": 4, "scadute": 1, "adempiute_mese": 1,
    }


# ------------------------------------------------------------- validazione


def test_prepara_scadenza_minima():
    record = agenda.prepara_scadenza({
        "titolo": "Raccolta dati ARERA",
        "categoria": "regolatorio",
        "data_scadenza": "2026-09-04",
    })
    assert record["stato"] == "aperta"
    assert record["ricorrenza"] == "una_tantum"
    assert record["riferimento"] == ""


def test_prepara_scadenza_respinge_stato_scaduta():
    with pytest.raises(agenda.AgendaError) as exc:
        agenda.prepara_scadenza({
            "titolo": "X",
            "categoria": "personale",
            "data_scadenza": "2026-09-04",
            "stato": "scaduta",
        })
    assert any(e["field"] == "stato" for e in exc.value.errors)


def test_prepara_scadenza_valida_campi():
    with pytest.raises(agenda.AgendaError) as exc:
        agenda.prepara_scadenza({"titolo": "A", "categoria": "sconosciuta"})
    campi = {e["field"] for e in exc.value.errors}
    assert "titolo" in campi and "categoria" in campi and "data_scadenza" in campi


def test_istanzia_modello_idempotente_per_voce():
    esito = agenda.istanzia_modello(2026, [])
    assert len(esito["da_creare"]) == len(agenda.modello_per_at(2026))
    assert esito["gia_presenti"] == []
    # Dopo l'istanziazione tutte le voci risultano presenti: niente duplicati.
    istanziate = [
        {"modello_chiave": v["chiave"], "modello_anno": 2026}
        for v in esito["da_creare"]
    ]
    with pytest.raises(agenda.AgendaError) as exc:
        agenda.istanzia_modello(2026, istanziate)
    assert exc.value.errors[0]["field"] == "anno"


def test_istanzia_modello_anno_fuori_intervallo():
    with pytest.raises(agenda.AgendaError):
        agenda.istanzia_modello(1999, [])


# ------------------------------------------------------------------- API


def test_agenda_richiede_sessione(client):
    assert client.get("/api/agenda").status_code == 401
    assert client.get("/api/agenda/catalogo").status_code == 401


def test_agenda_catalogo_fonti_e_modello(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    r = client.get("/api/agenda/catalogo")
    assert r.status_code == 200
    dati = r.json()
    assert set(dati["categorie"]) >= {
        "trasporto", "stoccaggio", "regolatorio", "remit", "operativo", "personale",
    }
    assert "fonti" in dati and "stoccaggio" in dati["fonti"]
    assert dati["modello_corrente"][0]["chiave"] == "stoccaggio.fase_iniezione_inizio"
    assert dati["anno_termico_corrente"] < dati["anno_termico_successivo"]


def test_agenda_ciclo_vita_scadenza_personalizzata(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    r = client.post("/api/agenda/scadenze", json={
        "titolo": "Verifica consultazione ARERA",
        "categoria": "regolatorio",
        "data_scadenza": "2026-09-25",
        "ricorrenza": "una_tantum",
        "riferimento": "Consultazione 267/2026/R/gas",
    })
    assert r.status_code == 201
    scadenza = r.json()
    assert scadenza["id"] and scadenza["stato"] == "aperta"

    r = client.get("/api/agenda")
    assert r.status_code == 200
    assert len(r.json()["scadenze"]) == 1
    assert r.json()["scadenze"][0]["stato_effettivo"] == "aperta"

    r = client.patch(f"/api/agenda/scadenze/{scadenza['id']}", json={"stato": "adempiuta"})
    assert r.status_code == 200
    r = client.get("/api/agenda")
    assert r.json()["scadenze"][0]["stato_effettivo"] == "adempiuta"

    r = client.delete(f"/api/agenda/scadenze/{scadenza['id']}")
    assert r.status_code == 200
    assert client.get("/api/agenda").json()["scadenze"] == []


def test_agenda_adempimento_ricorrente_genera_occorrenza(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    r = client.post("/api/agenda/scadenze", json={
        "titolo": "Controllo registro REMIT",
        "categoria": "remit",
        "data_scadenza": "2026-09-01",
        "ricorrenza": "mensile",
    })
    scadenza = r.json()
    client.patch(f"/api/agenda/scadenze/{scadenza['id']}", json={"stato": "adempiuta"})
    elenco = client.get("/api/agenda").json()["scadenze"]
    assert len(elenco) == 2
    assert {s["stato_effettivo"] for s in elenco} == {"adempiuta", "aperta"}
    prossima = next(s for s in elenco if s["stato_effettivo"] == "aperta")
    assert prossima["data_scadenza"] == "2026-10-01"


def test_agenda_validazione_422(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    r = client.post("/api/agenda/scadenze", json={"titolo": "X"})
    assert r.status_code == 422
    assert any(e["field"] == "data_scadenza" for e in r.json()["errors"])


def test_agenda_istanzia_modello_e_non_duplica(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    r = client.post("/api/agenda/modello/istanzia", json={"anno": 2026})
    assert r.status_code == 200
    assert r.json()["create"] == len(agenda.modello_per_at(2026))
    r = client.post("/api/agenda/modello/istanzia", json={"anno": 2026})
    assert r.status_code == 409
    elenco = client.get("/api/agenda").json()["scadenze"]
    assert len(elenco) == len(agenda.modello_per_at(2026))
    assert all(s["modello_chiave"] for s in elenco)
    assert all(s["ricorrenza"] == "annuale" for s in elenco)


def test_agenda_istanzia_anno_non_valido(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    r = client.post("/api/agenda/modello/istanzia", json={"anno": "boh"})
    assert r.status_code == 422


def test_agenda_modello_istanziato_con_date_della_fonte(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    client.post("/api/agenda/modello/istanzia", json={"anno": 2026})
    elenco = client.get("/api/agenda").json()["scadenze"]
    attese = {
        "stoccaggio.fase_iniezione_inizio": "2026-04-01",
        "stoccaggio.programma_erogazione": "2026-10-23",
        "trasporto.uioli_nota": "2027-10-11",
    }
    per_chiave = {s["modello_chiave"]: s for s in elenco}
    for chiave, attesa in attese.items():
        assert per_chiave[chiave]["data_scadenza"] == attesa


def test_agenda_duplicato_modello_409_da_post_diretto(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    corpo = {
        "titolo": "Avvio fase di iniezione",
        "categoria": "stoccaggio",
        "data_scadenza": "2026-04-01",
        "modello_chiave": "stoccaggio.fase_iniezione_inizio",
        "modello_anno": 2026,
    }
    assert client.post("/api/agenda/scadenze", json=corpo).status_code == 201
    assert client.post("/api/agenda/scadenze", json=corpo).status_code == 409


def test_agenda_patch_inesistente_404(client):
    client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    assert client.patch("/api/agenda/scadenze/inesistente", json={"stato": "adempiuta"}).status_code == 404
    assert client.delete("/api/agenda/scadenze/inesistente").status_code == 404
