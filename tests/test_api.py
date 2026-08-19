import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient


def test_index_contiene_template_e_schermate(client):
    r = client.get("/")
    assert r.status_code == 200
    for label in [
        "Login", "Hub moduli", "Logistica Gas", "Dashboard", "Nomine e Programmazione",
        "Bilanciamento", "Capacita e Contratti", "Stoccaggio", "Report e Analisi",
        "Impostazioni", "Configuratore", "Sistema", "Remit", "PDR · GME", "Agenda",
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
        # controparte distinta dal dichiarante: Table 1/2 li vogliono soggetti diversi
        "counterparty": "B0011111X.IT",
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
    # il blocco non è un rinvio tecnico: quegli schemi non ammettono lo shipper come emittente
    assert any("ZSO" in error["message"] for error in transport["validation_errors"])

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


def _esporta_report_per_ricevuta(client):
    report = client.post("/api/remit/reports", json=_remit_valida()).json()
    return _esporta(client, _valida(client, report))


def _payload_ricevuta(report, artifact, content: bytes, **extra):
    payload = {
        "report_id": report["id"],
        "source": "pdr_portal",
        "outcome": "pdr_accepted",
        "load_code": "123456",
        "reported_at": "2026-08-01T10:15:00+02:00",
        "detail": "Esito importato dal portale PDR.",
        "filename": f"FA_{artifact['filename']}",
        "mime_type": "application/xml",
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    payload.update(extra)
    return payload


def test_ricevuta_pdr_importata_immutabile_idempotente_e_senza_raw_nelle_api(client):
    from app import db

    client.post("/api/login", json={"email": "ricevute@azienda.it"})
    exported = _esporta_report_per_ricevuta(client)
    report, artifact = exported["report"], exported["artifact"]
    raw = b"raw-receipt-secret-4e282ef2"
    payload = _payload_ricevuta(report, artifact, raw)

    created = client.post("/api/pdr/receipts/import", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    receipt = body["receipt"]
    assert body["idempotent"] is False
    assert body["connector_verified"] is False
    assert receipt["source"] == "pdr"
    assert receipt["outcome"] == "pdr_accepted"
    assert receipt["artifact_id"] == artifact["id"]
    assert receipt["artifact_sha256"] == artifact["sha256"]
    assert receipt["load_code"] == "123456"
    assert receipt["reported_at"].endswith("+02:00")
    assert receipt["connector_verified"] is False
    assert receipt["verification_state"] == "manual_import_unverified"
    assert "content" not in receipt and "content_base64" not in receipt
    # Il documento importato è una prova locale: non può promuovere il report
    # a inviato/accettato senza un connettore PDR autorizzato.
    assert client.get(f"/api/remit/reports/{report['id']}").json()["status"] == "xml_validato_xsd"

    duplicate = client.post("/api/pdr/receipts/import", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True
    assert duplicate.json()["receipt"]["id"] == receipt["id"]

    listed = client.get(f"/api/pdr/receipts?report_id={report['id']}")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["receipts"]] == [receipt["id"]]
    assert raw.decode("ascii") not in listed.text
    detail = client.get(f"/api/pdr/receipts/{receipt['id']}")
    assert detail.status_code == 200
    assert raw.decode("ascii") not in detail.text
    assert "content" not in detail.json()["receipt"]

    downloaded = client.get(f"/api/pdr/receipts/{receipt['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == raw
    assert downloaded.headers["content-type"].startswith("application/xml")
    assert "filename*=UTF-8''FA_" in downloaded.headers["content-disposition"]

    audit = client.get(f"/api/remit/reports/{report['id']}/audit").json()["events"]
    imported = [event for event in audit if event["event_type"] == "PDR_RECEIPT_IMPORTED"]
    assert len(imported) == 1
    assert imported[0]["detail"]["receipt_id"] == receipt["id"]
    assert imported[0]["detail"]["connector_verified"] is False
    assert imported[0]["detail"]["error_detail_sha256"]

    # Il database impedisce modifiche e cancellazioni anche se una chiamata
    # interna provasse a bypassare l'API HTTP.
    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE pdr_ricevuta SET esito = 'acer_accepted' WHERE id = ?", (receipt["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM pdr_ricevuta WHERE id = ?", (receipt["id"],))


def test_ricevuta_pdr_parser_gme_prevale_sull_esito_manuale_e_rileva_partial(client):
    client.post("/api/login", json={"email": "parser@azienda.it"})
    exported = _esporta_report_per_ricevuta(client)
    report, artifact = exported["report"], exported["artifact"]
    acknowledgement = (
        '<PIPEFunctionalAcknowledgement xmlns="urn:XML-PIPE" Status="Partial" '
        f'OriginalReferenceNumber="{artifact["filename"]}" '
        'CreationDate="2026-08-01T10:16:00Z">'
        '<RejectInformation><ReasonText>Riga 3 da ritrasmettere</ReasonText></RejectInformation>'
        '</PIPEFunctionalAcknowledgement>'
    ).encode("utf-8")
    imported = client.post(
        "/api/pdr/receipts/import",
        json=_payload_ricevuta(report, artifact, acknowledgement, outcome="unknown", detail="ignorato dal parser"),
    )
    assert imported.status_code == 201, imported.text
    receipt = imported.json()["receipt"]
    assert receipt["outcome"] == "pdr_partial"
    assert receipt["reported_at"] == "2026-08-01T10:16:00Z"
    assert receipt["error_detail"] == "Riga 3 da ritrasmettere"
    assert receipt["parser_version"] == "gme-pdr-functional-ack/1"
    assert receipt["parser_metadata"]["original_reference_matches_artifact"] is True
    assert receipt["parser_metadata"]["outcome_derived"] is True

    rejected_ack = acknowledgement.replace(b'Status="Partial"', b'Status="Reject"')
    conflict = client.post(
        "/api/pdr/receipts/import",
        json=_payload_ricevuta(
            report,
            artifact,
            rejected_ack,
            outcome="pdr_accepted",
            filename="FA_conflict.xml",
        ),
    )
    assert conflict.status_code == 422
    assert "non coincide" in conflict.json()["errore"]


def test_ricevute_richiedono_xml_corretto_validano_campi_e_isolano_utenti(monkeypatch, client):
    from app import pdr
    from app.main import app

    client.post("/api/login", json={"email": "prima-ricevute@azienda.it"})
    incomplete = client.post("/api/remit/reports", json=_remit_valida()).json()
    bare = _payload_ricevuta(incomplete, {"filename": "unused.xml"}, b"receipt")
    assert client.post("/api/pdr/receipts/import", json=bare).status_code == 422

    exported = _esporta_report_per_ricevuta(client)
    report, artifact = exported["report"], exported["artifact"]
    invalid_code = _payload_ricevuta(report, artifact, b"invalid-code", load_code="12345")
    assert client.post("/api/pdr/receipts/import", json=invalid_code).status_code == 422
    unicode_code = _payload_ricevuta(report, artifact, b"unicode-code", load_code="١٢٣٤٥٦")
    assert client.post("/api/pdr/receipts/import", json=unicode_code).status_code == 422
    invalid_time = _payload_ricevuta(report, artifact, b"invalid-time", reported_at="2026-08-01T10:15:00")
    assert client.post("/api/pdr/receipts/import", json=invalid_time).status_code == 422
    wrong_artifact = _payload_ricevuta(report, artifact, b"wrong-artifact", artifact_id="not-the-report-artifact")
    assert client.post("/api/pdr/receipts/import", json=wrong_artifact).status_code == 422
    assert client.post(
        "/api/pdr/receipts/import",
        json=_payload_ricevuta(report, artifact, b"not-base64", content_base64="***"),
    ).status_code == 422
    monkeypatch.setattr(pdr, "MAX_RECEIPT_BYTES", 4)
    too_large = _payload_ricevuta(report, artifact, b"12345")
    assert client.post("/api/pdr/receipts/import", json=too_large).status_code == 422
    monkeypatch.setattr(pdr, "MAX_RECEIPT_BYTES", 2 * 1024 * 1024)

    valid = client.post(
        "/api/pdr/receipts/import", json=_payload_ricevuta(report, artifact, b"isolated-receipt")
    )
    assert valid.status_code == 201
    receipt_id = valid.json()["receipt"]["id"]

    with TestClient(app) as other:
        other.post("/api/login", json={"email": "seconda-ricevute@azienda.it"})
        assert other.get("/api/pdr/receipts").json()["receipts"] == []
        # Un ID altrui non deve rivelare se la ricevuta esiste.
        assert other.get(f"/api/pdr/receipts/{receipt_id}").status_code == 404
        assert other.get(f"/api/pdr/receipts/{receipt_id}/download").status_code == 404
        assert other.get(f"/api/pdr/receipts?report_id={report['id']}").status_code == 404


def test_import_ricevuta_e_audit_sono_atomici(monkeypatch, client):
    from app import db, pdr, remit

    email = "atomic-ricevuta@azienda.it"
    client.post("/api/login", json={"email": email})
    exported = _esporta_report_per_ricevuta(client)
    report, artifact = exported["report"], exported["artifact"]
    payload = _payload_ricevuta(report, artifact, b"rollback-receipt")

    def audit_failure(*args, **kwargs):
        raise RuntimeError("audit non disponibile")

    monkeypatch.setattr(remit, "registra_ricevuta_pdr_importata", audit_failure)
    with db.connect() as conn:
        with pytest.raises(RuntimeError, match="audit non disponibile"):
            pdr.importa_ricevuta(conn, email, payload)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pdr_ricevuta").fetchone()[0] == 0
        events = db.leggi_eventi_remit(conn, email, report["id"])
        assert not [event for event in events if event["event_type"] == "PDR_RECEIPT_IMPORTED"]


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


# --- Regressioni dell'audit REMIT: i vincoli degli XSD valgono già in locale ---


def _record_t2(**extra):
    base = {
        "report_kind": "gas_nonstandard",
        "acer_code": "A0045821W.IT",
        "counterparty": "B0011111X.IT",
        "counterparty_scheme": "ace",
        "quantity_mwh": "500",
        "quantity_unit": "MWh",
        "price_eur_mwh": "33.50",
        "price_currency": "EUR",
        "action": "new",
        "side": "buy",
        "trading_capacity": "P",
        "contract_id": "PSV-2026-0142",
        "contract_date": "2026-07-01",
        "contract_type": "FW",
        "energy_commodity": "NG",
        "delivery_point": "21YIT-SNAMRG--PX",
        "delivery_start_date": "2026-08-01",
        "delivery_end_date": "2026-08-31",
        "settlement_method": "P",
    }
    base.update(extra)
    return base


def _record_t1(**extra):
    base = {
        "report_kind": "gas_standard",
        "acer_code": "A0045821W.IT",
        "counterparty": "B0011111X.IT",
        "counterparty_scheme": "ace",
        "quantity_mwh": "250",
        "quantity_unit": "MWh",
        "price_eur_mwh": "33.45",
        "price_currency": "EUR",
        "action": "new",
        "side": "sell",
        "trading_capacity": "P",
        "contract_id": "MGP-GAS-2026-0042",
        "transaction_id": "UTI-2026-0000042",
        "transaction_at": "2026-08-01T10:15:00Z",
        "marketplace_id": "XGME",
        "marketplace_scheme": "mic",
    }
    base.update(extra)
    return base


def _campi_in_errore(record):
    from app import acer_xml

    return {e["field"] for e in acer_xml.errori_dati(record)}


def test_record_completi_passano_e_generano_xml_valido():
    from app import acer_xml

    for record in (_record_t1(), _record_t2()):
        assert acer_xml.errori_dati(record) == []
        acer_xml.genera_xml(record)  # solleva se l'XSD non è soddisfatto


def test_settlement_method_obbligatorio_e_mai_inventato():
    """Table 2 campo 31: un contratto finanziario non deve diventare 'P' da solo."""
    from lxml import etree

    from app import acer_xml

    assert "settlement_method" in _campi_in_errore(_record_t2(settlement_method=""))
    assert "settlement_method" in _campi_in_errore(_record_t2(settlement_method="F"))
    doc = acer_xml.genera_xml(_record_t2(settlement_method="C"))
    grezzo = doc.xml.encode() if isinstance(doc.xml, str) else doc.xml
    radice = etree.fromstring(grezzo)
    assert radice.find(".//{*}settlementMethod").text == "C"


def test_liste_chiuse_bloccate_prima_dellexport():
    for campo, valore in (
        ("trading_capacity", "X"),
        ("price_currency", "XXX"),
        ("quantity_unit", "BOH"),
        ("energy_commodity", "GAS"),
        ("contract_type", "CO"),  # valore di Table 1, non ammesso su Table 2
    ):
        assert campo in _campi_in_errore(_record_t2(**{campo: valore})), campo


def test_identificativi_rispettano_lunghezza_e_pattern_dello_schema():
    assert "counterparty" in _campi_in_errore(_record_t2(counterparty_scheme="lei", counterparty="ABC123"))
    assert "counterparty" in _campi_in_errore(_record_t2(counterparty_scheme="bic", counterparty="UNCRITMM"))
    assert "delivery_point" in _campi_in_errore(_record_t2(delivery_point="PIPPO"))
    assert "delivery_point" in _campi_in_errore(_record_t2(delivery_point="AAAAAAAAAAAAAAAA"))
    assert "marketplace_id" in _campi_in_errore(_record_t1(marketplace_scheme="bil", marketplace_id="GME"))


def test_controparte_diversa_dal_dichiarante():
    assert "counterparty" in _campi_in_errore(_record_t2(counterparty="A0045821W.IT"))


def test_identificativi_non_vengono_troncati_in_silenzio():
    """Un UTI o un contract_id troppo lunghi sono un errore, non un taglio."""
    assert "transaction_id" in _campi_in_errore(_record_t1(transaction_id="U" * 120))
    assert "contract_id" in _campi_in_errore(_record_t1(contract_id="C" * 60))
    # Table 2 ammette 100 caratteri: 80 devono passare
    assert _campi_in_errore(_record_t2(contract_id="C" * 80)) == set()


def test_numeri_rispettano_i_facet_dello_schema():
    assert "price_eur_mwh" in _campi_in_errore(_record_t2(price_eur_mwh="33.1234567"))
    assert "quantity_mwh" in _campi_in_errore(_record_t2(quantity_mwh="-500"))


def test_i_vincoli_sono_derivati_dagli_xsd_non_ricopiati():
    """Le liste cambiano tra i due tracciati: la fonte deve essere lo schema."""
    from app import acer_xml

    t1 = acer_xml._facet("gas_standard", "contractIdType")
    t2 = acer_xml._facet("gas_nonstandard", "contractIdType")
    assert t1["maxLength"] == 50 and t2["maxLength"] == 100
    assert "CO" in acer_xml._facet("gas_standard", "contractTypeType")["enum"]
    assert "CO" not in acer_xml._facet("gas_nonstandard", "contractTypeType")["enum"]


# ------------------------------------------------------------------ EDIG@S


def _nomina_valida(**extra):
    return {
        "identificativo": "NOMINT-20260803-001",
        "versione": 1,
        "tipo_documento": "01G",
        "tipo_nomina": "A02",
        "emittente_eic": "21X000000001234A",
        "emittente_ruolo": "ZSH",
        "destinatario_eic": "21X00000000567KB",
        "destinatario_ruolo": "ZSO",
        "giorno_gas": "2026-08-03",
        "conto_interno": "SHIPPER-01",
        "punto_eic": "21Z0000000001234",
        "unita": "KW1",
        "controparti": [{"conto": "CTP-ALFA", "direzione": "Z02", "quantita_giornaliera": "12500"}],
        **extra,
    }


def test_edigas_richiede_la_sessione(client):
    for metodo, url in [
        ("get", "/api/edigas/catalogo"),
        ("get", "/api/edigas/nomine"),
        ("post", "/api/edigas/nomine"),
        ("post", "/api/edigas/risposte"),
    ]:
        assert getattr(client, metodo)(url).status_code == 401, url


def test_edigas_catalogo_espone_i_codici_dello_schema(client):
    client.post("/api/login", json={})
    catalogo = client.get("/api/edigas/catalogo").json()
    assert catalogo["versione"] == "6.1"
    assert {t["codice"] for t in catalogo["tipi_documento"]} == {"01G", "02G", "03G", "04G"}
    assert set(catalogo["direzioni"]) == {"Z02", "Z03"}
    assert catalogo["unita"]["A15"] == "kg/h"
    assert [s["radice"] for s in catalogo["schemi"]] == [
        "Nomination_Document",
        "NominationResponse_Document",
        "Acknowledgement_Document",
    ]
    assert catalogo["tipi_riscontro"]["294"] == "Riscontro applicativo"
    assert len(catalogo["motivazioni"]) == 63


def test_edigas_ciclo_nomina_download_e_risposta(client):
    client.post("/api/login", json={})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida())
    assert creata.status_code == 201
    corpo = creata.json()
    assert corpo["valido_xsd"] is True and corpo["periodi"] == 1
    assert len(corpo["sha256"]) == 64
    assert "xml" not in corpo

    elenco = client.get("/api/edigas/nomine").json()["nomine"]
    assert [n["id"] for n in elenco] == [corpo["id"]]

    scaricata = client.get(f"/api/edigas/nomine/{corpo['id']}/download")
    assert scaricata.status_code == 200
    assert scaricata.headers["content-type"].startswith("application/xml")
    assert "NOMINT" in scaricata.headers["content-disposition"]
    assert scaricata.text.startswith("<?xml")

    # la risposta del trasportatore cita la nomina: l'abbinamento è automatico
    risposta = _risposta_per(scaricata.text, quantita="9000")
    letta = client.post("/api/edigas/risposte", content=risposta).json()
    assert letta["nomina_trovata"] is True
    assert letta["nomina_id"] == corpo["id"]
    assert [s["esito"] for s in letta["scostamenti"]] == ["ridotto"]


def _risposta_per(nomina_xml: str, *, quantita: str, business_code: str = "16G") -> bytes:
    """Costruisce il NOMRES che un trasportatore invierebbe per quella nomina."""

    from lxml import etree

    from app import edigas

    ns_n = edigas.SCHEMI["nomina"]["namespace"]
    ns_r = edigas.SCHEMI["risposta"]["namespace"]
    origine = etree.fromstring(nomina_xml.encode("utf-8"))

    def valore(nome, radice=origine):
        el = radice.find(f"{{{ns_n}}}{nome}")
        return (el.text or "").strip() if el is not None else ""

    periodo = origine.find(f".//{{{ns_n}}}Period")
    intervallo = periodo.find(f"{{{ns_n}}}timeInterval").text
    conto_esterno = origine.find(f".//{{{ns_n}}}externalAccount").text

    radice = etree.Element(f"{{{ns_r}}}NominationResponse_Document", nsmap={None: ns_r})
    radice.set("schemaVersion", "1")

    def figlio(padre, nome, testo, **attrs):
        el = etree.SubElement(padre, f"{{{ns_r}}}{nome}")
        el.text = testo
        for k, v in attrs.items():
            el.set(k, v)
        return el

    figlio(radice, "identification", "NOMRES-TEST")
    figlio(radice, "version", "1")
    figlio(radice, "documentCode", "08G")
    figlio(radice, "creationDateTime", "2026-08-02T12:00:00Z")
    figlio(radice, "validityPeriod", valore("validityPeriod"))
    figlio(radice, "issuer_MarketParticipant.identification", valore("recipient_MarketParticipant.identification"), codingScheme="305")
    figlio(radice, "issuer_MarketParticipant.marketRole.roleCode", "ZSO")
    figlio(radice, "recipient_MarketParticipant.identification", valore("issuer_MarketParticipant.identification"), codingScheme="305")
    figlio(radice, "recipient_MarketParticipant.marketRole.roleCode", "ZSH")
    figlio(radice, "nomination_Document.identification", valore("identification"))
    figlio(radice, "nomination_Document.version", valore("version"))
    figlio(radice, "nomination_Document.documentCode", valore("documentCode"))
    interno = etree.SubElement(radice, f"{{{ns_r}}}Internal_Account")
    figlio(interno, "internalAccount", origine.find(f".//{{{ns_n}}}internalAccount").text, codingScheme="ZSO")
    punto = etree.SubElement(interno, f"{{{ns_r}}}ConnectionPoint")
    figlio(punto, "identification", origine.find(f".//{{{ns_n}}}ConnectionPoint/{{{ns_n}}}identification").text, codingScheme="305")
    figlio(punto, "measureUnit.unitOfMeasureCode", "KW1")
    esterno = etree.SubElement(punto, f"{{{ns_r}}}External_Account")
    figlio(esterno, "externalAccount", conto_esterno, codingScheme="ZSO")
    serie = etree.SubElement(esterno, f"{{{ns_r}}}InformationOrigin_TimeSeries")
    figlio(serie, "businessCode", business_code)
    periodo_r = etree.SubElement(serie, f"{{{ns_r}}}Period")
    figlio(periodo_r, "timeInterval", intervallo)
    figlio(periodo_r, "direction.gasDirectionCode", "Z02")
    figlio(periodo_r, "quantity.amount", quantita)
    return etree.tostring(radice, xml_declaration=True, encoding="UTF-8")


def test_edigas_solo_la_serie_confermata_entra_nel_confronto(client):
    """14G/15G/18G descrivono l'elaborazione, non la conferma allo shipper.

    Confrontarle produrrebbe scostamenti inesistenti su ogni risposta reale.
    """

    client.post("/api/login", json={})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    xml = client.get(f"/api/edigas/nomine/{creata['id']}/download").text

    # la serie di lavorazione del trasportatore non è una conferma
    solo_14g = client.post("/api/edigas/risposte", content=_risposta_per(xml, quantita="9000", business_code="14G")).json()
    assert [s["esito"] for s in solo_14g["scostamenti"]] == ["senza risposta"]

    # la conferma per l'intero importo non produce scostamenti
    piena = client.post("/api/edigas/risposte", content=_risposta_per(xml, quantita="12500")).json()
    assert piena["scostamenti"] == []


def test_edigas_confronta_anche_le_nomine_al_cliente_finale(client):
    """Con 04G i periodi pendono dal punto: il confronto deve vederli lo stesso."""

    client.post("/api/login", json={})
    payload = _nomina_valida()
    del payload["controparti"], payload["tipo_nomina"]
    payload.update(tipo_documento="04G", direzione="Z03", quantita_giornaliera="12500")
    creata = client.post("/api/edigas/nomine", json=payload)
    assert creata.status_code == 201, creata.json()
    xml = client.get(f"/api/edigas/nomine/{creata.json()['id']}/download").text

    letta = client.post("/api/edigas/risposte", content=_risposta_04g(xml, quantita="8000")).json()
    assert letta["nomina_trovata"] is True
    assert [s["esito"] for s in letta["scostamenti"]] == ["ridotto"]
    assert letta["scostamenti"][0]["nominato"] == "12500"


def _risposta_04g(nomina_xml: str, *, quantita: str) -> bytes:
    """NOMRES non-matching: le serie pendono dal punto, senza controparte."""

    from lxml import etree

    from app import edigas

    ns_n = edigas.SCHEMI["nomina"]["namespace"]
    ns_r = edigas.SCHEMI["risposta"]["namespace"]
    origine = etree.fromstring(nomina_xml.encode("utf-8"))

    def valore(nome):
        el = origine.find(f"{{{ns_n}}}{nome}")
        return (el.text or "").strip() if el is not None else ""

    radice = etree.Element(f"{{{ns_r}}}NominationResponse_Document", nsmap={None: ns_r})
    radice.set("schemaVersion", "1")

    def figlio(padre, nome, testo, **attrs):
        el = etree.SubElement(padre, f"{{{ns_r}}}{nome}")
        el.text = testo
        for k, v in attrs.items():
            el.set(k, v)
        return el

    figlio(radice, "identification", "NOMRES-04G-TEST")
    figlio(radice, "version", "1")
    figlio(radice, "documentCode", "08G")
    figlio(radice, "creationDateTime", "2026-08-02T12:00:00Z")
    figlio(radice, "validityPeriod", valore("validityPeriod"))
    figlio(radice, "issuer_MarketParticipant.identification", valore("recipient_MarketParticipant.identification"), codingScheme="305")
    figlio(radice, "issuer_MarketParticipant.marketRole.roleCode", "ZSO")
    figlio(radice, "recipient_MarketParticipant.identification", valore("issuer_MarketParticipant.identification"), codingScheme="305")
    figlio(radice, "recipient_MarketParticipant.marketRole.roleCode", "ZSH")
    figlio(radice, "nomination_Document.identification", valore("identification"))
    figlio(radice, "nomination_Document.version", valore("version"))
    figlio(radice, "nomination_Document.documentCode", valore("documentCode"))
    interno = etree.SubElement(radice, f"{{{ns_r}}}Internal_Account")
    figlio(interno, "internalAccount", origine.find(f".//{{{ns_n}}}internalAccount").text, codingScheme="ZSO")
    punto = etree.SubElement(interno, f"{{{ns_r}}}ConnectionPoint")
    figlio(punto, "identification", origine.find(f".//{{{ns_n}}}ConnectionPoint/{{{ns_n}}}identification").text, codingScheme="305")
    figlio(punto, "measureUnit.unitOfMeasureCode", "KW1")
    serie = etree.SubElement(punto, f"{{{ns_r}}}InformationOrigin_TimeSeries")
    figlio(serie, "businessCode", "16G")
    periodo = origine.find(f".//{{{ns_n}}}Period")
    pr = etree.SubElement(serie, f"{{{ns_r}}}Period")
    figlio(pr, "timeInterval", periodo.find(f"{{{ns_n}}}timeInterval").text)
    figlio(pr, "direction.gasDirectionCode", "Z03")
    figlio(pr, "quantity.amount", quantita)
    return etree.tostring(radice, xml_declaration=True, encoding="UTF-8")


def test_edigas_nomina_non_conforme_elenca_i_campi(client):
    client.post("/api/login", json={})
    r = client.post("/api/edigas/nomine", json=_nomina_valida(emittente_ruolo="ZUM"))
    assert r.status_code == 422
    assert any(e["field"] == "emittente_ruolo" for e in r.json()["errors"])


def test_edigas_risposte_rifiuta_file_non_pertinenti(client):
    client.post("/api/login", json={})
    assert client.post("/api/edigas/risposte", content=b"").status_code == 400
    assert client.post("/api/edigas/risposte", content=b"<rotto>").status_code == 422


def test_edigas_le_nomine_sono_isolate_per_account(client, tmp_path):
    client.post("/api/login", json={"email": "primo@azienda.it"})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    from app.main import app

    with TestClient(app) as altro:
        altro.post("/api/login", json={"email": "secondo@azienda.it"})
        assert altro.get("/api/edigas/nomine").json()["nomine"] == []
        assert altro.get(f"/api/edigas/nomine/{creata['id']}").status_code == 404
        assert altro.get(f"/api/edigas/nomine/{creata['id']}/download").status_code == 404


@pytest.mark.parametrize(
    "descrizione,modifiche",
    [
        ("versione con cifra unicode", {"versione": "²"}),
        ("versione oltre il limite di int()", {"versione": "1" * 4301}),
        ("carattere di controllo", {"conto_interno": "SHIP\x0bPER"}),
        ("byte nullo", {"identificativo": "N\x001"}),
        ("anno fuori scala", {"giorno_gas": "9999-12-31"}),
        ("istante di creazione impossibile", {"creato_il": "0001-01-01T00:00"}),
        ("dict al posto di una stringa", {"identificativo": {"a": 1}}),
        ("lista al posto di un conto", {"controparti": [{"conto": ["A"], "direzione": "Z02", "quantita_giornaliera": "1"}]}),
        ("quantità in notazione esponenziale", {"controparti": [{"conto": "C", "direzione": "Z02", "quantita_giornaliera": "1e5"}]}),
        ("quantità non finita", {"controparti": [{"conto": "C", "direzione": "Z02", "quantita_giornaliera": "nan"}]}),
    ],
)
def test_edigas_nessun_input_produce_un_errore_interno(client, descrizione, modifiche):
    """Ogni input malformato deve tornare come 422 spiegato, mai come 500."""

    client.post("/api/login", json={})
    r = client.post("/api/edigas/nomine", json={**_nomina_valida(), **modifiche})
    assert r.status_code == 422, f"{descrizione}: atteso 422, ricevuto {r.status_code}"
    assert r.json()["errore"] or r.json()["errors"]


def test_edigas_risposta_con_versione_non_numerica_non_rompe(client):
    """La versione citata la scrive il trasportatore: un refuso non è un 500."""

    client.post("/api/login", json={})
    ns = "urn:easee-gas.eu:edigas:BrpNominationAndMatching:NominationResponseDocument:6:1"
    xml = (
        f'<?xml version="1.0"?><NominationResponse_Document xmlns="{ns}">'
        "<identification>X</identification>"
        "<nomination_Document.version>1.0</nomination_Document.version>"
        "</NominationResponse_Document>"
    ).encode()
    r = client.post("/api/edigas/risposte", content=xml)
    assert r.status_code == 200
    assert r.json()["nomina_trovata"] is False
    assert r.json()["valido_xsd"] is False


def test_edigas_corpo_troppo_grande_respinto_prima_di_leggerlo(client):
    client.post("/api/login", json={})
    r = client.post(
        "/api/edigas/nomine",
        content=b"{}",
        headers={"Content-Length": "999999999", "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_edigas_controparte_ripetuta_respinta(client):
    """Due voci con lo stesso conto genererebbero periodi doppi sullo stesso intervallo."""

    client.post("/api/login", json={})
    payload = _nomina_valida(
        controparti=[
            {"conto": "CTP", "direzione": "Z02", "quantita_giornaliera": "100"},
            {"conto": "CTP", "direzione": "Z02", "quantita_giornaliera": "900"},
        ]
    )
    r = client.post("/api/edigas/nomine", json=payload)
    assert r.status_code == 422
    assert any("compare più volte" in e["message"] for e in r.json()["errors"])


def test_edigas_i_metadati_salvati_sono_quelli_normalizzati(client):
    """Il record deve descrivere il file prodotto, non il payload ricevuto."""

    client.post("/api/login", json={})
    creata = client.post(
        "/api/edigas/nomine",
        json=_nomina_valida(giorno_gas="2026-08-03", punto_eic=" 21Z0000000001234 "),
    ).json()
    assert creata["giorno_gas"] == "2026-08-03"
    assert creata["punto"] == "21Z0000000001234"


def _acknow_per(nomina_xml: str, *, motivo: str = "01G", nota: str = "Presa in carico") -> bytes:
    """Il riscontro che il trasportatore invierebbe per quella nomina."""

    from lxml import etree

    from app import edigas

    ns_n = edigas.SCHEMI["nomina"]["namespace"]
    origine = etree.fromstring(nomina_xml.encode("utf-8"))

    def valore(nome):
        el = origine.find(f"{{{ns_n}}}{nome}")
        return (el.text or "").strip() if el is not None else ""

    documento = edigas.genera_riscontro({
        "identificativo": f"ACKNOW-{valore('identification')}",
        "tipo_documento": "294",
        # il trasportatore riscontra allo shipper: ruoli invertiti rispetto alla nomina
        "emittente_eic": valore("recipient_MarketParticipant.identification"),
        "emittente_ruolo": "ZSO",
        "destinatario_eic": valore("issuer_MarketParticipant.identification"),
        "destinatario_ruolo": "ZSH",
        "documento_riscontrato": {
            "identificativo": valore("identification"),
            "versione": valore("version"),
            "tipo_documento": valore("documentCode"),
            "creato_il": valore("creationDateTime"),
        },
        "motivazioni": [{"codice": motivo, "nota": nota}],
    })
    return documento.xml.encode("utf-8")


def test_edigas_riscontro_si_collega_alla_nomina(client):
    """Il ciclo completo: nomina inviata, riscontro ricevuto, prova conservata."""

    client.post("/api/login", json={})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    xml = client.get(f"/api/edigas/nomine/{creata['id']}/download").text

    r = client.post("/api/edigas/riscontri", content=_acknow_per(xml))
    assert r.status_code == 201, r.json()
    corpo = r.json()
    assert corpo["valido_xsd"] is True
    assert corpo["accettato"] is True
    assert corpo["nomina_trovata"] is True
    assert corpo["nomina_id"] == creata["id"]
    assert corpo["motivazioni"][0]["descrizione"] == "Elaborato e accettato"

    elenco = client.get("/api/edigas/riscontri").json()["riscontri"]
    assert len(elenco) == 1
    assert elenco[0]["nomina_id"] == creata["id"]
    assert elenco[0]["accettato"] is True


def test_edigas_riscontro_di_rifiuto(client):
    client.post("/api/login", json={})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    xml = client.get(f"/api/edigas/nomine/{creata['id']}/download").text
    r = client.post("/api/edigas/riscontri", content=_acknow_per(xml, motivo="14G", nota="conto ignoto"))
    assert r.json()["accettato"] is False
    assert r.json()["motivazioni"][0]["descrizione"] == "Conto non riconosciuto"


def test_edigas_lo_stesso_riscontro_non_si_duplica(client):
    """Reimportare lo stesso file è lo stesso fatto, non un secondo riscontro."""

    client.post("/api/login", json={})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    xml = client.get(f"/api/edigas/nomine/{creata['id']}/download").text
    ack = _acknow_per(xml)
    primo = client.post("/api/edigas/riscontri", content=ack)
    secondo = client.post("/api/edigas/riscontri", content=ack)
    assert primo.status_code == 201 and secondo.status_code == 200
    assert secondo.json()["gia_importato"] is True
    assert len(client.get("/api/edigas/riscontri").json()["riscontri"]) == 1


def test_edigas_riscontro_di_una_nomina_sconosciuta(client):
    """Un riscontro che cita un documento non nostro si legge comunque."""

    client.post("/api/login", json={})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    xml = client.get(f"/api/edigas/nomine/{creata['id']}/download").text
    ack = _acknow_per(xml).replace(b"<receiving_Document.identification>NOMINT-20260803-001",
                                   b"<receiving_Document.identification>ALTRA-NOMINA")
    r = client.post("/api/edigas/riscontri", content=ack)
    assert r.status_code == 201
    assert r.json()["nomina_trovata"] is False
    assert r.json()["accettato"] is True


def test_edigas_riscontri_richiedono_la_sessione(client):
    assert client.get("/api/edigas/riscontri").status_code == 401
    assert client.post("/api/edigas/riscontri", content=b"<x/>").status_code == 401


def test_edigas_riscontri_isolati_per_account(client):
    client.post("/api/login", json={"email": "primo@azienda.it"})
    creata = client.post("/api/edigas/nomine", json=_nomina_valida()).json()
    xml = client.get(f"/api/edigas/nomine/{creata['id']}/download").text
    client.post("/api/edigas/riscontri", content=_acknow_per(xml))
    from app.main import app

    with TestClient(app) as altro:
        altro.post("/api/login", json={"email": "secondo@azienda.it"})
        assert altro.get("/api/edigas/riscontri").json()["riscontri"] == []


def test_edigas_riscontro_malformato_non_rompe(client):
    client.post("/api/login", json={})
    assert client.post("/api/edigas/riscontri", content=b"").status_code == 400
    assert client.post("/api/edigas/riscontri", content=b"<rotto>").status_code == 422
    # un NOMRES non è un ACKNOW
    assert client.post("/api/edigas/riscontri", content=b'<?xml version="1.0"?><Altro/>').status_code == 422
