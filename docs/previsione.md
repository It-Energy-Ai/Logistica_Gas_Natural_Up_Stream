# Previsione della domanda

Modulo `app/previsione.py`, schermata **Previsione della domanda**, rotta `POST /api/previsione`.

## Perché nel portale

Uno shipper prevede per nominare: la domanda attesa dei prossimi giorni è la
base del NOMINT. Il modulo prende lo storico giornaliero incollato come CSV e
restituisce tre cose, nell'ordine giusto:

1. **il backtest** — il modello viene addestrato *senza vedere* gli ultimi
   giorni noti e confrontato con la realtà: MAE, RMSE e MAPE dicono quanto ha
   sbagliato, ed è quella la misura da guardare prima di fidarsi;
2. **la previsione del futuro vero** — giorni strettamente successivi
   all'ultimo osservato. Sembra ovvio; una proposta esterna respinta
   presentava come "previsione" la riproiezione degli ultimi giorni già noti;
3. **una banda dichiarata** — quantili 10°–90° dei residui del modello,
   allargati con la radice dell'orizzonte. Non è un intervallo di confidenza
   parametrico e non viene spacciato per tale.

## Il metodo, dichiarato

Holt-Winters additivo con stagionalità settimanale (il gas civile respira sui
giorni della settimana), implementato in **puro Python**: nessuna dipendenza
da pandas, statsmodels o pmdarima. Il portale viaggia anche come eseguibile e
cento megabyte di librerie scientifiche non valgono un'etichetta più
altisonante sullo stesso lavoro. I tre coefficienti di lisciamento (α, β, γ)
si scelgono su una piccola griglia deterministica minimizzando l'errore
quadratico a un passo: **stesso input, stessa previsione, sempre** — e un
test lo presidia.

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

## Cosa NON fa

Non conserva nulla (lo storico resta dell'operatore), non pretende di essere
un oracolo, non nasconde il metodo dietro un nome di libreria. Se un giorno
serviranno modelli più ricchi (ARIMA, regressori climatici), la strada è la
stessa: metodo dichiarato, backtest incorporato, determinismo verificato.
