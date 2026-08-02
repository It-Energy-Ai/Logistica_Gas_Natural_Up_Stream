# EMIR REFIT — segnalazione dei derivati al Trade Repository

Modulo `app/emir.py`, schermata **EMIR · Trade Repository**, rotte `/api/emir/*`.

## Perché è separato dal REMIT

Sono due obblighi diversi, e vengono spesso confusi perché uno stesso forward
sul gas può ricadere in entrambi.

| | REMIT | EMIR REFIT |
|---|---|---|
| Norma | Reg. (UE) 1227/2011 | Reg. (UE) 648/2012, come modificato dal Reg. 2019/834 |
| Oggetto | contratti e ordini sui mercati energetici all'ingrosso | contratti derivati |
| Destinatario | ACER, tramite un RRM | un Trade Repository registrato ESMA |
| Tracciato | XML ACER Table 1 V3 / Table 2 V1 | ISO 20022 `auth.030.001.03` |
| Identificativo | UTI ACER (TRUM Annex IV) | UTI ISO 23897 |

Gli identificativi si chiamano allo stesso modo ma sono generati con regole
diverse: quello ACER ha 45 caratteri e un algoritmo pubblicato, quello EMIR ne
ha da 20 a 52 e il primo blocco è il LEI di chi lo genera. Riusarne uno al
posto dell'altro produce due segnalazioni che nessun sistema riesce ad
appaiare. Per questo il modulo non condivide nulla con `app/remit.py`: tabelle
separate, schermata separata, registro separato.

## Cosa è implementato

| Documento | Schema | Direzione | Stato |
|---|---|---|---|
| Segnalazione del derivato | `auth.030.001.03` | soggetto → Trade Repository | Generata e validata XSD |
| Intestazione applicativa | `head.001.001.01` | soggetto → Trade Repository | Generata e validata XSD |
| Esito delle segnalazioni | `auth.092.001.04` | Trade Repository → soggetto | Letto, validato e abbinato agli UTI inviati |

Gli schemi sono quelli pubblicati da ESMA (pacchetti *EMIR Refit — Incoming
Messages* e *Outgoing Messages*, v1.1.0), inclusi in `app/schemas/emir/` e
verificati per impronta SHA-256 a ogni compilazione: uno schema sostituito
blocca la generazione invece di produrre un file che sembra valido.

### Le otto azioni

Nel tracciato EMIR REFIT il tipo di azione **non è un campo**: è il nome
dell'elemento che avvolge la segnalazione. Cercare un `<ActnTp>` nel messaggio
è inutile — esiste solo nell'esito che torna dal registro.

| Azione | Elemento | Sigla | Quando |
|---|---|---|---|
| Nuova operazione | `New` | NEWT | primo invio del derivato |
| Modifica | `Mod` | MODI | cambiano le condizioni |
| Correzione | `Crrctn` | CORR | si corregge un dato sbagliato |
| Componente di posizione | `PosCmpnt` | POSC | l'operazione confluisce in una posizione |
| Riattivazione | `Rvv` | REVI | si riapre un derivato chiuso per errore |
| Cessazione anticipata | `Termntn` | TERM | chiusura prima della scadenza |
| Aggiornamento valutazione | `ValtnUpd` | VALU | nuovo valore, condizioni invariate |
| Annullamento per errore | `Err` | EROR | il contratto non esisteva o non andava segnalato |

Ogni azione porta con sé una **forma diversa del documento**, non per scelta di
interfaccia ma perché lo XSD usa una variante distinta per ciascun elemento
contenitore. Le differenze che contano:

- **Cessazione, valutazione e annullamento** non ammettono la controparte
  estesa né i dati di contratto: portano il solo identificativo dei soggetti.
- Il **tipo di evento** (`DerivEvt/Tp`) è obbligatorio per `New` e `Termntn`,
  facoltativo per `Mod`, e **vietato** per `Crrctn`, `Rvv`, `PosCmpnt`,
  `ValtnUpd` ed `Err`. Il modulo lo scarta con un avviso invece di lasciarlo
  arrivare allo schema, che respingerebbe il file con un messaggio oscuro.
- Il **componente di posizione** ammette solo il livello `TCTN` e richiede
  l'UTI della posizione in cui confluisce.
- La cessazione richiede la **data di cessazione anticipata**, che negli altri
  profili non esiste.

### Il profilo gas

Un contratto a termine sul gas si segnala come merce fisica:

```xml
<Cmmdty><Nrgy><NtrlGas>
  <BasePdct>NRGY</BasePdct><SubPdct>NGAS</SubPdct><AddtlSubPdct>TTFG</AddtlSubPdct>
</NtrlGas></Nrgy></Cmmdty>
<NrgySpcfcAttrbts>
  <DlvryPtOrZone><Cd>21Y100A1001A1011</Cd></DlvryPtOrZone>
  <LdTp>GASD</LdTp>
  <DlvryAttr>…</DlvryAttr>
</NrgySpcfcAttrbts>
```

Gli indici ammessi da ESMA per il gas sono cinque più «altro»: `GASP`
(GASPOOL), `LNGG` (GNL), `NCGG` (NetConnect Germany), `TTFG` (TTF), `NBPG`
(NBP). **Il PSV italiano non ha un codice proprio**: va segnalato come `OTHR`.
È una lacuna dello schema, non del modulo, e vale la pena saperlo prima di
cercare un codice che non esiste.

Il punto di consegna è un EIC di 16 caratteri. Il tipo di carico `GASD` è
letteralmente «giorno gas»: è il valore giusto per un contratto giornaliero
italiano, dove il giorno va dalle 06:00 alle 06:00 locali.

### I codici vengono letti dagli schemi, non ricopiati

Ogni tendina dell'interfaccia è costruita a runtime dai facet dello XSD
(`emir.codici_ammessi`). Le etichette italiane stanno in una tabella separata,
e un test verifica che **l'insieme delle chiavi tradotte coincida esattamente
con quello dei valori ammessi dallo schema**: un codice inventato fa cadere la
suite, un codice nuovo aggiunto da ESMA viene segnalato invece di restare
fuori dalle tendine in silenzio.

Il test serve a qualcosa di concreto. Codici come `FORE`, `BIL`, `PTNG`,
`NCC`, `TCTC`, `IRDS` compaiono con disinvoltura in esempi e integrazioni
trovabili in rete: **nessuno di questi esiste** in `auth.030.001.03`.

### Il LEI viene verificato davvero

Le ultime due cifre di un LEI sono di controllo (ISO 17442, che rimanda a
ISO/IEC 7064 MOD 97-10 — la stessa aritmetica dell'IBAN). Un LEI con una cifra
sbagliata supera qualsiasi espressione regolare e viene respinto giorni dopo
dalle regole di validazione del registro. Qui si controlla subito.

> Attenzione a una confusione ricorrente: il controllo del LEI **non è
> l'algoritmo di Luhn**. Chi implementa Luhn ottiene un validatore che accetta
> LEI sbagliati e ne rifiuta di buoni.

### L'UTI

Il formato è quello di ISO 23897: da 20 a 52 caratteri alfanumerici maiuscoli,
di cui i primi 20 sono il LEI del soggetto che lo genera. Lo standard fissa la
forma, **non la regola con cui si costruisce la coda**: quella la sceglie chi
genera il codice.

Se il campo è lasciato vuoto, il modulo costruisce una coda deterministica
dalla chiave dell'operazione, così due invii dello stesso contratto producono
lo stesso UTI invece di duplicarlo in registro. È una regola nostra, non un
algoritmo ESMA, e viene dichiarata come avviso sul documento generato. Se la
controparte ha già assegnato un UTI, va usato quello.

## L'esito del Trade Repository

Il documento `auth.092` è l'unico che dice davvero se una segnalazione è
passata. Vettore lo legge riga per riga ed espone, per ogni operazione:

- l'UTI e il tipo di azione,
- lo stato (`ACPT` accettato, `RJCT` respinto, `INCF` nome file errato, `CRPT`
  file danneggiato, `NAUT` non autorizzato),
- **le regole di validazione violate**, con identificativo e descrizione.

Un esito arriva per l'intero flusso della giornata e può contenere righe che
riguardano altri soggetti: quelle vengono scartate dall'elenco a schermo.
Mostrarle porterebbe a cercare nei propri file la causa di un rifiuto altrui.

Gli esiti importati sono **immutabili** (trigger `emir_esito_no_update` e
`emir_esito_no_delete`): valgono come prova di ciò che il registro ha accolto.
Reimportare lo stesso file non crea un secondo record, si riconosce
dall'impronta.

## Cosa NON è implementato, e perché

**L'involucro del file.** ESMA pubblica lo schema del messaggio
(`auth.030.001.03`) e quello dell'intestazione (`head.001.001.01`), ma **non**
l'elemento che li unisce in un unico file, né la regola di denominazione: sono
definiti da ciascun Trade Repository. Vettore genera e valida i due documenti
separatamente e lo dichiara; il confezionamento va fatto seguendo le
istruzioni del registro scelto.

**L'invio.** La trasmissione passa dal canale del TR (portale, SFTP, API) con
credenziali e abilitazioni che restano fuori dal portale. Non c'è finto invio
e non c'è finta ricevuta: l'unica ricevuta che il modulo conosce è il file
`auth.092` che l'operatore importa.

**Il collaterale e i margini.** L'azione `MARU` esiste nell'esito ma la
segnalazione dei margini usa un altro messaggio, non coperto.

**Le altre classi di attività.** Tassi, credito, azionario e valute sono
rappresentabili dallo schema ma non hanno un pannello: il modulo copre il
profilo merci/energia, che è quello di uno shipper. I campi delle altre classi
non vengono generati vuoti né a caso.

**Le regole di validazione ESMA.** Il documento supera lo schema XSD, che è
condizione necessaria ma non sufficiente: i registri applicano anche le
*Validation Rules* pubblicate a parte (coerenza fra nozionale, quantità e
prezzo, date, combinazioni ammesse). Vettore ne implementa alcune per buon
senso — scadenza non anteriore all'efficacia, capacità senza unità di misura,
data iniziale senza data finale — ma non la tabella completa. Il primo esito
dal registro resta il collaudo vero.

## Fonte degli schemi

Pacchetti ufficiali ESMA, sezione *EMIR Reporting — XML schemas*:

| File | SHA-256 |
|---|---|
| `auth.030.001.03_ESMAUG_DATTAR_1.1.0.xsd` | `cdff94d4…2fd06a7a` |
| `head.001.001.01_ESMA_restricted.xsd` | `3e3f399b…8a24c2f9` |
| `auth.092.001.04_ESMAUG_DATREJ_1.0.0.xsd` | `cacaddfe…fa0a6de9` |

Le impronte complete sono in `emir.SCHEMI` e vengono verificate a ogni
compilazione dello schema.
