"""Avvio di Vettore come programma singolo (eseguibile o `python launcher.py`).

Avvia il server su http://127.0.0.1:8080 e apre il browser. I dati vivono
in ~/.vettore/vettore.db (sovrascrivibile con la variabile VETTORE_DB), così
sopravvivono ad aggiornamenti e spostamenti dell'eseguibile.
"""

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

INDIRIZZO = "127.0.0.1"
PORTA = int(os.environ.get("VETTORE_PORTA", "8080"))


def _porta_libera() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((INDIRIZZO, PORTA))
            return True
        except OSError:
            return False


def _apri_quando_pronto() -> None:
    # attende che il server accetti connessioni prima di aprire il browser:
    # l'eseguibile onefile impiega qualche secondo a partire
    scadenza = time.monotonic() + 30
    while time.monotonic() < scadenza:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((INDIRIZZO, PORTA)) == 0:
                webbrowser.open(f"http://{INDIRIZZO}:{PORTA}")
                return
        time.sleep(0.3)


def main() -> None:
    if not _porta_libera():
        print(
            f"La porta {PORTA} è già in uso: forse Vettore è già aperto "
            f"su http://{INDIRIZZO}:{PORTA}, oppure imposta VETTORE_PORTA "
            f"su un'altra porta.",
            flush=True,
        )
        sys.exit(1)

    if "VETTORE_DB" not in os.environ:
        dati = Path.home() / ".vettore"
        dati.mkdir(exist_ok=True)
        os.environ["VETTORE_DB"] = str(dati / "vettore.db")

    import uvicorn

    from app.main import app

    if not os.environ.get("VETTORE_NO_BROWSER"):
        threading.Thread(target=_apri_quando_pronto, daemon=True).start()

    print(f"Vettore in avvio su http://{INDIRIZZO}:{PORTA} — Ctrl+C per uscire", flush=True)
    # loop/http espliciti: evitano dipendenze binarie opzionali nell'eseguibile
    uvicorn.run(app, host=INDIRIZZO, port=PORTA, loop="asyncio", http="h11", log_level="warning")


if __name__ == "__main__":
    main()
