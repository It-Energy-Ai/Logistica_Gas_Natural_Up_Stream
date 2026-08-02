# REMIT e PDR GME

Questo documento descrive esattamente cosa il progetto fa e cosa non dichiara
di fare nel flusso REMIT.

## Flusso implementato

```text
Bozza server-side
  → controlli di completezza per il tracciato scelto
  → generazione XML deterministica
  → validazione contro XSD ACER fissato nel repository
  → nome file conforme alla convenzione PDR
  → preflight PDR locale
  → eventuale import manuale e immutabile delle ricevute PDR/ACER
  → upload PDR e verifica live delle ricevute (operazione esterna)
```

Gli artefatti sono tracciati con hash SHA-256 e con una catena di audit locale.
Hash e XSD sono riverificati anche prima del download e del preflight, così un
contenuto locale corrotto o alterato non può essere presentato come validato.
Uno stato `xml_validato_xsd` prova soltanto che l'XML prodotto ha superato lo
schema incluso: non prova che GME lo abbia ricevuto né che ACER/ARIS lo abbia
accettato.

## Tracciati disponibili

| Ambito | File generato | XSD ACER fissato | Stato |
|---|---|---|---|
| Contratto standard gas | `REMITTable1_V3` / TradeReport | `app/schemas/REMITTable1_V3.xsd` | Disponibile |
| Contratto non-standard gas a prezzo fisso | `REMITTable2_V1` / nonStandardContractReport | `app/schemas/REMITTable2_V1.xsd` | Disponibile |
| Capacità/trasporto gas | CANMON / NOMASS · EDIG@S | — | Bloccato: emittente riservato al trasportatore |

Sul trasporto gas la scelta non è un rinvio tecnico. Gli XSD EDIG@S dei
documenti di monitoraggio capacità e nomina restringono l'emittente ai ruoli
`ZSO` (System Operator) e `ZUA` (Market Information Aggregator): uno shipper
(`ZSH`) non è nell'elenco, e un file del genere emesso da lui sarebbe invalido
già a livello di schema. L'obbligo di segnalare quei dati ricade sul
trasportatore. Le nomine che lo shipper *deve* invece emettere sono coperte,
e sono documentate in [edigas.md](edigas.md).

Le impronte dei due XSD sono controllate a runtime prima dell'export. Le fonti
sono gli archivi ufficiali ACER pubblicati il 4 luglio 2024:

- [schema per contratti standard](https://www.acer.europa.eu/sites/default/files/REMIT/REMIT%20Reporting%20Guidance/Manual%20of%20Procedures%20%28MoP%29%20on%20Data%20Reporting/standard-contract-schema.zip)
- [schema per contratti non-standard](https://www.acer.europa.eu/sites/default/files/REMIT/REMIT%20Reporting%20Guidance/Manual%20of%20Procedures%20%28MoP%29%20on%20Data%20Reporting/nonstandard-schema.zip)

ACER segnala che il materiale di reporting può essere in aggiornamento dopo
le modifiche normative del 2026. Prima di un uso produttivo occorre confrontare
le versioni incluse con la [pagina ACER di reporting guidance](https://www.acer.europa.eu/remit-documents/remit-reporting-guidance), il [TRUM](https://www.acer.europa.eu/transaction-reporting-user-manual-trum) e le Data Validation Rules vigenti.

Il dettaglio della motivazione, con la citazione degli XSD, è in [edigas.md](edigas.md).

## Nome file PDR

Per gli XML supportati il nome è:

```text
YYYYMMDD_SCHEMANAME_SCHEMAVERSION_CODICEACER_PROGRESSIVO.XML
```

Il progressivo viene riservato in SQLite in modo atomico e condiviso per data,
schema, versione e codice ACER: non dipende dall'utente perché l'email non
compare nel nome file. Un eventuale salto di numerazione è preferibile al
riuso di un nome file. La convenzione e il limite massimo di 10 MB sono
verificati dal preflight, secondo il [Manuale utente PDR](https://www.mercatoelettrico.org/portals/0/Documents/it-it/20241001_Manuale_Utente_PDR.pdf).

## Preflight PDR

Il profilo PDR conserva solo attestazioni non segrete:

- ambiente `test` o `production`;
- canale `portal` o `web_service`;
- identificativo operatore GME;
- codice ACER/CEREMP abilitato presso PDR;
- riferimento del contratto PDR (richiesto in produzione);
- disponibilità dell'accesso test e dell'autenticazione a due livelli.

Il preflight richiede un XML già validato XSD, ne riverifica hash e schema,
controlla il nome file, il limite dimensionale e la corrispondenza fra codice
ACER del file e quello registrato. Non memorizza password, PIN o OTP.

Le attestazioni del profilo sono **solo dichiarazioni manuali**: anche quando
tutti i controlli locali passano, l'API non restituisce mai `upload_ready=true`
e indica che l'accesso/test PDR non è verificato. Un esito di upload o di
accettazione può essere registrato solo da un futuro connettore autorizzato
che riceva le ricevute GME/ACER.

GME indica che l'operatore deve preparare un file già nel formato ACER; PDR lo
trasmette poi ad ACER. L'accesso PDR usa autenticazione a due livelli e sono
previsti ambiente e credenziali distinti per i test. Vedi [Come operare](https://www.mercatoelettrico.org/it-it/Home/Monitoraggio-e-REMIT/PDR/ComeOperare), [Come partecipare](https://www.mercatoelettrico.org/it-it/Home/Monitoraggio-e-REMIT/PDR/ComePartecipare) e le [FAQ PDR](https://www.mercatoelettrico.org/it-it/Home/Monitoraggio-e-REMIT/PDR/FAQ).

## Invio reale: prerequisiti esterni obbligatori

L'endpoint di invio è deliberatamente bloccato fino a quando non sono
disponibili tutti questi elementi:

1. abilitazione o contratto PDR GME dell'operatore;
2. accesso PDR di test, credenziali e secondo fattore distribuiti da GME;
3. specifica tecnica del web service e endpoint autorizzati per quel profilo;
4. esito positivo dei test di upload e gestione delle ricevute GME/ACER;
5. conferma che gli XSD e le regole ACER incluse sono ancora vigenti.

Non è sicuro né corretto dedurre un protocollo di upload dai soli URL pubblici,
né archiviare segreti di autenticazione in SQLite. Quando i prerequisiti
saranno disponibili, il connettore dovrà usare un secret store, inviare solo
gli artefatti passati dal preflight e registrare le ricevute ufficiali senza
mai convertire un errore di rete in uno stato di successo.

## Ricevute PDR e ACER

L'[Implementation Guide PDR](https://www.mercatoelettrico.org/portals/0/Documents/it-it/20241001_PDR_Implementation_Guide.pdf)
documenta due evidenze per ogni report: la ricevuta GME
`FA_NomeFileInviato.xml` e la ricevuta ACER contenuta nell'archivio
`NomeFileInviato.zip`. La ricevuta GME usa `PIPEFunctionalAcknowledgement` e
può riportare `Accept`, `Reject` o `Partial`; una ricevuta ACER negativa può
essere rappresentata da un file `Receipt_…XML` nell'archivio.

L'app consente di importare manualmente il file ricevuto e lo associa in modo
immutabile all'XML ACER esportato tramite report, nome e SHA-256. Conserva
anche Load Code, data dell'esito, fonte, dettagli di errore e un evento audit.
La ricevuta resta etichettata come **importata, non verificata dal
connettore**: il file è preservato, ma il progetto non può attestare da solo
che sia stato scaricato da PDR o che il suo esito sia definitivo.

Per una ricevuta GME XML riconoscibile, il sistema estrae lo stato tecnico e
gli eventuali motivi di rigetto. Non deduce invece un'accettazione ACER dal
solo nome di uno ZIP: tale esito deve provenire dalla ricevuta o dal futuro
canale PDR autorizzato.

## Verifica locale

```bash
python3 build_frontend.py
python -m pytest -q                    # include tests/test_sicurezza.py
node tests/logic.test.cjs
node tests/runtime.test.cjs
```

`tests/test_sicurezza.py` presidia le difese invece delle funzioni: entità XML
esterne, payload ostili, tetti sui corpi, isolamento fra account, immutabilità
delle ricevute e intestazioni del browser. Ogni test fallisce se la protezione
corrispondente viene tolta.

La suite API copre Table 1, Table 2, validazione XSD, progressivi PDR,
isolamento per utente, audit e blocco dell'invio reale.
