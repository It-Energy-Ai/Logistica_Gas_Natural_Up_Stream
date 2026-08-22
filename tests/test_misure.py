"""Test del modulo misure dei PDR da SIICloud (WebDAV Nextcloud).

Le chiamate di rete sono sempre finte: il modulo non tocca mai internet
durante i test. L'accesso salvato resta solo nel database locale.
"""

import io
import urllib.error
import zipfile

import pytest

from app import db, misure


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


def test_classifica_riconosce_i_nomi_reali_dei_flussi():
    # Nomi reali pubblicati dal distributore: il tipo è un token centrale.
    assert misure.classifica("00489490011_09080630966_202512_TGL_20260102120702_1_M.zip") == "giornaliera"
    assert misure.classifica("00489490011_09080630966_202512_TMV_20260102120702_1.zip") == "mensile"
    assert misure.classifica("00489490011_09080630966_202512_SWG1_20260102120702_1.zip") == "mensile"
    assert misure.classifica("00489490011_09080630966_202604_IGMG_20260416090000_1.zip") == "cambio"


def test_classifica_prefisso_cartella_tmg():
    assert misure.classifica("TMG_00489490011_09080630966") == "mensile"


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

    def open_finto(richiesta, timeout=None):
        catturata["auth"] = richiesta.get_header("Authorization")
        catturata["metodo"] = richiesta.get_method()
        return _RispostaFinta(b"ok")

    monkeypatch.setattr(misure, "_verifica_destinazione", lambda url: None)
    monkeypatch.setattr(misure._OPENER, "open", open_finto)
    corpo = misure._richiesta("https://x/dav/", "PROPFIND", "utente", "segreto")
    assert corpo == b"ok"
    assert catturata["metodo"] == "PROPFIND"
    assert catturata["auth"].startswith("Basic ")


def test_richiesta_traduce_errore_http_401(monkeypatch):
    def open_finto(*args, **kwargs):
        raise urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(misure, "_verifica_destinazione", lambda url: None)
    monkeypatch.setattr(misure._OPENER, "open", open_finto)
    with pytest.raises(misure.MisureError) as errore:
        misure._richiesta("https://x/dav/", "PROPFIND", "u", "p")
    assert "credenziali rifiutate" in str(errore.value)


def test_richiesta_traduce_server_irraggiungibile(monkeypatch):
    def open_finto(*args, **kwargs):
        raise urllib.error.URLError("dns fallito")

    monkeypatch.setattr(misure, "_verifica_destinazione", lambda url: None)
    monkeypatch.setattr(misure._OPENER, "open", open_finto)
    with pytest.raises(misure.MisureError) as errore:
        misure._richiesta("https://x/dav/", "GET", "u", "p")
    assert "non raggiungibile" in str(errore.value)


# ------------------------------------------------------------- anti-SSRF

def _finto_dns(monkeypatch, ip):
    def getaddrinfo_finto(host, porta, proto=0):
        return [(2, 1, proto, "", (ip, porta))]

    monkeypatch.setattr(misure.socket, "getaddrinfo", getaddrinfo_finto)


def test_ssrf_blocca_loopback(monkeypatch):
    _finto_dns(monkeypatch, "127.0.0.1")
    with pytest.raises(misure.MisureError) as errore:
        misure._verifica_destinazione("https://siicloud.example/dav/")
    assert "rete privata o locale" in str(errore.value)


def test_ssrf_blocca_metadata_link_local(monkeypatch):
    _finto_dns(monkeypatch, "169.254.169.254")
    with pytest.raises(misure.MisureError) as errore:
        misure._verifica_destinazione("http://siicloud.example/dav/")
    assert "rete privata o locale" in str(errore.value)


def test_ssrf_blocca_reti_private(monkeypatch):
    for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.9", "::1"):
        _finto_dns(monkeypatch, ip)
        with pytest.raises(misure.MisureError):
            misure._verifica_destinazione("https://siicloud.example/dav/")


def test_ssrf_blocca_schemi_non_http():
    with pytest.raises(misure.MisureError) as errore:
        misure._verifica_destinazione("ftp://siicloud.example/dav/")
    assert "solo gli indirizzi http" in str(errore.value)


def test_ssrf_host_mancante():
    with pytest.raises(misure.MisureError) as errore:
        misure._verifica_destinazione("https:///dav/")
    assert "host mancante" in str(errore.value)


def test_ssrf_host_non_risolvibile(monkeypatch):
    def getaddrinfo_finto(host, porta, proto=0):
        raise misure.socket.gaierror("nome sconosciuto")

    monkeypatch.setattr(misure.socket, "getaddrinfo", getaddrinfo_finto)
    with pytest.raises(misure.MisureError) as errore:
        misure._verifica_destinazione("https://inesistente.example/dav/")
    assert "non risolvibile" in str(errore.value)


def test_ssrf_ammette_host_pubblico(monkeypatch):
    _finto_dns(monkeypatch, "93.184.216.34")
    misure._verifica_destinazione("https://siicloud.example/dav/")


def test_ssrf_redirect_verso_host_privato_bloccato(monkeypatch):
    _finto_dns(monkeypatch, "127.0.0.1")
    richiesta = urllib.request.Request("https://siicloud.example/dav/")
    gestore = misure._RedirectSicuro()
    with pytest.raises(misure.MisureError):
        gestore.redirect_request(richiesta, None, 302, "Found", {}, "http://127.0.0.1/segreto")


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


def test_sistema_serie_restituisce_serie_e_dettagli(monkeypatch):
    def costruisci_finta(url, utente, password, percorso, giorni):
        return {
            "serie": [{"data": "2026-01-01", "valore": 139}],
            "dettagli": {"pdr": 1, "letture": 2, "cambi": 0, "file_elaborati": 1, "giorni_coperti": 1},
            "avvisi": [],
        }

    monkeypatch.setattr(misure, "costruisci_serie", costruisci_finta)
    esito = misure.sistema({
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
        "percorso": "TMG_123/2026", "azione": "serie", "giorni": 30,
    })
    assert esito["serie"] == [{"data": "2026-01-01", "valore": 139}]
    assert esito["dettagli"]["pdr"] == 1
    assert esito["fonte"]["origine"] == "SIICloud (WebDAV)"


def test_sistema_apri_apre_anche_gli_zip(monkeypatch):
    monkeypatch.setattr(misure, "scarica_file", lambda *a, **k: _zip_con("flusso.xml", XML_MISURE))
    esito = misure.sistema({
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
        "percorso": "TMG_123/x_TGL_1.zip", "azione": "apri",
    })
    assert esito["file"]["tipo"] == "giornaliera"
    assert esito["contenuto"]["numero_record"] == 2


def test_sistema_azione_non_valida():
    with pytest.raises(misure.MisureError) as errore:
        misure.sistema({
            "url": "https://x/dav/", "utente": "u", "password": "p", "azione": "cancella",
        })
    messaggio = str(errore.value)
    assert "azione non valida" in messaggio
    assert "serie" in messaggio


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


# ------------------------------------------------------------- apertura ZIP


def _zip_con(nome_interno, contenuto):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archivio:
        archivio.writestr(nome_interno, contenuto)
    return buffer.getvalue()


def test_contenuto_xml_apre_zip_e_restituisce_xml():
    xml = _zip_con("flusso.xml", b"<FlussoMisure/>")
    assert misure._contenuto_xml(xml) == b"<FlussoMisure/>"


def test_contenuto_xml_zip_senza_xml():
    archivio = _zip_con("leggimi.txt", b"ciao")
    with pytest.raises(misure.MisureError) as errore:
        misure._contenuto_xml(archivio)
    assert "non contiene file XML" in str(errore.value)


def test_contenuto_xml_zip_corrotto():
    with pytest.raises(misure.MisureError) as errore:
        misure._contenuto_xml(b"PK\x03\x04non uno zip")
    assert "ZIP valido" in str(errore.value)


def test_contenuto_xml_lascia_passare_xml_nudo():
    assert misure._contenuto_xml(b"<xml/>") == b"<xml/>"


def test_data_it_converte_e_rifiuta():
    assert misure._data_it("31/12/2025") == "2025-12-31"
    assert misure._data_it("01/01/2026") == "2026-01-01"
    assert misure._data_it("2025-12-31") is None
    assert misure._data_it("31/13/2025") is None
    assert misure._data_it("") is None
    assert misure._data_it(None) is None


# ------------------------------------------------------------- flussi reali


XML_TGL = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlussoMisure cod_flusso="TGL">
  <IdentificativiFlusso>
    <piva_utente>09080630966</piva_utente>
    <piva_distr>00489490011</piva_distr>
  </IdentificativiFlusso>
  <DatiPdr>
    <cod_pdr>00881100000001</cod_pdr>
    <mese_comp>12/2025</mese_comp>
    <data_prest>02/01/2026</data_prest>
    <DatiTecnPdr><Trattamento>G</Trattamento><coeff_corr>1</coeff_corr><Raccolta>S</Raccolta></DatiTecnPdr>
    <LettureGiornaliere>
      <matr_mis>11111111</matr_mis>
      <data_comp>31/12/2025</data_comp>
      <tipo_lettura>E</tipo_lettura>
      <let_tot_prel>000358200</let_tot_prel>
      <let_tot_conv>000235661</let_tot_conv>
    </LettureGiornaliere>
    <LettureGiornaliere>
      <matr_mis>11111111</matr_mis>
      <data_comp>01/01/2026</data_comp>
      <tipo_lettura>E</tipo_lettura>
      <let_tot_prel>000358400</let_tot_prel>
      <let_tot_conv>000235800</let_tot_conv>
    </LettureGiornaliere>
  </DatiPdr>
</FlussoMisure>"""

XML_TMV = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlussoMisure cod_flusso="TMV">
  <DatiPdr>
    <cod_pdr>00881100000002</cod_pdr>
    <DatiLettura>
      <tipo_lettura>R</tipo_lettura>
      <data_racc>30/11/2025</data_racc>
      <let_tot_prel>000100000</let_tot_prel>
      <data_mis_eff>30/11/2025</data_mis_eff>
    </DatiLettura>
  </DatiPdr>
</FlussoMisure>"""

XML_IGMG = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlussoIGMG CodFlusso="IGMG">
  <DatiPdR>
    <cod_PdDR>01617979000004</cod_PdDR>
    <cau_int_mis>SOST</cau_int_mis>
    <data_misura>15/04/2026</data_misura>
    <Pre-int><matr_mis>22222222</matr_mis><let_misuratore>000046097</let_misuratore><tipo_let>R</tipo_let></Pre-int>
    <Post-int><matr_mis>33333333</matr_mis><classe_gruppo_mis>G4</classe_gruppo_mis><let_misuratore>000000000</let_misuratore></Post-int>
  </DatiPdR>
</FlussoIGMG>"""


def test_leggi_flusso_tgl_letture_consecutive():
    letture, cambi = misure.leggi_flusso(XML_TGL)
    assert cambi == []
    assert letture == [
        {"pdr": "00881100000001", "data": "2025-12-31", "valore": 235661},
        {"pdr": "00881100000001", "data": "2026-01-01", "valore": 235800},
    ]


def test_leggi_flusso_tmv_usa_data_racc_e_prelievo():
    letture, cambi = misure.leggi_flusso(XML_TMV)
    assert letture == [{"pdr": "00881100000002", "data": "2025-11-30", "valore": 100000}]
    assert cambi == []


def test_leggi_flusso_igmg_lettura_avvio_nuovo_apparato():
    letture, cambi = misure.leggi_flusso(XML_IGMG)
    assert letture == []
    assert cambi == [{"pdr": "01617979000004", "data": "2026-04-15", "valore": 0}]


def test_leggi_flusso_xml_non_valido():
    with pytest.raises(misure.MisureError) as errore:
        misure.leggi_flusso(b"<rotto>")
    assert "XML valido" in str(errore.value)


# ------------------------------------------------------------- serie giornaliera


def test_serie_giornaliera_differenze_consecutive():
    letture = [
        {"pdr": "A", "data": "2026-01-01", "valore": 100},
        {"pdr": "A", "data": "2026-01-02", "valore": 150},
        {"pdr": "A", "data": "2026-01-03", "valore": 230},
    ]
    serie = misure.serie_giornaliera(letture, [])
    assert serie == [
        {"data": "2026-01-02", "valore": 50},
        {"data": "2026-01-03", "valore": 80},
    ]


def test_serie_giornaliera_cambio_contatore_azzera_base():
    letture = [
        {"pdr": "A", "data": "2026-04-14", "valore": 46097},
        {"pdr": "A", "data": "2026-04-16", "valore": 10},
    ]
    cambi = [{"pdr": "A", "data": "2026-04-15", "valore": 0}]
    serie = misure.serie_giornaliera(letture, cambi)
    assert serie == [{"data": "2026-04-16", "valore": 10}]


def test_serie_giornaliera_salta_differenze_negative():
    letture = [
        {"pdr": "A", "data": "2026-01-01", "valore": 200},
        {"pdr": "A", "data": "2026-01-02", "valore": 100},
        {"pdr": "A", "data": "2026-01-03", "valore": 160},
    ]
    serie = misure.serie_giornaliera(letture, [])
    assert serie == [{"data": "2026-01-03", "valore": 60}]


def test_serie_giornaliera_aggrega_piu_pdr_nello_stesso_giorno():
    letture = [
        {"pdr": "A", "data": "2026-01-01", "valore": 10},
        {"pdr": "A", "data": "2026-01-02", "valore": 30},
        {"pdr": "B", "data": "2026-01-01", "valore": 100},
        {"pdr": "B", "data": "2026-01-02", "valore": 105},
    ]
    serie = misure.serie_giornaliera(letture, [])
    assert serie == [{"data": "2026-01-02", "valore": 25}]


def test_serie_giornaliera_vuota_senza_dati():
    assert misure.serie_giornaliera([], []) == []


# ------------------------------------------------------------- costruisci serie


def _voce(nome, percorso=None, cartella=False, tipo=None):
    return {
        "nome": nome,
        "percorso": percorso or nome,
        "cartella": cartella,
        "dimensione": 10,
        "modificato": "",
        "tipo": None if cartella else tipo,
    }


def test_costruisci_serie_legge_file_nella_cartella(monkeypatch):
    monkeypatch.setattr(misure, "elenca", lambda *a, **k: [
        _voce("x_TGL_1.zip", tipo="giornaliera"),
    ])
    monkeypatch.setattr(misure, "scarica_file", lambda *a, **k: _zip_con("x.xml", XML_TGL))
    esito = misure.costruisci_serie("https://x/dav/", "u", "p", "TMG_1/2026/0102")
    assert esito["dettagli"]["pdr"] == 1
    assert esito["dettagli"]["letture"] == 2
    assert esito["dettagli"]["file_elaborati"] == 1
    assert esito["serie"] == [{"data": "2026-01-01", "valore": 139}]
    assert esito["avvisi"] == []


def test_costruisci_serie_scende_nelle_sottocartelle(monkeypatch):
    chiamate = []

    def elenca_finto(url, utente, password, percorso=""):
        chiamate.append(percorso)
        if percorso == "":
            return [_voce("0102", cartella=True), _voce("0101", cartella=True)]
        return [_voce(f"{percorso}_TGL_1.zip", percorso=f"{percorso}/f.zip", tipo="giornaliera")]

    monkeypatch.setattr(misure, "elenca", elenca_finto)
    monkeypatch.setattr(misure, "scarica_file", lambda *a, **k: _zip_con("x.xml", XML_TGL))
    esito = misure.costruisci_serie("https://x/dav/", "u", "p", "")
    assert "0102" in chiamate and "0101" in chiamate
    assert esito["dettagli"]["file_elaborati"] == 2


def test_costruisci_serie_senza_file_solleva_errore(monkeypatch):
    monkeypatch.setattr(misure, "elenca", lambda *a, **k: [_voce("ElencoFileGiornalieri.txt")])
    with pytest.raises(misure.MisureError) as errore:
        misure.costruisci_serie("https://x/dav/", "u", "p", "TMG_1/2026/0827")
    assert "nessun file di misura" in str(errore.value)


def test_costruisci_serie_file_rotto_finisce_negli_avvisi(monkeypatch):
    monkeypatch.setattr(misure, "elenca", lambda *a, **k: [
        _voce("rotto_TGL_1.zip", tipo="giornaliera"),
        _voce("sano_TGL_2.zip", tipo="giornaliera"),
    ])

    def scarica_finto(url, utente, password, percorso):
        if percorso.startswith("rotto"):
            return b"non un archivio"
        return _zip_con("x.xml", XML_TGL)

    monkeypatch.setattr(misure, "scarica_file", scarica_finto)
    esito = misure.costruisci_serie("https://x/dav/", "u", "p", "")
    assert esito["dettagli"]["file_elaborati"] == 1
    assert len(esito["avvisi"]) == 1
    assert "rotto_TGL_1.zip" in esito["avvisi"][0]


def test_giorni_richiesti_limita_e_converte():
    assert misure._giorni_richiesti({}) == misure.MAX_GIORNI_SERIE
    assert misure._giorni_richiesti({"giorni": "7"}) == 7
    assert misure._giorni_richiesti({"giorni": 999}) == misure.MAX_GIORNI_SERIE
    assert misure._giorni_richiesti({"giorni": -5}) == 1
    assert misure._giorni_richiesti({"giorni": "abc"}) == misure.MAX_GIORNI_SERIE


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


# ------------------------------------------------------------- accesso salvato (db)


def test_accesso_sii_scrivi_leggi_elimina(client):
    with db.connect() as connessione:
        db.scrivi_accesso_sii(connessione, "shipper@esempio.it", {
            "url": "https://cloud.example.com/dav/", "utente": "u",
            "password": "p", "percorso": "TMG_1/2026", "attivo": True,
        })
        letto = db.leggi_accesso_sii(connessione, "shipper@esempio.it")
        assert letto["url"] == "https://cloud.example.com/dav/"
        assert letto["utente"] == "u"
        assert letto["password"] == "p"
        assert letto["percorso"] == "TMG_1/2026"
        assert letto["attivo"] is True
        assert letto["ultima_sync"] is None
        # aggiornamento (upsert) sulla stessa email
        db.scrivi_accesso_sii(connessione, "shipper@esempio.it", {
            "url": "https://cloud.example.com/dav/", "utente": "u2",
            "password": "p2", "percorso": "", "attivo": False,
        })
        aggiornato = db.leggi_accesso_sii(connessione, "shipper@esempio.it")
        assert aggiornato["utente"] == "u2"
        assert aggiornato["attivo"] is False
        assert db.elimina_accesso_sii(connessione, "shipper@esempio.it") is True
        assert db.leggi_accesso_sii(connessione, "shipper@esempio.it") is None


def test_accessi_sii_attivi_filtra_e_registra_esito(client):
    with db.connect() as connessione:
        db.scrivi_accesso_sii(connessione, "a@esempio.it", {
            "url": "https://a.example.com/", "utente": "a", "password": "p",
            "percorso": "", "attivo": True,
        })
        db.scrivi_accesso_sii(connessione, "b@esempio.it", {
            "url": "https://b.example.com/", "utente": "b", "password": "p",
            "percorso": "", "attivo": False,
        })
        attivi = db.accessi_sii_attivi(connessione)
        assert [a["email"] for a in attivi] == ["a@esempio.it"]
        db.registra_esito_sync_sii(connessione, "a@esempio.it",
                                   quando="2026-08-22T09:00:00", errore="")
        assert db.leggi_accesso_sii(connessione, "a@esempio.it")["ultima_sync"] == "2026-08-22T09:00:00"
        db.registra_esito_sync_sii(connessione, "a@esempio.it",
                                   quando="2026-08-22T11:00:00", errore="credenziali rifiutate")
        letto = db.leggi_accesso_sii(connessione, "a@esempio.it")
        assert letto["errore_sync"] == "credenziali rifiutate"


# ------------------------------------------------------------- salva_accesso / stato


def test_salva_accesso_valida_le_credenziali(client):
    with pytest.raises(misure.MisureError) as errore:
        misure.salva_accesso("shipper@esempio.it", {"url": "", "utente": "", "password": ""})
    assert errore.value.errors


def test_salva_accesso_riusa_password_vuota(client):
    misure.salva_accesso("shipper@esempio.it", {
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "segreta",
    })
    esito = misure.salva_accesso("shipper@esempio.it", {
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "",
    })
    assert esito["salvato"] is True
    with db.connect() as connessione:
        assert db.leggi_accesso_sii(connessione, "shipper@esempio.it")["password"] == "segreta"


def test_salva_accesso_non_espone_la_password(client):
    esito = misure.salva_accesso("shipper@esempio.it", {
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "segreta",
    })
    assert "password" not in esito["accesso"]
    assert esito["accesso"]["url"] == "https://cloud.example.com/dav/"


def test_stato_accesso_non_configurato(client):
    stato = misure.stato_accesso("nessuno@esempio.it")
    assert stato["configurato"] is False
    assert stato["archivio"]["file"] == 0


def test_stato_accesso_configurato_non_espone_password(client):
    misure.salva_accesso("shipper@esempio.it", {
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "segreta",
    })
    stato = misure.stato_accesso("shipper@esempio.it")
    assert stato["configurato"] is True
    assert stato["password_presente"] is True
    assert "password" not in stato
    assert "segreta" not in str(stato)


# ------------------------------------------------------------- sincronizzazione


def _albero_sync():
    """Elenco finto: radice -> distributore -> anno -> giorno -> file TGL."""
    voci = {
        "": [_voce("TMG_1", percorso="TMG_1", cartella=True)],
        "TMG_1": [_voce("2026", percorso="TMG_1/2026", cartella=True),
                  _voce("2020", percorso="TMG_1/2020", cartella=True)],
        "TMG_1/2026": [_voce("0101", percorso="TMG_1/2026/0101", cartella=True)],
        "TMG_1/2026/0101": [_voce(
            "1_2_202601_TGL_20260102_1.zip",
            percorso="TMG_1/2026/0101/1_2_202601_TGL_20260102_1.zip",
            tipo="giornaliera")],
        "TMG_1/2020": [],
    }

    def elenca_finto(url, utente, password, percorso=""):
        return voci.get(percorso.strip("/"), [])

    return elenca_finto


def test_sincronizza_scarica_e_deduplica(client, monkeypatch):
    monkeypatch.setattr(misure, "elenca", _albero_sync())
    scaricati = []

    def scarica_finto(url, utente, password, percorso):
        scaricati.append(percorso)
        return _zip_con("flusso.xml", XML_TGL)

    monkeypatch.setattr(misure, "scarica_file", scarica_finto)
    esito = misure.sincronizza("https://cloud.example.com/dav/", "u", "p")
    assert esito["file_nuovi"] == 1
    assert esito["file_visti"] == 1
    assert esito["avvisi"] == []
    assert len(scaricati) == 1
    # seconda corsa: il file è già in archivio, nessun nuovo download
    esito2 = misure.sincronizza("https://cloud.example.com/dav/", "u", "p")
    assert esito2["file_nuovi"] == 0
    assert esito2["file_visti"] == 1
    assert len(scaricati) == 1


def test_sincronizza_senza_file_solleva_errore(client, monkeypatch):
    monkeypatch.setattr(misure, "elenca", lambda *a, **k: [])
    with pytest.raises(misure.MisureError, match="nessun file di misura"):
        misure.sincronizza("https://cloud.example.com/dav/", "u", "p")


def test_serie_da_archivio_legge_i_file_scaricati(client, monkeypatch):
    monkeypatch.setattr(misure, "elenca", _albero_sync())
    monkeypatch.setattr(misure, "scarica_file",
                        lambda *a, **k: _zip_con("flusso.xml", XML_TGL))
    misure.sincronizza("https://cloud.example.com/dav/", "u", "p")
    esito = misure.serie_da_archivio()
    assert esito["dettagli"]["file_elaborati"] == 1
    assert esito["dettagli"]["letture"] == 2
    assert esito["serie"][0]["data"] == "2026-01-01"
    assert esito["serie"][0]["valore"] == 139


def test_serie_da_archivio_vuoto_solleva_errore(client):
    with pytest.raises(misure.MisureError, match="archivio locale è vuoto"):
        misure.sistema({"azione": "serie_archivio"}, email="shipper@esempio.it")


def test_sincronizza_accesso_registra_errore(client, monkeypatch):
    misure.salva_accesso("shipper@esempio.it", {
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
    })

    def sincronizza_rotta(*a, **k):
        raise misure.MisureError("credenziali rifiutate dal server (401)")

    monkeypatch.setattr(misure, "sincronizza", sincronizza_rotta)
    with pytest.raises(misure.MisureError, match="401"):
        misure.sincronizza_accesso("shipper@esempio.it")
    with db.connect() as connessione:
        assert "401" in db.leggi_accesso_sii(connessione, "shipper@esempio.it")["errore_sync"]


def test_sincronizza_accesso_senza_accesso(client):
    with pytest.raises(misure.MisureError, match="non configurato"):
        misure.sincronizza_accesso("nessuno@esempio.it")


# ------------------------------------------------------------- rotte accesso salvato


def test_rotta_salva_accesso_e_stato(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post("/api/misure", json={
        "azione": "salva_accesso",
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
    })
    assert risposta.status_code == 200
    assert risposta.json()["salvato"] is True
    stato = client.post("/api/misure", json={"azione": "stato"}).json()
    assert stato["configurato"] is True
    assert stato["password_presente"] is True
    assert "password" not in stato


def test_rotta_sincronizza_usa_accesso_salvato(client, monkeypatch):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    client.post("/api/misure", json={
        "azione": "salva_accesso",
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
    })
    monkeypatch.setattr(misure, "elenca", _albero_sync())
    monkeypatch.setattr(misure, "scarica_file",
                        lambda *a, **k: _zip_con("flusso.xml", XML_TGL))
    risposta = client.post("/api/misure", json={"azione": "sincronizza"})
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["file_nuovi"] == 1
    assert corpo["ultima_sync"]


def test_rotta_sincronizza_senza_accesso_dà_422(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post("/api/misure", json={"azione": "sincronizza"})
    assert risposta.status_code == 422
    assert "non configurato" in risposta.json()["errore"]


def test_rotta_serie_archivio_via_http(client, monkeypatch):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    client.post("/api/misure", json={
        "azione": "salva_accesso",
        "url": "https://cloud.example.com/dav/", "utente": "u", "password": "p",
    })
    monkeypatch.setattr(misure, "elenca", _albero_sync())
    monkeypatch.setattr(misure, "scarica_file",
                        lambda *a, **k: _zip_con("flusso.xml", XML_TGL))
    client.post("/api/misure", json={"azione": "sincronizza"})
    risposta = client.post("/api/misure", json={"azione": "serie_archivio"})
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["fonte"]["origine"] == "Archivio locale"
    assert corpo["serie"][0]["valore"] == 139
