# ADR-0001: Importazione manuale e immutabile delle ricevute PDR/ACER

**Stato:** Accettata

**Data:** 2026-08-01
**Decisori:** It-Energy-Ai

## Contesto

La PDR consente di monitorare lo stato del reporting e mette a disposizione la
ricevuta di ritorno ACER dopo il processo di invio. Il progetto può già
generare XML ACER e conservarne hash e audit, ma non dispone di contratto,
credenziali, PIN né dell'Implementation Guide configurata per l'operatore.

L'Implementation Guide PDR v2.0 documenta una ricevuta GME
`FA_NomeFileInviato.xml` e un archivio ACER `NomeFileInviato.zip`. La ricevuta
GME usa `PIPEFunctionalAcknowledgement` con stato `Accept`, `Reject` o
`Partial`; il contenuto della ricevuta ACER deve invece essere conservato senza
inferirne arbitrariamente l'esito.

Occorre quindi collegare una ricevuta al preciso XML inviato, conservarne il
contenuto originale e non trasformare una dichiarazione dell'utente in una
conferma verificata GME/ACER.

## Decisione

Implementiamo un importatore manuale di file di ricevuta, con:

- byte originali, media type, nome e SHA-256 immutabili;
- associazione obbligatoria a report e artefatto XML ACER del medesimo utente;
- Load Code PDR opzionale, timestamp, fonte ed esito dichiarato;
- idempotenza sulla stessa impronta del file per lo stesso report;
- evento di audit append-only;
- chiara etichetta `importata_non_verificata`: l'esito riportato dalla
  ricevuta non è attestato da un connettore live.

Quando il file importato è una ricevuta GME XML riconoscibile, il sistema può
estrarre in modo deterministico lo stato tecnico e i motivi di rigetto; la
provenienza resta comunque `importata_non_verificata`.

## Opzioni considerate

### A. Import manuale con evidenza immutabile — scelta

| Dimensione | Valutazione |
|---|---|
| Complessità | Bassa |
| Attendibilità | Media: il file è preservato, la provenienza non è verificata live |
| Compatibilità | Alta |
| Dipendenze esterne | Nessuna |

Consente il tracciamento immediato senza riprodurre protocolli proprietari e
crea una base stabile per un futuro connettore.

### B. Automazione/scraping del portale PDR — rifiutata

È fragile, non usa un'interfaccia contrattuale e richiederebbe la gestione di
credenziali/PIN in un flusso non autorizzato.

### C. Polling diretto del web service PDR — rinviata

È l'obiettivo successivo, ma richiede contratto, credenziali di test e
Implementation Guide/endpoint ufficiali abilitati per l'operatore.

## Conseguenze

- Gli utenti possono importare e consultare subito ricevute PDR/ACER.
- Il sistema distingue gli esiti dichiarati nella ricevuta dalla verifica
  tecnica di un connettore GME.
- Il futuro client web service potrà riutilizzare lo stesso modello dati,
  impostando una provenienza verificata e registrando il payload ricevuto.

## Azioni successive

1. Ottenere accesso PDR di test e Implementation Guide GME.
2. Aggiungere il client ufficiale con secret store, polling e riconciliazione.
3. Verificare la firma/formato della ricevuta se e quando GME ne documenterà
   la semantica per l'operatore.
