# Previsione della domanda

Modulo `app/previsione.py`, schermata **Previsione della domanda**, rotta `POST /api/previsione`.

## Perché nel portale

Uno shipper prevede per nominare: la domanda attesa dei prossimi giorni è la
base del NOMINT. Il modulo prende lo storico giornaliero incollato come CSV e
restituisce tre cose, nell'ordine giusto:

1. **il backtest a finestre scorrevoli** — l'ensemble viene addestrato più
   volte (quante lo storico consente, fino a quattro) *senza vedere* gli
   ultimi giorni di ogni finestra e confrontato con la realtà: MAE, RMSE,
   MAPE, MASE e sMAPE dicono quanto ha sbagliato, ed è quella la misura da
   guardare prima di fidarsi;
2. **la previsione del futuro vero** — giorni strettamente successivi
   all'ultimo osservato. Sembra ovvio; una proposta esterna respinta
   presentava come "previsione" la riproiezione degli ultimi giorni già noti;
3. **una banda dichiarata** — quantili 10°–90° dei residui out-of-sample
   dell'ensemble misurati sulle finestre del backtest, allargati con la
   radice dell'orizzonte. Non è un intervallo di confidenza parametrico e
   non viene spacciato per tale.

## Il metodo, dichiarato

Un **ensemble pesato** dei tre approcci che le competizioni M3/M4 di
Makridakis indicano come i più affidabili sulle serie brevi con stagionalità:

- **Holt-Winters additivo con trend smorzato** (ETS(A,Ad,A) nella tassonomia
  di Hyndman): il trend non viene estrapolato all'infinito, il suo
  contributo si attenua passo dopo passo (φ in griglia: 0.80, 0.90, 0.98,
  1.00). Sulle serie brevi di domanda è la scelta prudente;
- **metodo Theta** (Assimakopoulos & Nikolopoulos 2000, il più accurato
  dell'M3): regressione lineare sulle medie settimanali per il trend,
  lisciamento esponenziale semplice per il livello, stagionalità come
  aggiustamento medio per giorno della settimana;
- **naive stagionale**: l'ultima settimana osservata, ripetuta. Il
  riferimento che ogni modello deve battere — qui invece di nasconderlo lo
  si mette nell'ensemble e lo si dichiara.

I pesi sono proporzionali all'inverso dell'errore (MAE) di ciascun membro
sul backtest: i membri perfetti si dividono il peso in parti uguali, gli
altri restano a zero. La previsione finale riaddestra ogni membro su **tutto**
lo storico, ma con i pesi derivati dal backtest — non dai dati che i membri
hanno già visto, altrimenti i pesi sarebbero truccati.

Tutto in **puro Python**: nessuna dipendenza da pandas, statsmodels o
pmdarima. Il portale viaggia anche come eseguibile e cento megabyte di
librerie scientifiche non valgono un'etichetta più altisonante sullo stesso
lavoro. I coefficienti di lisciamento si scelgono su piccole griglie
deterministiche minimizzando l'errore a un passo: **stesso input, stessa
previsione, sempre** — e un test lo presidia.

## Le metriche del backtest

- **MAE** e **RMSE**: errore medio assoluto e quadratico, nella stessa unità
  di misura della domanda;
- **MAPE**: errore percentuale (assente se lo storico contiene zeri);
- **MASE**: errore diviso quello del naive a un passo sullo storico di
  addestramento — insensibile alla scala, confrontabile fra serie; sotto 1
  significa battere il naive a un passo;
- **sMAPE**: errore percentuale simmetrico, robusto sui valori piccoli.

Le stesse cinque metriche sono calcolate per il naive stagionale, così il
confronto è dichiarato nei due sensi («Batte il riferimento del X%» oppure
«NON batte il riferimento»).

Servono almeno **28 giorni di addestramento più l'orizzonte** (35 per
prevedere una settimana). Orizzonti ammessi: 7, 14, 28 giorni.

## Il CSV, senza sorprese

Una riga per giorno, data e valore. Il parser accetta `gg/mm/aaaa` e
`AAAA-MM-GG`, separatori `;` `,` o tabulazione, decimali con la virgola e
migliaia col punto, intestazioni riconosciute per nome (`data`/`valore`,
`date`/`value`, `giorno`/`domanda`…) in qualunque ordine. Le righe illeggibili
vengono indicate **per numero di riga**; i valori negativi sono respinti.

Gli interventi sui dati sono dichiarati, mai silenziosi: i giorni doppi si
aggregano con il metodo scelto (somma o media) e vengono contati; i buchi
fino a 7 giorni si colmano per interpolazione lineare e vengono contati; un
buco più largo **ferma il calcolo** — l'interpolazione inventerebbe una
settimana intera.

## Il riferimento che non si può barare

Ogni backtest confronta il modello con il **naive stagionale** — l'ultima
settimana osservata, ripetuta. È il minimo sindacale della letteratura
previsionale: un modello che non batte «stesso giorno della settimana
scorsa» non merita fiducia, e la schermata lo dichiara nei due sensi. Su un
profilo perfettamente periodico il naive è imbattibile, e il portale lo
ammette invece di fingere un vantaggio.

## Cosa NON fa

Non conserva nulla (lo storico resta dell'operatore), non pretende di essere
un oracolo, non nasconde il metodo dietro un nome di libreria. Se un giorno
serviranno modelli più ricchi (ARIMA, regressori climatici), la strada è la
stessa: metodo dichiarato, backtest incorporato, determinismo verificato.

## La correzione climatica Wkr (facoltativa)

Accanto alla previsione il modulo può mostrare — e, se l'operatore lo
chiede, applicare — il **fattore di correzione climatica Wkr** pubblicato
ogni giorno da Snam per ciascuna zona climatica (vedi
[docs/wkr.md](wkr.md)). Si incolla il CSV di Jarvis e si indica la zona:

- ogni giorno previsto espone il fattore ufficiale (tipo C/I/P…) in una
  colonna dedicata;
- con «Applica il fattore» attivo, valore, minimo e massimo sono
  moltiplicati per il fattore del giorno; il valore del modello resta
  visibile. È una semplice moltiplicazione per il fattore ufficiale, **non**
  una stima del modello;
- la finestra pubblicata arriva a G+5: i giorni previsti oltre non hanno un
  fattore (mai inventato) e un avviso lo dichiara.

Senza CSV Wkr l'output è identico a prima: l'aggancio è del tutto
facoltativo e retrocompatibile.
