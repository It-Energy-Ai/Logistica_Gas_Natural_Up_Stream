"""Trasporto lato shipper: registro interruzioni, Utilizzo Medio, nota UIOLI."""

from datetime import date, timedelta

import pytest

from app import trasporto


BASE = {
    "punto": "Tarvisio",
    "tipo": "totale",
    "data_inizio": "2026-01-10",
    "giorni": 3,
    "preavviso_ore": 48,
}


def interruzione(**extra):
    return trasporto.registra_interruzione({**BASE, **extra.pop("dati", {})}, extra.pop("esistenti", []))


# ------------------------------------------------------------- anno termico

def test_lanno_termico_inizia_il_primo_ottobre():
    assert trasporto.anno_termico(date(2026, 9, 30)) == 2025
    assert trasporto.anno_termico(date(2026, 10, 1)) == 2026
    assert trasporto.etichetta_anno_termico(2025) == "2025/2026"


def test_la_scadenza_della_nota_conta_sette_giorni_lavorativi():
    """L'AT 2025/2026 termina mercoledì 30/9/2026: 7 lavorativi → 9 ottobre.

    gio 1, ven 2, lun 5, mar 6, mer 7, gio 8, ven 9: il fine settimana non
    conta. Il calcolo ignora i festivi infrasettimanali, che potrebbero solo
    accorciare il termine reale: la data mostrata è quindi prudente.
    """

    assert trasporto.scadenza_nota(2025) == date(2026, 10, 9)
    # L'AT 2026/2027 termina giovedì 30/9/2027: ven 1, lun 4 … lun 11.
    assert trasporto.scadenza_nota(2026) == date(2027, 10, 11)


# ------------------------------------------------------------- interruzioni

def test_registrazione_di_base():
    record = interruzione()
    assert record["data_fine"] == "2026-01-12"
    assert record["anno_termico"] == 2025
    assert record["avvisi"] == []


def test_lintervallo_sotto_i_quattro_giorni_va_contestato():
    prima = interruzione()
    # Fine il 12/1: ricominciare il 15/1 lascia solo 2 giorni pieni.
    dopo = interruzione(dati={"data_inizio": "2026-01-15", "giorni": 2}, esistenti=[prima])
    assert len(dopo["avvisi"]) == 1
    assert "4 giorni" in dopo["avvisi"][0]
    assert "contestare" in dopo["avvisi"][0]


def test_lintervallo_vale_anche_alla_rovescia():
    """Registrare prima la seconda interruzione non deve nascondere il vizio."""

    seconda = interruzione(dati={"data_inizio": "2026-01-15", "giorni": 2})
    prima = interruzione(esistenti=[seconda])
    assert any("4 giorni" in a for a in prima["avvisi"])


def test_con_cinque_giorni_pieni_nessun_avviso():
    prima = interruzione()
    # Fine il 12/1: il 18/1 sono trascorsi 5 giorni pieni (13-17).
    dopo = interruzione(dati={"data_inizio": "2026-01-18", "giorni": 1}, esistenti=[prima])
    assert dopo["avvisi"] == []


def test_le_sovrapposizioni_vengono_respinte():
    prima = interruzione()
    with pytest.raises(trasporto.TrasportoError) as errore:
        interruzione(dati={"data_inizio": "2026-01-12", "giorni": 2}, esistenti=[prima])
    assert "sovrapposto" in str(errore.value.errors)


def test_punti_diversi_non_interferiscono():
    prima = interruzione()
    dopo = interruzione(dati={"punto": "Passo Gries", "data_inizio": "2026-01-11", "giorni": 1},
                        esistenti=[prima])
    assert dopo["avvisi"] == []


def test_il_cavallo_dellanno_termico_viene_segnalato():
    record = interruzione(dati={"data_inizio": "2026-09-29", "giorni": 4})
    assert any("Anno Termico" in a for a in record["avvisi"])


def test_la_parziale_richiede_la_capacita():
    with pytest.raises(trasporto.TrasportoError) as errore:
        interruzione(dati={"tipo": "parziale"})
    assert any(e["field"] == "capacita" for e in errore.value.errors)


def test_i_campi_malformati_tornano_come_errori_di_campo():
    with pytest.raises(trasporto.TrasportoError) as errore:
        trasporto.registra_interruzione(
            {"punto": "T", "tipo": "boh", "data_inizio": "ieri", "giorni": 0}, []
        )
    campi = {e["field"] for e in errore.value.errors}
    assert {"punto", "tipo", "data_inizio", "giorni"} <= campi


def test_un_payload_non_oggetto_non_diventa_500():
    with pytest.raises(trasporto.TrasportoError):
        trasporto.registra_interruzione(["lista"], [])


def test_il_riepilogo_somma_per_punto_e_anno_termico():
    righe = [
        interruzione(),
        interruzione(dati={"data_inizio": "2026-02-10", "giorni": 5}),
        interruzione(dati={"punto": "Gorizia", "data_inizio": "2026-01-10", "giorni": 1}),
    ]
    riepilogo = trasporto.riepilogo_interruzioni(righe)
    tarvisio = next(g for g in riepilogo if g["punto"] == "Tarvisio")
    assert tarvisio["interruzioni"] == 2
    assert tarvisio["giorni_totali"] == 8
    assert tarvisio["giorni_massimi_consecutivi"] == 5


# ------------------------------------------------------------ utilizzo medio

def test_utilizzo_medio_semplice():
    esito = trasporto.calcola_utilizzo_medio({
        "semestre": "invernale", "immessi": "45.000.000", "capacita_conferita": "60.000.000",
    })
    assert esito["percentuale"] == 75.0
    assert esito["sotto_soglia"] is True
    assert esito["denominatore"] == 60_000_000


def test_le_detrazioni_riducono_il_denominatore():
    """Ignorare le detrazioni del §4.3.1 farebbe sembrare a rischio chi non lo è."""

    senza = trasporto.calcola_utilizzo_medio({
        "semestre": "estivo", "immessi": "45.000.000", "capacita_conferita": "60.000.000",
    })
    con = trasporto.calcola_utilizzo_medio({
        "semestre": "estivo", "immessi": "45.000.000", "capacita_conferita": "60.000.000",
        "non_disponibile": "5.000.000", "messa_disposizione": "2.000.000",
    })
    assert senza["sotto_soglia"] is True
    assert con["denominatore"] == 53_000_000
    assert con["sotto_soglia"] is False  # 45/53 ≈ 84.9%


def test_il_denominatore_nullo_e_un_errore_spiegato():
    with pytest.raises(trasporto.TrasportoError) as errore:
        trasporto.calcola_utilizzo_medio({
            "semestre": "invernale", "immessi": "10", "capacita_conferita": "5",
            "messa_disposizione": "5",
        })
    assert "denominatore" in str(errore.value).lower()


def test_il_semestre_deve_essere_uno_dei_due():
    with pytest.raises(trasporto.TrasportoError) as errore:
        trasporto.calcola_utilizzo_medio({
            "semestre": "annuale", "immessi": "1", "capacita_conferita": "2",
        })
    assert any(e["field"] == "semestre" for e in errore.value.errors)


def test_la_forma_italiana_dei_numeri_non_cambia_il_valore():
    a = trasporto.calcola_utilizzo_medio({
        "semestre": "invernale", "immessi": "1.234.567", "capacita_conferita": "2.000.000",
    })
    b = trasporto.calcola_utilizzo_medio({
        "semestre": "invernale", "immessi": "1234567", "capacita_conferita": "2000000",
    })
    assert a["utilizzo_medio"] == b["utilizzo_medio"]


# --------------------------------------------------------------------- nota

NOTA = {
    "punto": "Tarvisio",
    "anno_termico": 2025,
    "capacita_detrazione": "500.000",
    "durata": "annuale, dal 1/10/2025",
    "mittente": "Esempio S.p.A.",
    "motivazioni": (
        "Fermo non programmato dell'impianto a monte dal 12/01 al 28/02, "
        "documentato con comunicazione del gestore estero."
    ),
}


def test_la_nota_contiene_cio_che_il_codice_prescrive():
    record = trasporto.prepara_nota(dict(NOTA))
    assert record["scadenza"] == "2026-10-09"
    assert "CAPITOLO 7, §4.3" in record["testo"]
    assert "500.000 kWh/g" in record["testo"]
    assert "Fermo non programmato" in record["testo"]
    # Niente finti invii: la nota dichiara che la trasmissione è dell'operatore.
    assert "modalità di trasmissione" in record["testo"]


def test_le_motivazioni_di_una_riga_non_bastano():
    with pytest.raises(trasporto.TrasportoError) as errore:
        trasporto.prepara_nota({**NOTA, "motivazioni": "guasto"})
    assert any(e["field"] == "motivazioni" for e in errore.value.errors)


def test_un_punto_fuori_perimetro_viene_avvisato_non_bloccato():
    record = trasporto.prepara_nota({**NOTA, "punto": "Mazara del Vallo"})
    assert any("Passo Gries" in a for a in record["avvisi"])


def test_la_nota_fuori_termine_viene_marcata():
    record = trasporto.prepara_nota({**NOTA, "anno_termico": 2020})
    assert record["fuori_termine"] is True
    assert any("termine" in a for a in record["avvisi"])


def test_il_catalogo_espone_solo_regole_del_codice():
    catalogo = trasporto.catalogo()
    assert catalogo["intervallo_minimo_giorni"] == 4
    assert catalogo["soglia_utilizzo"] == 0.80
    assert set(catalogo["punti_uioli"]) == {"Passo Gries", "Tarvisio", "Gorizia"}
    assert "Tmax" in catalogo["fonti"]["parametri"]


# ------------------------------------------------------------ rotte HTTP

def accedi(client, email="shipper@esempio.it"):
    assert client.post("/api/login", json={"email": email}).status_code == 200


def test_le_rotte_trasporto_richiedono_una_sessione(client):
    for metodo, percorso in (
        ("get", "/api/trasporto/catalogo"),
        ("get", "/api/trasporto/interruzioni"),
        ("post", "/api/trasporto/interruzioni"),
        ("post", "/api/trasporto/utilizzo"),
        ("get", "/api/trasporto/note"),
        ("post", "/api/trasporto/note"),
    ):
        assert getattr(client, metodo)(percorso).status_code == 401, percorso


def test_ciclo_completo_interruzioni(client):
    accedi(client)
    prima = client.post("/api/trasporto/interruzioni", json=BASE)
    assert prima.status_code == 201
    seconda = client.post("/api/trasporto/interruzioni",
                          json={**BASE, "data_inizio": "2026-01-15", "giorni": 2})
    assert seconda.status_code == 201
    assert len(seconda.json()["avvisi"]) == 1

    elenco = client.get("/api/trasporto/interruzioni").json()
    assert len(elenco["interruzioni"]) == 2
    assert elenco["riepilogo"][0]["giorni_totali"] == 5

    doppione = client.post("/api/trasporto/interruzioni", json=BASE)
    assert doppione.status_code == 422

    identificativo = prima.json()["id"]
    assert client.delete(f"/api/trasporto/interruzioni/{identificativo}").status_code == 200
    assert client.delete(f"/api/trasporto/interruzioni/{identificativo}").status_code == 404


def test_ciclo_completo_nota(client):
    accedi(client)
    creata = client.post("/api/trasporto/note", json=NOTA)
    assert creata.status_code == 201
    corpo = creata.json()
    assert "testo" not in corpo  # il testo si scarica, non viaggia negli elenchi
    scarico = client.get(f"/api/trasporto/note/{corpo['id']}/download")
    assert scarico.status_code == 200
    assert scarico.headers["content-type"].startswith("text/plain")
    assert "NOTA GIUSTIFICATIVA" in scarico.text


def test_lutilizzo_medio_non_conserva_nulla(client):
    accedi(client)
    esito = client.post("/api/trasporto/utilizzo", json={
        "semestre": "invernale", "immessi": "45.000.000", "capacita_conferita": "60.000.000",
    })
    assert esito.status_code == 200
    assert esito.json()["percentuale"] == 75.0


def test_i_dati_di_un_utente_non_si_vedono_da_un_altro(client):
    accedi(client, "primo@esempio.it")
    creata = client.post("/api/trasporto/interruzioni", json=BASE)
    nota = client.post("/api/trasporto/note", json=NOTA)

    accedi(client, "secondo@esempio.it")
    assert client.get("/api/trasporto/interruzioni").json()["interruzioni"] == []
    assert client.get("/api/trasporto/note").json()["note"] == []
    assert client.delete(f"/api/trasporto/interruzioni/{creata.json()['id']}").status_code == 404
    assert client.get(f"/api/trasporto/note/{nota.json()['id']}/download").status_code == 404


def test_gli_errori_di_campo_arrivano_al_client(client):
    accedi(client)
    risposta = client.post("/api/trasporto/interruzioni", json={"punto": "", "giorni": "molti"})
    assert risposta.status_code == 422
    assert risposta.json()["errors"]
    assert client.post("/api/trasporto/utilizzo", json=["lista"]).status_code == 400


# ---------------------------------------------- regressioni dalla revisione

def test_un_decimale_con_zero_iniziale_resta_un_decimale():
    """"0.500" non è mai un intero a migliaia: era letto come 500 (×1000)."""

    esito = trasporto.calcola_utilizzo_medio({
        "semestre": "estivo", "immessi": "0.500", "capacita_conferita": "1",
    })
    assert esito["immessi"] == 0.5


def test_la_nota_attesta_il_numero_esatto_decimali_compresi():
    record = trasporto.prepara_nota({**NOTA, "capacita_detrazione": "1.250.000,75"})
    assert record["capacita_detrazione"] == 1250000.75
    assert "1.250.000,75 kWh/g" in record["testo"]


def test_maiuscole_e_spazi_non_aggirano_i_controlli():
    prima = interruzione()
    with pytest.raises(trasporto.TrasportoError):
        interruzione(dati={"punto": "  TARVISIO ", "data_inizio": "2026-01-11", "giorni": 1},
                     esistenti=[prima])
    dopo = interruzione(dati={"punto": "tarvisio", "data_inizio": "2026-01-14", "giorni": 1},
                        esistenti=[prima])
    assert any("4 giorni" in a for a in dopo["avvisi"])


def test_i_giorni_consecutivi_fondono_le_interruzioni_adiacenti():
    """5-7/1 e 8-10/1 sono sei giorni consecutivi, non due volte tre."""

    prima = interruzione(dati={"data_inizio": "2026-01-05", "giorni": 3})
    seconda = interruzione(dati={"data_inizio": "2026-01-08", "giorni": 3}, esistenti=[prima])
    riepilogo = trasporto.riepilogo_interruzioni([prima, seconda])
    assert riepilogo[0]["giorni_massimi_consecutivi"] == 6


def test_il_fuori_termine_usa_il_giorno_italiano(monkeypatch):
    """Scadenze e termini valgono sul calendario italiano, non su quello UTC.

    Nelle ore 22–24 UTC i due calendari divergono: forzare oggi_roma a una
    data precisa deve cambiare l'esito, indipendentemente da dov'è il server.
    """

    termine = trasporto.scadenza_nota(NOTA["anno_termico"])
    monkeypatch.setattr(trasporto, "oggi_roma", lambda: termine)
    dentro = trasporto.prepara_nota({**NOTA})
    assert dentro["fuori_termine"] is False
    monkeypatch.setattr(trasporto, "oggi_roma", lambda: termine + timedelta(days=1))
    fuori = trasporto.prepara_nota({**NOTA})
    assert fuori["fuori_termine"] is True
    assert any("già passato" in a for a in fuori["avvisi"])


def test_il_riepilogo_distingue_le_parziali():
    """Le parziali possono ricadere nel T1max: sommarle al Tmax lo sovrastima."""

    totale = interruzione(dati={"data_inizio": "2026-01-05", "giorni": 20})
    parziale = interruzione(dati={"tipo": "parziale", "capacita": "100.000",
                                  "data_inizio": "2026-02-10", "giorni": 10},
                            esistenti=[totale])
    riepilogo = trasporto.riepilogo_interruzioni([totale, parziale])
    assert riepilogo[0]["giorni_totali"] == 30
    assert riepilogo[0]["giorni_parziali"] == 10


def test_il_tipo_omesso_diventa_totale():
    record = trasporto.registra_interruzione(
        {"punto": "Tarvisio", "data_inizio": "2026-01-10", "giorni": 1}, []
    )
    assert record["tipo"] == "totale"


def test_una_data_estrema_non_diventa_un_500():
    with pytest.raises(trasporto.TrasportoError) as errore:
        interruzione(dati={"data_inizio": "9999-12-31", "giorni": 2})
    assert any("2000-2100" in e["message"] for e in errore.value.errors)


def test_un_a_capo_nei_campi_della_nota_viene_respinto():
    """I campi a riga singola finiscono nel testo scaricato: niente righe fabbricate."""

    with pytest.raises(trasporto.TrasportoError) as errore:
        trasporto.prepara_nota({**NOTA, "mittente": "ACME\n   Capacità: 999.999 kWh/g"})
    assert any(e["field"] == "mittente" for e in errore.value.errors)
    # Le motivazioni sono un textarea: le righe multiple restano ammesse e
    # vengono indentate, così non possono imitare le sezioni del documento.
    record = trasporto.prepara_nota({**NOTA, "motivazioni": "Prima riga della motivazione documentata.\nSeconda riga."})
    assert "\n   Seconda riga." in record["testo"]


def test_un_corpo_enorme_viene_rifiutato_prima_di_leggerlo(client):
    accedi(client)
    risposta = client.post(
        "/api/trasporto/note",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(trasporto.MAX_CORPO_BYTES + 1)},
    )
    assert risposta.status_code == 413


def test_i_controlli_vedono_oltre_le_prime_cinquecento_righe(client):
    """L'elenco a schermo è limitato; i controlli del Codice no."""

    import sqlite3 as sq
    from app import db as base

    accedi(client)
    from datetime import timedelta

    with base.connect() as conn:
        for indice in range(505):
            giorno = (date(2005, 1, 1) + timedelta(days=indice * 7)).isoformat()
            conn.execute(
                "INSERT INTO trasporto_interruzione (id, email, punto, tipo, data_inizio, "
                "data_fine, giorni, riferimento, note, anno_termico, avvisi, creato_il) "
                "VALUES (?, ?, 'Vecchio', 'totale', ?, ?, 1, '', '', 2005, '[]', datetime('now'))",
                (f"vecchia{indice}", "shipper@esempio.it", giorno, giorno),
            )
    sotto = client.post("/api/trasporto/interruzioni", json={
        "punto": "Tarvisio", "tipo": "totale", "data_inizio": "2026-01-10", "giorni": 3,
    })
    assert sotto.status_code == 201
    sovrapposta = client.post("/api/trasporto/interruzioni", json={
        "punto": "TARVISIO", "tipo": "totale", "data_inizio": "2026-01-11", "giorni": 1,
    })
    assert sovrapposta.status_code == 422
