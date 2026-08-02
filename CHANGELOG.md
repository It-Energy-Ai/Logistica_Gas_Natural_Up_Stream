# Changelog

Tutte le modifiche rilevanti del progetto, in stile [Keep a Changelog](https://keepachangelog.com/it-IT/).

## [1.5.2] — 2026-08-02

Riallineamento dell'interfaccia, rimasta indietro mentre il backend cresceva con REMIT ed EDIG@S.

### Modificato
- **La dashboard mostra lo stato regolatorio reale**: quattro tessere cliccabili con bozze REMIT da validare, XML già generati, documenti EDIG@S del giorno gas e avvisi da leggere. Sono dati veri dell'utente, quindi restano visibili anche a modalità demo spenta, a differenza dei numeri di scena che continuano a mostrare `—`. Il conteggio del giorno gas usa `Europe/Rome`: contare sulla mezzanotte locale avrebbe dato zero alle due di notte, in pieno intraday.
- **L'hub racconta il prodotto di oggi**: le card citano EDIG@S 6.1, la validazione XSD e il generatore UTI. Sparisce la dicitura «invii reali simulati», che descriveva una versione precedente, e «REMIT · Workspace» diventa «REMIT · XML ACER» in tutta la navigazione, briciole comprese.
- **La schermata Nomine dichiara il confine.** Il documento EDIG@S — l'unico che produce un file da trasmettere — sale in cima; il vecchio pannello scende sotto ed è ora chiamato per quello che è, un **registro interno dei cicli** che non genera nulla. Prima i due convivevano senza che si capisse quale dei due facesse sul serio: il pulsante diceva «Invia nomina» e non inviava niente.
- **Stati vuoti** in REMIT, EDIG@S e preflight PDR: a portale pulito una tabella vuota senza spiegazione fa sembrare rotta un'applicazione che sta solo aspettando il primo dato. Il preflight, che senza XML era un vicolo cieco, offre ora il collegamento a REMIT.

Da una revisione a tappeto di tutti i moduli e di tutte le pagine: ogni difetto è stato riprodotto eseguendo codice e poi verificato una seconda volta in modo indipendente. Trenta difetti confermati, tutti corretti.

### Corretto — identificativi e dati regolatori
- **Un codice ACER di 13 caratteri veniva tagliato a 12 in silenzio**, diventando quello — formalmente valido — di un **altro soggetto**, che finiva in `reportingEntityID`, in `idOfMarketParticipant` e nel nome del file PDR. La regola già scritta per Contract ID e UTI («non troncare mai un identificativo regolatorio») non era stata estesa al dichiarante. Stesso trattamento per le date: `2026-08-0199` diventava `2026-08-01` senza un avviso.
- **Una validazione fallita cancellava dal database i valori digitati dall'operatore**: la normalizzazione azzera ciò che non sa interpretare, e quei vuoti venivano salvati. Restavano i messaggi d'errore, riferiti a campi ormai vuoti.
- **Un acknowledgement GME rifiutato poteva essere archiviato come accettato** dichiarando la fonte «acer»: il parser che confronta lo `Status` si attivava solo per fonte «pdr». Ora il riconoscimento non dipende da come l'operatore dichiara la fonte.
- **L'XML esportato non era più riscaricabile**: se il download del browser falliva, l'unico modo per riaverlo era rigenerarlo. Ora il registro espone «Scarica XML» finché l'artefatto esiste.

### Corretto — interfaccia
- **Le tre tendine delle ricevute PDR non hanno mai funzionato.** Il parser HTML ammette dentro `<select>` solo `option` e `optgroup`: i `<sc-for>` venivano scartati al caricamento della pagina e a schermo restavano voci con le interpolazioni non risolte, del tipo `{{ ro.label }}`. Non era intercettabile dai test, perché il difetto nasce nel parser del browser. La ripetizione è ora un attributo `sc-repeat` su un `<option>` legittimo, con un guardrail nel builder e due test che vietano la forma sbagliata per sempre.
- **Il pulsante «Audit» era morto**: scaricava la catena di eventi e non la mostrava, perché il markup non esisteva. Ora il pannello mostra gli eventi con impronta e transizione di stato.
- **Il profilo PDR non salvava nulla di ciò che si digitava**, ma annunciava «Profilo PDR salvato»: veniva inviato lo snapshot precedente alla digitazione. Stesso difetto sul codice ACER del form REMIT, che non entrava né nella bozza né nel calcolo dell'UTI.
- **Le risposte di una sessione chiusa finivano nello stato del nuovo utente**: entrando con un altro account si potevano vedere i dati del precedente. I caricamenti verificano ora di appartenere ancora alla sessione viva.
- Il doppio clic non crea più bozze, nomine e ricevute duplicate; il logout invia la coda di sincronizzazione invece di buttarla; uscire mentre il workspace carica non lascia più bloccato il pulsante «Accedi»; rimuovendo un punto sparisce anche la sua chiave di configurazione, che prima restava orfana fino a rompere il salvataggio.

### Corretto — robustezza
- Nessun input produce più un errore interno: corpo di login che non è un oggetto, progressivo `Infinity`, surrogati UTF-16 spaiati nello stato, caratteri di controllo nel punto di consegna, offset orari impossibili, versione NOMRES di diecimila cifre.
- **Una nomina con molte controparti bloccava il server per minuti** per un costo quadratico nel controllo dei duplicati: tremila controparti ora si risolvono in centesimi di secondo.
- Due nomine con lo stesso identificativo e versione non sono più possibili: rendevano ambiguo l'abbinamento della risposta del trasportatore.
- L'import del registro storico non si ripete più a ogni modifica della lista, che duplicava le bozze già convertite.
- Una segnalazione inesistente risponde 404 e non più 409; un ambiente o un canale PDR fuori lista viene respinto invece di essere sostituito in silenzio.

## [1.5.0] — 2026-08-02

### Aggiunto
- **Protocollo EDIG@S 6.1 per le nomine di trasporto gas.** La schermata Nomine produce il `Nomination_Document` (NOMINT) validato contro gli schemi ufficiali EASEE-gas inclusi nel repository, e legge il `NominationResponse_Document` (NOMRES) del trasportatore.
  - Coperti i quattro tipi di nomina: `01G` punto di connessione, `02G` PSV OTC, `03G` PSV borsa, `04G` cliente finale. Ruoli delle parti, presenza del tipo di nomina e struttura dell'XML seguono la decision table del MIG "BRP Nomination and Matching" v6r0: una combinazione fuori tabella viene respinta prima di produrre il file.
  - Quantità indicabili come valore costante sul giorno, profilo orario o periodi espliciti.
  - La risposta del trasportatore viene **abbinata da sola** alla nomina che cita e confrontata riga per riga: quantità ridotte, aumentate, periodi confermati senza nomina o rimasti senza risposta.
  - Nuovi endpoint `GET /api/edigas/catalogo`, `GET|POST /api/edigas/nomine`, `GET /api/edigas/nomine/{id}[/download]`, `POST /api/edigas/risposte`. Le nomine sono isolate per account.
- **Giorno gas calcolato sul fuso reale.** Il giorno gas va dalle 06:00 alle 06:00 locali: su `Europe/Rome` diventa 05:00Z d'inverno e 04:00Z d'estate, e i giorni di cambio ora durano 23 e 25 ore. Il cambio avviene alle 02:00 locali, quindi dentro il giorno gas iniziato il pomeriggio prima: sono il 28/03 e il 24/10 a essere anomali, non il 29/03 e il 25/10. Un profilo orario che non rispetta la durata reale viene rifiutato con il conteggio corretto.
- Oltre 100 nuovi test, fra cui la **riproduzione strutturale dei quattro esempi NOMINT ufficiali** del pacchetto EASEE-gas, la lettura dei quattro NOMRES e una batteria che verifica che nessun input malformato produca un errore interno.

### Modificato
- I codici ammessi dal tracciato (tipo documento, ruoli, direzioni, unità, tipo nomina) sono **letti dagli XSD a runtime**, risolvendo la catena `union` fra lista standard e lista locale, invece di essere ricopiati nel codice.
- Il tracciato "trasporto gas" del modulo REMIT resta bloccato, ma ora con la ragione tecnica esatta: gli XSD EDIG@S di monitoraggio capacità e nomina (CANMON, NOMASS) ammettono come emittente solo `ZSO` o `ZUA`, quindi un documento emesso da uno shipper sarebbe invalido già a livello di schema. L'obbligo ricade sul trasportatore.

### Corretto
- La versione `0` di una nomina non viene più corretta in silenzio a `1`: era un valore falsy che il default assorbiva senza segnalarlo.
- **Il confronto con la risposta del trasportatore considera solo la serie `16G`**, quella confermata. Prima pesava insieme anche `14G`, `15G` e `18G`: le ultime due portano per specifica la direzione speculare, quindi ogni risposta reale produceva scostamenti inesistenti.
- **Il confronto vede anche le nomine al cliente finale** (`04G`), dove i periodi pendono dal punto di connessione invece che da una controparte: prima una riduzione del trasportatore non veniva rilevata affatto.
- Un file NOMRES non conforme allo schema non viene più raccontato come "confermato per intero": l'esito della validazione XSD è mostrato all'operatore.
- Nessun input produce più un errore interno: cifre unicode nella versione, caratteri di controllo, byte nulli, anni fuori scala, dict o liste al posto di stringhe, quantità in notazione esponenziale e corpi di richiesta smisurati tornano tutti come errore sul campo. La versione citata dentro un NOMRES è testo scritto dal trasportatore e un refuso come `1.0` non fa più cadere la richiesta.
- La stessa controparte ripetuta due volte veniva scritta come due blocchi sullo stesso intervallo, falsando nomina e confronto.
- I secondi vengono troncati prima dei controlli, non dopo: un periodo di 40 secondi non diventa più un intervallo a durata zero dentro un file formalmente valido.
- I metadati salvati descrivono il documento prodotto e non il payload ricevuto, così l'elenco per giorno gas non perde le nomine con spazi o formati alternativi nella data.
- L'impronta dello schema restituita al client viene verificata a runtime, come già avveniva per gli schemi ACER.

## [1.4.0] — 2026-08-02

### Aggiunto
- **Generatore di UTI e Contract ID secondo l'algoritmo ufficiale ACER** (TRUM Annex IV, *UTI Generator* v2.3 del 2025). Per i contratti bilaterali le due controparti devono riportare lo stesso identificativo: l'algoritmo lo ricava dai soli termini economici, così ciascuna parte lo calcola per conto proprio e i due lati dell'operazione si appaiano in ARIS.
  - `POST /api/remit/identificativi` restituisce UTI (13 campi, Table 1), Contract ID (9 campi, Table 2) e la stringa concatenata da cui derivano.
  - Nel form REMIT il pulsante *Calcola con l'algoritmo ACER* compila il campo UTI.
- **Conformità verificata sui dati di ACER**: il repository include i 181 casi calcolati dal foglio ufficiale (`tests/dati/uti_acer.json`, 128 UTI e 53 Contract ID) e un test li riproduce tutti, impronta per impronta.

### Nota sull'algoritmo
Concatenazione dei termini economici → SHA-256 in UTF-8 → base64 con le sostituzioni previste dal foglio ACER (`+`→A, `/`→B, `=`→C, `-`→D, per ottenere un codice solo alfanumerico) → primi 42 caratteri → progressivo a 3 cifre, per un totale di 45 caratteri conformi al pattern XSD. L'esempio pubblicato nell'Annex IV del 2021 riportava una struttura diversa (30+2 caratteri) e conteneva refusi: fa fede il generatore v2.3, che è stato riprodotto esattamente.

## [1.3.1] — 2026-08-02

Da un audit del modulo REMIT che ha confrontato campo per campo ciò che l'app genera con gli XSD ACER inclusi e con i tracciati Table 1 / Table 2.

### Corretto
- **`settlementMethod` non viene più inventato.** Era obbligatorio per Table 2 ma non richiesto né validato: se mancava, il file dichiarava `P` (consegna fisica) anche per un contratto a regolamento finanziario. Ora è un campo obbligatorio con lista chiusa (P/C/O).
- **Niente più troncamenti silenziosi di identificativi.** UTI oltre 100 caratteri e Contract ID oltre 50 venivano tagliati: il file dichiarava una transazione o un contratto **diversi** da quelli reali. Ora sono errori espliciti, e Table 2 usa correttamente il proprio limite di 100 caratteri invece di quello di Table 1.
- **La validazione locale replica i vincoli degli XSD**, che prima erano verificati solo in fase di export (o non verificati affatto): liste chiuse (unità, valuta, capacità di negoziazione, tipo contratto, commodity), lunghezze e formati degli identificativi per schema (ace, lei, bic, eic, gln, mic, bil), pattern di Contract ID e UTI, EIC del punto di consegna, decimali e cifre di quantità e prezzo, quantità non negative.
- **La controparte non può coincidere con il soggetto che segnala** (Table 1 campi 1/2 e 4/5, Table 2 analoghi).
- **L'operatore vede quali campi sono sbagliati**, non più solo quanti: il registro elenca i messaggi per campo con i valori ammessi, e il dettaglio degli errori dell'API non viene più scartato dall'interfaccia.

### Nota tecnica
I vincoli non sono ricopiati a mano: vengono **derivati dagli XSD stessi** a ogni avvio. Le liste differiscono tra i due tracciati (per esempio `CO` è un tipo contratto valido su Table 1 ma non su Table 2, e Contract ID ammette 50 caratteri su Table 1 e 100 su Table 2), quindi aggiornando gli schemi la validazione locale resta allineata da sola. Otto nuovi test di regressione lo verificano.

## [1.3.0] — 2026-08-02

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
- **Smoke test della release**: la verifica del contenuto non usa più `curl | grep -q`. Con `pipefail`, `grep -q` chiudeva la pipe al primo match e curl falliva per SIGPIPE (exit 23), così lo smoke risultava fallito **anche a server sano** — causa reale dei fallimenti macOS attribuiti ai runner e, con la pagina cresciuta a ~124 KB, anche di quelli Linux. Ora la pagina si scarica su file e si verifica dopo, e lo smoke torna bloccante su tutte le piattaforme.

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

[1.4.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.4.0
[1.3.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.3.1
[1.3.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.3.0
[1.2.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.2.1
[1.1.3]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.3
[1.1.2]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.2
[1.1.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.1
[1.1.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.1.0
[1.0.1]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.0.1
[1.0.0]: https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/releases/tag/v1.0.0
