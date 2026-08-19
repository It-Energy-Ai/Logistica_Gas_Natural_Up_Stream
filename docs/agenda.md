# Agenda regolatoria — scadenze con data certa, senza date inventate

Modulo `app/agenda.py`, schermata **Agenda**, rotte `/api/agenda/*`.

## Il principio

Un calendario di scadenze per uno shipper di gas è utile nella misura in cui
le date sono **vere**. Le fonti fissano alcune scadenze nel proprio testo
(Codice di Stoccaggio Stogit, Codice di Rete Snam); per molte altre la data
non esiste a priori — la decide qualcuno, in un momento che nessun testo
dichiara. Questo modulo precompila solo il primo gruppo e lascia il secondo
alle voci personalizzate dell'operatore:

- **nel modello regolatorio** entrano solo voci con data fissata dalla
  fonte, con il riferimento al paragrafo che la fissa;
- **consultazioni ARERA** (le date di chiusura sono decise caso per caso),
  **aste Snam** (il calendario è pubblicato su Jarvis, anno per anno) e
  **obblighi REMIT** (legati alla singola transazione) **non** sono nel
  modello: si creano a mano, come si crea a mano qualunque scadenza interna.

Il modello contiene 14 voci per l'Anno Termico:

| Voce | Data | Fonte |
| --- | --- | --- |
| Inizio Fase di Iniezione | 1/4 | Codice di Stoccaggio · definizione fasi |
| Fine Fase di Iniezione | 31/10 | Codice di Stoccaggio · definizione fasi |
| Inizio Fase di Erogazione | 1/11 | Codice di Stoccaggio · definizione fasi |
| Fine Fase di Erogazione | 31/3 (AT+1) | Codice di Stoccaggio · definizione fasi |
| Programma stagionale di erogazione in SAMPEI | 23/10 | §6.3.2: inserimento entro il 23 ottobre |
| Accettazione del programma di erogazione | 31/10 | §6.3.2: comunicazione entro il 31 ottobre |
| Accettazione del programma di iniezione | 31/3 (AT+1) | §6.3.1: entro e non oltre il 31 marzo |
| Calendario di conferimento dei Servizi Base | 1/2 | Cap. 5: pubblicazione entro il 1° febbraio |
| Fattura Stogit, periodo iniezione | 31/3 (AT+1) | Cap. 7 Allegato 1: entro fine AT |
| Fattura Stogit, periodo erogazione | 31/5 (AT+1) | Cap. 7 Allegato 1: entro il 31 maggio |
| Fattura dell'utente a Stogit, iniezione | 30/4 (AT+1) | Cap. 7 Allegato 1: entro il 30 aprile |
| Fattura dell'utente a Stogit, erogazione | 30/6 (AT+1) | Cap. 7 Allegato 1: entro il 30 giugno |
| Inizio Anno Termico di trasporto | 1/10 | Codice di Rete Snam |
| Termine nota giustificativa UIOLI | 7 gg lavorativi dal 30/9 (AT+1) | Codice di Rete, Cap. 7 §4.3 |

L'Anno Termico di istanziazione è quello dello **stoccaggio** (avvio 1
aprile); le voci del trasporto si riferiscono all'Anno Termico con avvio
nello stesso anno civile (1 ottobre). La data della nota UIOLI è l'unica non
fissa: la calcola il modulo Trasporto con la regola dei sette giorni
lavorativi (stessa funzione `scadenza_nota` già usata e testata lì).

## Stati: cosa si scrive e cosa si deriva

Gli stati scrivibili sono tre: **aperta**, **adempiuta**, **saltata**.
«Scaduta» non è uno stato: è **derivato dalla data** (una voce aperta oltre
la data di scadenza è mostrata come scaduta, ma resta aperta). Il motivo è
operativo: una scadenza superata non è un fatto compiuto, è una decisione da
prendere — adempierla (anche in ritardo, generando la prossima occorrenza se
ricorrente) o dichiararla saltata. Il contatore «scadute» conta proprio le
aperte oltre la data, perché sono il problema più urgente.

Adempiendo una voce **ricorrente** (annuale, mensile, trimestrale,
settimanale, giorno gas) nasce la prossima occorrenza, **aperta**, con la
data calcolata dall'adempimento (non dal calendario: un adempimento in
ritardo non recupera le occorrenze perse). Il giorno del mese è chiuso al
mese di arrivo (31/1 → 28/2; 29/2 → 28/2 l'anno dopo). Le occorrenze
successive mantengono la chiave del modello ma perdono l'anno di riferimento
(`modello_anno = NULL`), così l'istanziazione del modello per un nuovo AT non
le tocca.

## Istanziazione idempotente

`POST /api/agenda/modello/istanzia {anno}` crea le 14 voci per l'AT scelto,
ciascuna come scadenza **annuale** con la data della fonte. La tabella ha un
vincolo `UNIQUE(email, modello_chiave, modello_anno)`: ripetere l'operazione
non duplica nulla, e se tutte le voci sono già presenti la richiesta è
respinta (409) — l'operatore ha già davanti l'elenco.

## Fonti

- Codice di Stoccaggio Stogit (testo vigente, allegato alle delibere ARERA):
  fasi, §6.3.1, §6.3.2, capitolo 5, Cap. 7 Allegato 1.
- Codice di Rete Snam Rete Gas: Anno Termico dal 1° ottobre, Cap. 7 §4.3
  per la nota UIOLI (termine già implementato nel modulo Trasporto).

La scadenza «oggi» e il mese corrente si calcolano sul calendario italiano
(Europe/Rome): il giorno gas inizia alle 6:00, ma una scadenza regolatoria
è una data civile, non un ciclo di nomina.