import sqlite3

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
        "Impostazioni", "Configuratore", "Sistema", "Remit", "PDR · GME",
    ]:
        assert f'data-screen-label="{label}"' in r.text, label
    assert 'id="app-template"' in r.text
    assert "style-hover" not in r.text  # pseudo-stili convertiti dal builder
    assert 'value="{{ loginEmail }}"' in r.text  # campi login controllati (builder)
    assert r.text.count('onKeyDown="{{ loginKey }}"') == 2
    assert 'value="{{ loginErrore }}"' in r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_static_serviti(client):
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/static/runtime.js").status_code == 200
    assert client.get("/static/logic.js").status_code == 200


def test_state_richiede_sessione(client):
    assert client.get("/api/state").status_code == 401
    assert client.put("/api/state", json={"demoMode": True}).status_code == 401


def test_login_e_persistenza_stato(client):
    r = client.post("/api/login", json={"email": "m.rossi@azienda1.it"})
    assert r.status_code == 200 and r.json()["email"] == "m.rossi@azienda1.it"

    # stato vuoto: la risposta porta solo l'identità della sessione
    assert client.get("/api/state").json() == {"email": "m.rossi@azienda1.it"}

    nomina = {"punto": "PSV", "ciclo": "R4", "qta": "500", "stato": "Inviata"}
    r = client.put("/api/state", json={"nomList": [nomina], "demoMode": True, "nextU": 2})
    assert r.status_code == 200
    assert r.json()["salvate"] == ["demoMode", "nextU", "nomList"]

    stato = client.get("/api/state").json()
    assert stato["nomList"] == [nomina]
    assert stato["demoMode"] is True
    assert stato["nextU"] == 2


def test_stato_isolato_per_utente(client):
    from app.main import app

    client.post("/api/login", json={"email": "prima@azienda.it"})
    assert client.put("/api/state", json={"demoMode": True}).status_code == 200

    # Due client simulano browser diversi sulla stessa base SQLite.
    with TestClient(app) as altro:
        altro.post("/api/login", json={"email": "seconda@azienda.it"})
        assert altro.get("/api/state").json() == {"email": "seconda@azienda.it"}
        assert altro.put("/api/state", json={"demoMode": False}).status_code == 200

    assert client.get("/api/state").json()["demoMode"] is True


def test_validazione_chiavi_e_forme(client):
    client.post("/api/login", json={})
    assert client.put("/api/state", json={"altro": 1}).status_code == 422
    assert client.put("/api/state", json={"nomList": [{"punto": "PSV"}]}).status_code == 422
    assert client.put("/api/state", json={"nextU": -5}).status_code == 422
    assert client.put("/api/state", json={"demoMode": "sì"}).status_code == 422
    assert client.put("/api/state", json="testo").status_code == 400
    # una chiave invalida respinge l'intera patch
    r = client.put("/api/state", json={"demoMode": True, "altro": 1})
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


def test_remlist_roundtrip_e_validazione(client):
    client.post("/api/login", json={})
    riga = {"rif": "PSV-2026-0142", "tipo": "Standard", "qta": "500", "prezzo": "33,50", "stato": "Da inviare"}
    assert client.put("/api/state", json={"remList": [riga]}).status_code == 200
    assert client.get("/api/state").json()["remList"] == [riga]
    # forma sbagliata respinta
    assert client.put("/api/state", json={"remList": [{"rif": "x"}]}).status_code == 422
    assert client.put("/api/state", json={"remList": [{**riga, "extra": "no"}]}).status_code == 422


def _remit_valida(kind="gas_standard", **extra):
    report = {
        "report_kind": kind,
        "action": "new",
        "source_ref": "PSV-2026-0142",
        "event_at": "2026-07-31",
        "counterparty_scheme": "ace",
        "counterparty": "A0045821W.IT",
        "side": "buy",
        "quantity_mwh": "1.250,50",
        "quantity_unit": "MWh",
        "price_eur_mwh": "33.50",
        "price_currency": "EUR",
        "acer_code": "A0045821W.IT",
        "trading_capacity": "P",
        "contract_id": "PSV-2026-0142",
    }
    if kind == "gas_standard":
        report.update(
            {
                "marketplace_scheme": "mic",
                "marketplace_id": "XGAS",
                "transaction_at": "2026-07-31T10:15:00Z",
                "transaction_id": "UTI-2026-0001",
            }
        )
    elif kind == "gas_nonstandard":
        report.update(
            {
                "delivery_point": "21YIT-SNAMP-S-PS",
                "contract_date": "2026-07-31",
                "contract_type": "SP",
                "energy_commodity": "NG",
                "delivery_start_date": "2026-08-01",
                "delivery_end_date": "2026-08-31",
                "settlement_method": "P",
            }
        )
    report.update(extra)
    return report


def _valida(client, report):
    response = client.post(
        f"/api/remit/reports/{report['id']}/validate",
        headers={"If-Match": str(report["version"])},
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _esporta(client, report):
    response = client.post(
        f"/api/remit/reports/{report['id']}/export",
        headers={"If-Match": str(report["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_remit_workspace_genera_table1_xml_xsd_e_conserva_audit(client):
    client.post("/api/login", json={"email": "remit@azienda.it"})
    created = client.post("/api/remit/reports", json=_remit_valida())
    assert created.status_code == 201
    report = created.json()
    assert report["status"] == "bozza"
    assert report["is_complete"] is True
    assert report["data"]["quantity_mwh"] == "1250.5"
    assert report["data"]["price_eur_mwh"] == "33.5"

    report = _valida(client, report)
    assert report["status"] == "validata_localmente"

    result = _esporta(client, report)
    assert result["report"]["status"] == "xml_validato_xsd"
    artifact = result["artifact"]
    assert artifact["kind"] == "acer_xml_xsd_validated"
    assert artifact["xsd_valid"] is True
    assert artifact["schema_name"] == "REMITTable1"
    assert artifact["schema_version"] == "V3"
    assert artifact["filename"].endswith("_REMITTable1_V3_A0045821W.IT_1.XML")
    assert len(artifact["sha256"]) == 64

    downloaded = client.get(f"/api/remit/artifacts/{artifact['id']}")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/xml")
    assert "<REMITTable1" in downloaded.text
    assert "A0045821W.IT" in downloaded.text

    audit = client.get(f"/api/remit/reports/{report['id']}/audit").json()["events"]
    assert [event["event_type"] for event in audit] == ["CREATED", "VALIDATED", "ACER_XML_EXPORTED_XSD_VALID"]
    assert audit[0]["prev_hash"] == ""
    assert audit[1]["prev_hash"] == audit[0]["hash"]
    assert audit[2]["prev_hash"] == audit[1]["hash"]
    assert client.post(f"/api/remit/reports/{report['id']}/submit").status_code == 409


def test_remit_table2_xml_e_progressivo_pdr(client):
    client.post("/api/login", json={"email": "remit@azienda.it"})
    first = client.post("/api/remit/reports", json=_remit_valida("gas_nonstandard")).json()
    first_result = _esporta(client, _valida(client, first))
    assert first_result["artifact"]["schema_name"] == "REMITTable2"
    assert first_result["artifact"]["filename"].endswith("_REMITTable2_V1_A0045821W.IT_1.XML")

    second = client.post(
        "/api/remit/reports", json=_remit_valida("gas_nonstandard", transaction_id="unused")
    ).json()
    second_result = _esporta(client, _valida(client, second))
    assert second_result["artifact"]["filename"].endswith("_REMITTable2_V1_A0045821W.IT_2.XML")
    xml = client.get(f"/api/remit/artifacts/{second_result['artifact']['id']}").text
    assert "<REMITTable2" in xml
    assert "<deliveryPointOrZone>21YIT-SNAMP-S-PS</deliveryPointOrZone>" in xml

    # Il nome PDR non contiene l'utente: un secondo utente con lo stesso
    # codice ACER deve ricevere il progressivo successivo, non riusare _1.
    client.post("/api/login", json={"email": "secondo@azienda.it"})
    third = client.post("/api/remit/reports", json=_remit_valida("gas_nonstandard")).json()
    third_result = _esporta(client, _valida(client, third))
    assert third_result["artifact"]["filename"].endswith("_REMITTable2_V1_A0045821W.IT_3.XML")


def test_remit_blocca_export_incompleto_conflitti_e_ambito_non_supportato(client):
    client.post("/api/login", json={"email": "remit@azienda.it"})
    created = client.post("/api/remit/reports", json={}).json()
    assert created["status"] == "bozza"
    assert created["validation_errors"]
    assert client.post(f"/api/remit/reports/{created['id']}/validate", json={}).status_code == 428
    validated = _valida(client, created)
    assert validated["status"] == "bozza"
    assert client.post(
        f"/api/remit/reports/{created['id']}/export",
        headers={"If-Match": str(validated["version"])},
    ).status_code == 409

    valid = client.post("/api/remit/reports", json=_remit_valida()).json()
    updated = client.patch(
        f"/api/remit/reports/{valid['id']}",
        headers={"If-Match": str(valid["version"])},
        json={"source_ref": "PSV-2026-0143"},
    )
    assert updated.status_code == 200
    conflict = client.patch(
        f"/api/remit/reports/{valid['id']}",
        headers={"If-Match": str(valid["version"])},
        json={"source_ref": "PSV-2026-0144"},
    )
    assert conflict.status_code == 409

    transport = client.post("/api/remit/reports", json=_remit_valida("gas_transport")).json()
    assert any("GasCapacity" in error["message"] for error in transport["validation_errors"])

    future_transaction = client.post(
        "/api/remit/reports", json=_remit_valida(transaction_at="2099-01-01T00:00:00Z")
    ).json()
    assert any(error["field"] == "transaction_at" for error in future_transaction["validation_errors"])
    future_contract = client.post(
        "/api/remit/reports", json=_remit_valida("gas_nonstandard", contract_date="2099-01-01")
    ).json()
    assert any(error["field"] == "contract_date" for error in future_contract["validation_errors"])


def test_remit_export_rollback_su_conflitto_non_lascia_artefatti(monkeypatch, client):
    """Un conflitto tardivo non deve lasciare XML o progressivi orfani."""

    from app import db

    client.post("/api/login", json={"email": "atomicita@azienda.it"})
    report = client.post("/api/remit/reports", json=_remit_valida()).json()
    report = _valida(client, report)

    def conflict(*args, **kwargs):
        raise db.ConflittoStato("collisione simulata")

    monkeypatch.setattr(db, "aggiorna_report_remit", conflict)
    result = client.post(
        f"/api/remit/reports/{report['id']}/export",
        headers={"If-Match": str(report["version"])},
    )
    assert result.status_code == 409
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM remit_artifact").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pdr_progressivo").fetchone()[0] == 0


def test_migrazione_progressivo_pdr_condivide_la_sequenza_per_codice_acer(tmp_path, monkeypatch):
    from app import db

    database = tmp_path / "progressivi-v1.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE pdr_progressivo ("
            "email TEXT NOT NULL, data_file TEXT NOT NULL, schema_nome TEXT NOT NULL, "
            "schema_versione TEXT NOT NULL, codice_acer TEXT NOT NULL, prossimo INTEGER NOT NULL, "
            "PRIMARY KEY (email, data_file, schema_nome, schema_versione, codice_acer))"
        )
        conn.executemany(
            "INSERT INTO pdr_progressivo VALUES (?, '2026-08-01', 'REMITTable1', 'V3', 'A0045821W.IT', ?)",
            [("uno@azienda.it", 3), ("due@azienda.it", 5)],
        )
    monkeypatch.setenv("VETTORE_DB", str(database))
    db.init_db()
    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(pdr_progressivo)")}
        assert "email" not in columns
        assert db.prossimo_progressivo_pdr(
            conn,
            data_file="2026-08-01",
            schema_nome="REMITTable1",
            schema_versione="V3",
            codice_acer="A0045821W.IT",
        ) == 5


def test_remit_blocca_download_e_preflight_di_artefatto_corrotto(client):
    from app import db

    client.post("/api/login", json={"email": "integrita@azienda.it"})
    report = client.post("/api/remit/reports", json=_remit_valida()).json()
    exported = _esporta(client, _valida(client, report))
    artifact_id = exported["artifact"]["id"]
    with db.connect() as conn:
        conn.execute("UPDATE remit_artifact SET contenuto = ? WHERE id = ?", ("<alterato />", artifact_id))

    download = client.get(f"/api/remit/artifacts/{artifact_id}")
    assert download.status_code == 409
    preflight = client.post(f"/api/pdr/reports/{report['id']}/preflight").json()
    assert preflight["manual_prerequisites_declared"] is False
    assert "XML_INTEGRITY" in {issue["code"] for issue in preflight["issues"]}


def test_remit_isola_utenti_e_importa_il_registro_legacy_senza_falsi_esiti(client):
    from app.main import app

    client.post("/api/login", json={"email": "prima@azienda.it"})
    legacy = {"rif": "PSV-legacy", "tipo": "Standard", "qta": "500", "prezzo": "33,50", "stato": "Accettata"}
    assert client.put("/api/state", json={"remList": [legacy]}).status_code == 200
    first = client.get("/api/remit/reports")
    assert first.status_code == 200
    assert first.json()["legacy_imported"] == 1
    imported = first.json()["reports"][0]
    assert imported["status"] == "bozza"
    assert imported["legacy_unverified"] is True
    second = client.get("/api/remit/reports").json()
    assert second["legacy_imported"] == 0
    assert len(second["reports"]) == 1

    with TestClient(app) as other:
        other.post("/api/login", json={"email": "seconda@azienda.it"})
        assert other.get("/api/remit/reports").json()["reports"] == []
        assert other.get(f"/api/remit/reports/{imported['id']}").status_code == 404


def test_pdr_preflight_lega_xml_codice_abilitato_e_blocca_linvio_reale(client):
    client.post("/api/login", json={"email": "pdr@azienda.it"})
    status = client.get("/api/pdr")
    assert status.status_code == 200
    assert status.json()["profile"]["environment"] == "test"
    assert status.json()["endpoint"] == "https://provepdr.ipex.it"
    assert status.json()["fees"]["external_upload_annual_eur"] == 300
    assert {schema["schema_name"] for schema in status.json()["schemas"]} >= {"REMITTable1", "REMITTable2"}

    assert client.put("/api/pdr/profile", json={"password": "non-salvare"}).status_code == 422
    assert client.put("/api/pdr/profile", json={"test_access_requested": "false"}).status_code == 422
    profile = {
        "environment": "test",
        "channel": "web_service",
        "gme_operator_code": "M-GAS-123",
        "registered_acer_code": "A0045821W.IT",
        "test_access_requested": True,
        "two_factor_ready": True,
        "pdr_contract_reference": "",
    }
    saved = client.put("/api/pdr/profile", json=profile)
    assert saved.status_code == 200
    assert saved.json()["stored_secrets"] is False

    report = client.post("/api/remit/reports", json=_remit_valida()).json()
    before_export = client.post(f"/api/pdr/reports/{report['id']}/preflight").json()
    assert before_export["upload_ready"] is False
    assert {issue["code"] for issue in before_export["issues"]} >= {"ACER_XML", "XML_ARTIFACT"}

    exported = _esporta(client, _valida(client, report))
    report = exported["report"]
    check = client.post(f"/api/pdr/reports/{report['id']}/preflight")
    assert check.status_code == 200
    assert check.json()["manual_prerequisites_declared"] is True
    assert check.json()["xml_ready_for_manual_upload"] is True
    assert check.json()["upload_ready"] is False
    assert "PDR_ACCESS_UNVERIFIED" in {issue["code"] for issue in check.json()["issues"]}
    assert check.json()["artifact"]["filename"] == exported["artifact"]["filename"]
    assert client.post(f"/api/pdr/reports/{report['id']}/submit").status_code == 409


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


def test_migrazione_stato_globale_conserva_un_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("VETTORE_DB", str(tmp_path / "legacy.db"))
    from app import db

    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE stato (chiave TEXT PRIMARY KEY, valore TEXT NOT NULL, aggiornata_il TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO stato (chiave, valore, aggiornata_il) VALUES ('demoMode', 'true', datetime('now'))"
        )
    db.init_db()
    with db.connect() as conn:
        colonne = {r["name"] for r in conn.execute("PRAGMA table_info(stato)")}
        assert colonne >= {"email", "chiave", "valore"}
        assert conn.execute("SELECT valore FROM stato_legacy WHERE chiave = 'demoMode'").fetchone()[0] == "true"
        assert db.leggi_stato(conn, "nuovo@azienda.it") == {}


def test_migrazione_stato_globale_importa_solo_per_proprietario_esplicito(tmp_path, monkeypatch):
    monkeypatch.setenv("VETTORE_DB", str(tmp_path / "legacy-import.db"))
    monkeypatch.setenv("VETTORE_LEGACY_EMAIL", "Proprietario@Azienda.it")
    from app import db

    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE stato (chiave TEXT PRIMARY KEY, valore TEXT NOT NULL, aggiornata_il TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO stato (chiave, valore, aggiornata_il) VALUES ('demoMode', 'true', datetime('now'))"
        )
    db.init_db()
    with db.connect() as conn:
        assert db.leggi_stato(conn, "proprietario@azienda.it") == {"demoMode": True}
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stato_legacy'"
        ).fetchone() is None
