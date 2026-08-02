"""Vettore — workspace locale per logistica gas e preparazione REMIT/PDR.

Il backend serve il frontend generato e conserva stato, audit e artefatti XML
localmente. Non dichiara integrazioni esterne concluse senza le relative
ricevute ufficiali.
"""

import json
import os
import re
import sqlite3
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from . import edigas, uti, pdr, remit

STATIC = Path(__file__).parent / "static"
COOKIE = "vettore_session"

# Durata della sessione: si cambia senza toccare il codice, perché è la
# politica che varia da azienda ad azienda, non il programma.
GIORNI_SESSIONE = max(1, min(365, int(os.environ.get("VETTORE_GIORNI_SESSIONE", "30") or 30)))

# Questo login accetta qualunque credenziale: è una scelta dichiarata per un
# portale che gira in locale, non una svista. Chi prova ad avviarlo come
# ambiente di produzione va fermato, non rassicurato con una password fissa:
# servirebbe un vero IdP (OIDC/SAML), e fingere di averlo sarebbe peggio che
# non averlo.
if os.environ.get("VETTORE_ENV", "").strip().lower() in {"production", "produzione", "prod"}:
    raise SystemExit(
        "VETTORE_ENV=production: questo portale non ha autenticazione reale.\n"
        "Il login accetta qualunque credenziale e il server si lega a 127.0.0.1.\n"
        "Per un uso produttivo servono un provider di identità (OIDC/SAML), TLS con\n"
        "reverse proxy e isolamento per azienda: vedi la sezione «Cosa sostituire per\n"
        "andare in produzione» nella documentazione. L'avvio è interrotto di proposito."
    )

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


def _is_rem_list(v):
    return (
        isinstance(v, list)
        and len(v) <= 500
        and all(
            isinstance(r, dict)
            and set(r) == {"rif", "tipo", "qta", "prezzo", "stato"}
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
    "remList": _is_rem_list,
    "cfg": _is_str_map,
    "hiddenPunti": _is_str_list,
    "extraPunti": _is_punti_list,
    "nextP": _is_int,
    "users": _is_str_map,
    "extraUsers": _is_utenti_list,
    "disabled": _is_str_map,
    "nextU": _is_int,
    "reps": _is_str_map,
    "demoMode": _is_bool,
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Vettore", lifespan=lifespan)


@app.middleware("http")
async def proteggi_risposte(request: Request, call_next):
    """Applica difese browser anche se l'app viene avviata fuori da Docker."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    )
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _sessione(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with db.connect() as conn:
        return db.email_sessione(conn, token)


async def _body_object(request: Request, *, required: bool = False) -> dict | None:
    """Legge un JSON oggetto senza trasformare un body vuoto in un 500."""
    try:
        body = await request.json()
    except Exception:
        if required:
            return None
        return {}
    return body if isinstance(body, dict) else None


def _versione_attesa(request: Request, payload: dict | None = None) -> int | None:
    """Legge un If-Match numerico (o il fallback JSON per client semplici)."""
    raw = request.headers.get("if-match", "").strip().removeprefix('W/').strip('"')
    if raw.isdecimal():
        return int(raw)
    value = (payload or {}).get("version")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _risposta_remit_error(error: remit.RemitError):
    if isinstance(error, remit.ConflittoVersione):
        status = 409
    elif error.errors:
        status = 422
    elif "non trovata" in str(error).lower():
        status = 404  # non è un conflitto di stato: la risorsa non esiste
    else:
        status = 409
    body = {"errore": str(error)}
    if error.errors:
        body["errors"] = error.errors
    return JSONResponse(body, status_code=status)


def _corpo_eccessivo(request: Request, massimo: int):
    """Rifiuta in base a Content-Length, prima di bufferizzare il corpo."""

    dichiarata = request.headers.get("content-length", "")
    if dichiarata.isdecimal() and int(dichiarata) > massimo:
        return JSONResponse(
            {"errore": f"Corpo della richiesta troppo grande: il limite è {massimo // 1024} KB."},
            status_code=413,
        )
    return None


def _risposta_pdr_error(error: pdr.PdrError):
    status = 404 if isinstance(error, pdr.PdrNotFoundError) else 422
    return JSONResponse({"errore": str(error)}, status_code=status)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz():
    """Sonda non autenticata per container e monitoraggio locale."""
    return {"ok": True}


@app.post("/api/login")
async def login(request: Request, response: Response):
    # `_body_object` restituisce {} su corpo illeggibile e None su un JSON che
    # non è un oggetto (una lista, un numero): senza questo, `body.get` su una
    # lista sollevava AttributeError e il login rispondeva 500.
    body = await _body_object(request) or {}
    # Su email assente o malformata si ripiega su un'identità NEUTRA, mai su
    # quella di scena (Marco Rossi), che contaminerebbe la modalità pulita.
    # Nessun troncamento: tagliare a 120 caratteri farebbe collassare due
    # indirizzi distinti sullo stesso account, che ne condividerebbe i dati.
    email = str(body.get("email") or "").strip().lower()
    if len(email) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        email = "utente@locale"
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        db.crea_sessione(conn, token, email)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * GIORNI_SESSIONE,
        path="/",
    )
    return {"ok": True, "email": email}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.connect() as conn:
            db.elimina_sessione(conn, token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/state")
def get_state(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        # "email" è l'identità della sessione: il client la separa dallo stato.
        return {"email": email, **db.leggi_stato(conn, email)}


@app.put("/api/state")
async def put_state(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    try:
        patch = await request.json()
    except Exception:
        return JSONResponse({"errore": "JSON non valido"}, status_code=400)
    # I surrogati UTF-16 spaiati superano il parser JSON ma non si possono
    # codificare in UTF-8: senza questo controllo la scrittura su SQLite dava
    # 500.  `ensure_ascii=False` è indispensabile: con il default i surrogati
    # verrebbero riscritti come sequenze di escape e il controllo passerebbe
    # sempre, lasciando il problema alla riga di INSERT.
    try:
        json.dumps(patch, ensure_ascii=False).encode("utf-8")
    except (UnicodeEncodeError, ValueError, TypeError):
        return JSONResponse({"errore": "Il contenuto include caratteri non codificabili."}, status_code=422)
    if not isinstance(patch, dict):
        return JSONResponse({"errore": "atteso un oggetto"}, status_code=400)
    respinte = [k for k, v in patch.items() if k not in VALIDATORS or not VALIDATORS[k](v)]
    if respinte:
        return JSONResponse({"errore": f"chiavi non valide: {', '.join(sorted(respinte))}"}, status_code=422)
    with db.connect() as conn:
        db.scrivi_stato(conn, email, patch)
    return {"ok": True, "salvate": sorted(patch)}


# --- Workspace REMIT e artefatti XML ACER ----------------------------------
# I dati REMIT non passano dalla patch generica /api/state: il browser non può
# assegnare da solo stati come "inviata" o "accettata". Le API generano XML
# validato XSD, ma non effettuano né simulano alcun invio ACER/PDR.


@app.get("/api/remit/reports")
def get_remit_reports(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        imported = remit.importa_legacy_se_necessario(conn, email)
        reports = remit.lista_report(conn, email)
    return {
        "mode": "local-acer-xml-generation",
        "live_submission": False,
        "legacy_imported": imported,
        "reports": reports,
        "notice": (
            "Workspace REMIT locale: gli XML esportati sono validati XSD ma nessun invio "
            "è stato effettuato verso ACER, GME/PDR, un RRM o un'IIP."
        ),
    }


@app.post("/api/remit/identificativi")
async def post_identificativo_acer(request: Request):
    """Calcola UTI e Contract ID con l'algoritmo ACER (TRUM Annex IV).

    Serve per i contratti bilaterali: entrambe le controparti, partendo dai
    propri dati, ottengono lo stesso identificativo senza scambiarsi nulla.
    """

    if not _sessione(request):
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    payload = await _body_object(request, required=True)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    progressivo = payload.get("progressivo", 1)
    try:
        # OverflowError: JSON ammette Infinity e 1e999, che int() non regge.
        progressivo = int(progressivo)
    except (TypeError, ValueError, OverflowError):
        return JSONResponse({"errore": "Il progressivo deve essere un numero intero."}, status_code=422)
    try:
        return {
            "uti": uti.genera_uti(payload, progressivo),
            "contract_id": uti.genera_contract_id(payload, progressivo),
            "concatenato_uti": uti.stringa_uti(payload),
            "progressivo": progressivo,
            "algoritmo": "ACER TRUM Annex IV · UTI Generator v2.3",
        }
    except uti.UtiError as errore:
        return JSONResponse({"errore": str(errore)}, status_code=422)


@app.get("/api/edigas/catalogo")
def get_edigas_catalogo(request: Request):
    """Codici e schemi del protocollo, letti dagli XSD invece che ricopiati."""

    if not _sessione(request):
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    return {
        "versione": edigas.PACCHETTO["versione"],
        "fonte": edigas.PACCHETTO["fonte"],
        "schemi": edigas.catalogo_schemi(),
        "tipi_documento": [
            {
                "codice": codice,
                "etichetta": regole["etichetta"],
                "emittente": regole["emittente"],
                "destinatario": regole["destinatario"],
                "tipi_nomina": list(regole["tipi_nomina"]),
                "con_controparti": regole["con_controparti"],
            }
            for codice, regole in edigas.TIPI_DOCUMENTO.items()
        ],
        "ruoli": edigas.RUOLI,
        "direzioni": edigas.DIREZIONI,
        "unita": edigas.UNITA,
        "tipi_nomina": edigas.TIPI_NOMINA,
        "tipi_riscontro": edigas.TIPI_RISCONTRO,
        "motivazioni": edigas.MOTIVAZIONI,
    }


@app.get("/api/edigas/nomine")
def get_edigas_nomine(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        return {"nomine": db.elenca_nomine_edigas(conn, email)}


@app.post("/api/edigas/nomine")
async def post_edigas_nomina(request: Request):
    """Genera un Nomination_Document validato e lo conserva per il download."""

    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    troppo_grande = _corpo_eccessivo(request, edigas.MAX_RISPOSTA_BYTES)
    if troppo_grande:
        return troppo_grande
    payload = await _body_object(request, required=True)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    try:
        documento = edigas.genera_nomina(payload)
    except edigas.EdigasError as errore:
        return JSONResponse({"errore": str(errore), "errors": errore.errors}, status_code=422)
    except (ValueError, OverflowError, TypeError, UnicodeError) as errore:
        # Rete di sicurezza: un input che sfugge ai controlli di campo deve
        # comunque tornare come errore comprensibile, non come 500 opaco.
        return JSONResponse({"errore": f"Dati della nomina non validi: {errore}"}, status_code=422)

    nomina_id = secrets.token_hex(8)
    try:
        with db.connect() as conn:
            db.crea_nomina_edigas(
                conn,
                nomina_id=nomina_id,
                email=email,
                identificativo=documento.identificativo,
                versione=documento.versione,
                tipo_documento=documento.tipo_documento,
                giorno_gas=documento.giorno_gas,
                punto=documento.punto,
                periodi=documento.periodi,
                avvisi=json.dumps(list(documento.avvisi)),
                xml=documento.xml,
                sha256=documento.xml_sha256,
            )
            record = db.leggi_nomina_edigas(conn, email, nomina_id)
    except sqlite3.IntegrityError:
        # Identificativo e versione sono ciò che il trasportatore cita nella
        # risposta: due nomine omonime renderebbero ambiguo l'abbinamento.
        return JSONResponse(
            {
                "errore": (
                    f"Esiste già una nomina {documento.identificativo} versione {documento.versione}. "
                    "Per una rinomina incrementa la versione."
                )
            },
            status_code=409,
        )
    return JSONResponse(
        {
            **{k: v for k, v in (record or {}).items() if k != "xml"},
            "valido_xsd": True,
            "schema_sha256": documento.schema_sha256,
            "versione_edigas": documento.versione_edigas,
            "size_bytes": documento.size_bytes,
        },
        status_code=201,
    )


@app.get("/api/edigas/nomine/{nomina_id}")
def get_edigas_nomina(nomina_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        record = db.leggi_nomina_edigas(conn, email, nomina_id)
    if not record:
        return JSONResponse({"errore": "nomina non trovata"}, status_code=404)
    return record


@app.get("/api/edigas/nomine/{nomina_id}/download")
def download_edigas_nomina(nomina_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        record = db.leggi_nomina_edigas(conn, email, nomina_id)
    if not record:
        return JSONResponse({"errore": "nomina non trovata"}, status_code=404)
    nome = f"NOMINT_{record['identificativo']}_v{record['versione']}.xml".replace("/", "-")
    return Response(
        content=record["xml"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
        media_type="application/xml",
    )


@app.get("/api/edigas/riscontri")
def get_edigas_riscontri(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        return {"riscontri": db.elenca_riscontri_edigas(conn, email)}


@app.post("/api/edigas/riscontri")
async def post_edigas_riscontro(request: Request):
    """Importa un ACKNOW e lo collega alla nomina che riscontra.

    Il riscontro è la prova che il trasportatore ha ricevuto il documento: la
    specifica lo richiede per la nomina proprio «per evitare contestazioni se
    il NOMINT non fosse arrivato». Va quindi conservato, non solo letto.
    """

    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    troppo_grande = _corpo_eccessivo(request, edigas.MAX_RISPOSTA_BYTES)
    if troppo_grande:
        return troppo_grande
    grezzo = await request.body()
    if not grezzo:
        return JSONResponse({"errore": "corpo della richiesta vuoto"}, status_code=400)
    if len(grezzo) > edigas.MAX_RISPOSTA_BYTES:
        return JSONResponse(
            {"errore": f"File troppo grande: il limite è {edigas.MAX_RISPOSTA_BYTES // 1024} KB."},
            status_code=413,
        )
    try:
        riscontro = edigas.leggi_riscontro(grezzo)
    except edigas.EdigasError as errore:
        return JSONResponse({"errore": str(errore), "errors": errore.errors}, status_code=422)

    rif = riscontro["documento_riscontrato"]
    with db.connect() as conn:
        gia_visto = db.riscontro_edigas_per_impronta(conn, email, riscontro["sha256"])
        if gia_visto:
            # Reimportare lo stesso file non crea un secondo riscontro: è lo
            # stesso fatto, e duplicarlo falserebbe il registro.
            return {**riscontro, "nomina_trovata": bool(gia_visto["nomina_id"]),
                    "nomina_id": gia_visto["nomina_id"], "riscontro_id": gia_visto["id"],
                    "gia_importato": True}
        nomina = db.trova_nomina_edigas(conn, email, rif["identificativo"], rif["versione"])
        riscontro_id = secrets.token_hex(8)
        db.crea_riscontro_edigas(
            conn,
            riscontro_id=riscontro_id,
            email=email,
            nomina_id=nomina["id"] if nomina else None,
            identificativo=riscontro["identificativo"],
            tipo_documento=riscontro["tipo_documento"],
            riferimento=rif["identificativo"] or rif["nome_file"],
            accettato=riscontro["accettato"],
            esito=riscontro["esito"],
            motivazioni=json.dumps(
                [f"{m['codice']} · {m['descrizione']}" for m in riscontro["motivazioni"]]
            ),
            creato_il=riscontro["creato_il"],
            xml=grezzo.decode("utf-8", "replace"),
            sha256=riscontro["sha256"],
        )
    return JSONResponse(
        {
            **riscontro,
            "nomina_trovata": bool(nomina),
            "nomina_id": nomina["id"] if nomina else None,
            "riscontro_id": riscontro_id,
            "gia_importato": False,
        },
        status_code=201,
    )


@app.post("/api/edigas/risposte")
async def post_edigas_risposta(request: Request):
    """Legge un NOMRES e lo confronta con la nomina che cita, se presente."""

    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    troppo_grande = _corpo_eccessivo(request, edigas.MAX_RISPOSTA_BYTES)
    if troppo_grande:
        return troppo_grande
    grezzo = await request.body()
    if not grezzo:
        return JSONResponse({"errore": "corpo della richiesta vuoto"}, status_code=400)
    if len(grezzo) > edigas.MAX_RISPOSTA_BYTES:
        return JSONResponse(
            {"errore": f"File troppo grande: il limite è {edigas.MAX_RISPOSTA_BYTES // 1024} KB."},
            status_code=413,
        )
    try:
        risposta = edigas.leggi_risposta(grezzo)
    except edigas.EdigasError as errore:
        return JSONResponse({"errore": str(errore), "errors": errore.errors}, status_code=422)

    riferita = risposta["nomina_riferita"]
    with db.connect() as conn:
        nomina = db.trova_nomina_edigas(conn, email, riferita["identificativo"], riferita["versione"])
    if nomina is None and not risposta["valido_xsd"]:
        risposta = {**risposta, "nota": "Il file non è conforme allo schema EDIG@S: controlla gli errori riportati."}
    scostamenti = edigas.confronta_con_nomina(nomina["xml"], risposta) if nomina else []
    return {
        **risposta,
        "nomina_trovata": bool(nomina),
        "nomina_id": nomina["id"] if nomina else None,
        "scostamenti": scostamenti,
    }


@app.post("/api/remit/reports")
async def post_remit_report(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    payload = await _body_object(request, required=True)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    with db.connect() as conn:
        record = remit.crea_report(conn, email, payload)
    return JSONResponse(record, status_code=201)


@app.get("/api/remit/reports/{report_id}")
def get_remit_report(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        record = remit.leggi_report(conn, email, report_id)
    if not record:
        return JSONResponse({"errore": "segnalazione non trovata"}, status_code=404)
    return record


@app.patch("/api/remit/reports/{report_id}")
async def patch_remit_report(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    payload = await _body_object(request, required=True)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    version = _versione_attesa(request, payload)
    if version is None:
        return JSONResponse({"errore": "If-Match con la versione corrente obbligatorio"}, status_code=428)
    with db.connect() as conn:
        try:
            record = remit.aggiorna_bozza(conn, email, report_id, payload, version)
        except remit.RemitError as error:
            return _risposta_remit_error(error)
    return record


@app.post("/api/remit/reports/{report_id}/validate")
async def validate_remit_report(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    payload = await _body_object(request)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    version = _versione_attesa(request, payload)
    if version is None:
        return JSONResponse({"errore": "If-Match con la versione corrente obbligatorio"}, status_code=428)
    with db.connect() as conn:
        try:
            record = remit.valida_report(conn, email, report_id, payload, version)
        except remit.RemitError as error:
            return _risposta_remit_error(error)
    return record


@app.post("/api/remit/reports/{report_id}/export")
async def export_remit_report(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    version = _versione_attesa(request)
    if version is None:
        return JSONResponse({"errore": "If-Match con la versione corrente obbligatorio"}, status_code=428)
    with db.connect() as conn:
        try:
            record, artifact = remit.esporta_report(conn, email, report_id, version)
        except remit.RemitError as error:
            return _risposta_remit_error(error)
    return {"report": record, "artifact": artifact}


@app.get("/api/remit/reports/{report_id}/audit")
def get_remit_audit(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        try:
            events = remit.eventi_report(conn, email, report_id)
        except remit.RemitError as error:
            return JSONResponse({"errore": str(error)}, status_code=404)
    return {"report_id": report_id, "events": events}


@app.get("/api/remit/artifacts/{artifact_id}")
def get_remit_artifact(artifact_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        artifact = remit.leggi_artifact(conn, email, artifact_id)
        if artifact:
            try:
                remit.verifica_integrita_artifact(conn, email, artifact)
            except remit.RemitError as error:
                return JSONResponse({"errore": f"Artefatto non scaricabile: {error}"}, status_code=409)
    if not artifact or artifact["content"] is None:
        return JSONResponse({"errore": "artefatto non trovato"}, status_code=404)
    return Response(
        content=artifact["content"],
        headers={"Content-Disposition": f'attachment; filename="{artifact["filename"]}"'},
        media_type=artifact["media_type"],
    )


@app.post("/api/remit/reports/{report_id}/submit")
def submit_remit_report(report_id: str, request: Request):
    """Endpoint esplicito che impedisce di scambiare l'export per un invio."""
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        if not remit.leggi_report(conn, email, report_id):
            return JSONResponse({"errore": "segnalazione non trovata"}, status_code=404)
    try:
        remit.richiesta_invio_reale()
    except remit.RemitError as error:
        return JSONResponse({"errore": str(error)}, status_code=409)


# --- PDR GME: configurazione preparatoria, nessuna credenziale o invio ------


@app.get("/api/pdr")
def get_pdr_status(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        return pdr.stato(conn, email)


@app.put("/api/pdr/profile")
async def put_pdr_profile(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    payload = await _body_object(request, required=True)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    with db.connect() as conn:
        try:
            profile = pdr.salva_profile(conn, email, payload)
        except pdr.PdrError as error:
            return JSONResponse({"errore": str(error)}, status_code=422)
    return {"profile": profile, "stored_secrets": False}


# --- Ricevute PDR/ACER: import manuale e archivio immutabile --------------
# Non vi è alcuna affermazione di upload, consegna o accettazione remota: il
# documento viene solo associato all'XML ACER del report e conservato per audit.


@app.post("/api/pdr/receipts/import")
async def import_pdr_receipt(request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    payload = await _body_object(request, required=True)
    if payload is None:
        return JSONResponse({"errore": "atteso un oggetto JSON"}, status_code=400)
    with db.connect() as conn:
        try:
            receipt, idempotent = pdr.importa_ricevuta(conn, email, payload)
        except pdr.PdrError as error:
            return _risposta_pdr_error(error)
    return JSONResponse(
        {
            "receipt": receipt,
            "idempotent": idempotent,
            "connector_verified": False,
            "notice": (
                "Ricevuta importata manualmente: non prova un upload PDR, una consegna ACER "
                "o una verifica eseguita dal connettore."
            ),
        },
        status_code=200 if idempotent else 201,
    )


@app.get("/api/pdr/receipts")
def get_pdr_receipts(request: Request, report_id: str | None = None):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        try:
            receipts = pdr.lista_ricevute(conn, email, report_id)
        except pdr.PdrError as error:
            return _risposta_pdr_error(error)
    return {
        "receipts": receipts,
        "connector_verified": False,
        "notice": "L'elenco mostra documenti importati manualmente; il loro contenuto raw è disponibile solo nel download protetto.",
    }


@app.get("/api/pdr/receipts/{receipt_id}")
def get_pdr_receipt(receipt_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        receipt = pdr.leggi_ricevuta(conn, email, receipt_id)
    if not receipt:
        return JSONResponse({"errore": "ricevuta non trovata"}, status_code=404)
    return {
        "receipt": pdr.presenta_ricevuta(receipt),
        "connector_verified": False,
        "notice": "La ricevuta è stata importata manualmente e non è verificata dal connettore.",
    }


@app.get("/api/pdr/receipts/{receipt_id}/download")
def download_pdr_receipt(receipt_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        receipt = pdr.leggi_ricevuta(conn, email, receipt_id)
    if not receipt:
        return JSONResponse({"errore": "ricevuta non trovata"}, status_code=404)
    # ``filename`` è validato al momento dell'import; il parametro RFC 5987
    # evita comunque header injection e conserva i nomi UTF-8.
    encoded_filename = quote(receipt["filename"], safe="")
    return Response(
        content=receipt["content"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
        media_type=receipt["mime_type"],
    )


@app.post("/api/pdr/reports/{report_id}/preflight")
def post_pdr_preflight(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        try:
            return pdr.preflight(conn, email, report_id)
        except pdr.PdrError as error:
            return JSONResponse({"errore": str(error)}, status_code=404)


@app.post("/api/pdr/reports/{report_id}/submit")
def post_pdr_submit(report_id: str, request: Request):
    email = _sessione(request)
    if not email:
        return JSONResponse({"errore": "sessione assente"}, status_code=401)
    with db.connect() as conn:
        if not remit.leggi_report(conn, email, report_id):
            return JSONResponse({"errore": "segnalazione non trovata"}, status_code=404)
    try:
        pdr.invio_reale_non_configurato()
    except pdr.PdrError as error:
        return JSONResponse({"errore": str(error)}, status_code=409)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
