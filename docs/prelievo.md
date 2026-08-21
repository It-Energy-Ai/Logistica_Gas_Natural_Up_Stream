# Profili di prelievo standard

Modulo `app/prelievo.py`, schermata **Profili di prelievo standard**, rotta `POST /api/prelievo`.

## Perché nel portale

Le percentuali giornaliere dei profili di prelievo standard sono pubblicate
da Snam su Jarvis (il portale dati pubblici) come file
«PERCENTUALI_DI_PRELIEVO_AT_…» per anno termico. Per uno shipper che prevede
la domanda sono il tassello che distribuisce il consumo annuo sui singoli
giorni gas: insieme ai coefficienti Wkr completano la base ufficiale della
previsione.

## Cosa fa

1. **Legge il file di Jarvis** — l'operatore carica il file `.xls` o `.xlsx`
   scaricato dalla pagina pubblica, oppure il portale lo scarica live
   (pulsante «Scarica da Jarvis», con l'anno termico facoltativo). Il parser
   è puro Python (solo libreria standard): `.xlsx` come archivio ZIP con XML,
   `.xls` come contenitore OLE2 con record BIFF8 (SST, LABELSST, NUMBER, RK,
   MULRK). La scelta del parser avviene dal firmamento del file, non
   dall'estensione.
2. **Valida la griglia senza inventare nulla** — una riga di intestazione con
   «Data», «Giorno» e i 20 parametri attesi (`c1%B1`…`c1%F3`, `c2%`, `c4%`,
   `t1%1`…`t1%3`); 365 o 366 righe dati (un giorno per riga, anno termico dal
   1° ottobre); **ogni colonna percentuale deve sommare esattamente 100**
   (tolleranza 1e-6), altrimenti l'errore elenca le colonne fuori controllo.
   Il valore `1E-8` significa zero / non applicabile ed è contato come tale.
3. **La sistema in una tabella** — una riga per giorno gas con i 20 valori,
   le somme per parametro (chip verdi se 100, rossi altrimenti), l'anno
   termico ricavato dalla prima data e gli avvisi (celle nulle, salti di
   date, inizio diverso dal 1° ottobre).

## Il download live, dichiarato

L'API di Jarvis **non è un contratto pubblico**: il portale la legge dalla
configurazione pubblica del sito Snam (`/config/portal-public-config.json`,
user_key e indirizzo dell'API compresi), così se Snam li aggiorna il modulo
continua a funzionare senza modifiche. La chiamata chiede l'elenco delle
pubblicazioni «VALORI PERCENTUALI PER LA DEFINIZIONE DEI PROFILI DI PRELIEVO
STANDARD» e scarica il file più recente, oppure quello dell'anno termico
richiesto (`2026-2027` o anche solo `2026`).

Se la rete o l'API non rispondono, l'errore invita a caricare il file a
mano: il portale non dipende dalla rete. Il fetch usa `urllib` della
libreria standard — **nessuna dipendenza nuova**, timeout 15 secondi.

## Due onestà dovute

- I dati sono pubblici ma Snam **vieta la redistribuzione a terzi**: qui
  sono mostrati all'operatore che li ha richiesti, **non conservati né
  ritrasmessi** — la stessa posizione di chi li apre nel browser. La rotta è
  stateless come quella della previsione e dei coefficienti Wkr.
- Le somme a 100 sono un **controllo di coerenza della pubblicazione**, non
  una rielaborazione: se una colonna non somma 100 il modulo si ferma e lo
  dice, invece di «aggiustare» i valori.

## Cosa NON fa

Non conserva nulla, non ritrasmette i dati, non corregge valori incoerenti,
non nasconde la fonte. Dettagli tecnici del formato: data come numero
seriale di Excel (epoca 1899-12-30), 22 colonne (Data, Giorno, 20 parametri),
valori decimali con punto; il file `.xls` è BIFF8 dentro un contenitore
OLE2/CFB con più sottoflussi (il parser non si ferma al primo record EOF).
