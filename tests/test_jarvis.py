"""Difese del modulo di accesso a Jarvis (Snam).

Gli indirizzi dell'API derivano dalla configurazione pubblica remota di Snam:
se quella fonte fosse compromessa, uno schema diverso da http(s) permetterebbe
letture locali. Questi test presidiano il rifiuto preventivo.
"""

from __future__ import annotations

import pytest

from app import jarvis


# ─────────────────────────────── schema degli indirizzi


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://jarvis.snam.it/file.xls",
        "gopher://jarvis.snam.it/",
        "jarvis.snam.it/senza-schema",
    ],
)
def test_schemi_diversi_da_http_sono_rifiutati(url):
    """Solo http(s): qualunque altro schema è bloccato prima della rete."""

    with pytest.raises(jarvis.JarvisError, match="non ammesso"):
        jarvis.http_json(url)
    with pytest.raises(jarvis.JarvisError, match="non ammesso"):
        jarvis.http_bytes(url)


def test_http_e_https_sono_ammessi():
    """La validazione guarda solo lo schema: http(s) passa al livello rete."""

    class _RispostaFinta:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def _finto_open(richiesta, timeout=None):
        return _RispostaFinta()

    import urllib.request

    originale = urllib.request.urlopen
    urllib.request.urlopen = _finto_open
    try:
        assert jarvis.http_json("https://jarvis.snam.it/config.json") == {}
        assert jarvis.http_bytes("http://jarvis.snam.it/file.xls") == b"{}"
    finally:
        urllib.request.urlopen = originale
