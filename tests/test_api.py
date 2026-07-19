import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VETTORE_DB", str(tmp_path / "test.db"))
    from app import db
    from app.main import app

    db.init_db()
    with TestClient(app) as c:
        yield c


def test_index_contiene_template_e_schermate(client):
    r = client.get("/")
    assert r.status_code == 200
    for label in [
        "Login", "Hub moduli", "Logistica Gas", "Dashboard", "Nomine e Programmazione",
        "Bilanciamento", "Capacita e Contratti", "Stoccaggio", "Report e Analisi",
        "Impostazioni", "Configuratore", "Sistema",
    ]:
        assert f'data-screen-label="{label}"' in r.text, label
    assert 'id="app-template"' in r.text
    assert "style-hover" not in r.text  # pseudo-stili convertiti dal builder
    assert 'value="{{ loginEmail }}"' in r.text  # campi login controllati (builder)
    assert r.text.count('onKeyDown="{{ loginKey }}"') == 2


def test_static_serviti(client):
    assert client.get("/static/runtime.js").status_code == 200
    assert client.get("/static/logic.js").status_code == 200


def test_state_richiede_sessione(client):
    assert client.get("/api/state").status_code == 401
    assert client.put("/api/state", json={"gmeOk": True}).status_code == 401


def test_login_e_persistenza_stato(client):
    r = client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    assert r.status_code == 200 and r.json()["email"] == "m.rossi@azienda1.it"

    # stato vuoto: la risposta porta solo l'identità della sessione
    assert client.get("/api/state").json() == {"email": "m.rossi@azienda1.it"}

    nomina = {"punto": "PSV", "ciclo": "R4", "qta": "500", "stato": "Inviata"}
    r = client.put("/api/state", json={"nomList": [nomina], "gmeOk": True, "nextU": 2})
    assert r.status_code == 200
    assert r.json()["salvate"] == ["gmeOk", "nextU", "nomList"]

    stato = client.get("/api/state").json()
    assert stato["nomList"] == [nomina]
    assert stato["gmeOk"] is True
    assert stato["nextU"] == 2


def test_validazione_chiavi_e_forme(client):
    client.post("/api/login", json={})
    assert client.put("/api/state", json={"altro": 1}).status_code == 422
    assert client.put("/api/state", json={"nomList": [{"punto": "PSV"}]}).status_code == 422
    assert client.put("/api/state", json={"nextU": -5}).status_code == 422
    assert client.put("/api/state", json={"gmeOk": "sì"}).status_code == 422
    assert client.put("/api/state", json="testo").status_code == 400
    # una chiave invalida respinge l'intera patch
    r = client.put("/api/state", json={"gmeOk": True, "altro": 1})
    assert r.status_code == 422
    assert client.get("/api/state").json() == {"email": "utente@locale"}
    # modalità demo persistibile
    assert client.put("/api/state", json={"demoMode": True}).status_code == 200
    # cap sul numero di chiavi delle mappe (DoS)
    grande = {f"k{i}": True for i in range(300)}
    assert client.put("/api/state", json={"cfg": grande}).status_code == 422
    # righe utenti a 3 elementi non ammesse (il frontend ne pretende 4)
    assert client.put("/api/state", json={"extraUsers": [["wu1", "AF", "Anna"]]}).status_code == 422
    assert client.put("/api/state", json={"extraPunti": [["a", "b", "c", "d"]]}).status_code == 422


def test_email_non_valida_ripiega_su_identita_neutra(client):
    # mai l'identità di scena (Marco Rossi), che contaminerebbe la modalità pulita
    r = client.post("/api/login", json={"email": "<script>alert(1)</script>"})
    assert r.json()["email"] == "utente@locale"
    r = client.post("/api/login", json={})
    assert r.json()["email"] == "utente@locale"


def test_scadenza_e_pulizia_sessioni(tmp_path, monkeypatch):
    monkeypatch.setenv("VETTORE_DB", str(tmp_path / "s.db"))
    from app import db

    db.init_db()
    with db.connect() as conn:
        # una sessione più vecchia della finestra di 30 giorni non è valida...
        conn.execute(
            "INSERT INTO sessioni (token, email, creata_il) VALUES ('vecchio', 'a@b.it', datetime('now','-31 days'))"
        )
        assert db.email_sessione(conn, "vecchio") is None
        # ...e una nuova sessione la elimina (pulizia) mentre resta valida
        db.crea_sessione(conn, "nuovo", "a@b.it")
        assert db.email_sessione(conn, "nuovo") == "a@b.it"
        assert conn.execute("SELECT COUNT(*) FROM sessioni WHERE token='vecchio'").fetchone()[0] == 0


def test_logout(client):
    client.post("/api/login", json={})
    assert client.get("/api/state").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/state").status_code == 401


def test_extra_punti_e_utenti_roundtrip(client):
    client.post("/api/login", json={})
    patch = {
        "extraPunti": [["ReMi 34521405 · Brescia Est", "Riconsegna", "px1"]],
        "extraUsers": [["wu1", "AF", "Anna Ferrari", "a.ferrari@azienda1.it"]],
        "users": {"mricci": "up", "wu1": "ro"},
        "disabled": {"gverdi": True},
        "hiddenPunti": ["cavarzere"],
    }
    assert client.put("/api/state", json=patch).status_code == 200
    stato = client.get("/api/state").json()
    stato.pop("email")
    assert stato == patch
