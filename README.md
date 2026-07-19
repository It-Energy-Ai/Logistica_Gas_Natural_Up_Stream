# Vettore — portale logistica gas

Webapp del portale operativo **Vettore** per shipper e trader gas sul mercato italiano, costruita a partire dal design realizzato su Claude Design ("Interfaccia webapp da creare" → *Vettore Portale Gas*). Il markup del design è preservato al carattere: quello che vedi è esattamente ciò che è stato disegnato, ma funzionante.

## Schermate

- **Login** con email/password e flusso SSO aziendale simulato (scelta account, redirect)
- **Hub moduli** → **Logistica Gas** e **Configurazione**
- **Dashboard**: KPI del giorno gas (navigabile ±7 giorni), nominato vs allocato (14 giorni), cicli di nomina, nomine per punto
- **Nomine & Programmazione**: invio nomine per punto/ciclo e storico giornaliero
- **Bilanciamento**: disequilibrio DS immissioni−prelievi, sbilanciamento 14 giorni, azioni correttive
- **Capacità & Contratti**, **Stoccaggio** (giacenza, fattori di adeguamento, movimenti), **Report & Analisi** (filtri per categoria, invii programmati)
- **Configuratore**: anagrafica shipper, parametri di nomina, punti di consegna, notifiche, credenziali API GME, utenti con wizard a 3 passi, log attività
- Tema chiaro/scuro persistente

## Cosa è reale e cosa è demo

**Reale**: navigazione, tutte le interazioni, invio nomine, configurazione, gestione utenti/punti, sessione con cookie e **persistenza su SQLite** (nomine, configurazione, utenti sopravvivono al riavvio).

**Demo**: i dati di mercato (KPI, allocazioni, prezzi PSV, giacenze) sono scenografia ancorata al giorno gas 17/07/2026; login e SSO accettano qualunque credenziale; le integrazioni Snam/GME/SSO non chiamano i sistemi veri. Sono i punti da sostituire per andare in produzione.

## Avvio

```bash
docker compose up -d --build     # → http://localhost:8080
```

Oppure senza Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8080
```

I font IBM Plex arrivano da Google Fonts: senza rete si degrada ai font di sistema.

## Architettura

```
design/design.html      # il design originale scaricato da Claude Design (fonte di verità)
build_frontend.py       # genera app/static/index.html dal design (pseudo-stili → CSS)
app/static/runtime.js   # mini-runtime: interpreta sc-if / sc-for / {{var}} e collega gli eventi
app/static/logic.js     # porting della logica del design (stato, azioni, dati demo) + sync API
app/main.py             # FastAPI: sessioni, GET/PUT /api/state con whitelist e validazione
app/db.py               # SQLite (sessioni + stato)
tests/                  # pytest (API) + node --test (logica frontend)
```

Il frontend è un porting fedele: `logic.js` riproduce la classe `Component` del design con quattro deviazioni documentate nel file (login reale, tema persistito, sync col backend, setter "silenziosi" per gli input). Per modificare l'interfaccia si modifica il design su Claude Design, si riscarica `design/design.html` e si rilancia `build_frontend.py`.

## Test

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest              # API: login, validazione stato, persistenza
node tests/logic.test.cjs     # logica frontend: navigazione, nomine, wizard, punti, report
```
