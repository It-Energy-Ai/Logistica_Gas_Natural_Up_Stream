# Misure dei PDR

Modulo `app/misure.py`, schermata **Misure dei PDR**, rotta `POST /api/misure`.

## Perché nel portale

Le misure dei punti di riconsegna sono pubblicate dal distributore su
SIICloud, il cloud basato su Nextcloud gestito da Acquirente Unico (il
Sistema Informativo Integrato). L'UDD — noi, come shipper — le scarica per
riconciliare i consumi e alimentare la previsione della domanda. Il portale
parla il protocollo WebDAV standard di Nextcloud: nessun formato proprietario,
nessuna dipendenza nuova.

## Cosa fa

1. **Accede alla cartella indicata** — l'operatore incolla l'indirizzo WebDAV
   (quello copiato dalle impostazioni di SIICloud), utente e password. Il
   modulo elenca file e sottocartelle con una `PROPFIND` (profondità 1) e
   autentica con HTTP Basic. Le credenziali **viaggiano solo nella richiesta
   e non vengono mai salvate**.
2. **Distingue le classi di lettura** — tutti i file sono XML; il prefisso
   del nome dice la classe: `TGL` letture **giornaliere**, `TMG` o `TML`
   letture **mensili**. L'elenco mostra cartelle, file giornalieri e mensili
   con badge distinti e conteggi separati.
3. **Apre il file scelto** — una `GET` scarica l'XML in memoria (mai su
   disco) e il modulo lo riassume in forma generica: radice, tag dei record,
   campi e prime righe, così l'operatore vede subito il contenuto senza
   dover conoscere lo schema del distributore.

## L'alberatura pubblicata dal distributore

```
TMG_[PIVA_DISTR]/DISTRIBUTORE/TMG_[PIVA_DISTR]_[PIVA_UDD]/[ANNO]/[MESEGIORNO]
```

`MESEGIORNO` è il giorno di pubblicazione nel formato `MMGG` (per esempio
`2018/1217` è il 17 dicembre 2018). Il percorso si naviga cartella per
cartella: cliccare una cartella aggiorna il percorso, «Elenca» rilegge.

## Due onestà dovute

- Le credenziali sono **usa e getta per la richiesta**: il portale non le
  memorizza, non le scrive nel database e non le ritrasmette. Se la sessione
  scade o la pagina si ricarica, vanno reinserite — è una scelta, non un
  limite.
- I file sono aperti **solo in memoria** e mostrati all'operatore che li ha
  richiesti: il portale non li conserva e non li ritrasmette, la stessa
  posizione di chi li scarica con un client WebDAV.

## Cosa NON fa

Non crea cartelle (SIICloud non lo consente lato client: le cartelle UDD
mancanti si chiedono via helpdesk), non modifica i file, non interpreta lo
schema XML del distributore (lo riassume soltanto), non nasconde errori di
rete o di credenziali: 401, 403, 404 e 502 hanno messaggi dedicati in
italiano.
