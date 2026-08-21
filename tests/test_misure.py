"""Test del modulo misure dei PDR da SIICloud (WebDAV Nextcloud).

Le chiamate di rete sono sempre finte: il modulo non tocca mai internet
durante i test. Le credenziali non devono mai essere salvate.
"""

import urllib.error

import pytest

from app import misure


# ------------------------------------------------------------- classificatore


def test_classifica_riconosce_le_giornaliere():
    assert misure.classifica("TGL_20260101.xml") == "giornaliera"
    assert misure.classifica("tgl_file.xml") == "giornaliera"


def test_classifica_riconosce_le_mensili():
    assert misure.classifica("TMG_123_2026.xml") == "mensile"
    assert misure.classifica("TML_456.xml") == "mensile"
    assert misure.classifica("tmg_x.xml") == "mensile"


def test_classifica_ignora_nomi_sconosciuti_o_vuoti():
    assert misure.classifica("elenco.txt") is None
    assert misure.classifica("README.xml") is None
    assert misure.classifica("") is None
    assert misure.classifica(None) is None


def test_classifica_toglie_spazi_e_non_sensibile_al_caso():
    assert misure.classifica("  TGL_x.xml  ") == "giornaliera"


# ------------------------------------------------------------- validazione


def test_credenziali_mancanti_sollevano_errori_di_campo():
    with pytest.raises(misure.MisureError) as errore:
        misure._valida_credenziali("", "", "")
    campi = {e["field"] for e in errore.value.errors}
    assert campi == {"url", "utente", "password"}


def test_url_senza_schema_viene_rifiutato():
    with pytest.raises(misure.MisureError) as errore:
        misure._valida_credenziali("ftp://server", "u", "p")
    assert errore.value.errors[0]["field"] == "url"


def test_credenziali_valide_non_sollevano_nulla():
    misure._valida_credenziali("https://cloud.example.com/dav/", "utente", "segreto")


# ------------------------------------------------------------- costruzione url


def test_url_percorso_aggiunge_barra_e_quota():
    base = "https://cloud.example.com/remote.php/dav/files/utente"
    assert misure._url_percorso(base, "") == base + "/"
    assert misure._url_percorso(base, "TMG_123/2026") == base + "/TMG_123/2026"


def test_url_percorso_quota_caratteri_speciali():
    base = "https://cloud.example.com/dav/"
    ottenuto = misure._url_percorso(base, "cartella con spazio/file.xml")
    assert "cartella%20con%20spazio" in ottenuto
    assert ottenuto.endswith("/file.xml")


# ------------------------------------------------------------- multistatus


MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/utente/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/utente/TMG_123/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/utente/TGL_20260101.xml</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>1234</d:getcontentlength>
      <d:getlastmodified>Mon, 01 Jan 2026 08:00:00 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/utente/TMG_202601.xml</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>99</d:getcontentlength>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""


def test_parse_multistatus_salta_se_stesso_e_classifica():
    voci = misure._parse_multistatus(MULTISTATUS, "/remote.php/dav/files/utente/")
    nomi = [v["nome"] for v in voci]
    assert nomi == ["TMG_123", "TGL_20260101.xml", "TMG_202601.xml"]

    cartella, giornaliera, mensile = voci
    assert cartella["cartella"] is True and cartella["tipo"] is None
    assert giornaliera["tipo"] == "giornaliera" and giornaliera["dimensione"] == 1234
    assert mensile["tipo"] == "mensile" and mensile["dimensione"] == 99
    assert giornaliera["percorso"] == "TGL_20260101.xml"


def test_parse_multistatus_xml_non_valido():
    with pytest.raises(misure.MisureError) as errore:
        misure._parse_multistatus(b"non xml", "/dav/")
    assert "multistatus" in str(errore.value)


# ------------------------------------------------------------- riassunto xml


XML_MISURE = b"""<?xml version="1.0"?>
<Misure>
  <Record><PDR>123</PDR><Data>2026-01-01</Data><Consumo>100</Consumo></Record>
  <Record><PDR>124</PDR><Data>2026-01-02</Data><Consumo>200</Consumo></Record>
</Misure>"""


def test_riassumi_xml_individua_record_e_campi():
    esito = misure.riassumi_xml(XML_MISURE)
    assert esito["radice"] == "Misure"
    assert esito["tag_record"] == "Record"
    assert esito["numero_record"] == 2
    assert esito["campi"] == ["PDR", "Data", "Consumo"]
    assert esito["record"][0] == {"PDR": "123", "Data": "2026-01-01", "Consumo": "100"}


def test_riassumi_xml_gestisce_namespace():
    xml = b'<root xmlns="http://example.com"><rec><a>1</a></rec></root>'
    esito = misure.riassumi_xml(xml)
    assert esito["radice"] == "root"
    assert esito["tag_record"] == "rec"
    assert esito["campi"] == ["a"]


def test_riassumi_xml_senza_record():
    esito = misure.riassumi_xml(b"<vuoto/>")
    assert esito["tag_record"] is None
    assert esito["numero_record"] == 0
    assert esito["record"] == []


def test_riassumi_xml_non_valido():
    with pytest.raises(misure.MisureError) as errore:
        misure.riassumi_xml(b"<rotto>")
    assert "XML valido" in str(errore.value)


def test_riassumi_xml_limita_i_record_mostrati():
    righe = "".join(f"<r><v>{i}</v></r>" for i in range(misure.MAX_RECORD + 50))
    esito = misure.riassumi_xml(f"<t>{righe}</t>".encode())
    assert esito["numero_record"] == misure.MAX_RECORD + 50
    assert len(esito["record"]) == misure.MAX_RECORD


# ------------------------------------------------------------- richieste finte


class _RispostaFinta:
    def __init__(self, corpo):
        self._corpo = corpo

    def read(self, limite=-1):
        return self._corpo if limite < 0 else self._corpo[:limite]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_richiesta_invia_autenticazione_basic(monkeypatch):
    catturata = {}

    def urlopen_finto(richiesta, timeout=None):
        catturata["auth"] = richiesta.get_header("Authorization")
        catturata["metodo"] = richiesta.get_method()
        return _RispostaFinta(b"ok")

    monkeypatch.setattr(misure.urllib.request, "urlopen", urlopen_finto)
    corpo = misure._richiesta("https://x/dav/", "PROPFIND", "utente", "segreto")
    assert corpo == b"ok"
    assert catturata["metodo"] == "PROPFIND"
    assert catturata["auth"].startswith("Basic ")


def test_richiesta_traduce_errore_http_401(monkeypatch):
    def urlopen_finto(*args, **kwargs):
        raise urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(misure.urllib.request, "urlopen", urlopen_finto)
    with pytest.raises(misure.MisureError) as errore:
        misure._richiesta("https://x/dav/", "PROPFIND", "u", "p")
    assert "credenziali rifiutate" in str(errore.value)


def test_richiesta_traduce_server_irraggiungibile(monkeypatch):
    def urlopen_finto(*args, **kwargs):
        raise urllib.error.URLError("dns fallito")

    monkeypatch.setattr(misure.urllib.request, "urlopen", urlopen_finto)
    with pytest.raises(misure.MisureError) as errore:
        misure._richiesta("https://x/dav/", "GET", "u", "p")
    assert "non raggiungibile" in str(errore.value)


# ------------------------------------------------------------- sistema


def test_sistema_elenca_restituisce_voci_e_nota(monkeypatch):
    monkeypatch.setattr(misure, "elenca", lambda *a, **k: [{"nome": "TGL_x.xml"}])
    esito = misure.sistema({
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
        "percorso": "TMG_123", "azione": "elenca",
    })
    assert esito["fonte"]["origine"] == "SIICloud (WebDAV)"
    assert esito["fonte"]["percorso"] == "TMG_123"
    assert esito["voci"][0]["nome"] == "TGL_x.xml"
    assert "TGL" in esito["nota"]


def test_sistema_apri_restituisce_file_e_contenuto(monkeypatch):
    monkeypatch.setattr(misure, "scarica_file", lambda *a, **k: XML_MISURE)
    esito = misure.sistema({
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
        "percorso": "TMG_123/TGL_20260101.xml", "azione": "apri",
    })
    assert esito["file"]["nome"] == "TGL_20260101.xml"
    assert esito["file"]["tipo"] == "giornaliera"
    assert esito["contenuto"]["numero_record"] == 2


def test_sistema_azione_non_valida():
    with pytest.raises(misure.MisureError) as errore:
        misure.sistema({
            "url": "https://x/dav/", "utente": "u", "password": "p", "azione": "cancella",
        })
    assert "azione non valida" in str(errore.value)


def test_sistema_credenziali_mancanti():
    with pytest.raises(misure.MisureError) as errore:
        misure.sistema({"azione": "elenca"})
    assert errore.value.errors


def test_sistema_accetta_dati_non_dict():
    with pytest.raises(misure.MisureError):
        misure.sistema(None)


def test_scarica_file_senza_percorso():
    with pytest.raises(misure.MisureError) as errore:
        misure.scarica_file("https://x/dav/", "u", "p", "")
    assert "percorso" in str(errore.value)


# ------------------------------------------------------------- rotte HTTP


def test_la_rotta_richiede_una_sessione(client):
    assert client.post("/api/misure", json={}).status_code == 401


def test_ciclo_elenca_via_http(client, monkeypatch):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    monkeypatch.setattr(misure, "elenca", lambda *a, **k: [
        {"nome": "TGL_x.xml", "percorso": "TGL_x.xml", "cartella": False,
         "dimensione": 10, "modificato": "", "tipo": "giornaliera"},
    ])
    risposta = client.post("/api/misure", json={
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
        "azione": "elenca",
    })
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["fonte"]["origine"] == "SIICloud (WebDAV)"
    assert corpo["voci"][0]["tipo"] == "giornaliera"


def test_errori_di_campo_tornano_come_422(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post("/api/misure", json={"azione": "elenca"})
    assert risposta.status_code == 422
    assert risposta.json()["errors"]


def test_corpo_non_oggetto_viene_rifiutato(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    assert client.post("/api/misure", json=["lista"]).status_code == 400


def test_un_corpo_enorme_viene_rifiutato_prima_di_leggerlo(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post(
        "/api/misure",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(misure.MAX_CORPO_BYTES + 1)},
    )
    assert risposta.status_code == 413
