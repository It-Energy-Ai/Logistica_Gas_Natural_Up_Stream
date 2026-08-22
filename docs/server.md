# Vettore sul server aziendale

Vettore nasce come applicazione locale: un file solo, doppio click, dati sul PC
dell'operatore. Questa guida descrive l'altra modalità dichiarata — **il server
aziendale** — pensata per le società che vogliono un'unica istanza sempre
accesa, raggiungibile dal browser degli operatori, capace di scaricare da sola
le misure da SIICloud ogni giorno.

## Che cosa cambia rispetto alla modalità locale

| | Locale (default) | Server (`VETTORE_ENV=production`) |
|---|---|---|
| Dove gira | PC dell'operatore, su `127.0.0.1` | Server aziendale, esposto sulla rete interna |
| Login | basta l'email (identità, non protezione) | email **+ password condivisa dell'azienda** |
| Avvio senza password | sempre ammesso | **interrotto** con messaggio chiaro |
| Cookie di sessione | `HttpOnly`, `SameSite=Lax` | anche `Secure` (viaggia solo su HTTPS) |
| Browser all'avvio | si apre da solo | no (il server non ha desktop) |

La filosofia non cambia: **una istanza per azienda**, sul server dell'azienda.
Le credenziali SIICloud salvate dagli operatori restano nel database di quella
istanza e non lasciano mai l'infrastruttura dell'azienda.

## Requisiti

- Docker e Docker Compose già installati sul server (l'immagine parte da
  `python:3.13-slim`, non serve Python sul sistema).
- Un nome DNS interno o pubblico che punti al server (per HTTPS con Caddy).

## Installazione rapida

```bash
git clone https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream.git vettore
cd vettore
export VETTORE_PASSWORD="la-password-condivisa"   # almeno 8 caratteri
docker compose -f docker-compose.server.yml up -d --build
```

Il portale risponde su `http://127.0.0.1:8080` **solo dal server stesso**: la
porta è pubblicata sul loopback di proposito. A esporlo sulla rete ci pensa il
reverse proxy aziendale (vedi sotto).

### Variabili d'ambiente

| Variabile | Obbligatoria | Significato |
|---|---|---|
| `VETTORE_ENV` | sì (`production`) | attiva la modalità server |
| `VETTORE_PASSWORD` | sì | password condivisa per il login; senza questa l'avvio si ferma |
| `VETTORE_GIORNI_SESSIONE` | no (default 30) | durata della sessione, da 1 a 365 giorni |
| `VETTORE_INDIRIZZO` | no (default `127.0.0.1`) | indirizzo di ascolto, se l'avvio è diretto senza Docker |
| `VETTORE_DB` | no | percorso del database (in Docker è già `/data/vettore.db`) |

## HTTPS con Caddy (profilo opzionale)

Il compose include un servizio Caddy attivabile con il profilo `tls`: TLS
automatico con Let's Encrypt, nessun certificato da gestire a mano.

```bash
export VETTORE_DOMINIO="vettore.azienda.it"
docker compose -f docker-compose.server.yml --profile tls up -d --build
```

Il file `Caddyfile.example` contiene solo il reverse proxy verso
`vettore:8080`. Se l'azienda ha già un reverse proxy (nginx, Apache, Traefik,
un appliance), basta puntarlo sulla porta locale `8080` e saltare Caddy.

**Il flag `Secure` del cookie è attivo in modalità server**: senza HTTPS il
browser non invierebbe il cookie. L'HTTPS non è un optional in questa modalità.

## Il download giornaliero delle misure

L'istanza sempre accesa **è** il job di aggiornamento: il filo in background
controlla ogni ora gli accessi SIICloud attivi e sincronizza quelli la cui
ultima sincronizzazione non è oggi. Non serve cron, non serve schedulatore
esterno.

Ogni operatore salva il proprio accesso una volta sola dalla schermata
«Misure dei PDR» (indirizzo WebDAV, utente, password); i file finiscono
nell'archivio accanto al database e la serie dei consumi è ricalcolabile anche
senza rete.

## Backup

Tutto lo stato vive nel volume `vettore-data` (database + archivio delle
misure). Per il backup:

```bash
docker compose -f docker-compose.server.yml stop vettore
docker run --rm -v vettore_vettore-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/vettore-backup-$(date +%F).tar.gz -C /data .
docker compose -f docker-compose.server.yml start vettore
```

## Onestà dovute

- La password è **condivisa**: distingue gli operatori dell'azienda dal resto
  del mondo, non un operatore dall'altro. L'identità individuale resta
  l'email, come in modalità locale.
- La password del server è verificata con derivazione **scrypt** e confronto a
  tempo costante; non è mai scritta nel database né rimandata al frontend.
- Le credenziali SIICloud degli operatori sono custodite in chiaro nel
  database dell'istanza, come in modalità locale: il perimetro di protezione è
  il server aziendale stesso (container senza privilegi, file system in sola
  lettura, porta esposta solo dal proxy).
