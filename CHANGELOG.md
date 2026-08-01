# Changelog

Tutte le modifiche rilevanti del progetto, in stile [Keep a Changelog](https://keepachangelog.com/it-IT/).

## [Non rilasciato]

### Aggiunto
- **REMIT XML ACER**: generazione deterministica di `REMITTable1 V3` (TradeReport) e `REMITTable2 V1` (non-standard a prezzo fisso), con validazione XSD reale, impronta SHA-256 degli schemi e artefatto XML scaricabile.
- **Nomenclatura PDR**: progressivo atomico in SQLite e nomi `YYYYMMDD_SCHEMANAME_SCHEMAVERSION_CODICEACER_PROGRESSIVO.XML` per gli artefatti supportati.
- **Preflight PDR GME**: controllo di XML, dimensione, nome file, codice ACER abilitato, ambiente, canale, accesso test, contratto di produzione e autenticazione a due livelli, senza memorizzare password/PIN/OTP.
- **Registro ricevute PDR/ACER**: import manuale di XML e ZIP, associato in modo immutabile al report REMIT e all'artefatto XML ACER; conserva file originale, SHA-256, Load Code, timestamp, esito e audit. Le ricevute GME `PIPEFunctionalAcknowledgement` riconosciute estraggono `Accept`, `Reject` o `Partial` senza trasformare l'import in una verifica live.
- Documentazione operativa in `docs/remit-pdr.md`, con fonti ACER/GME, versioni degli XSD, prerequisiti dell'invio reale e ambito esplicito del rilascio.

### Cambiato
- Il vecchio export JSON REMIT è sostituito da XML validato XSD; lo stato `xml_validato_xsd` non equivale a una ricevuta GME o ACER.
- Rimossi gli stati demo che facevano sembrare configurata un'integrazione GME; il percorso PDR espone solo readiness e preflight.
- Il tracciato GasCapacity/EDIG@S per trasporto gas è bloccato esplicitamente finché non viene implementato il set completo di dati specifici, invece di generare un file non conforme.

### Corretto
- **Confine PDR**: il preflight non può più dichiarare un file pronto all'upload sulla sola base di attestazioni manuali; mantiene `upload_ready=false` finché non esistono accesso e ricevute GME verificati.
- **Progressivi PDR**: la numerazione è ora condivisa per data/schema/versione/codice ACER, così utenti distinti non possono produrre lo stesso nome file. I contatori sperimentali per utente vengono migrati conservando il valore massimo.
- **Integrità REMIT**: download e preflight riverificano SHA-256 e XSD dell'artefatto, bloccando XML corrotti o alterati dopo l'export.
- **Date regolatorie**: le date di transazione Table 1 e del contratto Table 2 future sono respinte prima dell'export.
- **Interfaccia**: rimossa una sezione REMIT residua fuori dalla relativa schermata.
- **Isolamento dei dati**: ogni email ha ora il proprio stato SQLite; nomine, configurazione, utenti e segnalazioni di un account non sono più visibili o sovrascrivibili da un altro.
- **Cambio account e login offline**: logout e login successivo ripartono da uno stato pulito, mentre l'hub non viene più aperto se il backend non conferma l'accesso.
- **Sync**: una sola richiesta di salvataggio resta in volo alla volta, così una risposta fuori ordine non può ripristinare un valore precedente.
- **Release macOS**: un fallimento consultivo dello smoke test aggiorna un'unica issue diagnostica invece di aprirne una per ogni esecuzione.

### Sicurezza
- Aggiunti CSP, intestazioni anti-framing e anti-MIME-sniffing, policy per le API non memorizzabili in cache e normalizzazione dell'email di sessione.
- SQLite usa WAL e un timeout di attesa per ridurre gli errori di lock nei salvataggi ravvicinati.
- Le API delle ricevute non restituiscono mai il contenuto raw nei metadati, isolano i documenti per utente, limitano l'import a 2 MiB e usano un parser XML senza DTD/entità/rete. Il download dell'originale è disponibile solo tramite endpoint autenticato.

### Note di aggiornamento
- Un database creato con le versioni precedenti conserva il vecchio stato globale in `stato_legacy`, senza attribuirlo automaticamente a un account. Per importarlo una sola volta all'account corretto, avvia l'app con `VETTORE_LEGACY_EMAIL=nome@azienda.it`; il backup verrà poi rimosso.

## [1.2.1] — 2026-07-20

### Aggiunto
- **Modulo REMIT · Segnalazioni**: nuova area di lavoro in Logistica Gas con registro delle transazioni da segnalare (persistito), ciclo di vita degli invii (Da inviare → Inviata, con esiti Accettata/Respinta in demo), KPI calcolati dal proprio registro, codice ACER configurabile e card dei riferimenti normativi (Reg. UE 1227/2011 e 2024/1106: standard T+1 via RRM, non-standard entro 1 mese, registrazione CEREMP/ARERA).
- La schermata nasce nel design (fonte di verità) con la stessa pipeline delle altre; l'anteprima del canvas resta funzionante.
- Test: 4 nuovi test node sul modulo + validazione backend di `remList`.

### Corretto
- Scadenze REMIT allineate al **recast dell'Implementing Regulation** (in vigore dal 29/04/2026, fonte: Open Letter ACER): contratti standard **T+2** giorni lavorativi (era T+1), non-standard **T+10** (era 1 mese). Aggiunto il link ai documenti ufficiali ACER nella card dei riferimenti.

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

[1.2.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.2.1
[1.1.3]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.3
[1.1.2]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.2
[1.1.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.1
[1.1.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.0
[1.0.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.0.1
[1.0.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.0.0
