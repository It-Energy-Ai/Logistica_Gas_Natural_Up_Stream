# Coefficienti Wkr

Modulo `app/wkr.py`, schermata **Coefficienti Wkr**, rotta `POST /api/wkr`.

## Perché nel portale

Il fattore di correzione climatica Wkr, determinato per ciascuna zona
climatica, è pubblicato **ogni giorno** da Snam Rete Gas su Jarvis (il
portale dati pubblici). Per uno shipper che prevede la domanda per nominare
è il tassello ufficiale che collega la previsione «grezza» alla correzione
climatica usata nel settlement: il modulo Previsione lo mostra accanto a
ciascun giorno previsto e, se l'operatore lo chiede, lo applica.

## Cosa fa

1. **Legge il CSV di Jarvis** — l'operatore lo scarica dalla pagina pubblica
   «Coefficienti WKR» e lo incolla, oppure il portale lo scarica live
   (pulsante «Scarica da Jarvis»). Il parser valida la griglia zona × giorno
   senza inventare nulla: righe illeggibili indicate per numero, Wkr fuori
   dall'intervallo plausibile (0.1–5) rifiutati, tipi non previsti rifiutati,
   griglia non rettangolare o coppie duplicate fermano il calcolo.
2. **Lo sistema in una tabella** — 18 zone climatiche (codici 11–29) per la
   finestra di sette giorni pubblicata: ieri **C** (consuntivo), oggi **I**
   (in corso), i prossimi cinque **P…P5** (provvisori, la previsione di Snam
   stessa). I valori diversi da 1 sono evidenziati: sono la correzione
   effettiva; in estate il Wkr è 1 ovunque e il modulo lo dice.
3. **Espone il fattore per zona e data** — `fattori_per_zona()` restituisce
   la mappa data → Wkr usata dal modulo Previsione.

## Il download live, dichiarato

L'API di Jarvis **non è un contratto pubblico**: il portale la legge dalla
configurazione pubblica del sito Snam (`/config/portal-public-config.json`,
user_key e indirizzo dell'API compresi), così se Snam li aggiorna il modulo
continua a funzionare senza modifiche. La chiamata chiede l'elenco delle
pubblicazioni «Coefficienti WKR» per l'anno scelto e scarica il **CSV più
recente** (l'aggiornamento serale `ore18` prevale sul mattutino `ore11`).

Se la rete o l'API non rispondono, l'errore invita a incollare il CSV a
mano: il portale non dipende dalla rete. Il fetch usa `urllib` della
libreria standard — **nessuna dipendenza nuova**, timeout 15 secondi.

## Due onestà dovute

- I dati sono pubblici ma Snam **vieta la redistribuzione a terzi**: qui
  sono mostrati all'operatore che li ha richiesti, **non conservati né
  ritrasmessi** — la stessa posizione di chi li apre nel browser. La rotta è
  stateless come quella della previsione.
- Il Wkr **non è una stima del modello**: è il fattore ufficiale. Quando
  viene applicato alla previsione è una semplice moltiplicazione
  (`valore × Wkr`), dichiarata come tale, e la schermata avverte di
  verificare che la direzione (moltiplicare o dividere) corrisponda all'uso:
  normalizzazione di settlement e previsione della domanda sono operazioni
  diverse.

## L'aggancio con Previsione

Nella schermata Previsione, il riquadro facoltativo «Correzione climatica
Wkr» accetta il CSV di Jarvis e la zona climatica. Per ogni giorno previsto:

- il **fattore ufficiale** compare in una colonna dedicata (tipo C/I/P…);
- se «Applica il fattore» è attivo, valore, minimo e massimo sono
  moltiplicati per il fattore del giorno e il valore del modello resta
  visibile (`valore_modello`);
- la finestra pubblicata arriva a **G+5**: con orizzonti 14 o 28 i giorni
  scoperti restano senza fattore (mai inventato) e un avviso lo dichiara.

Zona assente o non presente nel CSV → errore che elenca le zone disponibili.

## Cosa NON fa

Non conserva nulla, non ritrasmette i dati, non inventa fattori per i giorni
fuori finestra, non nasconde la fonte. Dettagli tecnici del formato CSV:
colonne `ZONA_CLIMATICA;GIORNO;DATA_WKR;Wkr;TIPO;DATA_HDD`, giorni in
`AAAAMMGG`, decimali con punto o virgola.
