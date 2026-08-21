"""Profili di prelievo standard: parser .xls/.xlsx onesti, griglia verificata, fetch live."""

import base64
import io
import struct
import zipfile

import pytest

from app import prelievo


INTESTAZIONE = ["Data", "Giorno"] + list(prelievo.PARAMETRI_ATTESI)
SETTIMANA = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _lettera_colonna(indice):
    lettera = ""
    indice += 1
    while indice:
        indice, resto = divmod(indice - 1, 26)
        lettera = chr(65 + resto) + lettera
    return lettera


# ---------------------------------------------------- fabbriche di file finti


def xlsx_prelievo(inizio=46296, giorni=365, valore=None, stringa_1e8=False):
    """Costruisce un .xlsx minimo nella forma pubblicata da Snam."""

    valore = valore if valore is not None else 100.0 / giorni
    condivise = INTESTAZIONE + SETTIMANA
    parti_si = "".join(f"<si><t>{s}</t></si>" for s in condivise)
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{prelievo._NS[1:-1]}" count="{len(condivise)}" uniqueCount="{len(condivise)}">'
        f"{parti_si}</sst>"
    )
    righe_xml = ['<row r="1">']
    for colonna, _nome in enumerate(INTESTAZIONE):
        righe_xml.append(f'<c r="{_lettera_colonna(colonna)}1" t="s"><v>{colonna}</v></c>')
    righe_xml.append("</row>")
    for giorno in range(giorni):
        numero_riga = giorno + 2
        righe_xml.append(f'<row r="{numero_riga}">')
        righe_xml.append(f'<c r="A{numero_riga}"><v>{inizio + giorno}</v></c>')
        righe_xml.append(f'<c r="B{numero_riga}" t="s"><v>{22 + giorno % 7}</v></c>')
        for parametro in range(20):
            cella = f"{_lettera_colonna(2 + parametro)}{numero_riga}"
            if stringa_1e8 and parametro == 0 and giorno < 100:
                righe_xml.append(f'<c r="{cella}" t="str"><v>1E-8</v></c>')
            else:
                righe_xml.append(f'<c r="{cella}"><v>{valore!r}</v></c>')
        righe_xml.append("</row>")
    righe_xml.append(f'<row r="{giorni + 2}"/>')
    foglio = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{prelievo._NS[1:-1]}"><sheetData>'
        + "".join(righe_xml) + "</sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archivio:
        archivio.writestr("xl/sharedStrings.xml", shared)
        archivio.writestr("xl/worksheets/sheet1.xml", foglio)
    return buffer.getvalue()


def _record(tipo, dati=b""):
    return struct.pack("<HH", tipo, len(dati)) + dati


def _stringa_sst(testo):
    byte = testo.encode("latin-1")
    return struct.pack("<HB", len(byte), 0) + byte


def xls_prelievo(inizio=46296, giorni=365, con_speciali=False):
    """Costruisce un .xls BIFF8 minimo dentro un contenitore OLE2."""

    stringhe = INTESTAZIONE + SETTIMANA
    sst = struct.pack("<II", 0, len(stringhe)) + b"".join(_stringa_sst(s) for s in stringhe)
    records = [_record(0x0208, struct.pack("<HHIBBI", 0x0600, 0x10, 0, 0, 0, 0))]
    records.append(_record(0x00FC, sst))
    for colonna in range(len(INTESTAZIONE)):
        records.append(_record(0x00FD, struct.pack("<HHHI", 0, colonna, 0, colonna)))
    valore = 100.0 / giorni
    for giorno in range(giorni):
        riga = giorno + 1
        records.append(_record(0x0203, struct.pack("<HHHd", riga, 0, 0, float(inizio + giorno))))
        records.append(_record(0x00FD, struct.pack("<HHHI", riga, 1, 0, 22 + giorno % 7)))
        for parametro in range(20):
            colonna = 2 + parametro
            if con_speciali and riga == 1 and parametro == 0:
                records.append(_record(0x027E, struct.pack("<HHHi", riga, colonna, 0, (5 << 2) | 0x02)))
            elif con_speciali and riga == 1 and parametro == 1:
                blocco = struct.pack("<Hi", 0, (7 << 2) | 0x02) + struct.pack("<Hi", 0, (9 << 2) | 0x02)
                records.append(_record(0x00BD, struct.pack("<HH", riga, colonna) + blocco + struct.pack("<H", colonna + 1)))
            elif con_speciali and riga == 1 and parametro == 2:
                continue  # già scritto dal MULRK sulla colonna precedente
            else:
                records.append(_record(0x0203, struct.pack("<HHHd", riga, colonna, 0, valore)))
    records.append(_record(0x000A))
    workbook = b"".join(records)
    return _ole2(workbook)


def _voce_directory(nome, tipo, primo=-2, dimensione=0):
    nome_byte = nome.encode("utf-16-le") + b"\x00\x00"
    voce = bytearray(128)
    voce[:len(nome_byte)] = nome_byte
    struct.pack_into("<H", voce, 0x40, len(nome_byte))
    voce[0x42] = tipo
    for offset in (0x44, 0x48, 0x4C):
        struct.pack_into("<I", voce, offset, 0xFFFFFFFF)
    struct.pack_into("<i", voce, 0x74, primo)
    struct.pack_into("<I", voce, 0x78, dimensione)
    return bytes(voce)


def _ole2(workbook=None):
    """Contenitore OLE2 con settori da 4096 byte (un solo settore di FAT)."""

    settore = 4096
    dati = workbook or b""
    settori_wb = (len(dati) + settore - 1) // settore if dati else 0
    imbottito = dati + b"\x00" * (settori_wb * settore - len(dati)) if dati else b""
    fat = [-3, -2]
    for i in range(settori_wb):
        fat.append(3 + i if i + 1 < settori_wb else -2)
    fat += [-1] * (settore // 4 - len(fat))
    settore_fat = b"".join(struct.pack("<i", v) for v in fat)
    voci = [_voce_directory("Root Entry", 5)]
    if workbook is not None:
        voci.append(_voce_directory("Workbook", 2, primo=2, dimensione=len(workbook)))
    voci += [_voce_directory("", 0)] * (settore // 128 - len(voci))
    directory = b"".join(voci)
    testa = bytearray(512)
    testa[:8] = prelievo.FIRMAMENTO_OLE2
    struct.pack_into("<H", testa, 0x18, 0xFFFE)
    testa[0x1E] = 12
    testa[0x20] = 6
    struct.pack_into("<I", testa, 0x2C, 1)
    struct.pack_into("<i", testa, 0x30, 1)
    struct.pack_into("<i", testa, 0x4C, 0)
    for i in range(1, 109):
        struct.pack_into("<i", testa, 0x4C + 4 * i, -1)
    return bytes(testa) + settore_fat + directory + imbottito


def griglia_valida(giorni=365, inizio=46296):
    """Griglia Python (non file) nella forma letta dai parser."""

    griglia = [list(INTESTAZIONE)]
    for giorno in range(giorni):
        griglia.append([float(inizio + giorno), "giovedì"] + [100.0 / giorni] * 20)
    return griglia


# ------------------------------------------------------------- parser .xlsx


def test_un_xlsx_valido_dà_la_griglia_completa():
    griglia = prelievo.leggi_griglia(xlsx_prelievo(), "profili.xlsx")
    assert len(griglia) == 366
    assert [str(c) for c in griglia[0]] == INTESTAZIONE
    assert griglia[1][0] == 46296.0
    assert griglia[1][1] == "lunedì"
    assert len(griglia[1]) == 22


def test_le_stringhe_condivise_dell_xlsx_sono_ricompattate():
    griglia = prelievo.leggi_griglia(xlsx_prelievo(), "")
    assert griglia[0][2] == "c1%B1"
    assert griglia[0][-1] == "t1%3"


def test_la_stringa_1e8_è_letta_come_numero():
    griglia = prelievo.leggi_griglia(xlsx_prelievo(stringa_1e8=True), "")
    assert griglia[1][2] == pytest.approx(1e-8)


def test_un_xlsx_corrotto_è_rifiutato():
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.leggi_griglia(b"PK\x03\x04non e uno zip", "rotto.xlsx")
    assert "non è un archivio valido" in str(errore.value)


# -------------------------------------------------------------- parser .xls


def test_un_xls_biff8_valido_dà_la_griglia_completa():
    griglia = prelievo.leggi_griglia(xls_prelievo(), "profili.xls")
    assert len(griglia) == 366
    assert griglia[0][:3] == ["Data", "Giorno", "c1%B1"]
    assert griglia[1][0] == 46296.0
    assert griglia[1][1] == "lunedì"


def test_i_formati_rk_e_mulrk_del_biff8_sono_decodificati():
    griglia = prelievo.leggi_griglia(xls_prelievo(con_speciali=True), "")
    assert griglia[1][2] == 5.0
    assert griglia[1][3] == 7.0
    assert griglia[1][4] == 9.0


def test_un_xls_senza_firma_ole2_è_rifiutato():
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo._griglia_xls(b"\xd0\xcf\x11\xe0" + b"niente")
    assert "firma OLE2" in str(errore.value)


def test_un_xls_senza_workbook_è_rifiutato():
    contenuto = _ole2()
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.leggi_griglia(contenuto, "vuoto.xls")
    assert "Workbook" in str(errore.value)


def test_formato_non_riconosciuto_è_rifiutato():
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.leggi_griglia(b"ciao mondo", "testo.txt")
    assert "Formato non riconosciuto" in str(errore.value)


# --------------------------------------------------------------- validazione


def test_una_griglia_valida_dà_l_anno_termico_e_le_somme():
    esito = prelievo.valida_griglia(griglia_valida())
    assert esito["anno_termico"] == "2026-2027"
    assert esito["giorni"] == 365
    assert esito["parametri"] == list(prelievo.PARAMETRI_ATTESI)
    assert all(abs(v - 100.0) < 1e-9 for v in esito["somme"].values())
    assert esito["righe"][0]["data"] == "2026-10-01"
    assert esito["righe"][-1]["data"] == "2027-09-30"
    assert esito["zeri"] == 0


def test_l_anno_bisestile_di_366_giorni_è_accettato():
    esito = prelievo.valida_griglia(griglia_valida(giorni=366, inizio=44835))
    assert esito["giorni"] == 366
    assert esito["anno_termico"] == "2022-2023"


def test_parametri_mancanti_fermano_la_validazione():
    griglia = griglia_valida()
    griglia[0] = griglia[0][:-2]
    for riga in griglia[1:]:
        del riga[-2:]
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.valida_griglia(griglia)
    assert errore.value.errors == ["parametro mancante: t1%2", "parametro mancante: t1%3"]


def test_il_numero_di_giorni_sbagliato_è_rifiutato():
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.valida_griglia(griglia_valida(giorni=10))
    assert "365 o 366" in str(errore.value)


def test_somma_diversa_da_100_è_un_errore():
    griglia = griglia_valida()
    for riga in griglia[1:]:
        riga[2] = 0.1
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.valida_griglia(griglia)
    assert any("c1%B1" in e for e in errore.value.errors)


def test_data_illeggibile_è_un_errore():
    griglia = griglia_valida()
    griglia[5][0] = "non una data"
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.valida_griglia(griglia)
    assert any("riga 6" in e and "data" in e for e in errore.value.errors)


def test_valore_non_numerico_è_un_errore():
    griglia = griglia_valida()
    griglia[3][4] = "alto"
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.valida_griglia(griglia)
    assert any("riga 4" in e and "c1%D1" in e for e in errore.value.errors)


def test_il_salto_di_date_dà_un_avviso_senza_fermare():
    griglia = griglia_valida()
    griglia[10][0] = griglia[10][0] + 1.0
    esito = prelievo.valida_griglia(griglia)
    assert any("salto di date" in a for a in esito["avvisi"])


def test_inizio_diverso_dal_primo_ottobre_dà_avviso():
    esito = prelievo.valida_griglia(griglia_valida(inizio=46311))
    assert any("1° ottobre" in a for a in esito["avvisi"])


def test_il_valore_1e8_è_contato_come_zero():
    griglia = griglia_valida()
    for riga in griglia[1:101]:
        riga[2] = 1e-8
    for riga in griglia[101:]:
        riga[2] = (100.0 - 100 * 1e-8) / 265
    esito = prelievo.valida_griglia(griglia)
    assert esito["zeri"] == 100
    assert any("1E-8" in a for a in esito["avvisi"])


def test_stesso_input_stesso_output():
    assert prelievo.valida_griglia(griglia_valida()) == prelievo.valida_griglia(griglia_valida())


# ------------------------------------------------------------------ sistema


def test_sistema_con_file_xlsx_in_base64():
    contenuto = xlsx_prelievo()
    esito = prelievo.sistema({
        "contenuto_base64": base64.b64encode(contenuto).decode(),
        "nome_file": "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT.xlsx",
    })
    assert esito["fonte"]["origine"] == "File caricato"
    assert esito["fonte"]["file"] == "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT.xlsx"
    assert esito["fonte"]["pubblicazione"] == prelievo.TIPOLOGIA_PRELIEVO
    assert esito["anno_termico"] == "2026-2027"
    assert len(esito["righe"]) == 365


def test_sistema_con_file_xls_in_base64():
    contenuto = xls_prelievo()
    esito = prelievo.sistema({"contenuto_base64": base64.b64encode(contenuto).decode()})
    assert esito["fonte"]["origine"] == "File caricato"
    assert esito["giorni"] == 365


def test_sistema_senza_file_chiede_il_caricamento():
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.sistema({})
    assert "Carica il file" in str(errore.value)


def test_sistema_con_base64_non_valido():
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.sistema({"contenuto_base64": "non è base64!!!"})
    assert "base64" in str(errore.value)


def test_sistema_con_scarica_live(monkeypatch):
    monkeypatch.setattr(
        prelievo, "scarica_da_jarvis",
        lambda anno: (xlsx_prelievo(), {"file": "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT.xls", "aggiornato_il": "20260803084727"}),
    )
    esito = prelievo.sistema({"scarica": True, "anno": "2026-2027"})
    assert esito["fonte"]["origine"] == "Jarvis (Snam)"
    assert esito["fonte"]["aggiornato_il"] == "20260803084727"
    assert esito["giorni"] == 365


# ------------------------------------------------------------- fetch live


def _voce(nome, aggiornato, download_id="JPUBB022/x", formato="xls"):
    return {
        "nome_file_ITA": nome, "formato_file": formato,
        "aggiornato_il": aggiornato,
        "download_id": [{"lang": "ITA", "value": download_id}],
    }


def test_scarica_da_jarvis_sceglie_il_file_più_recente(monkeypatch):
    elenco = [
        _voce("PERCENTUALI_DI_PRELIEVO_AT_2025-2026_IT", "20250801084727", "JPUBB022/2025"),
        _voce("PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT", "20260803084727", "JPUBB022/2026"),
        {"nome_file_ITA": "ALTRO_FILE", "aggiornato_il": "20260901000000",
         "download_id": [{"lang": "ITA", "value": "JPUBB022/altro"}]},
    ]
    scaricati = []
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", lambda tipologia, anno=None: elenco)
    monkeypatch.setattr(prelievo.jarvis, "scarica_documento", lambda d, n: scaricati.append((d, n)) or xlsx_prelievo())
    contenuto, meta = prelievo.scarica_da_jarvis("")
    assert meta["file"] == "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT.xls"
    assert meta["aggiornato_il"] == "20260803084727"
    assert scaricati[0] == ("JPUBB022/2026", "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT.xls")
    assert contenuto[:4] == prelievo.FIRMAMENTO_ZIP


def test_scarica_da_jarvis_filtra_per_anno_termico(monkeypatch):
    elenco = [
        _voce("PERCENTUALI_DI_PRELIEVO_AT_2025-2026_IT", "20250801084727", "JPUBB022/2025"),
        _voce("PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT", "20260803084727", "JPUBB022/2026", formato="xlsx"),
    ]
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", lambda tipologia, anno=None: elenco)
    monkeypatch.setattr(prelievo.jarvis, "scarica_documento", lambda d, n: xlsx_prelievo())
    _, meta = prelievo.scarica_da_jarvis("2025-2026")
    assert meta["file"] == "PERCENTUALI_DI_PRELIEVO_AT_2025-2026_IT.xls"
    _, meta = prelievo.scarica_da_jarvis("2026")
    assert meta["file"] == "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT.xlsx"


def test_scarica_da_jarvis_anno_assente_elenca_i_disponibili(monkeypatch):
    elenco = [_voce("PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT", "20260803084727")]
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", lambda tipologia, anno=None: elenco)
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.scarica_da_jarvis("1999-2000")
    assert "1999-2000" in str(errore.value)
    assert "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT" in str(errore.value)


def test_scarica_da_jarvis_rete_giù_invita_a_caricare(monkeypatch):
    def rete_giù(tipologia, anno=None):
        raise prelievo.jarvis.JarvisError("La chiamata all'API pubblica di Jarvis non è andata a buon fine. (dettaglio: rete assente)")
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", rete_giù)
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.scarica_da_jarvis("")
    assert "caricare il file" in str(errore.value)


def test_scarica_da_jarvis_pubblicazione_vuota(monkeypatch):
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", lambda tipologia, anno=None: [])
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.scarica_da_jarvis("")
    assert "PERCENTUALI_DI_PRELIEVO_AT_" in str(errore.value)


def test_scarica_da_jarvis_senza_download_id(monkeypatch):
    elenco = [{"nome_file_ITA": "PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT",
               "formato_file": "xls", "aggiornato_il": "20260803084727", "download_id": []}]
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", lambda tipologia, anno=None: elenco)
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.scarica_da_jarvis("")
    assert "identificativo di download" in str(errore.value)


def test_scarica_da_jarvis_download_fallito(monkeypatch):
    elenco = [_voce("PERCENTUALI_DI_PRELIEVO_AT_2026-2027_IT", "20260803084727")]
    monkeypatch.setattr(prelievo.jarvis, "elenco_pubblicazioni", lambda tipologia, anno=None: elenco)
    def fallito(d, n):
        raise prelievo.jarvis.JarvisError("Il download del documento da Jarvis non è riuscito. (dettaglio: 500)")
    monkeypatch.setattr(prelievo.jarvis, "scarica_documento", fallito)
    with pytest.raises(prelievo.PrelievoError) as errore:
        prelievo.scarica_da_jarvis("")
    assert "caricare il file" in str(errore.value)


# ------------------------------------------------------------- rotte HTTP


def test_la_rotta_richiede_una_sessione(client):
    assert client.post("/api/prelievo", json={}).status_code == 401


def test_ciclo_completo_via_http(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    contenuto = base64.b64encode(xlsx_prelievo()).decode()
    risposta = client.post("/api/prelievo", json={"contenuto_base64": contenuto, "nome_file": "profili.xlsx"})
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["anno_termico"] == "2026-2027"
    assert corpo["giorni"] == 365
    assert corpo["fonte"]["origine"] == "File caricato"

    errore = client.post("/api/prelievo", json={"contenuto_base64": base64.b64encode(b"ciao").decode()})
    assert errore.status_code == 422
    assert client.post("/api/prelievo", json=["lista"]).status_code == 400


def test_un_corpo_enorme_viene_rifiutato_prima_di_leggerlo(client):
    client.post("/api/login", json={"email": "shipper@esempio.it"})
    risposta = client.post(
        "/api/prelievo",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(prelievo.MAX_CORPO_BYTES + 1)},
    )
    assert risposta.status_code == 413
