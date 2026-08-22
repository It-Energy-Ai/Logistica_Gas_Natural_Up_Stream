"""Verifiche delle difese, non delle funzioni.

Le protezioni di questo progetto — parser XML senza entità né rete, rifiuto dei
caratteri non codificabili, tetti sui corpi, isolamento per account, immutabilità
delle prove — esistono già nel codice, ma finora nessun test le presidiava: chi
domani togliesse ``_parser_sicuro()`` avrebbe visto la suite restare verde.

Ogni test qui dentro fallisce se una difesa viene rimossa.
"""

from __future__ import annotations

import base64
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import edigas, pdr


# ─────────────────────────────── XXE ed entità XML

# Legge un file del sistema: se il parser risolvesse le entità esterne, il suo
# contenuto finirebbe dentro il documento (e quindi nella risposta dell'API).
XXE_FILE = """<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<NominationResponse_Document xmlns="urn:easee-gas.eu:edigas:BrpNominationAndMatching:NominationResponseDocument:6:1">
  <identification>&xxe;</identification>
</NominationResponse_Document>"""

# Chiede una risorsa in rete: un parser che la scarica espone l'host a SSRF.
XXE_RETE = """<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY xxe SYSTEM "http://127.0.0.1:1/segreto">]>
<Acknowledgement_Document xmlns="urn:easee-gas.eu:edigas:General:AcknowledgementDocument:6:1">
  <identification>&xxe;</identification>
</Acknowledgement_Document>"""

# "Billion laughs": espansione esponenziale che satura la memoria.
BOMBA = """<?xml version="1.0"?>
<!DOCTYPE r [
 <!ENTITY a "aaaaaaaaaa">
 <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
 <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
 <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
 <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">
 <!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">
]>
<NominationResponse_Document xmlns="urn:easee-gas.eu:edigas:BrpNominationAndMatching:NominationResponseDocument:6:1">
  <identification>&f;</identification>
</NominationResponse_Document>"""


@pytest.mark.parametrize("nome,documento", [("entità su file", XXE_FILE), ("entità di rete", XXE_RETE), ("bomba di entità", BOMBA)])
def test_i_lettori_edigas_non_espandono_entita(nome, documento):
    """Nessun contenuto esterno deve entrare nel documento letto."""

    for lettore in (edigas.leggi_risposta, edigas.leggi_riscontro):
        try:
            letto = lettore(documento.encode())
        except edigas.EdigasError:
            continue  # rifiutato: va benissimo
        # se è stato accettato, l'entità non deve essere stata risolta
        assert "root:" not in str(letto), f"{nome}: contenuto di sistema trapelato"
        assert "aaaaaaaaaa" not in letto.get("identificativo", ""), f"{nome}: entità espansa"


def test_il_parser_del_modulo_ha_le_difese_attive():
    """La configurazione va verificata, non dedotta dai default della libreria."""

    from lxml import etree

    parser = edigas._parser_sicuro()
    assert isinstance(parser, etree.XMLParser)
    # un documento con DTD ed entità non deve risolverle
    radice = etree.fromstring(XXE_FILE.encode(), parser)
    testo = "".join(radice.itertext())
    assert "root:" not in testo


def test_anche_le_ricevute_pdr_rifiutano_le_entita():
    """Il registro delle ricevute è l'altro ingresso di XML di terzi."""

    ack = """<?xml version="1.0"?>
    <!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <PIPEFunctionalAcknowledgement><Status>&xxe;</Status></PIPEFunctionalAcknowledgement>"""
    letto = pdr._parse_pdr_functional_ack(ack.encode())
    if letto:
        assert "root:" not in str(letto)


# ─────────────────────────────── payload JSON ostili


@pytest.fixture()
def sessione(client):
    client.post("/api/login", json={"email": "sicurezza@azienda.it"})
    return client


def test_lo_stato_rifiuta_i_surrogati_non_codificabili(sessione):
    """Superano il parser JSON ma non l'UTF-8: senza controllo era un 500."""

    r = sessione.request(
        "PUT", "/api/state",
        content=b'{"cfg": {"a": "\\ud800"}}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert "codificabili" in r.json()["errore"]


@pytest.mark.parametrize(
    "corpo",
    [b"[1,2,3]", b'"stringa"', b"42", b"null", b"true", b"{", b"", b"\xff\xfe non-utf8"],
)
def test_nessun_corpo_malformato_abbatte_il_login(client, corpo):
    r = client.post("/api/login", content=corpo, headers={"Content-Type": "application/json"})
    assert r.status_code < 500, f"corpo {corpo!r} ha prodotto {r.status_code}"


def test_le_chiavi_di_stato_sconosciute_sono_respinte_in_blocco(sessione):
    """Tutto-o-niente: una chiave estranea non passa insieme a quelle buone."""

    r = sessione.request("PUT", "/api/state", json={"demoMode": True, "__proto__": {"x": 1}})
    assert r.status_code == 422
    assert sessione.get("/api/state").json().get("demoMode") is not True


@pytest.mark.parametrize(
    "patch",
    [
        {"cfg": {"a" * 80: "x"}},                       # nome chiave oltre il limite
        {"cfg": {"a": "v" * 80}},                       # valore oltre il limite
        {"cfg": {f"k{i}": "v" for i in range(300)}},    # troppe chiavi
        {"nextP": 99999},                               # intero fuori scala
        {"nomList": [{"punto": "P"}]},                  # forma incompleta
        {"extraPunti": [["solo", "due"]]},              # arità sbagliata
    ],
)
def test_forme_non_valide_dello_stato_respinte(sessione, patch):
    assert sessione.request("PUT", "/api/state", json=patch).status_code == 422


# ─────────────────────────────── limiti e risorse


def test_i_corpi_smisurati_sono_respinti_prima_di_essere_letti(sessione):
    """Il rifiuto guarda Content-Length: non si bufferizzano megabyte per dire di no."""

    for url in ("/api/edigas/nomine", "/api/edigas/risposte", "/api/edigas/riscontri"):
        r = sessione.post(url, content=b"{}", headers={"Content-Length": "999999999", "Content-Type": "application/json"})
        assert r.status_code == 413, url


def test_la_ricevuta_oltre_il_limite_e_respinta(sessione):
    troppo = base64.b64encode(b"x" * (pdr.MAX_RECEIPT_BYTES + 1024)).decode()
    r = sessione.post("/api/pdr/receipts/import", json={"report_id": "x", "content_base64": troppo, "filename": "r.xml"})
    assert r.status_code in (404, 422)
    assert r.status_code != 500


def test_una_nomina_enorme_non_blocca_il_server(sessione):
    """Il tetto sui periodi va applicato prima di espanderli tutti."""

    import time

    base = {
        "identificativo": "DOS-1", "versione": 1, "tipo_documento": "01G", "tipo_nomina": "A02",
        "emittente_eic": "21X000000001234A", "emittente_ruolo": "ZSH",
        "destinatario_eic": "21X00000000567KB", "destinatario_ruolo": "ZSO",
        "giorno_gas": "2026-08-03", "conto_interno": "S1",
        "punto_eic": "21Z0000000001234", "unita": "KW1",
        "controparti": [{"conto": f"C{i}", "direzione": "Z02", "quantita_giornaliera": "1"} for i in range(5000)],
    }
    avvio = time.monotonic()
    r = sessione.post("/api/edigas/nomine", json=base)
    durata = time.monotonic() - avvio
    assert r.status_code == 422
    assert durata < 10, f"rifiuto troppo lento: {durata:.1f}s"


# ─────────────────────────────── isolamento e prove


def test_nessun_dato_attraversa_gli_account(client):
    """Un account non deve poter leggere né indovinare le risorse di un altro."""

    from app.main import app

    client.post("/api/login", json={"email": "primo@azienda.it"})
    nomina = client.post("/api/edigas/nomine", json={
        "identificativo": "PRIVATA-1", "versione": 1, "tipo_documento": "01G", "tipo_nomina": "A02",
        "emittente_eic": "21X000000001234A", "emittente_ruolo": "ZSH",
        "destinatario_eic": "21X00000000567KB", "destinatario_ruolo": "ZSO",
        "giorno_gas": "2026-08-03", "conto_interno": "S1", "punto_eic": "21Z0000000001234",
        "unita": "KW1", "controparti": [{"conto": "C", "direzione": "Z02", "quantita_giornaliera": "1"}],
    }).json()

    with TestClient(app) as altro:
        altro.post("/api/login", json={"email": "secondo@azienda.it"})
        assert altro.get("/api/edigas/nomine").json()["nomine"] == []
        assert altro.get(f"/api/edigas/nomine/{nomina['id']}").status_code == 404
        assert altro.get(f"/api/edigas/nomine/{nomina['id']}/download").status_code == 404
        assert altro.get("/api/remit/reports").json()["reports"] == []


@pytest.mark.parametrize(
    "identificativo",
    ["../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "' OR '1'='1", "%00", "x' UNION SELECT 1--"],
)
def test_identificativi_ostili_non_raggiungono_nulla(sessione, identificativo):
    """Path traversal e injection sui parametri di percorso."""

    for base in ("/api/edigas/nomine/", "/api/remit/reports/", "/api/remit/artifacts/"):
        r = sessione.get(base + identificativo)
        assert r.status_code in (404, 422), f"{base}{identificativo} → {r.status_code}"
        assert "root:" not in r.text


def test_le_ricevute_restano_immutabili(sessione):
    """L'immutabilità è una garanzia del prodotto: va presidiata da un test."""

    from app import db

    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pdr_ricevuta (id, email, report_id, artifact_id, artifact_sha256, fonte, esito, "
                "segnalata_il, nome_file, media_type, contenuto, sha256, connettore_verificato, importata_il) "
                "VALUES ('x','e','r','a','h','pdr','pdr_accepted','2026-08-02','f.xml','application/xml', "
                f"x'00', '{'a' * 64}', 1, '2026-08-02')"
            )


def test_i_riscontri_edigas_non_si_duplicano(sessione):
    """La stessa prova importata due volte resta un fatto solo."""

    from app import db

    documento = edigas.genera_riscontro({
        "identificativo": "ACK-DUP", "tipo_documento": "294",
        "emittente_eic": "21X00000000567KB", "emittente_ruolo": "ZSO",
        "destinatario_eic": "21X000000001234A", "destinatario_ruolo": "ZSH",
        "documento_riscontrato": {"identificativo": "N-1"},
        "motivazioni": [{"codice": "01G"}],
    })
    primo = sessione.post("/api/edigas/riscontri", content=documento.xml.encode())
    secondo = sessione.post("/api/edigas/riscontri", content=documento.xml.encode())
    assert primo.status_code == 201 and secondo.status_code == 200
    assert secondo.json()["gia_importato"] is True
    assert len(sessione.get("/api/edigas/riscontri").json()["riscontri"]) == 1


# ─────────────────────────────── intestazioni e sessione


def test_le_difese_del_browser_sono_sempre_dichiarate(client):
    intestazioni = client.get("/").headers
    assert intestazioni["x-content-type-options"] == "nosniff"
    assert intestazioni["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in intestazioni["content-security-policy"]
    assert "default-src 'self'" in intestazioni["content-security-policy"]
    assert intestazioni["cache-control"] == "no-store"


def test_il_cookie_di_sessione_non_e_leggibile_da_javascript(client):
    r = client.post("/api/login", json={"email": "a@b.it"})
    cookie = r.headers.get("set-cookie", "")
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_senza_sessione_ogni_via_e_chiusa(client):
    for metodo, url in [
        ("get", "/api/state"), ("put", "/api/state"),
        ("get", "/api/remit/reports"), ("post", "/api/remit/reports"),
        ("get", "/api/edigas/nomine"), ("post", "/api/edigas/nomine"),
        ("get", "/api/edigas/riscontri"), ("post", "/api/edigas/riscontri"),
        ("get", "/api/pdr"), ("get", "/api/pdr/receipts"),
    ]:
        assert getattr(client, metodo)(url).status_code == 401, url


def test_l_invio_reale_resta_bloccato(sessione):
    """Il blocco non è un difetto: è la garanzia che nulla parta di nascosto."""

    r = sessione.post("/api/remit/reports/qualsiasi/submit")
    assert r.status_code in (404, 409)
    assert r.status_code != 200


# ─────────────────────────────── configurazione e ambiente


def test_avviare_in_produzione_senza_password_e_impedito():
    """In modalità server senza password l'avvio si ferma: meglio un errore
    chiaro che un login aperto esposto sulla rete."""

    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "VETTORE_PASSWORD"}
    esito = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=__import__("pathlib").Path(__file__).resolve().parent.parent,
        env={**env, "VETTORE_ENV": "production"},
        capture_output=True, text=True,
    )
    assert esito.returncode != 0
    assert "VETTORE_PASSWORD" in esito.stderr + esito.stdout


def test_avviare_in_produzione_con_password_e_ammesso():
    """Con la password aziendale configurata la modalità server parte."""

    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as cartella:
        esito = subprocess.run(
            [sys.executable, "-c", "import app.main; print('avviato')"],
            cwd=__import__("pathlib").Path(__file__).resolve().parent.parent,
            env={
                **os.environ,
                "VETTORE_ENV": "production",
                "VETTORE_PASSWORD": "segreta-di-prova",
                "VETTORE_DB": str(__import__("pathlib").Path(cartella) / "vettore.db"),
            },
            capture_output=True, text=True,
        )
    assert esito.returncode == 0, esito.stderr
    assert "avviato" in esito.stdout


# ─────────────────────────────── login con password (modalità server)


def test_login_senza_password_in_locale_funziona(sessione):
    """In modalità locale la password non è richiesta: basta l'email."""

    r = sessione.post("/api/login", json={"email": "shipper@esempio.it"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_password_corretta_senza_password_server():
    from app import main

    assert main._password_corretta("qualsiasi cosa") is True


def test_login_con_password_sbagliata_dà_401(sessione, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "PASSWORD_SERVER", "segreta123")
    r = sessione.post(
        "/api/login",
        json={"email": "shipper@esempio.it", "password": "sbagliata"},
    )
    assert r.status_code == 401
    assert "Password errata" in r.json()["errore"]
    # nessuna sessione deve essere stata creata
    assert "vettore_session" not in r.cookies


def test_login_con_password_giusta_crea_la_sessione(sessione, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "PASSWORD_SERVER", "segreta123")
    r = sessione.post(
        "/api/login",
        json={"email": "shipper@esempio.it", "password": "segreta123"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "vettore_session" in r.cookies


def test_cookie_secure_solo_in_modalità_server(sessione, monkeypatch):
    from app import main

    # modalità locale: niente flag Secure (si naviga su http://127.0.0.1)
    r = sessione.post("/api/login", json={"email": "shipper@esempio.it"})
    assert "Secure" not in r.headers.get("set-cookie", "")

    # modalità server: il cookie deve viaggiare solo su HTTPS
    monkeypatch.setattr(main, "MODALITA_SERVER", True)
    monkeypatch.setattr(main, "PASSWORD_SERVER", "segreta123")
    r = sessione.post(
        "/api/login",
        json={"email": "shipper@esempio.it", "password": "segreta123"},
    )
    assert "Secure" in r.headers.get("set-cookie", "")


def test_la_durata_della_sessione_e_configurabile():
    from app import main

    assert 1 <= main.GIORNI_SESSIONE <= 365


# ─────────────────────────────── assert in produzione


def test_nessun_assert_rimane_nel_codice_applicativo():
    """Gli assert spariscono con `python -O`: le difese devono essere raise.

    Presidia la conversione fatta nella review: se qualcuno reintroduce un
    assert in app/, questo test lo segnala subito.
    """

    import pathlib
    import re

    violazioni = []
    for percorso in sorted((pathlib.Path(__file__).resolve().parent.parent / "app").glob("*.py")):
        for numero, riga in enumerate(percorso.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^\s*assert\b", riga):
                violazioni.append(f"{percorso.name}:{numero}: {riga.strip()}")
    assert violazioni == [], "\n".join(violazioni)


def test_genera_xml_acer_rifiuta_senza_lxml():
    """Se lxml manca, l'export ACER dà un errore chiaro, non un AttributeError."""

    from app import acer_xml

    originale = acer_xml.etree
    acer_xml.etree = None
    try:
        with pytest.raises(acer_xml.AcerXmlError, match="lxml"):
            acer_xml.genera_xml({})
    finally:
        acer_xml.etree = originale
