# EDIG@S 6.1 — nomine di trasporto gas

EDIG@S è lo standard europeo di scambio dati per il gas naturale, pubblicato da
EASEE-gas su [edigas.org](https://edigas.org). La versione 6.1 conta 128 schemi
divisi in sette famiglie. Vettore ne implementa **una parte precisa**, quella che
serve davvero a uno shipper nel ciclo quotidiano, e dichiara apertamente il resto
come fuori copertura.

## Cosa è implementato

| Documento | Sigla | Direzione | Stato |
|---|---|---|---|
| `Nomination_Document` | NOMINT | shipper → trasportatore | Generato e validato XSD |
| `NominationResponse_Document` | NOMRES | trasportatore → shipper | Letto, validato e confrontato |

Il modulo è in [`app/edigas.py`](../app/edigas.py); gli schemi ufficiali sono
inclusi in `app/schemas/edigas/` e la loro impronta è verificata da un test.

### Tipi di nomina supportati

I ruoli delle due parti non si scelgono a mano: li fissa il tipo di documento,
secondo la decision table del MIG "BRP Nomination and Matching" v6r0.

| `documentCode` | Significato | Emittente | Destinatario | Tipo nomina | Controparti |
|---|---|---|---|---|---|
| `01G` | Nomina al punto di connessione | ZSH shipper | ZSO trasportatore | A01 o A02 | sì |
| `02G` | Punto di scambio virtuale, OTC | ZSH shipper | ZUK area coordinator | A02 | sì |
| `03G` | Punto di scambio virtuale, borsa | ZUM clearing responsible | ZUK area coordinator | non previsto | sì |
| `04G` | Nomina non-matching, cliente finale | ZSH shipper | ZSO trasportatore | non previsto | no |

La struttura dell'XML cambia di conseguenza: con `01G` e `02G` le controparti
sono annidate dentro `NominationType`, con `03G` pendono direttamente dal punto
di connessione, con `04G` non ci sono affatto e le quantità stanno sul punto.
Una combinazione fuori tabella viene respinta prima di produrre il file.

### Il giorno gas

Il giorno gas italiano va **dalle 06:00 alle 06:00 ora locale**, che in UTC
significa 05:00Z con l'ora solare e 04:00Z con l'ora legale. Vettore lo calcola
sulla zona `Europe/Rome`, non su un offset fisso, quindi i due giorni di cambio
ora hanno la durata reale:

| Giorno gas | Ore | Intervallo |
|---|---|---|
| 15/01/2026 | 24 | `2026-01-15T05:00Z/2026-01-16T05:00Z` |
| 03/08/2026 | 24 | `2026-08-03T04:00Z/2026-08-04T04:00Z` |
| **28/03/2026** | **23** | `2026-03-28T05:00Z/2026-03-29T04:00Z` |
| **24/10/2026** | **25** | `2026-10-24T04:00Z/2026-10-25T05:00Z` |

Il cambio dell'ora avviene alle 02:00 locali, cioè **dentro il giorno gas
iniziato il pomeriggio precedente**: sono il 28 marzo e il 24 ottobre a durare
23 e 25 ore, non il 29 e il 25. Un profilo orario che non rispetta la durata
reale di quel giorno viene rifiutato con il conteggio corretto nel messaggio.

### Come si indicano le quantità

Tre forme, dalla più sintetica alla più esplicita, una sola per controparte:

* `quantita_giornaliera` — una quantità costante sull'intero giorno gas;
* `profilo_orario` — una lista con un valore per ogni ora (23, 24 o 25);
* `periodi` — intervalli scritti a mano, con `inizio`, `fine`, `direzione`, `quantita`.

Le direzioni sono `Z02` (entrata, immissione in rete) e `Z03` (uscita, prelievo).
Le unità ammesse dal tracciato sono solo tre: `KW1` kWh/h, `KW2` kWh/g e `A15`
kg/h — **nella nomina il gas non si esprime in metri cubi**.

Gli intervalli hanno la precisione del minuto: i secondi vengono troncati
prima dei controlli, così un periodo che si annullerebbe nel file viene
respinto invece di finirci come durata zero. Un istante scritto senza fuso è
inteso come ora italiana, coerentemente con la definizione del giorno gas.

Il punto di connessione accetta due codifiche, come previsto dalla decision
table: `305` per un codice EIC — e in quel caso la forma viene verificata,
inclusa la lettera che indica il tipo di oggetto — oppure `ZSO` per un codice
assegnato dal trasportatore, che è quanto usano gli esempi ufficiali.

### Validazione

Tre livelli, in quest'ordine:

1. **Campi e formati** — obbligatorietà, lunghezze, forma dell'EIC (16 caratteri),
   quantità non negative, periodi dentro il giorno gas e non sovrapposti.
2. **Regole di processo** — la decision table qui sopra.
3. **Schema XSD ufficiale** — il documento prodotto viene validato contro
   l'XSD EASEE-gas prima di essere salvato; se non passa, non viene prodotto.

I codici ammessi (`documentCode`, `roleCode`, `gasDirectionCode`,
`unitOfMeasureCode`, `nominationCode`) **non sono ricopiati nel codice**: vengono
letti a runtime dagli XSD, risolvendo la catena `union` fra la lista standard e
la lista locale del trasportatore. Se il pacchetto EDIG@S viene aggiornato, la
validazione resta allineata da sola.

### Avvisi non bloccanti

Le Basic Ground Rules chiedono che gli intervalli coprano l'intero periodo di
validità. Una nomina parziale — poche ore acquistate da una controparte — è però
pratica corrente, quindi la lacuna è segnalata come **avviso**, non come errore:
il file si genera comunque e l'operatore decide.

## La risposta del trasportatore

Il NOMRES ricevuto si incolla nella schermata Nomine. Vettore lo valida, lo
**abbina da solo** alla nomina che cita (`nomination_Document.identification` e
versione) e mostra gli scostamenti riga per riga: quantità ridotte, aumentate,
periodi confermati senza essere stati nominati, periodi nominati rimasti senza
risposta. Gli stati e le note del trasportatore vengono riportati come sono.

La lettura gestisce entrambe le forme legittime: serie sotto `External_Account`
quando c'è una controparte, e serie direttamente sotto `ConnectionPoint` nelle
nomine non-matching.

Un NOMRES può contenere fino a quattro serie per lo stesso intervallo, e **solo
la `16G` è la quantità confermata** allo shipper:

| `businessCode` | Significato | Entra nel confronto |
|---|---|---|
| `14G` | Elaborata dal trasportatore | no |
| `15G` | Elaborata dal trasportatore adiacente — direzione speculare | no |
| `16G` | **Confermata** | sì |
| `18G` | Nomina della controparte — direzione speculare | no |

Confrontarle tutte insieme produrrebbe scostamenti inesistenti su ogni
risposta reale: `15G` e `18G` portano per specifica la direzione opposta a
quella nominata, quindi non combacerebbero mai. Se il file non è conforme allo
schema, il confronto non viene tentato e l'errore è mostrato all'operatore.

## Cosa NON è implementato, e perché

Fuori copertura, non simulato: allocazione capacità (famiglia 1), trading
(famiglia 2), bilanciamento e settlement (famiglia 4), operazione di sistema
(famiglia 7).

Merita una nota a parte la famiglia 5, **REMIT and Transparency**. I documenti
di monitoraggio capacità e nomina — `CapacityAndNominationMonitoringDocument`
(CANMON) e `NominationAssignmentDocument` (NOMASS) — restringono l'emittente,
nei rispettivi XSD, ai soli ruoli `ZSO` (System Operator) e `ZUA` (Market
Information Aggregator):

```xml
<xsd:simpleType name="StandardRestrictedRoleCodeTypeCodeList">
    <xsd:restriction base="ecl:StandardRoleCodeTypeCodeList">
        <xsd:enumeration value="ZSO"/>
        <xsd:enumeration value="ZUA"/>
    </xsd:restriction>
</xsd:simpleType>
```

Uno shipper (`ZSH`) non è nell'elenco: un documento del genere emesso da lui
sarebbe **invalido già a livello di schema**. Per questo il tracciato
"trasporto gas" del modulo REMIT resta dichiarato bloccato invece di essere
simulato — l'obbligo di segnalazione di quei dati ricade sul trasportatore.

Da verificare con il proprio consulente prima dell'uso produttivo: la
ripartizione giuridica dell'obbligo di reporting ex Reg. (UE) 1348/2014 non è
trattata dalla documentazione EDIG@S, che è una specifica di formato, non una
guida di conformità.

## Robustezza

Il documento prodotto passa tre volte dai controlli, ma conta anche cosa
succede quando l'input è sbagliato: nessun payload — tipi errati, caratteri di
controllo, date impossibili, quantità non decimali, corpi enormi — deve
produrre un errore interno invece di un messaggio sul campo, e c'è una batteria
di test che lo verifica. L'unico ingresso non fidato, il file NOMRES incollato
dall'operatore, viene letto con un parser che ha entità esterne, DTD e accesso
di rete disattivati esplicitamente. L'impronta dello schema restituita al
client viene verificata a ogni compilazione, non solo dichiarata.

## Fonte degli schemi

Pacchetto `Edigas 6.1 full` scaricato da edigas.org il 31/07/2026:

```
https://edigas.org/_files/downloads/9_Edigas_6.1_full_2026-07-31.zip
SHA-256 70c0bf6f6081649c52edb2f00add42640b3dbc7bf244b7d881e6b78e86f26bc7
```

I file di esempio ufficiali del pacchetto sono conservati in
`tests/dati/edigas/` e usati come casi di prova: la suite genera i documenti a
partire dai dati applicativi e ne confronta la struttura con quella degli
esempi EASEE-gas.

## API

| Metodo | Percorso | Cosa fa |
|---|---|---|
| `GET` | `/api/edigas/catalogo` | Codici, tipi documento e schemi, letti dagli XSD |
| `GET` | `/api/edigas/nomine` | Elenco delle nomine generate dall'account |
| `POST` | `/api/edigas/nomine` | Genera e valida un NOMINT |
| `GET` | `/api/edigas/nomine/{id}` | Dettaglio con XML |
| `GET` | `/api/edigas/nomine/{id}/download` | Scarica il file |
| `POST` | `/api/edigas/risposte` | Legge un NOMRES e lo confronta con la nomina |

Le nomine sono isolate per account, come il resto dello stato.

## Limiti dichiarati

* Nessun invio automatico al trasportatore: Vettore produce il file, il canale
  di trasmissione (AS4, portale, e-mail) resta esterno.
* Il messaggio di conferma `ACKNOW` non è gestito.
* Le nomine single-sided delegate (`NOMAUT`) non sono coperte.
* Dalla UI si indica una controparte per volta; l'API accetta più controparti e
  profili orari completi.
