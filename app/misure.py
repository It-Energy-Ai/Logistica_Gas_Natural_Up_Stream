"""Misure dei PDR da SIICloud (WebDAV Nextcloud).

Il distributore pubblica i file di misura su SIICloud; l'UDD li scarica.
I file sono archivi ZIP contenenti un singolo XML del tracciato
FlussiDatiMisuraPrelievoGAS. I flussi reali sono: TGL letture giornaliere,
TMV e SWG1 letture mensili, IGMG cambio contatore/correttore con lettura
d'avvio del nuovo apparato. Il modulo è stateless: indirizzo WebDAV, utente
e password arrivano con la richiesta e non vengono mai salvati.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date

MAX_CORPO_BYTES = 512 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RECORD = 200
MAX_GIORNI_SERIE = 60
TIMEOUT_SECONDS = 30
USER_AGENT = "Vettore (portale shipper; misure SIICloud)"
NS_DAV = "DAV:"
SCHEMI_CONSENTITI = ("http", "https")
FIRMAMENTO_ZIP = b"PK\x03\x04"

TIPO_GIORNALIERA = "giornaliera"
TIPO_MENSILE = "mensile"
TIPO_CAMBIO = "cambio"

NOTA_ALBERATURA = (
    "Alberatura tipica pubblicata dal distributore: "
    "TMG_[PIVA_DISTR]_[PIVA_UDD]/[ANNO]/[MESEGIORNO]. "
    "Flussi: TGL letture giornaliere, TMV e SWG1 letture mensili, "
    "IGMG cambio contatore con lettura d'avvio del nuovo apparato."
)

_FLUSSO_NEL_NOME = re.compile(r"_(TGL|TMV|SWG\d*|TML|TMG|IGMG)_")


class MisureError(ValueError):
    """Errore del modulo misure con elenco di errori di campo."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


def classifica(nome_file):
    """Riconosce il tipo di misura dal nome del file (prefisso o token)."""
    nome = (nome_file or "").strip().upper()
    if not nome:
        return None
    corrispondenza = _FLUSSO_NEL_NOME.search(nome)
    token = corrispondenza.group(1) if corrispondenza else nome
    if token.startswith("TGL"):
        return TIPO_GIORNALIERA
    if token.startswith(("TMV", "SWG", "TMG", "TML")):
        return TIPO_MENSILE
    if token.startswith("IGMG"):
        return TIPO_CAMBIO
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


def _indirizzo_pubblico(indirizzo):
    """Vero se l'IP non appartiene a reti private, locali o riservate."""
    return not (
        indirizzo.is_private
        or indirizzo.is_loopback
        or indirizzo.is_link_local
        or indirizzo.is_reserved
        or indirizzo.is_multicast
        or indirizzo.is_unspecified
    )


def _verifica_destinazione(url):
    """Impedisce richieste verso host interni o non HTTP(S) (SSRF)."""
    parti = urllib.parse.urlparse(url)
    if parti.scheme not in SCHEMI_CONSENTITI:
        raise MisureError("solo gli indirizzi http:// e https:// sono ammessi")
    host = parti.hostname
    if not host:
        raise MisureError("indirizzo WebDAV non valido: host mancante")
    try:
        risoluzioni = socket.getaddrinfo(host, parti.port or (443 if parti.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as errore:
        raise MisureError(f"host non risolvibile (dettaglio: {errore})") from errore
    for _famiglia, _tipo, _proto, _canon, (_ip, *_altro) in risoluzioni:
        try:
            indirizzo = ipaddress.ip_address(_ip)
        except ValueError as errore:
            raise MisureError("indirizzo WebDAV non valido") from errore
        if not _indirizzo_pubblico(indirizzo):
            raise MisureError("l'indirizzo punta a una rete privata o locale: non è ammesso")


class _RedirectSicuro(urllib.request.HTTPRedirectHandler):
    """Segue i redirect solo verso http(s) e host pubblici."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _verifica_destinazione(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_RedirectSicuro)


def _messaggio_http(errore):
    codici = {
        401: "credenziali rifiutate dal server (401): verificare utente e password",
        403: "accesso negato alla risorsa (403): verificare percorso e condivisioni",
        404: "percorso non trovato (404): verificare la cartella indicata",
        502: "bad gateway (502): SIICloud non è disponibile, riprovare più tardi",
    }
    return codici.get(errore.code, f"errore HTTP {errore.code} dal server")


def _richiesta(url, metodo, utente, password, intestazioni=None):
    _verifica_destinazione(url)
    token = base64.b64encode(f"{utente}:{password}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": USER_AGENT,
        **(intestazioni or {}),
    }
    richiesta = urllib.request.Request(url, method=metodo, headers=headers)
    try:
        with _OPENER.open(richiesta, timeout=TIMEOUT_SECONDS) as risposta:
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
            "dimensione": int(dimensione) if dimensione and dimensione.isdigit() else None,
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


def _elenca_sicura(url, utente, password, percorso):
    try:
        return elenca(url, utente, password, percorso)
    except MisureError:
        return []


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


def _contenuto_xml(contenuto, nome_file=""):
    """Apre lo ZIP se necessario e restituisce i byte dell'XML interno."""
    if contenuto[:4] == FIRMAMENTO_ZIP:
        try:
            archivio = zipfile.ZipFile(io.BytesIO(contenuto))
        except zipfile.BadZipFile as errore:
            raise MisureError("il file non è un archivio ZIP valido") from errore
        with archivio:
            nomi_xml = [n for n in archivio.namelist() if n.lower().endswith(".xml")]
            if not nomi_xml:
                raise MisureError("l'archivio non contiene file XML")
            return archivio.read(nomi_xml[0])
    return contenuto


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
    tag_record = max(conteggi, key=lambda tag: conteggi[tag]) if conteggi else None
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


def _testo_locale(elemento, *tags):
    """Testo del primo figlio il cui tag locale è fra quelli indicati."""
    cercati = {t.lower() for t in tags}
    for figlio in elemento:
        if _locale(figlio.tag).lower() in cercati:
            return (figlio.text or "").strip()
    return ""


def _data_it(testo):
    """Converte una data GG/MM/AAAA in formato ISO AAAA-MM-GG."""
    testo = (testo or "").strip()
    parti = testo.split("/")
    if len(parti) != 3 or not all(p.isdigit() for p in parti):
        return None
    giorno, mese, anno = parti
    try:
        return date(int(anno), int(mese), int(giorno)).isoformat()
    except ValueError:
        return None


def _intero(testo):
    testo = (testo or "").strip()
    return int(testo) if testo.isdigit() else None


def _lettura(blocco, cod_pdr):
    data = _data_it(_testo_locale(blocco, "data_comp", "data_racc", "data_mis_eff"))
    valore = _intero(_testo_locale(blocco, "let_tot_conv"))
    if valore is None:
        valore = _intero(_testo_locale(blocco, "let_tot_prel"))
    if data is None or valore is None:
        return None
    return {"pdr": cod_pdr, "data": data, "valore": valore}


def _cambio(blocco, cod_pdr, data_misura):
    valore = _intero(_testo_locale(blocco, "let_misuratore"))
    data = _data_it(data_misura)
    if data is None or valore is None:
        return None
    return {"pdr": cod_pdr, "data": data, "valore": valore}


def leggi_flusso(contenuto):
    """Estrae letture cumulative e cambi contatore da un XML di misura."""
    try:
        radice = ET.fromstring(contenuto)
    except ET.ParseError as errore:
        raise MisureError("il file non è un XML valido") from errore
    letture = []
    cambi = []
    for nodo in radice.iter():
        if _locale(nodo.tag).lower() != "datipdr":
            continue
        cod_pdr = _testo_locale(nodo, "cod_pdr", "cod_PdDR")
        if not cod_pdr:
            continue
        for blocco in nodo:
            tag = _locale(blocco.tag).lower()
            if tag in ("letturegiornaliere", "datilettura"):
                lettura = _lettura(blocco, cod_pdr)
                if lettura:
                    letture.append(lettura)
            elif tag == "post-int":
                cambio = _cambio(blocco, cod_pdr, _testo_locale(nodo, "data_misura"))
                if cambio:
                    cambi.append(cambio)
    return letture, cambi


def serie_giornaliera(letture, cambi):
    """Trasforma letture cumulative in consumi giornalieri aggregati.

    Il consumo di un giorno è la differenza fra letture consecutive dello
    stesso PDR. Un cambio contatore (IGMG) azzera il contatore: la lettura
    d'avvio del nuovo apparato diventa il punto di ripartenza.
    """
    per_pdr = {}
    for lettura in letture:
        per_pdr.setdefault(lettura["pdr"], []).append((lettura["data"], lettura["valore"], False))
    for cambio in cambi:
        per_pdr.setdefault(cambio["pdr"], []).append((cambio["data"], cambio["valore"], True))
    consumi = {}
    for _pdr, eventi in per_pdr.items():
        eventi.sort(key=lambda evento: evento[0])
        precedente = None
        for data, valore, e_cambio in eventi:
            if e_cambio:
                precedente = valore
                continue
            if precedente is not None and valore >= precedente:
                consumi[data] = consumi.get(data, 0) + (valore - precedente)
            precedente = valore
    return [{"data": data, "valore": consumi[data]} for data in sorted(consumi)]


def _unisci(percorso, nome):
    base = (percorso or "").strip("/")
    return f"{base}/{nome}" if base else nome


def _scarica_e_leggi(url, utente, password, voce):
    try:
        contenuto = scarica_file(url, utente, password, voce["percorso"])
        xml = _contenuto_xml(contenuto, voce["nome"])
        letture, cambi = leggi_flusso(xml)
        return letture, cambi, None
    except MisureError as errore:
        return [], [], str(errore)


def costruisci_serie(url, utente, password, percorso="", giorni=MAX_GIORNI_SERIE):
    """Scarica i file di misura di una cartella e costruisce la serie."""
    voci = elenca(url, utente, password, percorso)
    file_misura = [v for v in voci if not v["cartella"] and v["tipo"]]
    cartelle = [v for v in voci if v["cartella"]]
    avvisi = []
    if not file_misura and cartelle:
        cartelle.sort(key=lambda v: v["nome"], reverse=True)
        scelte = cartelle[:giorni]
        with ThreadPoolExecutor(max_workers=8) as pool:
            risultati = list(pool.map(lambda c: _elenca_sicura(url, utente, password, _unisci(percorso, c["nome"])), scelte))
        for sotto in risultati:
            file_misura.extend(v for v in sotto if not v["cartella"] and v["tipo"])
    if not file_misura:
        raise MisureError("nessun file di misura trovato nel percorso indicato")
    letture, cambi, elaborati = [], [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        risultati = list(pool.map(lambda v: _scarica_e_leggi(url, utente, password, v), file_misura))
    for voce, (letture_file, cambi_file, avviso) in zip(file_misura, risultati):
        if avviso:
            avvisi.append(f"{voce['nome']}: {avviso}")
            continue
        letture.extend(letture_file)
        cambi.extend(cambi_file)
        elaborati.append(voce["nome"])
    serie = serie_giornaliera(letture, cambi)
    pdr = sorted({l["pdr"] for l in letture} | {c["pdr"] for c in cambi})
    return {
        "serie": serie,
        "dettagli": {
            "pdr": len(pdr),
            "letture": len(letture),
            "cambi": len(cambi),
            "file_elaborati": len(elaborati),
            "giorni_coperti": len(serie),
        },
        "avvisi": avvisi,
    }


def _giorni_richiesti(dati):
    try:
        giorni = int(dati.get("giorni") or MAX_GIORNI_SERIE)
    except (TypeError, ValueError):
        giorni = MAX_GIORNI_SERIE
    return max(1, min(giorni, MAX_GIORNI_SERIE))


def _fonte(url, percorso):
    return {"origine": "SIICloud (WebDAV)", "url": url, "percorso": percorso or "/"}


def sistema(dati):
    """Punto d'ingresso: elenca, apre un file o costruisce la serie."""
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
        xml = _contenuto_xml(contenuto, nome)
        return {
            "fonte": _fonte(url, percorso),
            "file": {"nome": nome, "tipo": classifica(nome), "dimensione": len(contenuto)},
            "contenuto": riassumi_xml(xml),
            "nota": NOTA_ALBERATURA,
        }
    if azione == "serie":
        esito = costruisci_serie(url, utente, password, percorso, _giorni_richiesti(dati))
        return {"fonte": _fonte(url, percorso), **esito, "nota": NOTA_ALBERATURA}
    raise MisureError("azione non valida: atteso «elenca», «apri» o «serie»")
