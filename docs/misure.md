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
5. **Salva l'accesso e sincronizza ogni giorno** — su scelta dell'operatore
   le credenziali restano nel database locale e il portale scarica da solo,
   una volta al giorno, i file nuovi nell'archivio locale; la serie si può
   poi ricalcolare dall'archivio anche senza rete. I dettagli nella sezione
   dedicata più sotto.

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

## L'accesso salvato e la sincronizzazione giornaliera

Di base le credenziali sono usa e getta. Se però l'operatore preme «Salva
l'accesso su questo computer», il portale le custodisce **solo nel database
locale di questa macchina** (tabella `sii_accesso`, mai trasmesse altrove) e
da quel momento:

- **ogni giorno scarica da solo** — un filo in background controlla una volta
  all'ora gli accessi salvati e sincronizza quelli la cui ultima
  sincronizzazione non è oggi;
- **l'archivio cresce senza duplicati** — i file scaricati finiscono nella
  cartella `misure/` accanto al database, con lo stesso percorso di
  SIICloud; un file già presente non viene riscaricato;
- **«Sincronizza ora»** anticipa il giro quando serve, e lo stato della
  sincronizzazione (ultima volta, file in archivio, eventuale errore) resta
  visibile nella schermata;
- **«Costruisci la serie dall'archivio locale»** ricalcola la serie dei
  consumi dai file già scaricati, **senza rete**: utile per ripetere la
  previsione o lavorare offline.

La password non torna mai al frontend: lo stato dice solo se è custodita.
Per togliere l'accesso basta salvarne uno nuovo con credenziali diverse o
disattivarlo; l'eliminazione rimuove la riga dal database locale.

## Il ponte con la previsione della domanda

La serie costruita (data, consumo del giorno) è esattamente il formato che
si aspetta il modulo «Previsione della domanda»: il bottone «Usa la serie
nella previsione» compila il CSV della previsione e apre la schermata.
Servono almeno 28 giorni di storia perché l'ensemble possa addestrarsi: con
le letture mensili (TMV/SWG1) la copertura cresce in fretta, con le sole
giornaliere dipende da quante pubblicazioni sono disponibili.

## Due onestà dovute

- Le credenziali sono **usa e getta per la richiesta**, a meno che
  l'operatore non scelga esplicitamente «Salva l'accesso»: in quel caso
  restano **solo nel database locale di questa macchina** (in chiaro, come
  ogni altro dato del portale: l'app gira in locale e non ha un server),
  servono unicamente alla sincronizzazione giornaliera e non escono mai dal
  computer. La password non viene mai rimandata al frontend.
- I file sono aperti **solo in memoria** quando l'operatore li consulta, e
  conservati nell'archivio locale solo dalla sincronizzazione: il portale
  non li ritrasmette, la stessa posizione di chi li scarica con un client
  WebDAV.

## Cosa NON fa

Non crea cartelle (SIICloud non lo consente lato client: le cartelle UDD
mancanti si chiedono via helpdesk), non modifica i file, non interpreta
campi oltre letture e cambi (anagrafiche, coefficienti e classi di misura
sono mostrati ma non usati nei calcoli), non nasconde errori di rete o di
credenziali: 401, 403, 404 e 502 hanno messaggi dedicati in italiano.
