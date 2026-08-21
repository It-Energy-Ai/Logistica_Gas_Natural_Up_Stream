"""Profili di prelievo standard (pubblicazione Snam / Jarvis).

Legge i file «PERCENTUALI_DI_PRELIEVO_AT_...» (.xls BIFF8 oppure .xlsx) che
definiscono, per ogni giorno dell'anno termico, le 20 percentuali dei profili
di prelievo standard. Regole di validazione:

* una riga di intestazione con «Data», «Giorno» e i 20 parametri attesi;
* 365 o 366 righe dati (un giorno per riga, anno termico dal 1° ottobre);
* ogni colonna percentuale deve sommare esattamente 100 (tolleranza 1e-6);
* il valore ``1E-8`` significa zero / non applicabile.

Il modulo è puro Python (solo libreria standard), deterministico e senza
memoria: i file ricevuti non vengono mai salvati.
"""

from __future__ import annotations

import base64
import binascii
import io
import struct
import zipfile
from datetime import date, timedelta
from typing import Any
from xml.etree import ElementTree

from . import jarvis


class PrelievoError(ValueError):
    """Errore di dominio del modulo profili di prelievo."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


# ------------------------------------------------------------------ costanti

TIPOLOGIA_PRELIEVO = (
    "VALORI PERCENTUALI PER LA DEFINIZIONE DEI PROFILI DI PRELIEVO STANDARD"
)
PARAMETRI_ATTESI = (
    "c1%B1", "c1%C1", "c1%D1", "c1%E1", "c1%F1",
    "c1%B2", "c1%C2", "c1%D2", "c1%E2", "c1%F2",
    "c1%B3", "c1%C3", "c1%D3", "c1%E3", "c1%F3",
    "c2%", "c4%", "t1%1", "t1%2", "t1%3",
)
EPOCA_EXCEL = date(1899, 12, 30)
TOLLERANZA_SOMMA = 1e-6
MAX_CORPO_BYTES = 1024 * 1024
MAX_CELLE = 40_000
MAX_COLONNE = 16_384
MAX_XML_DECOMPRESSO = 64 * 1024 * 1024
POTENZE_SETTORE_OLE2 = (9, 12)
FIRMAMENTO_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FIRMAMENTO_ZIP = b"PK\x03\x04"
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# ------------------------------------------------------------- parser .xlsx


def _leggi_voce_zip(archivio: zipfile.ZipFile, nome: str) -> bytes:
    """Legge una voce dello zip con tetto sulla dimensione decompressa."""

    info = archivio.getinfo(nome)
    if info.file_size > MAX_XML_DECOMPRESSO:
        raise PrelievoError("Il file .xlsx contiene dati troppo grandi.")
    contenuto = archivio.read(nome)
    if len(contenuto) > MAX_XML_DECOMPRESSO:
        raise PrelievoError("Il file .xlsx contiene dati troppo grandi.")
    return contenuto


def _stringhe_condivise_xlsx(archivio: zipfile.ZipFile) -> list[str]:
    """Le stringhe condivise di una cartella .xlsx (xl/sharedStrings.xml)."""

    try:
        contenuto = _leggi_voce_zip(archivio, "xl/sharedStrings.xml")
    except KeyError:
        return []
    radice = ElementTree.fromstring(contenuto)
    return [
        "".join(nodo.text or "" for nodo in si.iter(f"{_NS}t"))
        for si in radice.findall(f"{_NS}si")
    ]


def _colonna_da_riferimento(riferimento: str) -> int:
    """Indice di colonna (0-based) da un riferimento tipo «C12»."""

    indice = 0
    for carattere in riferimento:
        if not carattere.isalpha():
            break
        indice = indice * 26 + (ord(carattere.upper()) - 64)
    return indice - 1


def _valore_cella_xlsx(cella: ElementTree.Element, stringhe: list[str]) -> Any:
    """Valore di una cella .xlsx: stringa condivisa, inline o numero."""

    tipo = cella.get("t")
    if tipo == "inlineStr":
        return "".join(nodo.text or "" for nodo in cella.iter(f"{_NS}t"))
    nodo_valore = cella.find(f"{_NS}v")
    testo = nodo_valore.text if nodo_valore is not None else None
    if testo is None:
        return None
    if tipo == "s":
        return stringhe[int(testo)]
    if tipo == "str":
        try:
            return float(testo)
        except ValueError:
            return testo
    try:
        return float(testo)
    except ValueError:
        return testo


def _griglia_xlsx(contenuto: bytes) -> list[list[Any]]:
    """Legge il primo foglio di un file .xlsx come griglia di valori."""

    try:
        archivio = zipfile.ZipFile(io.BytesIO(contenuto))
    except zipfile.BadZipFile as errore:
        raise PrelievoError("Il file .xlsx non è un archivio valido.") from errore
    nomi = sorted(
        nome for nome in archivio.namelist()
        if nome.startswith("xl/worksheets/sheet") and nome.endswith(".xml")
    )
    if not nomi:
        raise PrelievoError("Nessun foglio trovato nel file .xlsx.")
    stringhe = _stringhe_condivise_xlsx(archivio)
    radice = ElementTree.fromstring(_leggi_voce_zip(archivio, nomi[0]))
    griglia: list[list[Any]] = []
    for riga in radice.iter(f"{_NS}row"):
        valori: list[Any] = []
        for cella in riga.findall(f"{_NS}c"):
            indice = _colonna_da_riferimento(cella.get("r") or "")
            if indice < 0 or indice >= MAX_COLONNE:
                raise PrelievoError("Il file .xlsx contiene un riferimento di cella non valido.")
            while len(valori) <= indice:
                valori.append(None)
            valori[indice] = _valore_cella_xlsx(cella, stringhe)
        if valori:
            griglia.append(valori)
    return griglia


# --------------------------------------------------------------- parser .xls


def _flussi_ole2(contenuto: bytes) -> dict[str, bytes]:
    """Apre un contenitore OLE2/CFB e restituisce i suoi flussi (stream)."""

    if contenuto[:8] != FIRMAMENTO_OLE2:
        raise PrelievoError("Il file .xls non ha la firma OLE2 attesa.")
    potenza_settore = int.from_bytes(contenuto[0x1E:0x20], "little")
    if potenza_settore not in POTENZE_SETTORE_OLE2:
        raise PrelievoError("Il file .xls dichiara una dimensione di settore non valida.")
    dimensione_settore = 1 << potenza_settore
    primo_dir = int.from_bytes(contenuto[0x30:0x34], "little")
    fat: list[int] = []
    for i in range(109):
        secid = int.from_bytes(contenuto[0x4C + 4 * i:0x50 + 4 * i], "little", signed=True)
        if secid < 0:
            continue
        inizio = 512 + secid * dimensione_settore
        for j in range(dimensione_settore // 4):
            fat.append(
                int.from_bytes(contenuto[inizio + 4 * j:inizio + 4 * j + 4], "little", signed=True)
            )

    def catena(secid: int) -> bytes:
        parti: list[bytes] = []
        visti: set[int] = set()
        while 0 <= secid < 0xFFFFFFFA and secid not in visti:
            visti.add(secid)
            inizio = 512 + secid * dimensione_settore
            parti.append(contenuto[inizio:inizio + dimensione_settore])
            secid = fat[secid] if secid < len(fat) else -2
        return b"".join(parti)

    flussi: dict[str, bytes] = {}
    directory = catena(primo_dir)
    for inizio in range(0, len(directory), 128):
        voce = directory[inizio:inizio + 128]
        if len(voce) < 128 or voce[0x42] != 2:  # 2 = stream (non cartella)
            continue
        lunghezza_nome = int.from_bytes(voce[0x40:0x42], "little")
        if lunghezza_nome < 2:
            continue
        nome = voce[:lunghezza_nome - 2].decode("utf-16-le", errors="replace")
        primo = int.from_bytes(voce[0x74:0x78], "little", signed=True)
        dimensione = int.from_bytes(voce[0x78:0x7C], "little")
        flussi[nome] = catena(primo)[:dimensione]
    return flussi


def _stringhe_sst(record: bytes) -> list[str]:
    """Decodifica la tabella delle stringhe condivise (SST) del BIFF8."""

    _totale, uniche = struct.unpack_from("<II", record, 0)
    stringhe: list[str] = []
    pos = 8
    for _ in range(uniche):
        if pos + 3 > len(record):
            break
        caratteri, bandiere = struct.unpack_from("<HB", record, pos)
        pos += 3
        ricchi = estesi = 0
        if bandiere & 0x08:  # rich-text: conta le run da saltare
            ricchi = struct.unpack_from("<H", record, pos)[0]
            pos += 2
        if bandiere & 0x04:  # extended: byte extra da saltare
            estesi = struct.unpack_from("<I", record, pos)[0]
            pos += 4
        if bandiere & 0x01:
            testo = record[pos:pos + 2 * caratteri].decode("utf-16-le", errors="replace")
            pos += 2 * caratteri
        else:
            testo = record[pos:pos + caratteri].decode("latin-1", errors="replace")
            pos += caratteri
        pos += 4 * ricchi + estesi
        stringhe.append(testo)
    return stringhe


def _valore_rk(rk: int) -> float:
    """Decodifica un numero in formato RK del BIFF8."""

    if rk & 0x02:
        valore = float(rk >> 2)
    else:
        alti = struct.pack("<I", rk & 0xFFFFFFFC)
        valore = struct.unpack("<d", b"\x00\x00\x00\x00" + alti)[0]
    if rk & 0x01:
        valore /= 100.0
    return valore


def _griglia_xls(contenuto: bytes) -> list[list[Any]]:
    """Legge il foglio di un file .xls (BIFF8 dentro OLE2) come griglia."""

    flussi = _flussi_ole2(contenuto)
    workbook = flussi.get("Workbook") or flussi.get("Book")
    if not workbook:
        raise PrelievoError("Nessun flusso «Workbook» nel file .xls.")
    celle: dict[tuple[int, int], Any] = {}
    stringhe: list[str] = []
    pos = 0
    while pos + 4 <= len(workbook):
        tipo, lunghezza = struct.unpack_from("<HH", workbook, pos)
        dati = workbook[pos + 4:pos + 4 + lunghezza]
        pos += 4 + lunghezza
        if tipo == 0x00FC:  # SST
            stringhe = _stringhe_sst(dati)
        elif tipo == 0x00FD and len(dati) >= 10:  # LABELSST
            riga, colonna, _xf, indice = struct.unpack("<HHHI", dati[:10])
            if indice < len(stringhe):
                celle[(riga, colonna)] = stringhe[indice]
        elif tipo == 0x0203 and len(dati) >= 14:  # NUMBER
            riga, colonna, _xf, valore = struct.unpack("<HHHd", dati[:14])
            celle[(riga, colonna)] = valore
        elif tipo == 0x027E and len(dati) >= 10:  # RK
            riga, colonna, _xf, rk = struct.unpack("<HHHi", dati[:10])
            celle[(riga, colonna)] = _valore_rk(rk)
        elif tipo == 0x00BD and len(dati) >= 6:  # MULRK
            riga, prima = struct.unpack_from("<HH", dati, 0)
            ultima = struct.unpack_from("<H", dati, len(dati) - 2)[0]
            blocco = dati[4:-2]
            for k in range(ultima - prima + 1):
                if len(blocco) < 6 * (k + 1):
                    break
                _xf, rk = struct.unpack_from("<Hi", blocco, 6 * k)
                celle[(riga, prima + k)] = _valore_rk(rk)
        if len(celle) > MAX_CELLE:
            raise PrelievoError("Il file .xls contiene troppe celle.")
    if not celle:
        raise PrelievoError("Nessuna cella letta dal file .xls.")
    righe = max(r for r, _ in celle) + 1
    colonne = max(c for _, c in celle) + 1
    if righe * colonne > MAX_CELLE:
        raise PrelievoError("Il file .xls contiene una griglia troppo grande.")
    return [[celle.get((r, c)) for c in range(colonne)] for r in range(righe)]


def leggi_griglia(contenuto: bytes, nome_file: str = "") -> list[list[Any]]:
    """Sceglie il parser dal firmamento del file (non dall'estensione)."""

    if contenuto[:8] == FIRMAMENTO_OLE2:
        return _griglia_xls(contenuto)
    if contenuto[:4] == FIRMAMENTO_ZIP:
        return _griglia_xlsx(contenuto)
    raise PrelievoError(
        "Formato non riconosciuto: carica il file .xls o .xlsx scaricato dalla pagina Snam "
        f"(file ricevuto: {nome_file or 'sconosciuto'})."
    )


# --------------------------------------------------------------- validazione


def _numero(grezzo: Any) -> float | None:
    """Converte un valore di cella in numero, se possibile."""

    if isinstance(grezzo, bool) or grezzo is None:
        return None
    if isinstance(grezzo, (int, float)):
        return float(grezzo)
    try:
        return float(str(grezzo).strip().replace(",", "."))
    except ValueError:
        return None


def _data_da_seriale(seriale: float) -> date | None:
    """Data ISO da un numero seriale di Excel (epoca 1899-12-30)."""

    try:
        giorni = int(seriale)
        if not 1 <= giorni <= 200_000:
            return None
        return EPOCA_EXCEL + timedelta(days=giorni)
    except (ValueError, OverflowError):
        return None


def _anno_termico_di(prima_data: date) -> str:
    """Anno termico «AAAA-AAAA+1» a partire dalla prima data del file."""

    inizio = prima_data.year if prima_data.month >= 10 else prima_data.year - 1
    return f"{inizio}-{inizio + 1}"


def valida_griglia(griglia: list[list[Any]]) -> dict[str, Any]:
    """Valida la griglia letta e restituisce la struttura di risposta."""

    if not griglia:
        raise PrelievoError("Il file non contiene righe.")
    intestazione = [str(cella).strip() if cella is not None else "" for cella in griglia[0]]
    if not intestazione or intestazione[0].lower() != "data":
        raise PrelievoError("La prima colonna dell'intestazione deve essere «Data».")
    colonna = {nome: i for i, nome in enumerate(intestazione) if nome}
    mancanti = [p for p in PARAMETRI_ATTESI if p not in colonna]
    if mancanti:
        raise PrelievoError(
            "Parametri mancanti nell'intestazione: " + ", ".join(mancanti),
            errors=[f"parametro mancante: {p}" for p in mancanti],
        )
    righe = [riga for riga in griglia[1:] if riga and riga[0] is not None]
    if len(righe) not in (365, 366):
        raise PrelievoError(f"Attesi 365 o 366 giorni, trovate {len(righe)} righe.")
    return _compila(righe, colonna)


def _compila(righe: list[list[Any]], colonna: dict[str, int]) -> dict[str, Any]:
    """Costruisce righe, somme e controlli; alza PrelievoError se non validi."""

    errori: list[str] = []
    avvisi: list[str] = []
    record: list[dict[str, Any]] = []
    somme = {p: 0.0 for p in PARAMETRI_ATTESI}
    zeri = 0
    data_precedente: date | None = None
    for numero, riga in enumerate(righe, start=2):
        seriale = _numero(riga[0])
        data = _data_da_seriale(seriale) if seriale is not None else None
        if data is None:
            errori.append(f"riga {numero}: data non valida ({riga[0]!r})")
            continue
        if data_precedente is not None and data != data_precedente + timedelta(days=1):
            avvisi.append(
                f"riga {numero}: salto di date ({data_precedente.isoformat()} → {data.isoformat()})"
            )
        data_precedente = data
        valori, zeri = _valori_riga(riga, colonna, numero, somme, zeri, errori)
        giorno = riga[colonna["Giorno"]] if "Giorno" in colonna and colonna["Giorno"] < len(riga) else None
        record.append({
            "data": data.isoformat(),
            "giorno": str(giorno).strip() if giorno is not None else "",
            "valori": valori,
        })
    for parametro in PARAMETRI_ATTESI:
        if abs(somme[parametro] - 100.0) > TOLLERANZA_SOMMA:
            errori.append(f"colonna {parametro}: la somma è {somme[parametro]:.6f}, atteso 100")
    if errori:
        raise PrelievoError("Il file non supera i controlli sui profili di prelievo.", errors=errori)
    if zeri:
        avvisi.append(f"{zeri} celle con valore nullo (1E-8).")
    prima_data = date.fromisoformat(record[0]["data"])
    if prima_data.month != 10 or prima_data.day != 1:
        avvisi.append(f"L'anno termico non inizia il 1° ottobre (prima data: {prima_data.isoformat()}).")
    return {
        "anno_termico": _anno_termico_di(prima_data),
        "giorni": len(record),
        "parametri": list(PARAMETRI_ATTESI),
        "righe": record,
        "somme": {p: round(somme[p], 6) for p in PARAMETRI_ATTESI},
        "zeri": zeri,
        "avvisi": avvisi,
    }


def _valori_riga(
    riga: list[Any],
    colonna: dict[str, int],
    numero: int,
    somme: dict[str, float],
    zeri: int,
    errori: list[str],
) -> tuple[list[float], int]:
    """I 20 valori numerici di una riga, aggiornando somme e contatori."""

    valori: list[float] = []
    for parametro in PARAMETRI_ATTESI:
        indice = colonna[parametro]
        grezzo = riga[indice] if indice < len(riga) else None
        valore = _numero(grezzo)
        if valore is None:
            errori.append(f"riga {numero}, {parametro}: valore non numerico ({grezzo!r})")
            valore = 0.0
        somme[parametro] += valore
        if valore < 1e-6:
            zeri += 1
        valori.append(valore)
    return valori, zeri


# ------------------------------------------------------------- ingresso API


def _contenuto_da_base64(dati: dict[str, Any]) -> bytes:
    """Decodifica il contenuto del file caricato (payload base64)."""

    grezzo = dati.get("contenuto_base64") or dati.get("content_base64") or ""
    if not grezzo:
        raise PrelievoError(
            "Carica il file .xls o .xlsx delle percentuali di prelievo, "
            "oppure prova lo scarico automatico da Jarvis."
        )
    try:
        return base64.b64decode(grezzo, validate=True)
    except (binascii.Error, ValueError) as errore:
        raise PrelievoError("Il contenuto del file non è un base64 valido.") from errore


def sistema(dati: dict[str, Any]) -> dict[str, Any]:
    """Elabora i profili di prelievo: file caricato oppure scarico da Jarvis."""

    if dati.get("scarica"):
        contenuto, meta = scarica_da_jarvis(str(dati.get("anno") or ""))
        origine = "Jarvis (Snam)"
    else:
        contenuto = _contenuto_da_base64(dati)
        meta = {
            "file": str(dati.get("nome_file") or "profili.xls").strip(),
            "aggiornato_il": "",
        }
        origine = "File caricato"
    griglia = leggi_griglia(contenuto, meta["file"])
    esito = valida_griglia(griglia)
    return {
        "fonte": {
            "pubblicazione": TIPOLOGIA_PRELIEVO,
            "origine": origine,
            "file": meta["file"],
            "aggiornato_il": meta.get("aggiornato_il", ""),
        },
        **esito,
        "nota": (
            "Ogni colonna percentuale somma 100 sull'anno termico; "
            "il valore 1E-8 indica zero / non applicabile."
        ),
    }


# ------------------------------------------------------- fetch live (Jarvis)


def _scegli_file(candidati: list[dict[str, Any]], anno_termico: str) -> dict[str, Any]:
    """Sceglie il file: quello dell'anno termico chiesto, altrimenti il più recente."""

    if anno_termico:
        if "-" in anno_termico:
            gettone = anno_termico
        elif anno_termico.isdigit():
            gettone = f"{anno_termico}-{int(anno_termico) + 1}"
        else:
            gettone = anno_termico
        filtrati = [
            voce for voce in candidati
            if gettone in str(voce.get("nome_file_ITA", ""))
        ]
        if not filtrati:
            disponibili = sorted({str(v.get("nome_file_ITA", "")) for v in candidati})
            raise PrelievoError(
                f"Nessun file per l'anno termico {anno_termico}. "
                "Disponibili: " + ", ".join(disponibili)
            )
        candidati = filtrati
    return max(candidati, key=lambda v: str(v.get("aggiornato_il", "")))


def scarica_da_jarvis(anno_termico: str = "") -> tuple[bytes, dict[str, str]]:
    """Scarica live il file delle percentuali di prelievo per l'anno dato.

    Chiede l'elenco delle pubblicazioni «Profili di prelievo standard» e
    scarica il file più recente (o quello dell'anno termico richiesto).
    Ogni fallimento diventa un ``PrelievoError`` che invita al caricamento
    manuale: il portale non dipende dalla rete.
    """

    try:
        elenco = jarvis.elenco_pubblicazioni(TIPOLOGIA_PRELIEVO)
    except jarvis.JarvisError as errore:
        raise PrelievoError(
            f"Il recupero automatico da Jarvis non è riuscito ({errore}). "
            "Puoi comunque caricare il file .xls/.xlsx scaricato dalla pagina Snam."
        ) from errore
    candidati = [
        voce for voce in elenco
        if isinstance(voce, dict)
        and str(voce.get("nome_file_ITA", "")).startswith("PERCENTUALI_DI_PRELIEVO_AT_")
    ]
    if not candidati:
        raise PrelievoError(
            "Jarvis non ha file «PERCENTUALI_DI_PRELIEVO_AT_» pubblicati. "
            "Carica il file scaricato dalla pagina Snam."
        )
    prescelto = _scegli_file(candidati, anno_termico.strip())
    download_id = jarvis.download_id_ita(prescelto)
    if not download_id:
        raise PrelievoError(
            "Il file scelto non ha un identificativo di download: carica il file a mano."
        )
    nome_base = str(prescelto["nome_file_ITA"])
    formato = str(prescelto.get("formato_file") or "").lower().lstrip(".")
    if formato not in ("xls", "xlsx"):
        formato = "xls"
    nome_file = f"{nome_base}.{formato}"
    try:
        contenuto = jarvis.scarica_documento(download_id, nome_file)
    except jarvis.JarvisError as errore:
        raise PrelievoError(
            f"Il download del file da Jarvis non è riuscito ({errore}). "
            "Puoi comunque caricare il file scaricato dalla pagina Snam."
        ) from errore
    return contenuto, {
        "file": nome_file,
        "aggiornato_il": str(prescelto.get("aggiornato_il", "")),
    }
