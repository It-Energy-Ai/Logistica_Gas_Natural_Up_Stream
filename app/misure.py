"""Misure dei PDR da SIICloud (WebDAV Nextcloud).

Il distributore pubblica i file di misura su SIICloud; l'UDD li scarica.
Tutti i file sono XML: i prefissi TGL indicano letture giornaliere,
TMG e TML letture mensili. Il modulo è stateless: indirizzo WebDAV,
utente e password arrivano con la richiesta e non vengono mai salvati.
"""

from __future__ import annotations

import base64
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

MAX_CORPO_BYTES = 512 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RECORD = 200
TIMEOUT_SECONDS = 30
USER_AGENT = "Vettore (portale shipper; misure SIICloud)"
NS_DAV = "DAV:"

TIPO_GIORNALIERA = "giornaliera"
TIPO_MENSILE = "mensile"

NOTA_ALBERATURA = (
    "Alberatura tipica pubblicata dal distributore: "
    "TMG_[PIVA_DISTR]/DISTRIBUTORE/TMG_[PIVA_DISTR]_[PIVA_UDD]/[ANNO]/[MESEGIORNO]. "
    "Prefissi dei file: TGL letture giornaliere, TMG o TML letture mensili."
)


class MisureError(ValueError):
    """Errore del modulo misure con elenco di errori di campo."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


def classifica(nome_file):
    """Riconosce il tipo di misura dal prefisso del nome del file."""
    nome = (nome_file or "").strip().upper()
    if nome.startswith("TGL"):
        return TIPO_GIORNALIERA
    if nome.startswith(("TMG", "TML")):
        return TIPO_MENSILE
    return None


def _valida_credenziali(url, utente, password):
    errori = []
    if not url:
        errori.append({"field": "url", "message": "indirizzo WebDAV mancante"})
    elif not url.lower().startswith(("http://", "https://")):
        errori.append({"field": "url", "message": "l'indirizzo deve iniziare con http:// o https://"})
    if not utente:
        errori.append({"field": "utente", "message": "utente mancante"})
    if not password:
        errori.append({"field": "password", "message": "password mancante"})
    if errori:
        raise MisureError("dati di accesso incompleti", errori)


def _messaggio_http(errore):
    codici = {
        401: "credenziali rifiutate dal server (401): verificare utente e password",
        403: "accesso negato alla risorsa (403): verificare percorso e condivisioni",
        404: "percorso non trovato (404): verificare la cartella indicata",
        502: "bad gateway (502): SIICloud non è disponibile, riprovare più tardi",
    }
    return codici.get(errore.code, f"errore HTTP {errore.code} dal server")


def _richiesta(url, metodo, utente, password, intestazioni=None):
    token = base64.b64encode(f"{utente}:{password}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": USER_AGENT,
        **(intestazioni or {}),
    }
    richiesta = urllib.request.Request(url, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(richiesta, timeout=TIMEOUT_SECONDS) as risposta:
            return risposta.read(MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as errore:
        raise MisureError(_messaggio_http(errore)) from errore
    except urllib.error.URLError as errore:
        raise MisureError(f"server non raggiungibile (dettaglio: {errore.reason})") from errore
    except (TimeoutError, OSError) as errore:
        raise MisureError(f"problema di rete verso il server (dettaglio: {errore})") from errore


def _url_percorso(base, percorso):
    base_norm = base.strip()
    if not base_norm.endswith("/"):
        base_norm += "/"
    if not percorso:
        return base_norm
    return base_norm + urllib.parse.quote(str(percorso).strip("/"), safe="/")


def _parse_multistatus(corpo, base_path):
    try:
        radice = ET.fromstring(corpo)
    except ET.ParseError as errore:
        raise MisureError("risposta del server non valida (atteso XML multistatus)") from errore
    prefisso = base_path.rstrip("/")
    voci = []
    for risposta in radice.findall(f"{{{NS_DAV}}}response"):
        href = risposta.findtext(f"{{{NS_DAV}}}href") or ""
        path_voce = urllib.parse.unquote(href).rstrip("/")
        if path_voce == prefisso:
            continue
        proprieta = risposta.find(f"{{{NS_DAV}}}propstat/{{{NS_DAV}}}prop")
        cartella = (
            proprieta is not None
            and proprieta.find(f"{{{NS_DAV}}}resourcetype/{{{NS_DAV}}}collection") is not None
        )
        dimensione = proprieta.findtext(f"{{{NS_DAV}}}getcontentlength") if proprieta is not None else None
        modificato = proprieta.findtext(f"{{{NS_DAV}}}getlastmodified") if proprieta is not None else None
        nome = path_voce.rsplit("/", 1)[-1]
        relativo = path_voce[len(prefisso):].strip("/") if path_voce.startswith(prefisso) else path_voce
        voci.append({
            "nome": nome,
            "percorso": relativo,
            "cartella": cartella,
            "dimensione": int(dimensione) if (dimensione or "").isdigit() else None,
            "modificato": modificato or "",
            "tipo": None if cartella else classifica(nome),
        })
    return voci


def elenca(url, utente, password, percorso=""):
    """Elenca file e sottocartelle di una cartella di SIICloud."""
    destinazione = _url_percorso(url, percorso)
    corpo = _richiesta(destinazione, "PROPFIND", utente, password, {"Depth": "1"})
    base_path = urllib.parse.urlparse(_url_percorso(url, "")).path
    return _parse_multistatus(corpo, base_path)


def scarica_file(url, utente, password, percorso):
    """Scarica un file di misura e ne restituisce il contenuto."""
    if not percorso:
        raise MisureError("percorso del file mancante")
    destinazione = _url_percorso(url, percorso)
    contenuto = _richiesta(destinazione, "GET", utente, password)
    if len(contenuto) > MAX_FILE_BYTES:
        raise MisureError("file troppo grande per essere aperto")
    return contenuto


def _locale(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def riassumi_xml(contenuto):
    """Riassume il contenuto di un file di misura XML in forma generica."""
    try:
        radice = ET.fromstring(contenuto)
    except ET.ParseError as errore:
        raise MisureError("il file non è un XML valido") from errore
    elementi = list(radice.iter())
    conteggi = {}
    for elemento in elementi:
        if elemento is not radice and len(elemento) > 0:
            tag = _locale(elemento.tag)
            conteggi[tag] = conteggi.get(tag, 0) + 1
    tag_record = max(conteggi, key=conteggi.get) if conteggi else None
    campi = []
    record = []
    if tag_record:
        for elemento in elementi:
            if elemento is radice or _locale(elemento.tag) != tag_record or len(elemento) == 0:
                continue
            riga = {}
            for figlio in elemento:
                chiave = _locale(figlio.tag)
                if chiave not in campi:
                    campi.append(chiave)
                riga[chiave] = (figlio.text or "").strip()
            record.append(riga)
            if len(record) >= MAX_RECORD:
                break
    return {
        "radice": _locale(radice.tag),
        "elementi": len(elementi),
        "tag_record": tag_record,
        "numero_record": conteggi.get(tag_record, 0) if tag_record else 0,
        "campi": campi,
        "record": record,
    }


def _fonte(url, percorso):
    return {"origine": "SIICloud (WebDAV)", "url": url, "percorso": percorso or "/"}


def sistema(dati):
    """Punto d'ingresso: elenca una cartella oppure apre un file di misura."""
    dati = dati if isinstance(dati, dict) else {}
    url = str(dati.get("url") or "").strip()
    utente = str(dati.get("utente") or "").strip()
    password = str(dati.get("password") or "")
    percorso = str(dati.get("percorso") or "").strip()
    azione = str(dati.get("azione") or "elenca").strip().lower()
    _valida_credenziali(url, utente, password)
    if azione == "elenca":
        return {"fonte": _fonte(url, percorso), "voci": elenca(url, utente, password, percorso), "nota": NOTA_ALBERATURA}
    if azione == "apri":
        contenuto = scarica_file(url, utente, password, percorso)
        nome = urllib.parse.unquote(percorso).rstrip("/").rsplit("/", 1)[-1]
        return {
            "fonte": _fonte(url, percorso),
            "file": {"nome": nome, "tipo": classifica(nome), "dimensione": len(contenuto)},
            "contenuto": riassumi_xml(contenuto),
            "nota": NOTA_ALBERATURA,
        }
    raise MisureError("azione non valida: atteso «elenca» o «apri»")
