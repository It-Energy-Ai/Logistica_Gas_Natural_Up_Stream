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
   e non vengono mai salvate**. Gli indirizzi che puntano a reti private,
   loopback o link-local sono rifiutati (anti-SSRF) e i redirect sono
   ammessi solo verso `http(s)` già verificati.
2. **Distingue i flussi reali** — ogni file è un archivio ZIP che contiene
   un solo XML; il token nel nome dice il flusso: `TGL` letture
   **giornaliere**, `TMV` e `SWG1` letture **mensili**, `IGMG` interventi di
   **cambio contatore/correttore**. L'elenco mostra cartelle e file con
   badge distinti e conteggi separati.
3. **Apre il file scelto** — una `GET` scarica l'archivio in memoria (mai su
   disco), il modulo apre lo ZIP e riassume l'XML in forma generica: radice,
   tag dei record, campi e prime righe, così l'operatore vede subito il
   contenuto senza dover conoscere lo schema del distributore.
4. **Costruisce la serie giornaliera dei consumi** — con l'azione «serie» il
   modulo scarica i file di misura del percorso (o delle ultime sottocartelle
   giorno, fino a 60), interpreta i tracciati `FlussoMisure`/`FlussoIGMG`,
   calcola per ogni PDR la differenza fra letture cumulative consecutive e
   somma i contributi per giorno. Il flusso `IGMG` comunica il cambio del
   misuratore: la lettura `Post-int` diventa la nuova base della serie, così
   il contatore che riparte da zero non produce consumi negativi. Le
   differenze negative (ricalcoli del distributore) sono ignorate, non
   inventate.

## L'alberatura pubblicata dal distributore

```
TMG_[PIVA_DISTR]_[PIVA_UDD]/[ANNO]/[MESEGIORNO]
```

`MESEGIORNO` è il giorno di pubblicazione nel formato `MMGG` (per esempio
`2018/1217` è il 17 dicembre 2018). Il percorso si naviga cartella per
cartella: cliccare una cartella aggiorna il percorso, «Elenca» rilegge.
Dentro ogni giorno si trovano l'indice `ElencoFileGiornalieri.txt` e gli
archivi dei flussi, con nomi del tipo
`[PIVA_DISTR]_[PIVA_UDD]_[AAAAMM]_[FLUSSO]_[marca]_[progressivo].zip`.

## Il ponte con la previsione della domanda

La serie costruita (data, consumo del giorno) è esattamente il formato che
si aspetta il modulo «Previsione della domanda»: il bottone «Usa la serie
nella previsione» compila il CSV della previsione e apre la schermata.
Servono almeno 28 giorni di storia perché l'ensemble possa addestrarsi: con
le letture mensili (TMV/SWG1) la copertura cresce in fretta, con le sole
giornaliere dipende da quante pubblicazioni sono disponibili.

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
mancanti si chiedono via helpdesk), non modifica i file, non interpreta
campi oltre letture e cambi (anagrafiche, coefficienti e classi di misura
sono mostrati ma non usati nei calcoli), non nasconde errori di rete o di
credenziali: 401, 403, 404 e 502 hanno messaggi dedicati in italiano.
