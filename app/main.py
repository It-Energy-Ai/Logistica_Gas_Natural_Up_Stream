"""Vettore — portale demo di logistica gas per shipper.

Backend leggero: serve il frontend (generato dal file di design) e
persiste su SQLite lo stato mutabile (nomine, configurazione, utenti).
Le integrazioni reali (Snam, GME, SSO) sono scenografia demo nel frontend.
"""

import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db

STATIC = Path(__file__).parent / "static"
COOKIE = "vettore_session"

# Chiavi di stato accettate dal client, con un validatore di forma ciascuna.
_is_bool = lambda v: isinstance(v, bool)
_is_int = lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10_000
# cap sul numero di chiavi: cfg cresce con i punti dinamici (px1, px2, ...)
_is_str_map = lambda v: isinstance(v, dict) and len(v) <= 256 and all(
    isinstance(k, str) and len(k) < 64 and isinstance(x, (str, bool)) and (len(x) < 64 if isinstance(x, str) else True)
    for k, x in v.items()
)


def _is_nom_list(v):
    return (
        isinstance(v, list)
        and len(v) <= 500
        and all(
            isinstance(r, dict)
            and set(r) == {"punto", "ciclo", "qta", "stato"}
            and all(isinstance(r[k], str) and len(r[k]) <= 120 for k in r)
            for r in v
        )
    )


def _righe_di(n):
    def check(v):
        return (
            isinstance(v, list)
            and len(v) <= 200
            and all(
                isinstance(r, list)
                and len(r) == n
                and all(isinstance(x, str) and len(x) <= 160 for x in r)
                for r in v
            )
        )
    return check


_is_punti_list = _righe_di(3)   # [nome, tipo, chiave]
_is_utenti_list = _righe_di(4)  # [chiave, iniziali, nome, email]


def _is_str_list(v):
    return isinstance(v, list) and len(v) <= 200 and all(isinstance(x, str) and len(x) <= 64 for x in v)



VALIDATORS = {
    "nomList": _is_nom_list,
    "cfg": _is_str_map,
    "hiddenPunti": _is_str_list,
    "extraPunti": _is_punti_list,
    "nextP": _is_int,
    "users": _is_str_map,
    "extraUsers": _is_utenti_list,
    "disabled": _is_str_map,
    "nextU": _is_int,
    "reps": _is_str_map,
    "gmeAuto": _is_bool,
    "gmeOk": _is_bool,
    "demoMode": _is_bool,
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Vettore", lifespan=lifespan)


def _sessione(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with db.connect() as conn:
        return db.email_sessione(conn, token)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/login")
async def login(request: Request, response: Response):
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email") or "m.rossi@azienda1.it").strip()[:120]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        email = "m.rossi@azienda1.it"
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        db.crea_sessione(conn, token, email)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"ok": True, "email": email}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.connect() as conn:
            db.elimina_sessione(conn, token)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/state")
def get_state(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        # "email" è l'identità della sessione: il client la separa dallo stato.
        return {"email": email, **db.leggi_stato(conn)}


@app.put("/api/state")
async def put_state(request: Request):
    if not _sessione(request):
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    try:
        patch = await request.json()
    except Exception:
        return JSONResponse({"errore": "JSON non valido"}, status_code=400)
    if not isinstance(patch, dict):
        return JSONResponse({"errore": "atteso un oggetto"}, status_code=400)
    respinte = [k for k, v in patch.items() if k not in VALIDATORS or not VALIDATORS[k](v)]
    if respinte:
        return JSONResponse({"errore": f"chiavi non valide: {', '.join(sorted(respinte))}"}, status_code=422)
    with db.connect() as conn:
        db.scrivi_stato(conn, patch)
    return {"ok": True, "salvate": sorted(patch)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
