# Changelog

Tutte le modifiche rilevanti del progetto, in stile [Keep a Changelog](https://keepachangelog.com/it-IT/).

## [1.9.0] — 2026-08-03

Una proposta esterna voleva aggiungere la previsione della domanda come app Streamlit che **sovrascriveva `app/main.py`** (le 1.203 righe del portale) e `requirements.txt`. Verificata eseguendola: `pd` mai importato nel main (NameError al primo clic), `fillna(method=…)` che crasha con le dipendenze non bloccate (pandas 3 lo ha rimosso), e il difetto di fondo — la "previsione" copriva **gli ultimi giorni già noti**, mai il futuro: la tabella presentava date passate come previsione. Respinta; l'idea, che per uno shipper è giusta (si prevede per nominare), è stata costruita nel modo del progetto.

### Aggiunto
- **Modulo Previsione della domanda**, con card e schermata proprie. Storico giornaliero incollato come CSV → **backtest sugli ultimi giorni noti** (MAE, RMSE, MAPE: la misura da guardare prima di fidarsi) → **previsione dei giorni futuri veri**, strettamente successivi all'ultimo osservato — con un test che lo presidia. Banda dichiarata per ciò che è: quantili 10°–90° dei residui, allargati con la radice dell'orizzonte.
- **Metodo trasparente, zero dipendenze nuove**: Holt-Winters additivo con stagionalità settimanale in puro Python, coefficienti scelti su griglia deterministica. Stesso input, stessa previsione — verificato. Niente pandas/statsmodels/pmdarima nei requisiti: il portale viaggia anche come eseguibile.
- **Parser CSV senza sorprese**: formati italiani e ISO, separatori `;`/`,`/tab, decimali con la virgola, intestazioni riconosciute in qualunque ordine, righe illeggibili indicate per numero. Giorni doppi aggregati e **contati**, buchi fino a 7 giorni interpolati e **contati**, buchi più larghi bloccano il calcolo invece di inventare una settimana.
- Rotta `POST /api/previsione` (stateless: nulla viene conservato), 20 test Python e 5 Node dedicati — compresa la stagionalità imparata su un profilo settimanale puro e il rifiuto dei corpi oltre 1 MiB prima della lettura.

## [1.8.1] — 2026-08-03

Revisione grafica di tutte le pagine — scritte e posizionamenti — condotta sulle schermate reali con dati veri: 7 revisori indipendenti sui 19 scatti, ogni rilievo verificato guardando l'immagine e il markup prima di essere accettato. **48 correzioni applicate su 50 rilievi validi** (2 respinti dal verificatore).

### Scritte
- **Accordi singolare/plurale ovunque**: «1 documenti del giorno gas», «1 segnalazioni generate», «1 file validati XSD» e simili ora si declinano col numero, nelle card dei moduli e nelle tessere della dashboard.
- **Il saluto non usa più il cognome secco**: da «Buonasera, Rossi» a «Buonasera, M. Rossi» — l'iniziale derivata dall'email prende il punto, l'azienda «azienda1» si mostra «Azienda 1» (solo spaziatura: nessun dato inventato).
- Coerenza terminologica: «punti di consegna» (non «in consegna»), «Costo sbilanciamento» (non «sbilancio»), «Passo Gries» e «Mazara del Vallo» per esteso, «Bilancio giornaliero PDF» uniformato fra Notifiche e Report, badge ACKNOW con l'iniziale maiuscola, «Autenticazione a due livelli», «lo schema ESMA» al posto de «lo XSD».
- **Niente più booleani grezzi**: i «true/false» della schermata PDR diventano «Sì/No» e «incluso/escluso»; il chip dell'endpoint ora dice cos'è («Endpoint PDR · …»).
- **Date in formato italiano a schermo** nel modulo Trasporto (i dati restano ISO), numeri con il punto delle migliaia, Anno Termico sempre «2025/2026»; il bottone «Registra nel registro» diventa «Registra l'interruzione»; virgola che salva il «provvisorio, comunicato in G+1»; placeholder della password rimosso (dieci pallini finti sembravano una password precompilata).

### Posizionamenti
- **La card orfana dei moduli** (10 card su 3 colonne) ora chiude la griglia a piena riga; la didascalia dell'hub non è più coperta dalle carte decorative; «Confine operativo» non si spezza più su due righe in nessuno dei quattro banner.
- **Tema scuro**: le barre «Nominato» del grafico erano quasi invisibili (contrasto 2:1) — nuova variabile `--primChart`; gli avvisi arancioni passano a `--warn` con variante scura leggibile; `color-scheme:dark` rende chiare le icone native di date picker e select.
- Griglie allineate (registro interno delle nomine a 1fr/1.15fr come la riga sopra), card che non si stirano più (Sistema con `align-items:start`, «Utenti e privilegi» a piena riga, Configuratore a due colonne), fili separatori sopra le righe invece che sotto l'ultima, colonna destra di EMIR sticky accanto al form lungo, UTI lunghi troncati con ellissi e `title`, riquadri Table 1/Table 2 nell'ordine del select, stato vuoto per le ricevute PDR senza record selezionato.

Non applicati, con motivazione: il suggerimento di mostrare «Marco Rossi» al posto di «M. Rossi» — il nome è derivato dall'email di login e «Marco» sarebbe un dato inventato, contro la regola dell'avvio pulito.

## [1.8.0] — 2026-08-03

Una proposta esterna voleva coprire trasporto interrompibile, UIOLI, allocazione TISG, coefficienti di stoccaggio e sicurezza. Verificata contro i testi ufficiali (Codice di Rete rev. XCI, 648 pagine; Codice di Stoccaggio 2026 rev. I, 283 pagine), è stata respinta: capitoli citati sbagliati, parametri «normativi» inesistenti nei Codici, algoritmi EIC/LEI errati (Luhn spacciato per ISO 7064; validatore che rifiuta tutti i LEI GLEIF reali), formule della Delibera 147/19 con l'operazione invertita, e — di nuovo — una finta autenticazione. Ma due idee stavano dal lato giusto, e sono state costruite dalle fonti.

### Aggiunto
- **Modulo Trasporto · Interruzioni e UIOLI, dal lato dello shipper.** Nel Codice di Rete interruzioni e ritiri li esegue Snam: il portale li subisce, li controlla e vi risponde — non li simula.
  - **Registro delle interruzioni comunicate da Snam** (Cap. 3, §2.2): trascrizione della comunicazione, conteggio dei giorni per punto e Anno Termico (sono i giorni che erodono il Tmax pubblicato), e il controllo dell'**unica regola quantitativa che il Codice fissa in proprio** — almeno 4 giorni fra il termine di un'interruzione e l'inizio della successiva. La violazione non blocca: produce un avviso, perché è un elemento **da contestare a Snam**. Sovrapposizioni respinte, cavallo del 30 settembre segnalato. I parametri Tmax/T1max/Dmax/Pmin non vengono ricopiati: sono pubblicati da Snam per punto e durata, e il Codice non ne fissa i valori.
  - **Utilizzo Medio per semestre** (Cap. 7, §4.3.1): la condizione b) del ritiro richiede la soglia dell'80% violata in **ciascuno** dei due semestri (1/10–31/3 e 1/4–30/9), non su una media annuale, e il denominatore va diminuito delle tre detrazioni previste — capacità messa a disposizione, capacità non disponibile per riduzioni/interruzioni, quantitativi attestati. Ignorarle fa sembrare a rischio chi non lo è. Il risultato dichiara quali condizioni restano verificabili solo da Snam.
  - **Nota giustificativa** (Cap. 7, §4.3): l'unico atto attivo dello shipper nel processo UIOLI. Contenuto prescritto dal Codice, termine dei sette giorni lavorativi dal 30 settembre calcolato (prudenzialmente: i festivi infrasettimanali potrebbero solo accorciarlo), note fuori termine marcate, file di testo scaricabile. **Nessun invio simulato**: le modalità di trasmissione le pubblica Snam.
  - Nuove rotte `/api/trasporto/{catalogo, interruzioni, utilizzo, note}` con download della nota; tabelle `trasporto_interruzione` (correggibile: è una trascrizione manuale, non una prova) e `uioli_nota`.
- Prima del rilascio il modulo è passato da una **revisione adversariale interna** (19 agenti, ogni rilievo riprodotto prima di essere accettato): 15 difetti confermati e corretti, fra cui un decimale con zero iniziale letto come migliaia («0.500» diventava 500), il confronto dei punti sensibile alle maiuscole che aggirava i controlli, i giorni consecutivi che non fondevano le interruzioni adiacenti, il riepilogo che sommava le parziali al plafond Tmax, un badge che dichiarava verificata la condizione b) con un semestre solo, e una citazione del Codice non più verbatim.
- **47 verifiche nuove** fra Python e Node: regola dei 4 giorni in entrambe le direzioni, sovrapposizioni anche con maiuscole diverse, fusione dei periodi adiacenti, riparto totali/parziali, riparto per Anno Termico, detrazioni del §4.3.1, denominatore nullo spiegato, scadenza della nota sui giorni lavorativi, isolamento fra account, binding della schermata.

### Respinto, con prova
- **Coefficienti di stoccaggio «esemplificativi»**: il §2.5.2 del Codice di Stoccaggio dice che i coefficienti temporali sono determinati da Stogit e pubblicati sul suo sito; nessuno dei valori proposti compare nelle 283 pagine, e la «fonte» citata (stogit.it/coefficienti_temporali) reindirizza a una pagina commerciale generica. Numeri inventati non entrano nel portale nemmeno con l'etichetta «esemplificativi».
- **Formule TISG/147-19**: la CTC proposta divideva dove la delibera moltiplica (CTC = PCM × z_cg), il PCM non corrisponde alla definizione del comma 3.1, e il comma 3.2 assegna il calcolo al SII — non allo shipper.
- **Autenticazione JWT** (terza riproposizione): chiave con default nel codice, nessuna verifica di credenziali, token non revocabili. Vale la decisione già scritta nel guardrail di produzione: fingere un'autenticazione è peggio che non averla.

## [1.7.1] — 2026-08-02

Da una revisione esterna del modulo EMIR: sei rilievi, verificati uno per uno contro il codice. Due accolti (in parte), quattro respinti con prova — fra questi «test assenti» (sono 63 e passano) e «manca il checksum del LEI» (c'è, MOD 97-10, su tutti e sette i campi LEI, con test).

### Corretto
- **Gli XSD si leggono una volta sola, dai byte verificati, col parser esplicito.** L'impronta SHA-256 era controllata alla compilazione dello schema ma non nell'indice dei tipi che alimenta le tendine: uno schema sostituito su disco avrebbe bloccato la generazione continuando però a riempire il catalogo — due verità diverse sullo stesso file. In più c'era una finestra fra il controllo dell'impronta e la rilettura dal percorso, e il parse degli XSD si affidava ai default della libreria invece che al parser indurito già usato per l'XML in ingresso. Ora `_byte_schema` verifica e restituisce i byte, e tutto il resto parte da lì. Stesso trattamento alla compilazione degli schemi EDIG@S (lì il base_url resta quello del percorso, perché gli XSD EASEE-gas importano le code list accanto a sé).
- **La scadenza non può più precedere il giorno dell'esecuzione.** Il confronto con la sola efficacia non bastava: con un'efficacia retrodatata, un contratto «scaduto prima di essere concluso» passava la validazione XSD senza un avviso. Con una tolleranza deliberata di un giorno: un within-day sul giorno gas D negoziato dopo la mezzanotte ha legittimamente l'esecuzione con data di calendario D+1 e la scadenza a D — quel caso genera un avviso, non un errore, perché il giorno gas finisce alle 06:00 del giorno dopo.

### Respinto, con prova
- «Il modulo non parsifica XML esterno»: falso, l'esito `auth.092` arriva dall'esterno e viene letto col parser indurito; il test XXE dedicato esiste già.
- «Manca la validazione del LEI»: falsa, `cifra_di_controllo_lei` implementa ISO 17442 → MOD 97-10 ed è applicata ovunque.
- «Test assenti»: falso, 51 test Python + 12 Node dedicati al modulo, tutti verdi.
- «Il limite di profondità delle enumerazioni fallirebbe silenziosamente»: negli schemi ESMA reali il ramo ricorsivo non viene mai percorso (le restriction puntano tutte a tipi built-in), e se un giorno fallisse la suite cadrebbe con un messaggio per-tipo — rumorosamente, non in silenzio.

## [1.7.0] — 2026-08-02

### Aggiunto
- **Modulo EMIR REFIT, separato dal REMIT, con card e schermata proprie.** Sono due obblighi distinti verso destinatari distinti — REMIT va all'ACER tramite un RRM, EMIR a un Trade Repository registrato — e lo stesso forward sul gas può richiederli entrambi. Anche gli identificativi si chiamano allo stesso modo ma seguono regole diverse: l'UTI ACER ha 45 caratteri e un algoritmo pubblicato, quello EMIR da 20 a 52 con il LEI in testa. Tenerli insieme avrebbe reso impossibile dire quale file è stato prodotto per quale regolatore.
  - **Segnalazione `auth.030.001.03`** generata e validata contro lo schema ufficiale ESMA incluso in `app/schemas/emir/`, in tutte e otto le azioni: nuova operazione, modifica, correzione, componente di posizione, riattivazione, cessazione anticipata, aggiornamento della valutazione, annullamento per errore.
  - **Il tipo di azione non è un campo: è il nome dell'elemento** che avvolge la segnalazione (`New`, `Mod`, `Crrctn`, `PosCmpnt`, `Rvv`, `Termntn`, `ValtnUpd`, `Err`). Cercare un `<ActnTp>` nel messaggio non porta a nulla — esiste solo nell'esito che torna dal registro.
  - Ogni azione porta la **forma che le impone lo schema**: il tipo di evento è obbligatorio per due azioni, facoltativo per una e vietato per cinque; cessazione, valutazione e annullamento non ammettono la controparte estesa né i dati di contratto; il componente di posizione resta a livello di operazione e richiede l'UTI della posizione. Il modulo intercetta questi casi con un messaggio comprensibile invece di lasciarli arrivare allo schema.
  - **Profilo gas completo**: ramo merce `NRGY/NGAS`, indice del punto (TTF, NBP, NCG, GASPOOL, GNL), punto di consegna EIC, tipo di carico, intervalli e capacità di consegna con l'unità di misura.
  - **Esito del Trade Repository `auth.092.001.04`**: si incolla, viene validato, abbinato da solo agli UTI inviati e mostrato riga per riga con lo stato e **le regole di validazione violate**. Le righe che riguardano altri operatori vengono scartate: cercare nei propri file la causa di un rifiuto altrui è tempo perso. Gli esiti sono immutabili come le ricevute PDR.
  - **Intestazione `head.001.001.01`** generabile e validata — con l'avvertenza che ESMA *non* pubblica l'involucro che la unisce al messaggio in un unico file: quello lo definisce ciascun Trade Repository, e il portale non lo inventa.
  - **Il LEI viene verificato con le sue cifre di controllo** (ISO 17442 → ISO/IEC 7064 MOD 97-10, la stessa aritmetica dell'IBAN). Un LEI con una cifra sbagliata supera qualsiasi espressione regolare e verrebbe respinto dal registro giorni dopo. Da non confondere con l'algoritmo di Luhn, che accetterebbe LEI sbagliati e ne rifiuterebbe di buoni.
  - **Nessun codice ricopiato a mano.** Le tendine sono costruite a runtime dai facet degli XSD e un test verifica che l'insieme delle traduzioni italiane coincida *esattamente* con i valori ammessi dallo schema: un codice inventato fa cadere la suite, uno nuovo aggiunto da ESMA viene segnalato invece di restare fuori in silenzio. Codici come `FORE`, `BIL`, `PTNG`, `NCC`, `TCTC` e `IRDS` circolano negli esempi ma non esistono in `auth.030.001.03`.
  - **UTI generato solo se manca**, nella forma ISO 23897 e con coda deterministica sulla chiave dell'operazione, così due invii dello stesso contratto non producono due identificativi. La regola della coda è nostra e viene dichiarata come avviso: lo standard fissa la forma, non la derivazione.
  - Nuovi endpoint `/api/emir/catalogo`, `/api/emir/segnalazioni` (elenco, creazione, dettaglio, download), `/api/emir/intestazioni`, `/api/emir/esiti`.
- **68 nuove verifiche** fra Python e Node: le otto azioni contro lo XSD reale, la copertura esatta delle enumerazioni, il controllo del LEI, la lettura dell'esito, l'isolamento fra account, l'immutabilità degli esiti e la protezione dalle entità esterne sul documento che arriva dal registro.

### Modificato
- Le tessere regolatorie in dashboard passano da quattro a sei, su tre colonne: si aggiungono **EMIR · segnalazioni** ed **EMIR · respinte**. Un rifiuto è l'unico numero EMIR che chiede un'azione, e si conta a parte dagli accolti.

## [1.6.1] — 2026-08-02

Da una revisione esterna di sicurezza: le difese c'erano, ma nessun test le presidiava.

### Aggiunto
- **`tests/test_sicurezza.py`**: 38 verifiche che falliscono se una protezione viene rimossa — entità XML esterne (file, rete, bomba di espansione) sui tre lettori di XML di terzi, surrogati non codificabili, corpi JSON ostili sul login, forme non valide dello stato, tetti sui corpi verificati prima della bufferizzazione, path traversal e injection sugli identificativi di percorso, isolamento fra account, immutabilità delle ricevute, intestazioni di sicurezza e cookie `HttpOnly`.
- **Guardrail di produzione**: avviare con `VETTORE_ENV=production` **interrompe l'avvio** con la spiegazione di cosa manca (IdP, TLS, isolamento per azienda). Fingere un'autenticazione con una password fissa sarebbe peggio che non averla.
- **Durata della sessione configurabile** con `VETTORE_GIORNI_SESSIONE` (1–365, default 30): è una politica aziendale, non una costante del programma.

### Corretto
- **Un file XML con entità dichiarate ma non risolte causava un errore interno.** Il parser bloccava correttamente l'XXE — nessun contenuto veniva letto — ma il validatore di schema esplodeva subito dopo, e l'endpoint rispondeva 500 invece di «documento non valido». Trovato dai test nuovi appena scritti.

## [1.6.0] — 2026-08-02

### Aggiunto
- **Documento ACKNOW: il riscontro di ricezione EDIG@S.** È il documento con cui il trasportatore dichiara di aver preso in carico — o di respingere — quello che gli è arrivato, e il protocollo lo richiede per la nomina: serve, cita la specifica, «in order to avoid reclamations from the Balance Responsible Party if the NOMINT had not been received».
  - Il riscontro si incolla nella schermata Nomine, viene validato contro l'XSD ufficiale, **collegato da solo alla nomina che cita** e archiviato con la sua impronta SHA-256, perché è la prova che il documento è arrivato. Reimportare lo stesso file non crea un secondo riscontro.
  - Distingue i due tipi previsti: `294` applicativo (il documento è stato interpretato) e `AMU` tecnico (un problema di formato o di sistema ne ha impedito l'elaborazione).
  - I **63 codici di motivazione** della `ReasonCodeTypeCodeList` sono tradotti in italiano: l'operatore legge «Conto non riconosciuto» invece di `14G`.
  - **Tre esiti, non due.** Il tracciato non ha un campo «accettato»: l'esito sta nel codice, e alcuni codici dicono esplicitamente *accepted, but…* — `95G` è «accettato, ma le modifiche nel passato sono ignorate». Mostrarli come rifiuti direbbe il contrario del vero, quindi si distingue fra accettato, accettato con riserva e respinto. Un riscontro conta come presa in carico solo se tutte le motivazioni lo sono e nessun punto di connessione è stato respinto.
  - **Ogni nomina mostra il proprio stato di riscontro** nell'elenco dei documenti generati — presa in carico, con riserva, respinta o in attesa — senza dover incrociare a mano due elenchi.
  - Gestito anche il caso in cui il trasportatore riscontri **un documento che non è riuscito a interpretare**: al posto degli identificativi cita il nome del file ricevuto.
  - Nuovi endpoint `GET|POST /api/edigas/riscontri`. La generazione (`genera_riscontro`) è disponibile per completezza, ma nel ciclo italiano lo shipper il riscontro lo riceve.
- 25 nuovi test, fra cui la lettura dei **due esempi ACKNOW ufficiali** del pacchetto EASEE-gas e la verifica che ogni codice di motivazione abbia la sua traduzione.

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
