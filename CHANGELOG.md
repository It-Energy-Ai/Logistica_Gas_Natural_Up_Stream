# Changelog

Tutte le modifiche rilevanti del progetto, in stile [Keep a Changelog](https://keepachangelog.com/it-IT/).

## [1.1.3] — 2026-07-20

Da una revisione profonda finale (7 lenti + browser reale + critico di completezza).

### Corretto
- **Fuga di dati demo**: nella schermata Report il chip "Ultimo agg. 06:31" era generato con un `sc-if` malformato e compariva sempre, anche in modalità pulita (con un `>` spurio). Ora è correttamente sotto la modalità demo.
- **Identità di scena**: con email di login vuota o malformata l'app ripiegava su "Marco Rossi / Azienda 1"; ora ripiega su un'identità neutra e usa l'email normalizzata dal server come fonte di verità (coerente dopo un refresh).
- **Permessi demo**: gli utenti di esempio Laura Bianchi e Giulio Verdi tornano in sola lettura come nel design.
- **Sync**: un retry non riporta più indietro un valore già superato da una scrittura più recente.
- **Launcher**: rileva la porta occupata (messaggio chiaro invece di aprire il browser su un servizio estraneo) e apre il browser solo quando il server è pronto (niente più "connessione rifiutata" all'avvio dell'eseguibile).

### Aggiunto
- **Accessibilità da tastiera**: card, breadcrumb e interruttori (elementi cliccabili non nativi) sono ora raggiungibili con Tab e attivabili con Invio/Spazio.
- Test: ramo 5xx e retry della sync effettivamente verificati, boot dell'idratazione, permessi demo, scadenza sessioni, e una suite dedicata al runtime del template (`tests/runtime.test.cjs`).
- `.dockerignore` (contesto di build da ~126 MB a pochi KB).

## [1.1.2] — 2026-07-19

### Corretto
- La sync verso il backend non entra più in loop di retry dopo una sessione scaduta: si sospende e riparte al login, svuotando la coda conservata.
- Lo smoke test Windows della release usa la porta 8123 come quello unix.
- README: rimossa la promessa dell'eseguibile per Mac Intel (mai esistito); conteggi e refusi sistemati.

### Aggiunto
- `SHA256SUMS.txt` in ogni release per verificare l'integrità degli eseguibili.
- CONTRIBUTING (con la regola del frontend generato), Codice di condotta, Dependabot per le immagini Docker.
- Scadenza delle sessioni a 30 giorni con pulizia automatica.

### Sicurezza
- Tutte le GitHub Actions pinnate al commit SHA; permessi minimi e concurrency nei workflow; cache pip in CI.

### Rimosso
- La feature "import sbilanci" rimasta senza interfaccia (stato, validatori e valori mai usati dal template).

## [1.1.1] — 2026-07-19

### Corretto
- Due dati demo che trapelavano in modalità pulita: il "Totale nominato" della dashboard e i numeri sulle card delle aree di lavoro. Verifica automatica anti-fuga su tutte le schermate (browser headless + marcatori della scenografia).

## [1.1.0] — 2026-07-19

### Cambiato
- **Primo avvio pulito**: identità derivata dall'email di login (niente più "Marco Rossi / Azienda 1" fissi), giorno gas reale, liste e contatori a zero, banner che guida ai primi passi.
- La scenografia del design è diventata la **modalità demo**: interruttore in Configuratore → Sistema, persistita, ancorata al 17/07/2026; le nomine demo compaiono in coda a quelle reali e non vengono mai salvate.

## [1.0.1] — 2026-07-19

### Aggiunto
- Favicon con il logo Vettore; tour animato delle schermate nel README.
- CHANGELOG, security policy, template per issue e pull request, Dependabot.

### Aggiornato
- Dipendenze (FastAPI 0.139, uvicorn 0.51, pytest 9, httpx 0.28) e azioni CI, dalle prime PR di Dependabot — test completi rieseguiti.

## [1.0.0] — 2026-07-19

Prima release pubblica.

### Aggiunto
- 12 schermate del portale: login con SSO simulato, hub moduli, dashboard del giorno gas (navigabile ±7 giorni), nomine & programmazione, bilanciamento, capacità & contratti, stoccaggio, report & analisi, configuratore (impresa, sistema, wizard utenti a 3 passi, credenziali GME).
- Tema chiaro/scuro persistente.
- Backend FastAPI con sessioni via cookie e persistenza SQLite dello stato (nomine, configurazione, punti, utenti) con validazione a whitelist.
- Sync client→server con retry automatico su errori di rete e ritorno al login su sessione scaduta.
- Pipeline dal design: `design/design.html` (fonte di verità) → `build_frontend.py` → runtime che interpreta il template `sc-if`/`sc-for`.
- Eseguibili standalone per Windows, macOS (Apple Silicon) e Linux, costruiti e smoke-testati da GitHub Actions.
- Script di avvio `avvio.sh` / `avvio.bat` per chi ha solo Python; Docker come terza opzione.
- Suite di test: 8 test API (pytest) + 18 test della logica frontend (node:test), CI su ogni push.

[1.1.3]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.3
[1.1.2]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.2
[1.1.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.1
[1.1.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.0
[1.0.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.0.1
[1.0.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.0.0
