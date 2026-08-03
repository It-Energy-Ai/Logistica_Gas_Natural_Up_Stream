# Trasporto — interruzioni ricevute e UIOLI, dal lato dello shipper

Modulo `app/trasporto.py`, schermata **Trasporto · Interruzioni e UIOLI**, rotte `/api/trasporto/*`.

## Da che parte sta questo modulo

Nel Codice di Rete l'interruzione della capacità interrompibile e il ritiro
use-it-or-lose-it sono **atti di Snam Rete Gas**: è il Trasportatore che «ha
facoltà di interrompere» (Cap. 3, §2.2.1) e che «effettua la verifica delle
condizioni» del ritiro comunicandone l'esito all'Autorità (Cap. 7, §4.3). Un
portale per shipper che eseguisse quei processi incarnerebbe la controparte.

Qui il perimetro è quello vero dello shipper: **subire, controllare,
rispondere**.

## Registro delle interruzioni

Quando Snam comunica un'interruzione — «i giorni di interruzione comunicati
in via definitiva dal Trasportatore sono operativi senza ulteriore conferma»
(§2.2.1-c) — l'operatore la trascrive nel registro. Il portale:

- conteggia i giorni per **punto e Anno Termico** (1 ottobre → 30
  settembre), distinguendo le interruzioni **totali dalle parziali**: il
  Codice tiene separati i plafond — Tmax copre le interruzioni totali o
  parziali, T1max è un periodo **aggiuntivo** riservato alle parziali entro
  il 20% della capacità conferita (§2.2.1-a) — e sommare tutto in un numero
  solo direbbe che il Tmax è più eroso di quanto sia. I giorni consecutivi
  si misurano fondendo i periodi adiacenti;
- verifica la prima delle **due regole quantitative che il Codice fissa nel
  proprio testo**: «il Trasportatore potrà esercitare nuovamente la propria
  facoltà di interruzione non prima che siano trascorsi almeno 4 giorni dal
  termine della precedente interruzione» (§2.2.1-b). Una violazione non
  blocca la registrazione: produce un avviso, perché è **un elemento da
  contestare a Snam**, non un errore dell'operatore. Il controllo vale in
  entrambe le direzioni, anche registrando le comunicazioni in ordine
  sparso, e ignora maiuscole e spazi doppi nel nome del punto. La seconda
  regola — il tetto del **20%** per le parziali aggiuntive in T1max — non è
  verificabile dal registro da solo: richiede la capacità interrompibile
  conferita e il valore di T1max pubblicato da Snam;
- respinge le **sovrapposizioni** sullo stesso punto: nel registro di uno
  shipper possono essere solo refusi di trascrizione o comunicazioni anomale;
- segnala le interruzioni a **cavallo del 30 settembre**, perché i giorni si
  conteggiano sull'Anno Termico in cui cadono.

**Cosa non c'è, e perché.** I **valori** di Tmax, T1max, Dmax e Pmin non
sono nel Codice: sono pubblicati da Snam sul proprio sito, per ciascun Punto
di Entrata interconnesso con l'estero e per durata di conferimento. Il
portale non li ricopia né li inventa — una proposta che fissava «Tmax=30,
Dmax=5, Pmin=48» è stata verificata contro tutte le 648 pagine del Codice:
quei valori non esistono. Le interruzioni sono trascrizioni manuali, quindi
**correggibili** (a differenza di ricevute PDR ed esiti EMIR, che sono prove
e restano immutabili).

## Utilizzo Medio del semestre

La condizione b) del ritiro (Cap. 7, §4.2) scatta se l'Utilizzo Medio è
**sotto l'80% in ciascuno dei due semestri** — 1/10–31/3 e 1/4–30/9 — non su
una media annuale. La calcolatrice implementa la definizione del §4.3.1:

    Utilizzo Medio = Σ immessi/prelevati (bilanci definitivi)
                     ─────────────────────────────────────────
                     Σ capacità conferita giornaliera
                       − capacità messa a disposizione
                       − capacità non disponibile (riduzioni/interruzioni, Capp. 14 e 21)
                       − quantitativi attestati con nota giustificativa

Le tre detrazioni al denominatore non sono un dettaglio: ignorarle gonfia il
denominatore e fa sembrare a rischio ritiro chi non lo è. Il risultato dice
esplicitamente che le altre condizioni (titolarità pluriennale, mancata messa
a disposizione, capacità interamente conferita) **le verifica Snam**: il
portale non finge di conoscere ciò che sta nei sistemi del Trasportatore.

## Nota giustificativa

È l'unico atto **attivo** che il §4.3 riconosce allo shipper nel processo
UIOLI: «entro sette giorni lavorativi dal termine dell'Anno Termico di
Riferimento, l'Utente può trasmettere a Snam Rete Gas una nota giustificativa»
con l'attestazione dei quantitativi da portare a detrazione (e la loro
durata) e le **motivazioni documentate** del mancato utilizzo. Se la nota
manca, il ritiro procede; se c'è, decide l'Autorità.

Il portale prepara la nota con il contenuto prescritto, calcola il termine
(7 giorni lavorativi lunedì–venerdì dopo il 30 settembre; i festivi
infrasettimanali potrebbero solo accorciarlo, quindi la data mostrata è
prudente), marca le note preparate fuori termine e le fa **scaricare come
file di testo**. Non le trasmette: le modalità di invio le pubblica Snam sul
proprio sito, e una spedizione simulata varrebbe zero come tutte le
spedizioni simulate che questo progetto rifiuta.

## Il use-it-or-lose-it in breve

Si applica ai punti di **Passo Gries, Tarvisio e Gorizia** (art. 14ter della
Delibera 137/02). Il ritiro richiede *tutte* le condizioni del §4.2:
titolarità di capacità continua per più di un anno, Utilizzo Medio sotto
l'80% in entrambi i semestri, mancata messa a disposizione a prezzo non
superiore al Prezzo di Riserva, capacità disponibile interamente conferita.
La formula della capacità ritirabile — CNU = max[0; C − CUM/0,8] — e la
comunicazione all'Autorità entro il 30 novembre sono del Trasportatore. Il
FDA UIOLI di breve termine (§5, art. 14quater) resta anch'esso un processo di
Snam: le sue due condizioni sono **alternative**, non cumulative, e l'unica
esenzione riguarda chi ha detenuto meno del 10% della capacità.

## Fonte

Codice di Rete Snam Rete Gas, revisione XCI: Capitolo 3 §2.2 (pagg. 3-3/3-4)
per il trasporto interrompibile, Capitolo 7 §4-5 (pagg. 7-19/7-26) per il
use-it-or-lose-it. Le citazioni tra virgolette sono verbatim dal testo.
