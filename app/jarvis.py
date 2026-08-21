"""Accesso comune alle pubblicazioni pubbliche di Jarvis (Snam Rete Gas).

Jarvis è il portale dati pubblici di Snam. Più moduli del portale leggono le
sue pubblicazioni (coefficienti Wkr, valori percentuali dei profili di
prelievo standard, …): questo modulo raccoglie il codice di accesso comune,
così ogni modulo si occupa solo del *proprio* formato di file.

Due scelte di fondo, dichiarate:

* **Nessuna credenziale hardcoded**: il portale legge la configurazione
  pubblica del sito Snam (``/config/portal-public-config.json``) per ricavare
  ``user_key`` e indirizzo dell'API. Se Snam li aggiorna, il modulo continua a
  funzionare senza modifiche.
* **Nessuna dipendenza nuova**: il fetch usa ``urllib`` della libreria
  standard, con timeout. L'API di Jarvis non è un contratto pubblico: se la
  chiamata fallisce, chi usa questo modulo deve dirlo all'operatore in modo
  chiaro (di solito invitandolo a scaricare il file a mano).

I dati sono pubblici ma Snam vieta la redistribuzione a terzi: i moduli che
usano queste primitive li mostrano all'operatore che li ha richiesti, senza
conservarli né ritrasmetterli.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

JARVIS_CONFIG_URL = "https://jarvis.snam.it/config/portal-public-config.json"
JARVIS_PAGINA_URL = (
    "https://jarvis.snam.it/public-data?pubblicazione={pubblicazione}"
    "&periodo={periodo}&lang=it"
)
USER_AGENT = "Vettore (portale shipper; lettura operativa dei dati pubblici Snam)"
TIMEOUT_SECONDS = 15

# Intestazioni richieste dall'API pubblica di Jarvis (le stesse che usa il
# frontend del sito Snam).
INTESTAZIONI_API = {
    "X-jarvis-multiCompany": "SNM",
    "Accept": "application/jarvis.pubblicazioni_smart.v2+json",
    "Origin": "https://jarvis.snam.it",
    "Referer": "https://jarvis.snam.it/",
}


class JarvisError(ValueError):
    """Errore di accesso a Jarvis; i moduli lo traducono nel proprio errore."""


# ------------------------------------------------------------------ HTTP


def _verifica_url(url: str) -> None:
    """Difesa in profondità: ammette solo http(s).

    Gli indirizzi dell'API derivano dalla configurazione pubblica remota di
    Snam: se quella fonte fosse compromessa, uno schema diverso (per esempio
    ``file://``) permetterebbe letture locali. Meglio rifiutare prima.
    """

    schema = urllib.parse.urlparse(url).scheme
    if schema not in ("http", "https"):
        raise JarvisError(f"Indirizzo di Jarvis non valido: schema «{schema}» non ammesso.")


def http_json(url: str, payload: Any = None, headers: dict[str, str] | None = None) -> Any:
    """GET (payload=None) o POST JSON verso Jarvis; restituisce il JSON decodificato."""

    _verifica_url(url)
    intestazioni = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        intestazioni.update(headers)
    corpo = json.dumps(payload).encode("utf-8") if payload is not None else None
    if corpo is not None:
        intestazioni["Content-Type"] = "application/json"
    richiesta = urllib.request.Request(url, data=corpo, headers=intestazioni)
    with urllib.request.urlopen(richiesta, timeout=TIMEOUT_SECONDS) as risposta:
        return json.loads(risposta.read().decode("utf-8"))


def http_bytes(url: str, headers: dict[str, str] | None = None) -> bytes:
    """Scarica un documento binario da Jarvis."""

    _verifica_url(url)
    intestazioni = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    if headers:
        intestazioni.update(headers)
    richiesta = urllib.request.Request(url, headers=intestazioni)
    with urllib.request.urlopen(richiesta, timeout=TIMEOUT_SECONDS) as risposta:
        return risposta.read()


# ------------------------------------------------------------------ config


def leggi_config() -> dict[str, str]:
    """Legge la configurazione pubblica del sito Snam.

    Restituisce ``{"user_key": …, "base": …}`` dove ``base`` è l'indirizzo
    dell'API delle pubblicazioni. Ogni fallimento diventa ``JarvisError``.
    """

    try:
        config = http_json(JARVIS_CONFIG_URL)
        return {
            "user_key": config["key_config"]["user_key"],
            "base": config["ms_config"]["pubblicazioni_public"],
        }
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError, OSError) as errore:
        raise JarvisError(
            f"Non riesco a raggiungere la configurazione pubblica di Jarvis. (dettaglio: {errore})"
        ) from errore


# ------------------------------------------------------------- pubblicazioni


def elenco_pubblicazioni(tipologia: str, anno: int | str | None = None) -> list[dict[str, Any]]:
    """Elenca i file pubblicati da Jarvis per una tipologia (e un anno, se dato)."""

    config = leggi_config()
    url = (
        f"{config['base']}/pubblicazioni/getPublications"
        f"?user_key={urllib.parse.quote(config['user_key'])}"
    )
    payload = [{"tag": "tipologia", "value": tipologia}]
    if anno is not None:
        payload.append({"tag": "anno_pubblicazione", "value": str(anno)})
    try:
        elenco = http_json(url, payload=payload, headers=INTESTAZIONI_API)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as errore:
        raise JarvisError(
            f"La chiamata all'API pubblica di Jarvis non è andata a buon fine. (dettaglio: {errore})"
        ) from errore
    return elenco if isinstance(elenco, list) else []


def scarica_documento(download_id: str, nome_file: str) -> bytes:
    """Scarica un singolo documento di Jarvis dato il suo identificativo."""

    config = leggi_config()
    url = (
        f"{config['base']}/document/get?d={urllib.parse.quote(download_id)}"
        f"&fileName={urllib.parse.quote(nome_file)}"
        f"&user_key={urllib.parse.quote(config['user_key'])}"
    )
    try:
        return http_bytes(url, headers={"X-jarvis-multiCompany": "SNM"})
    except (urllib.error.URLError, TimeoutError, OSError) as errore:
        raise JarvisError(
            f"Il download del documento da Jarvis non è riuscito. (dettaglio: {errore})"
        ) from errore


def download_id_ita(voce: dict[str, Any]) -> str | None:
    """L'identificativo di download in lingua italiana di una voce dell'elenco."""

    return next(
        (d["value"] for d in voce.get("download_id", []) if d.get("lang") == "ITA"),
        None,
    )
