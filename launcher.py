"""Avvio di Vettore come programma singolo (eseguibile o `python launcher.py`).

Avvia il server su http://127.0.0.1:8080 e apre il browser. I dati vivono
in ~/.vettore/vettore.db (sovrascrivibile con la variabile VETTORE_DB), così
sopravvivono ad aggiornamenti e spostamenti dell'eseguibile.
"""

import os
import threading
import webbrowser
from pathlib import Path

INDIRIZZO = "127.0.0.1"
PORTA = int(os.environ.get("VETTORE_PORTA", "8080"))


def main() -> None:
    if "VETTORE_DB" not in os.environ:
        dati = Path.home() / ".vettore"
        dati.mkdir(exist_ok=True)
        os.environ["VETTORE_DB"] = str(dati / "vettore.db")

    import uvicorn

    from app.main import app

    if not os.environ.get("VETTORE_NO_BROWSER"):
        threading.Timer(
            1.5, lambda: webbrowser.open(f"http://{INDIRIZZO}:{PORTA}")
        ).start()

    print(f"Vettore in ascolto su http://{INDIRIZZO}:{PORTA} — Ctrl+C per uscire")
    # loop/http espliciti: evitano dipendenze binarie opzionali nell'eseguibile
    uvicorn.run(app, host=INDIRIZZO, port=PORTA, loop="asyncio", http="h11", log_level="warning")


if __name__ == "__main__":
    main()
