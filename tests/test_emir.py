"""Segnalazione EMIR REFIT: conformità allo schema ESMA e confini dichiarati."""

import hashlib
import json
from pathlib import Path

import pytest
from lxml import etree

from app import emir


DATI = Path(__file__).parent / "dati" / "emir"

# LEI reali, con cifre di controllo valide: servono perché il modulo verifica
# il MOD 97-10 e un codice inventato verrebbe respinto prima dello schema.
LEI_A = "529900T8BM49AURSDO55"
LEI_B = "213800QILIUD4ROSUO03"

BASE = {
    "azione": "nuovo",
    "segnalante_lei": LEI_A,
    "controparte_lei": LEI_B,
    "mittente_lei": LEI_A,
    "segnalante_natura": "NFC",
    "segnalante_settore": "D",
    "segnalante_lato": "BYER",
    "controparte_obbligo": True,
    "contratto_tipo": "FORW",
    "classe_attivo": "COMM",
    "cfi": "JCXXXX",
    "valuta_regolamento": "EUR",
    "nozionale": "1000000",
    "valuta_nozionale": "EUR",
    "consegna": "PHYS",
    "momento_esecuzione": "2026-08-01T14:30:00Z",
    "data_efficacia": "2026-09-01",
    "accordo_tipo": "EFMA",
    "evento_tipo": "TRAD",
    "evento_data": "2026-08-01",
    "prodotto": "gas",
    "dettaglio_prodotto": "TTFG",
    "punti_consegna": ["21Y100A1001A1011"],
    "tipo_carico": "GASD",
    "uti": LEI_A + "GAS2026090100001",
}

EXTRA = {
    "componente_posizione": {"uti_posizione": LEI_A + "POSIZIONE00000001"},
    "cessazione": {"data_cessazione": "2026-09-15"},
    "valutazione": {
        "valutazione_valore": "-125000.50",
        "valutazione_valuta": "EUR",
        "valutazione_momento": "2026-08-31T22:00:00Z",
        "valutazione_tipo": "MTMO",
    },
}


def dati(azione, **extra):
    return {**BASE, "azione": azione, **EXTRA.get(azione, {}), **extra}


def albero(documento):
    return etree.fromstring(documento.xml.encode("utf-8"))


def q(nome, codice="segnalazione"):
    return f"{{{emir.SCHEMI[codice]['namespace']}}}{nome}"


# --------------------------------------------------------- generazione base

@pytest.mark.parametrize("azione", sorted(emir.AZIONI))
def test_ogni_azione_produce_un_documento_valido(azione):
    documento = emir.genera_segnalazione(dati(azione))
    assert documento.sigla_azione == emir.AZIONI[azione]["sigla"]
    # genera_segnalazione valida già contro l'XSD e alza EmirError altrimenti:
    # arrivare qui significa che il documento è conforme.
    radice = albero(documento)
    assert radice.tag == q("Document")
    involucro = radice.find(f"{q('DerivsTradRpt')}/{q('TradData')}/{q('Rpt')}")
    assert [e.tag for e in involucro] == [q(emir.AZIONI[azione]["elemento"])]


def test_il_si_no_rifiuta_una_stringa_non_riconosciuta():
    """«forse» non è un sì: forzarlo a false cambierebbe il file in silenzio."""

    for testo in ("forse", "maybe", "vero!"):
        with pytest.raises(emir.EmirError) as exc:
            emir._booleano({"campo": testo}, "campo")
        assert "non è riconosciuto" in str(exc.value)
    assert emir._booleano({"campo": True}, "campo") == "true"
    assert emir._booleano({"campo": "si"}, "campo") == "true"
    assert emir._booleano({"campo": "no"}, "campo") == "false"
    assert emir._booleano({"campo": ""}, "campo") == "false"


def test_il_tipo_di_evento_segue_lo_schema_non_i_dati():
    """Lo XSD usa una variante di DerivativeEvent diversa per ogni azione.

    Correzione, riattivazione, posizione, valutazione e annullamento non
    ammettono <Tp>: se il modulo lo scrivesse comunque, il documento sarebbe
    respinto dal Trade Repository.
    """

    atteso = {
        "nuovo": True, "modifica": True, "cessazione": True,
        "correzione": False, "riattivazione": False, "componente_posizione": False,
        "valutazione": False, "errore": False,
    }
    for azione, con_tipo in atteso.items():
        documento = emir.genera_segnalazione(dati(azione))
        evento = albero(documento).find(f".//{q('DerivEvt')}")
        assert (evento.find(q("Tp")) is not None) is con_tipo, azione


def test_il_tipo_di_evento_di_troppo_viene_segnalato_non_taciuto():
    documento = emir.genera_segnalazione(dati("correzione"))
    assert any("tipo di evento" in a.lower() for a in documento.avvisi)


def test_il_gas_finisce_nel_ramo_merce_giusto():
    radice = albero(emir.genera_segnalazione(dati("nuovo")))
    gas = radice.find(f".//{q('Cmmdty')}/{q('Nrgy')}/{q('NtrlGas')}")
    assert [(e.tag.split('}')[1], e.text) for e in gas] == [
        ("BasePdct", "NRGY"), ("SubPdct", "NGAS"), ("AddtlSubPdct", "TTFG"),
    ]
    attributi = radice.find(f".//{q('NrgySpcfcAttrbts')}")
    assert attributi.find(f"{q('DlvryPtOrZone')}/{q('Cd')}").text == "21Y100A1001A1011"
    assert attributi.find(q("LdTp")).text == "GASD"


def test_la_valutazione_negativa_usa_il_campo_segno():
    """L'importo nello schema è sempre positivo: il segno viaggia a parte."""

    radice = albero(emir.genera_segnalazione(dati("valutazione")))
    valore = radice.find(f".//{q('Valtn')}/{q('CtrctVal')}")
    assert valore.find(q("Amt")).text == "125000.50"
    assert valore.find(q("Sgn")).text == "false"


def test_il_nozionale_accetta_la_forma_italiana():
    radice = albero(emir.genera_segnalazione(dati("nuovo", nozionale="1.234.567,89")))
    importo = radice.find(f".//{q('NtnlAmt')}/{q('FrstLeg')}/{q('Amt')}/{q('Amt')}")
    assert importo.text == "1234567.89"
    assert importo.get("Ccy") == "EUR"


def test_senza_portafoglio_si_dichiara_non_applicabile():
    radice = albero(emir.genera_segnalazione(dati("nuovo")))
    assert radice.find(f".//{q('CollPrtflCd')}/{q('Prtfl')}/{q('NoPrtfl')}").text == "NOAP"


# ------------------------------------------------------------- validazioni

def test_il_lei_con_cifra_di_controllo_errata_viene_respinto():
    # Ultima cifra alterata: la regex passa, il MOD 97-10 no.
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", segnalante_lei=LEI_A[:-1] + "4"))
    campi = {e["field"] for e in errore.value.errors}
    assert "segnalante_lei" in campi
    assert any("97" in e["message"] for e in errore.value.errors)


def test_i_lei_veri_superano_il_controllo():
    assert emir.cifra_di_controllo_lei(LEI_A)
    assert emir.cifra_di_controllo_lei(LEI_B)
    assert not emir.cifra_di_controllo_lei("529900T8BM49AURSDO54")
    assert not emir.cifra_di_controllo_lei("troppo-corto")


def test_le_due_controparti_non_possono_coincidere():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", controparte_lei=LEI_A))
    assert any(e["field"] == "controparte_lei" for e in errore.value.errors)


def test_un_codice_inventato_non_passa():
    """Il presidio contro i codici che «suonano giusti» ma non esistono."""

    for campo, valore in (
        ("contratto_tipo", "FORE"),
        ("accordo_tipo", "BIL"),
        ("evento_tipo", "PTNG"),
        ("tipo_carico", "NCC"),
        ("dettaglio_prodotto", "PSVG"),
    ):
        with pytest.raises(emir.EmirError) as errore:
            emir.genera_segnalazione(dati("nuovo", **{campo: valore}))
        assert any(e["field"] == campo for e in errore.value.errors), campo


def test_la_scadenza_non_puo_precedere_lefficacia():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", data_efficacia="2026-09-30", data_scadenza="2026-09-01"))
    assert any(e["field"] == "data_scadenza" for e in errore.value.errors)


def test_la_scadenza_non_puo_precedere_il_giorno_dellesecuzione():
    """Un contratto non può scadere prima di essere stato concluso.

    L'efficacia retrodatata rende il caso invisibile al solo confronto
    scadenza-efficacia: qui l'esecuzione è un mese dopo la scadenza.
    """

    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati(
            "nuovo",
            data_efficacia="2026-06-01",
            data_scadenza="2026-07-01",
            momento_esecuzione="2026-08-01T14:30:00Z",
        ))
    assert any(e["field"] == "data_scadenza" for e in errore.value.errors)


def test_il_within_day_a_cavallo_di_mezzanotte_passa_con_avviso():
    """Giorno gas D negoziato alle 01:30 di calendario D+1: scadenza D è legittima.

    Il giorno gas finisce alle 06:00 del giorno dopo, quindi l'esecuzione può
    avere data di calendario successiva alla scadenza di un giorno esatto: un
    errore bloccante qui sarebbe un falso positivo. Oltre un giorno, no.
    """

    documento = emir.genera_segnalazione(dati(
        "nuovo",
        data_efficacia="2026-09-01",
        data_scadenza="2026-09-01",
        momento_esecuzione="2026-09-02T01:30:00Z",
    ))
    assert any("within-day" in a for a in documento.avvisi)


def test_un_punto_di_consegna_non_eic_viene_respinto():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", punti_consegna=["PSV"]))
    assert any(e["field"] == "punti_consegna" for e in errore.value.errors)


def test_il_settore_nace_ammette_solo_le_sezioni_reali():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", segnalante_settore="Z"))
    assert any(e["field"] == "segnalante_settore" for e in errore.value.errors)


def test_un_componente_di_posizione_resta_a_livello_di_operazione():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("componente_posizione", livello="PSTN"))
    assert any(e["field"] == "livello" for e in errore.value.errors)


def test_i_caratteri_non_ammessi_in_xml_non_arrivano_al_serializzatore():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", riferimento_interno="rif\x00nullo"))
    assert any(e["field"] == "riferimento_interno" for e in errore.value.errors)


def test_un_payload_che_non_e_un_oggetto_non_diventa_500():
    with pytest.raises(emir.EmirError):
        emir.genera_segnalazione(["non", "un", "oggetto"])


# --------------------------------------------------------------------- UTI

def test_luti_assente_viene_generato_nella_forma_iso_e_dichiarato():
    documento = emir.genera_segnalazione({k: v for k, v in dati("nuovo").items() if k != "uti"})
    assert documento.uti.startswith(LEI_A)
    assert emir.UTI_RE.fullmatch(documento.uti)
    assert any("UTI" in a for a in documento.avvisi)


def test_luti_generato_e_deterministico():
    """Due invii dello stesso contratto non devono produrre due UTI."""

    assert emir.genera_uti(LEI_A, "chiave") == emir.genera_uti(LEI_A, "chiave")
    assert emir.genera_uti(LEI_A, "chiave") != emir.genera_uti(LEI_A, "altra")
    assert emir.genera_uti(LEI_A, "chiave") != emir.genera_uti(LEI_B, "chiave")


def test_un_uti_malformato_viene_respinto():
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo", uti="uti-minuscolo-e-corto"))
    assert any(e["field"] == "uti" for e in errore.value.errors)


# ------------------------------------------------ codici letti dallo schema

def test_le_etichette_coprono_esattamente_lo_schema():
    """Nessun codice inventato, nessuna enumerazione dimenticata.

    Se ESMA aggiunge un valore, la suite lo segnala invece di lasciarlo fuori
    dalle tendine; se qualcuno traduce un codice che non esiste, il test cade.
    """

    for tipo, tradotti in emir.ETICHETTE.items():
        ammessi = emir.codici_ammessi(tipo)
        assert ammessi, f"{tipo}: nessun valore letto dallo XSD"
        assert set(tradotti) == set(ammessi), tipo
    for tipo, tradotti in emir.ETICHETTE_ESITO.items():
        ammessi = emir.codici_ammessi(tipo, "esito")
        assert ammessi, f"{tipo}: nessun valore letto dallo XSD"
        assert set(tradotti) == set(ammessi), tipo


def test_i_codici_arrivano_dai_facet_non_da_una_costante():
    assert emir.codici_ammessi("PhysicalTransferType4Code") == ["PHYS", "OPTL", "CASH"]
    assert "TTFG" in emir.codici_ammessi("AssetClassDetailedSubProductType31Code")
    # Codici plausibili ma assenti dallo schema reale.
    assert "TCTC" not in emir.codici_ammessi("ModificationLevel1Code")
    assert "IRDS" not in emir.codici_ammessi("ProductType4Code__1")


def test_il_catalogo_espone_tendine_pronte_per_linterfaccia():
    catalogo = emir.catalogo()
    assert len(catalogo["azioni"]) == len(emir.AZIONI)
    assert {"codice", "etichetta"} <= set(catalogo["contratti"][0])
    assert len(catalogo["unita_energia"]) == 20
    # Nessun percorso locale deve trapelare al client.
    assert "filename" not in json.dumps(catalogo)


# ------------------------------------------------------------ intestazione

def test_lintestazione_e_valida_e_dichiara_il_limite():
    documento = emir.genera_intestazione({
        "mittente_lei": LEI_A, "destinatario_lei": LEI_B, "identificativo": "VETTORE-0001",
    })
    radice = etree.fromstring(documento.xml.encode("utf-8"))
    assert radice.tag == q("AppHdr", "intestazione")
    ns = emir.SCHEMI["intestazione"]["namespace"]
    # Nell'AppHdr il LEI non ha un tag proprio: sta in un identificativo generico.
    percorso = f"{{{ns}}}Fr/{{{ns}}}OrgId/{{{ns}}}Id/{{{ns}}}OrgId/{{{ns}}}Othr/{{{ns}}}Id"
    assert radice.find(percorso).text == LEI_A
    assert radice.find(f"{{{ns}}}MsgDefIdr").text == "auth.030.001.03"
    assert any("involucro" in a.lower() for a in documento.avvisi)


# ------------------------------------------------------- esito dal registro

def test_lesito_del_trade_repository_viene_letto_riga_per_riga():
    esito = emir.leggi_esito((DATI / "esito-tr.xml").read_bytes())
    assert esito["valido_xsd"] is True
    assert esito["errori_schema"] == []
    assert esito["riepilogo"]["data"] == "2026-08-03"
    assert esito["riepilogo"]["operazioni_respinte"] == "2"
    assert len(esito["righe"]) == 3
    respinta = next(r for r in esito["righe"] if r["uti"].endswith("00002"))
    assert respinta["accolto"] is False
    assert respinta["stato_etichetta"] == "Respinto"
    assert respinta["azione_etichetta"] == "Nuova operazione"
    assert [g["id"] for g in respinta["regole"]] == ["VR-30-001", "VR-30-114"]


def test_labbinamento_distingue_le_righe_nostre_da_quelle_altrui():
    esito = emir.leggi_esito((DATI / "esito-tr.xml").read_bytes())
    righe = emir.abbina_esito(esito, [LEI_A + "GAS2026090100001"])
    assert [r["nostro"] for r in righe] == [True, False, False]


def test_un_documento_che_non_e_un_esito_viene_respinto():
    with pytest.raises(emir.EmirError) as errore:
        emir.leggi_esito((DATI / "esempio-forward-gas.xml").read_bytes())
    assert "auth.092" in str(errore.value)


def test_un_esito_vuoto_non_diventa_un_errore_interno():
    with pytest.raises(emir.EmirError):
        emir.leggi_esito(b"   ")


def test_lesito_non_risolve_entita_esterne():
    """XXE: il file arriva da fuori, il parser non deve leggere il disco."""

    ostile = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE Document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:auth.092.001.04">&xxe;</Document>'
    ).encode("utf-8")
    esito = emir.leggi_esito(ostile)
    assert esito["valido_xsd"] is False
    assert "root:" not in json.dumps(esito)


def test_un_esito_troppo_grande_viene_rifiutato():
    with pytest.raises(emir.EmirError) as errore:
        emir.leggi_esito(b"<a/>" * (emir.MAX_ESITO_BYTES // 3))
    assert "MB" in str(errore.value)


# ------------------------------------------------------------ integrità XSD

@pytest.mark.parametrize("codice", sorted(emir.SCHEMI))
def test_limpronta_dichiarata_dello_schema_e_quella_reale(codice):
    meta = emir.SCHEMI[codice]
    contenuto = (emir.SCHEMA_DIR / meta["filename"]).read_bytes()
    assert hashlib.sha256(contenuto).hexdigest() == meta["sha256"]


def test_uno_schema_manomesso_blocca_la_generazione(tmp_path, monkeypatch):
    """L'impronta va controllata davvero, non solo restituita al client.

    Lo schema falso resta uno schema *valido*, con un commento in più: così il
    test misura il controllo dell'impronta e non un errore di parsing, e non
    dipende da quali cache altri test hanno già riscaldato.
    """

    finto = tmp_path / "emir"
    finto.mkdir()
    for meta in emir.SCHEMI.values():
        originale = (emir.SCHEMA_DIR / meta["filename"]).read_bytes()
        (finto / meta["filename"]).write_bytes(originale + b"<!-- manomesso -->")
    monkeypatch.setattr(emir, "SCHEMA_DIR", finto)
    monkeypatch.setattr(emir, "_cache_schemi", {})
    monkeypatch.setattr(emir, "_cache_definizioni", {})
    monkeypatch.setattr(emir, "_cache_enumerazioni", {})
    with pytest.raises(emir.EmirError) as errore:
        emir.genera_segnalazione(dati("nuovo"))
    assert "impronta" in str(errore.value)
    with pytest.raises(emir.EmirError):
        emir.leggi_esito((DATI / "esito-tr.xml").read_bytes())
    # Anche il catalogo deve fermarsi: le tendine lette da uno schema
    # manomesso e la generazione bloccata sarebbero due verità diverse
    # sullo stesso file.
    with pytest.raises(emir.EmirError) as errore_catalogo:
        emir.catalogo()
    assert "impronta" in str(errore_catalogo.value)


def test_il_documento_dichiara_limpronta_di_ciò_che_ha_prodotto():
    documento = emir.genera_segnalazione(dati("nuovo"))
    assert documento.xml_sha256 == hashlib.sha256(documento.xml.encode("utf-8")).hexdigest()
    assert documento.schema_sha256 == emir.SCHEMI["segnalazione"]["sha256"]


# ------------------------------------------------------------ rotte HTTP

def accedi(client, email="shipper@esempio.it"):
    assert client.post("/api/login", json={"email": email}).status_code == 200


def test_le_rotte_emir_richiedono_una_sessione(client):
    for metodo, percorso in (
        ("get", "/api/emir/catalogo"),
        ("get", "/api/emir/segnalazioni"),
        ("post", "/api/emir/segnalazioni"),
        ("get", "/api/emir/esiti"),
        ("post", "/api/emir/esiti"),
        ("post", "/api/emir/intestazioni"),
    ):
        risposta = getattr(client, metodo)(percorso)
        assert risposta.status_code == 401, percorso


def test_ciclo_completo_dalla_segnalazione_allesito(client):
    accedi(client)
    creata = client.post("/api/emir/segnalazioni", json=dati("nuovo"))
    assert creata.status_code == 201
    corpo = creata.json()
    assert corpo["valido_xsd"] is True and corpo["sigla_azione"] == "NEWT"
    # L'XML non viaggia nell'elenco: si scarica dalla rotta dedicata.
    assert "xml" not in corpo

    elenco = client.get("/api/emir/segnalazioni").json()["segnalazioni"]
    assert [s["uti"] for s in elenco] == [BASE["uti"]]

    scarico = client.get(f"/api/emir/segnalazioni/{corpo['id']}/download")
    assert scarico.status_code == 200
    assert scarico.headers["content-type"].startswith("application/xml")
    assert "EMIR_NEWT_" in scarico.headers["content-disposition"]
    assert scarico.content.startswith(b"<?xml")

    esito = client.post(
        "/api/emir/esiti",
        content=(DATI / "esito-tr.xml").read_bytes(),
        headers={"Content-Type": "application/xml"},
    )
    assert esito.status_code == 201
    righe = esito.json()["righe"]
    assert sum(1 for r in righe if r["nostro"]) == 1
    assert client.get("/api/emir/esiti").json()["esiti"][0]["respinte"] == 0


def test_la_stessa_azione_sullo_stesso_uti_non_si_ripete(client):
    accedi(client)
    assert client.post("/api/emir/segnalazioni", json=dati("nuovo")).status_code == 201
    doppia = client.post("/api/emir/segnalazioni", json=dati("nuovo"))
    assert doppia.status_code == 409
    assert "Esiste già" in doppia.json()["errore"]
    # Un'azione diversa sullo stesso contratto invece è legittima.
    assert client.post("/api/emir/segnalazioni", json=dati("valutazione")).status_code == 201


def test_lo_stesso_esito_importato_due_volte_non_si_duplica(client):
    accedi(client)
    contenuto = (DATI / "esito-tr.xml").read_bytes()
    intestazioni = {"Content-Type": "application/xml"}
    assert client.post("/api/emir/esiti", content=contenuto, headers=intestazioni).status_code == 201
    ripetuto = client.post("/api/emir/esiti", content=contenuto, headers=intestazioni)
    assert ripetuto.json()["gia_importato"] is True
    assert len(client.get("/api/emir/esiti").json()["esiti"]) == 1


def test_le_segnalazioni_di_un_utente_non_si_vedono_da_un_altro(client):
    accedi(client, "primo@esempio.it")
    assert client.post("/api/emir/segnalazioni", json=dati("nuovo")).status_code == 201
    identificativo = client.get("/api/emir/segnalazioni").json()["segnalazioni"][0]["id"]

    accedi(client, "secondo@esempio.it")
    assert client.get("/api/emir/segnalazioni").json()["segnalazioni"] == []
    assert client.get(f"/api/emir/segnalazioni/{identificativo}").status_code == 404
    assert client.get(f"/api/emir/segnalazioni/{identificativo}/download").status_code == 404


def test_gli_errori_di_campo_arrivano_al_client(client):
    accedi(client)
    risposta = client.post("/api/emir/segnalazioni", json=dati("nuovo", segnalante_lei="NONVALIDO"))
    assert risposta.status_code == 422
    assert any(e["field"] == "segnalante_lei" for e in risposta.json()["errors"])


def test_un_corpo_non_oggetto_non_diventa_500(client):
    accedi(client)
    assert client.post("/api/emir/segnalazioni", json=["lista"]).status_code == 400
    assert client.post("/api/emir/esiti", content=b"", headers={"Content-Type": "application/xml"}).status_code == 400


def test_un_esito_oltre_il_limite_viene_rifiutato_prima_di_leggerlo(client):
    accedi(client)
    risposta = client.post(
        "/api/emir/esiti",
        content=b"<a/>",
        headers={"Content-Type": "application/xml", "Content-Length": str(emir.MAX_ESITO_BYTES + 1)},
    )
    assert risposta.status_code == 413


def test_gli_esiti_conservati_sono_immutabili(client):
    """Valgono come prova di cosa il registro ha accolto: non si riscrivono."""

    import sqlite3
    from app import db

    accedi(client)
    client.post("/api/emir/esiti", content=(DATI / "esito-tr.xml").read_bytes(),
                headers={"Content-Type": "application/xml"})
    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE emir_esito SET accolte = 99")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM emir_esito")
