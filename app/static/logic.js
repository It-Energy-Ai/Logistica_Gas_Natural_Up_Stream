/* Logica applicativa di Vettore — porting della classe Component del design
 * di partenza ("Vettore Portale Gas", script data-dc-script).
 *
 * Il corpo di renderVals() è portato quasi alla lettera; le deviazioni sono:
 *   1. doLogin/ssoPick/logout parlano con l'API (/api/login, /api/logout) e i
 *      campi del login sono controllati (loginEmail/loginPass + Invio);
 *   2. il tema è persistito in localStorage;
 *   3. lo stato mutabile (nomine, configurazione, utenti, ...) viene
 *      sincronizzato col backend via PUT /api/state (auto-diff in setState,
 *      con retry su errore di rete e ritorno al login su sessione scaduta);
 *   4. i setter di input di testo e select usano setSilent: aggiornano lo
 *      stato senza ri-renderizzare (il runtime ri-renderizza tutto, e un
 *      re-render per ogni tasto farebbe perdere il focus);
 *   5. i valori inseriti dall'utente sono troncati ai limiti dei validatori
 *      del backend (cap), per non far respingere la patch di sync;
 *   6. l'effetto hover del "mazzo" nel hub è CSS puro (:hover generato da
 *      build_frontend.py) invece di deckHover+setState;
 *   7. avvio pulito: l'identità deriva dall'email di login, le date sono il
 *      giorno gas reale e la scenografia del canvas (KPI, grafici, cicli,
 *      contratti, report, blocchi statici) vive dietro la "modalità demo",
 *      attivabile dal Configuratore → Sistema e persistita.
 * Le props del canvas (tema, colori) sono fissate ai default del design.
 */
(function () {
  "use strict";

  const PROPS = { tema: "chiaro", colorePrimario: "#0E5A75", coloreAccento: "#4C93C9" };

  // Elenca i campi respinti dal server: senza, resta solo un messaggio generico.
  const _dettagli = (error) =>
    error && Array.isArray(error.dettagli) && error.dettagli.length
      ? ` — ${error.dettagli.map((d) => d.message || d.field).join(" · ")}`
      : "";

  const store = typeof localStorage !== "undefined" ? localStorage : { getItem: () => null, setItem: () => {} };

  // Chiavi di stato persistite sul backend (il resto è scenografia demo).
  const PERSIST = [
    "nomList", "cfg", "hiddenPunti", "extraPunti", "nextP",
    "users", "extraUsers", "disabled", "nextU",
    "reps", "demoMode",
  ];

  // Tronca ai limiti accettati dai validatori del backend: un valore fuori
  // misura farebbe respingere l'intera patch (422) e perdere la persistenza.
  const cap = (s, n) => String(s ?? "").slice(0, n);

  // Le ricevute PDR sono importate come file binari. L'API locale riceve il
  // contenuto Base64, così il browser non invia mai il file a un servizio
  // esterno. Il percorso arrayBuffer è disponibile nei browser moderni e
  // permette anche test frontend senza simulare FileReader.
  const fileToBase64 = async (file) => {
    if (!file) throw new Error("seleziona prima il file della ricevuta");
    let bytes;
    if (typeof file.arrayBuffer === "function") {
      bytes = new Uint8Array(await file.arrayBuffer());
    } else if (typeof FileReader !== "undefined") {
      bytes = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("impossibile leggere il file della ricevuta"));
        reader.onload = () => resolve(new Uint8Array(reader.result));
        reader.readAsArrayBuffer(file);
      });
    } else {
      throw new Error("lettura file non disponibile in questo ambiente");
    }
    let binary = "";
    // Evita di superare il limite di argomenti di fromCharCode per ricevute
    // ZIP/XML di dimensioni significative.
    for (let start = 0; start < bytes.length; start += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
    }
    if (typeof btoa === "function") return btoa(binary);
    if (typeof Buffer !== "undefined") return Buffer.from(bytes).toString("base64");
    throw new Error("codifica Base64 non disponibile in questo ambiente");
  };

  // ``datetime-local`` non include un fuso orario. Lo interpreta come orario
  // locale dell'operatore e lo serializza in ISO 8601 UTC prima del salvataggio
  // immutabile, evitando timestamp ambigui nel registro di audit.
  const localDateTimeToIso = (value) => {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) throw new Error("data-ora della ricevuta non valida");
    return date.toISOString();
  };

  class App {
    constructor() {
      this.state = this._nuovoStato();
      this._pending = {};
      this._syncTimer = null;
      this._flushing = false;
      this._sessionEmail = null;
      this._sessionEpoch = 0;
      // Azioni in volo: un doppio clic non deve creare due bozze, due nomine
      // o due ricevute, che il backend non saprebbe distinguere.
      this._inCorso = new Set();
      this._loginInCorso = false;
    }

    _nuovoStato(theme = store.getItem("vt-theme")) {
      return {
        screen: "login", theme, sso: null, dashOff: 0,
        loginEmail: "", loginPass: "", loginErrore: "", utenteEmail: "", demoMode: false,
        saved: false,
        users: {}, extraUsers: [], nextU: 1,
        wiz: null, disabled: {}, hiddenPunti: [], extraPunti: [], nextP: 1, newPunto: "", repCat: "tutti",
        nomList: [],
        nomPunto: "PSV", nomCiclo: "R4", nomQta: "",
        edgTipo: "01G", edgTipoNomina: "A02", edgIdent: "", edgVersione: "1",
        edgEmittente: "", edgDestinatario: "", edgConto: "", edgPunto: "", edgUnita: "KW1",
        edgGiorno: "", edgControparte: "", edgDirezione: "Z02", edgQta: "",
        edgNomine: [], edgErrore: "", edgElencoErrore: "", edgInfo: "", edgErroriCampo: [],
        edgRisposta: "", edgRispostaErrore: "", edgRispostaInfo: "", edgScostamentiList: [],
        edgAck: "", edgAckErrore: "", edgAckInfo: "", edgRiscontriList: [],
        remReports: [], remCaricamento: false, remErrore: "", remInfo: "", remAudit: null,
        remTipo: "gas_standard", remAzione: "new", remRif: "", remData: "", remPunto: "", remControparte: "", remControparteTipo: "ace", remLato: "buy", remQta: "", remUnita: "MWh", remPrezzo: "", remValuta: "EUR", remCapacita: "P",
        remContractId: "", remContractDate: "", remContractType: "SP", remCommodity: "NG", remDeliveryStart: "", remDeliveryEnd: "", remSettlement: "P", remMarketplaceTipo: "mic", remMarketplaceId: "", remTransactionAt: "", remTransactionId: "",
        pdr: { environment: "test", channel: "portal", gme_operator_code: "", pdr_contract_reference: "", test_access_requested: false, two_factor_ready: false, registered_acer_code: "" },
        pdrCaricamento: false, pdrErrore: "", pdrInfo: "", pdrCheck: null, pdrFees: null, pdrSchemas: [],
        pdrReceiptReportId: "", pdrReceipts: [], pdrReceiptCaricamento: false, pdrReceiptImporting: false, pdrReceiptErrore: "", pdrReceiptInfo: "", pdrReceiptOpen: null,
        pdrReceiptFile: null, pdrReceiptFileName: "", pdrReceiptOutcome: "unknown", pdrReceiptSource: "pdr", pdrReceiptLoadCode: "", pdrReceiptReportedAt: "", pdrReceiptDetailText: "",
        reps: { rg: true, rs: true, rr: false },
        cfg: {
          psv: true, gries: true, mazara: true, remi: true, stogit: true, cavarzere: false,
          nEmail: true, nAlert: true, nReport: false, unit: "MWh", ciclo: "Intraday",
        },
      };
    }

    setState(patch, opts) {
      const prev = this.state;
      const delta = typeof patch === "function" ? patch(prev) : patch;
      if (!delta || Object.keys(delta).length === 0) return;
      this.state = { ...prev, ...delta };
      for (const k of PERSIST) {
        if (k in delta && delta[k] !== prev[k]) this._pending[k] = this.state[k];
      }
      this._scheduleSync();
      if (!(opts && opts.silent) && this._vt) this._vt.render();
    }

    setSilent(patch) { this.setState(patch, { silent: true }); }

    _scheduleSync(ritardo = 250) {
      if (this._sospesa) return; // sessione scaduta: si riparte dopo il login
      if (Object.keys(this._pending).length === 0) return;
      clearTimeout(this._syncTimer);
      this._syncTimer = setTimeout(() => this._flush(), ritardo);
    }

    // Invia la patch e NON la scarta finché il server non ha risposto 2xx:
    // su errore di rete o 5xx la rimette in coda e ritenta; su 401 riporta
    // al login (sessione scaduta) conservando la coda per il dopo-login;
    // su 422 la scarta con errore in console (dato non salvabile).
    async _flush() {
      // Una sola richiesta in volo conserva l'ordine delle modifiche: due PUT
      // concorrenti potrebbero arrivare al server in ordine inverso e
      // ripristinare un valore ormai superato.
      if (this._flushing || this._sospesa || Object.keys(this._pending).length === 0) return;
      const patch = this._pending;
      const epoca = this._sessionEpoch;
      let ritardoSuccessivo = 250;
      this._pending = {};
      this._flushing = true;
      try {
        const r = await fetch("/api/state", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
        if (r.ok) return;
        // La risposta appartiene a una sessione precedente (logout o nuovo
        // login): non deve modificare la nuova schermata né riaccodare dati
        // dell'utente precedente.
        if (epoca !== this._sessionEpoch) return;
        if (r.status === 401) {
          // sospende la sync (niente loop di retry contro un 401 permanente):
          // login() la riattiva e svuota la coda conservata
          this._sospesa = true;
          this._riaccoda(patch);
          console.warn("sessione scaduta: torno al login, modifiche in coda");
          this.setState({ screen: "login", loginErrore: "La sessione è scaduta. Accedi di nuovo per salvare le modifiche in coda." });
          return;
        }
        if (r.status === 400 || r.status === 422) {
          console.error("sync respinta dal server (dati non validi), patch scartata:", await r.text());
          return;
        }
        this._riaccoda(patch);
        ritardoSuccessivo = 3000;
      } catch (e) {
        if (epoca === this._sessionEpoch) {
          this._riaccoda(patch);
          console.warn("sync fallita (rete), ritento tra 3s", e);
          ritardoSuccessivo = 3000;
        }
      } finally {
        this._flushing = false;
        if (epoca === this._sessionEpoch) this._scheduleSync(ritardoSuccessivo);
      }
    }

    // Rimette in coda le chiavi di un flush fallito SENZA sovrascrivere valori
    // più recenti: se una scrittura successiva ha già ri-accodato la chiave,
    // vince quella; altrimenti si riparte dallo stato corrente (non dallo
    // snapshot inviato, che potrebbe essere già superato).
    _riaccoda(patch) {
      for (const k of Object.keys(patch)) {
        if (!(k in this._pending)) this._pending[k] = this.state[k];
      }
    }

    // Ritorna l'email normalizzata dal server. Non considera completato
    // l'accesso finché non riceve una risposta 2xx con un'identità valida.
    async login(email) {
      try {
        const r = await fetch("/api/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        if (!r.ok) throw new Error(`login HTTP ${r.status}`);
        const dati = await r.json();
        if (!dati || typeof dati.email !== "string" || !dati.email) throw new Error("risposta login non valida");
        return dati.email;
      } catch (e) {
        console.warn("login API non raggiungibile", e);
        return null;
      }
    }

    go(s) { return () => this.setState({ screen: s }); }

    // Idrata lo stato dal payload di /api/state: separa l'identità (email)
    // dallo stato persistito e, se c'è una sessione, salta il login.
    idrata(saved, { mantieniCoda = false } = {}) {
      if (!saved || typeof saved !== "object" || typeof saved.email !== "string") return false;
      const { email, ...stato } = saved;
      const pulito = this._nuovoStato(this.state.theme);
      for (const chiave of PERSIST) {
        if (mantieniCoda && chiave in this._pending) {
          pulito[chiave] = this.state[chiave];
        } else if (chiave in stato) {
          pulito[chiave] = stato[chiave];
        }
      }
      this.state = { ...pulito, utenteEmail: email, screen: "hub" };
      this._sessionEmail = email;
      return true;
    }

    async _leggiStato() {
      try {
        const r = await fetch("/api/state");
        if (!r.ok) throw new Error(`stato HTTP ${r.status}`);
        const saved = await r.json();
        if (!saved || typeof saved.email !== "string") throw new Error("risposta stato non valida");
        return saved;
      } catch (e) {
        console.warn("stato API non raggiungibile", e);
        return null;
      }
    }

    async _json(url, options = {}) {
      const response = await fetch(url, options);
      let body = {};
      try { body = await response.json(); } catch (_) { /* risposta senza JSON */ }
      if (!response.ok) {
        const errore = new Error(body && body.errore ? body.errore : `richiesta HTTP ${response.status}`);
        // conserva il dettaglio per campo: senza, l'operatore vedrebbe solo un
        // messaggio generico e non saprebbe cosa correggere
        errore.dettagli = Array.isArray(body && body.errors) ? body.errors : [];
        throw errore;
      }
      return body;
    }

    async _caricaRemit() {
      if (!this._sessionEmail) return;
      // Se l'utente esce e ne entra un altro mentre la richiesta è in volo, la
      // risposta della sessione precedente non deve atterrare nello stato del
      // nuovo utente: i suoi dati comparirebbero a schermo.
      const epoca = this._sessionEpoch;
      const miaSessione = () => epoca === this._sessionEpoch;
      this.setState({ remCaricamento: true, remErrore: "" });
      try {
        const payload = await this._json("/api/remit/reports");
        if (!miaSessione()) return;
        this.setState({
          remReports: Array.isArray(payload.reports) ? payload.reports : [],
          remCaricamento: false,
          remInfo: payload.legacy_imported ? `${payload.legacy_imported} record storico importato come bozza da verificare.` : this.state.remInfo,
        });
      } catch (error) {
        if (!miaSessione()) return;
        this.setState({ remCaricamento: false, remErrore: `Registro REMIT non disponibile: ${error.message}` });
      }
    }

    async _caricaEdigas() {
      if (!this._sessionEmail) return;
      const epoca = this._sessionEpoch;
      const miaSessione = () => epoca === this._sessionEpoch;
      try {
        const [payload, riscontri] = await Promise.all([
          this._json("/api/edigas/nomine"),
          this._json("/api/edigas/riscontri"),
        ]);
        if (!miaSessione()) return;
        this.setState({
          edgNomine: Array.isArray(payload.nomine) ? payload.nomine : [],
          edgRiscontriList: Array.isArray(riscontri.riscontri) ? riscontri.riscontri : [],
          edgElencoErrore: "",
        });
      } catch (error) {
        if (!miaSessione()) return;
        this.setState({ edgElencoErrore: `Elenco nomine EDIG@S non disponibile: ${error.message}` });
      }
    }

    async _caricaPdr() {
      if (!this._sessionEmail) return;
      const epoca = this._sessionEpoch;
      const miaSessione = () => epoca === this._sessionEpoch;
      this.setState({ pdrCaricamento: true, pdrErrore: "" });
      try {
        const payload = await this._json("/api/pdr");
        if (!miaSessione()) return;
        this.setState({
          pdr: payload.profile || this.state.pdr,
          pdrFees: payload.fees || null,
          pdrSchemas: Array.isArray(payload.schemas) ? payload.schemas : [],
          pdrCaricamento: false,
        });
      } catch (error) {
        if (!miaSessione()) return;
        this.setState({ pdrCaricamento: false, pdrErrore: `Configurazione PDR non disponibile: ${error.message}` });
      }
    }

    async _caricaWorkspaceRegolatorio() {
      await Promise.all([this._caricaRemit(), this._caricaPdr(), this._caricaEdigas()]);
    }

    async _apriSessione(email) {
      if (this._loginInCorso) return false;
      this._loginInCorso = true;
      this.setState({ loginErrore: "" });
      try {
        const confermata = await this.login(email);
        if (!confermata) {
          this.setState({ loginErrore: "Accesso non riuscito. Verifica la connessione e riprova." });
          return false;
        }
        const riprendiCoda = this._sospesa && this._sessionEmail === confermata;
        const saved = await this._leggiStato();
        if (!saved) {
          this.setState({ loginErrore: "Sessione creata, ma non riesco a leggere i dati. Riprova." });
          return false;
        }
        if (!riprendiCoda) this._pending = {};
        this._sospesa = false;
        this._sessionEpoch += 1;
        this.idrata(saved, { mantieniCoda: riprendiCoda });
        this._scheduleSync();
        if (this._vt) this._vt.render();
        await this._caricaWorkspaceRegolatorio();
        return true;
      } finally {
        this._loginInCorso = false;
      }
    }

    _reimpostaDopoLogout() {
      // Senza questo, uscire mentre il workspace è ancora in caricamento
      // lasciava il lucchetto chiuso e il pulsante "Accedi" non rispondeva più.
      this._loginInCorso = false;
      if (this._timerSso) { clearTimeout(this._timerSso); this._timerSso = null; }
      clearTimeout(this._syncTimer);
      this._pending = {};
      this._sospesa = false;
      this._sessionEmail = null;
      this._sessionEpoch += 1;
      this.state = this._nuovoStato(this.state.theme);
      if (this._vt) this._vt.render();
    }

    // Identità mostrata nell'interfaccia, derivata dall'email di login.
    _identita() {
      const email = (this.state.utenteEmail || "").trim().toLowerCase();
      const [locale, dominio] = email.includes("@") ? email.split("@") : ["", ""];
      const parti = locale.split(/[._-]+/).filter(Boolean);
      const maiuscola = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
      const nome = parti.length ? parti.map(maiuscola).join(" ") : "Utente";
      const iniziali = (parti.length ? parti.map((p) => p[0]).join("") : "U").slice(0, 2).toUpperCase();
      const estesa = parti.find((p) => p.length > 2);
      return {
        nome,
        iniziali,
        nomeSaluto: estesa ? maiuscola(estesa) : nome,
        azienda: dominio ? maiuscola(dominio.split(".")[0]) : "La tua azienda",
        dominio: dominio || "azienda.it",
      };
    }

    renderVals() {
      const p = PROPS;
      const theme = this.state.theme ?? ((p.tema ?? "chiaro") === "scuro" ? "dark" : "light");
      const s = this.state.screen;
      const go = (x) => this.go(x);
      const demoOn = !!this.state.demoMode;
      const ident = this._identita();
      // Non c'è dato in attesa: badge neutro per le card senza numeri.
      const ATTESA = { delta: "in attesa di dati", dBg: "var(--surface2)", dFg: "var(--ink3)" };
      const trail = ({
        hub: [{ label: "Moduli" }],
        moduli: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas" }],
        dash: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Dashboard" }],
        config: [{ label: "Moduli", t: "hub" }, { label: "Configuratore" }],
        cfgSis: [{ label: "Moduli", t: "hub" }, { label: "Configuratore", t: "config" }, { label: "Sistema" }],
        cfgImp: [{ label: "Moduli", t: "hub" }, { label: "Configuratore", t: "config" }, { label: "Impostazioni" }],
        nomine: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Nomine · EDIG@S" }],
        bilancio: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Bilanciamento" }],
        capacita: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Capacità & Contratti" }],
        stoccaggio: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Stoccaggio" }],
        report: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Report & Analisi" }],
        remit: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "REMIT · XML ACER" }],
        pdr: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "PDR · GME" }],
      })[s] || [];
      const crumbs = trail.map((c, i) => ({
        label: c.label, pre: i ? "›" : "",
        go: c.t ? go(c.t) : () => {}, cur: c.t ? "pointer" : "default",
        col: i === trail.length - 1 ? "var(--ink)" : "var(--ink2)",
        fw: i === trail.length - 1 ? 600 : 400,
      }));
      const OK = { bg: "rgba(47,163,124,.15)", fg: "#259372" };
      const RUN = { bg: "rgba(62,150,180,.16)", fg: "#3E96B4" };
      // Nomi leggibili degli eventi del registro REMIT (app/db.py li scrive in
  // inglese come tipo tecnico; qui vengono mostrati all'operatore italiano).
  const REM_EVENTI = {
    CREATED: "Creata", UPDATED: "Modificata", VALIDATED: "Validata",
    EXPORTED: "XML generato", IMPORTED: "Importata", SUBMITTED: "Trasmessa",
    RECEIPT_IMPORTED: "Ricevuta importata", PREFLIGHT: "Preflight PDR",
  };

  const WAIT = { bg: "var(--surface2)", fg: "var(--ink2)" };
      const WARN = { bg: "rgba(196,140,42,.16)", fg: "#B0842E" };
      const NEG = { bg: "rgba(197,96,80,.15)", fg: "#C05B4D" };
      const cfg = this.state.cfg;
      const tg = (k) => () => this.setState((st) => ({ cfg: { ...st.cfg, [k]: !st.cfg[k] }, saved: false }));
      const setC = (k, v) => () => this.setState((st) => ({ cfg: { ...st.cfg, [k]: v }, saved: false }));
      const knob = (on) => ({ on, kBg: on ? "var(--acc)" : "var(--surface2)", kX: on ? "translateX(16px)" : "translateX(0)" });
      const basePunti = [["PSV", "Scambio virtuale", "psv"], ["Passo Gries", "Import · Svizzera", "gries"], ["Mazara del Vallo", "Import · Algeria", "mazara"], ["ReMi 34521301 · Milano Ovest", "Riconsegna", "remi"], ["Stogit", "Stoccaggio", "stogit"], ["Cavarzere · GNL", "Rigassificazione", "cavarzere"]];
      const hiddenP = this.state.hiddenPunti || [];
      const punti = [...basePunti.filter((pp) => !hiddenP.includes(pp[2])), ...this.state.extraPunti].map(([name, tipo, k]) => ({
        name, tipo, go: tg(k), ...knob(cfg[k]),
        removable: true,
        // Rimuovendo un punto va tolta anche la sua chiave in cfg: lasciarla
        // orfana faceva crescere la mappa fino a superare il tetto di 256
        // chiavi del backend, che da allora respingeva ogni salvataggio.
        removeP: () => this.setState((st) => {
          const cfgRidotto = { ...st.cfg };
          delete cfgRidotto[k];
          return st.extraPunti.some((pp) => pp[2] === k)
            ? { extraPunti: st.extraPunti.filter((pp) => pp[2] !== k), cfg: cfgRidotto, saved: false }
            : { hiddenPunti: [...(st.hiddenPunti || []), k], cfg: cfgRidotto, saved: false };
        }),
      }));
      const addPunto = () => this.setState((st) => {
        const name = cap((st.newPunto || "").trim(), 160);
        if (!name || st.extraPunti.length >= 200) return {}; // tetto del backend
        const k = "px" + st.nextP;
        return { extraPunti: [...st.extraPunti, [name, "Riconsegna", k]], cfg: { ...st.cfg, [k]: true }, nextP: st.nextP + 1, newPunto: "", saved: false };
      });
      const notifiche = [["Email a chiusura ciclo", "Riepilogo esiti a ogni ciclo di rinomina", "nEmail"], ["Alert sbilanciamento", "Avviso quando la posizione supera la tolleranza", "nAlert"], ["Report giornaliero PDF", "Sintesi del giorno gas alle 06:30", "nReport"]].map(([name, desc, k]) => ({ name, desc, go: tg(k), ...knob(cfg[k]) }));
      const seg = (key, opts) => opts.map((o) => ({ label: o, go: setC(key, o), bg: cfg[key] === o ? "var(--surface)" : "transparent", fg: cfg[key] === o ? "var(--ink)" : "var(--ink2)", sh: cfg[key] === o ? "0 1px 2px rgba(16,34,45,.12)" : "none" }));
      const unitOpts = seg("unit", ["MWh", "Smc"]);
      const cicloOpts = seg("ciclo", ["D−1", "Intraday"]);
      const nAbil = punti.filter((x) => x.on).length;
      // Contatori REALI del workspace regolatorio: non sono scenografia, quindi
      // restano visibili anche a demo spenta. Il giorno gas non e' la mezzanotte
      // locale: alle 02:00, in pieno intraday, contare su new Date() direbbe
      // zero documenti con cinque nomine aperte.
      const GIORNO_GAS_ISO = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Europe/Rome", year: "numeric", month: "2-digit", day: "2-digit",
      });
      const giornoGasIso = (d = new Date()) => GIORNO_GAS_ISO.format(new Date(d.getTime() - 6 * 3600 * 1000));
      const _rem = this.state.remReports || [];
      const _edg = this.state.edgNomine || [];
      const oggiGG = giornoGasIso();
      const regCaricamento = !!(this.state.remCaricamento || this.state.pdrCaricamento);
      const nReg = {
        bozze: _rem.filter((r) => r.status === "bozza").length,
        xml: _rem.filter((r) => r.status === "xml_validato_xsd").length,
        nomineOggi: _edg.filter((n) => n.giorno_gas === oggiGG).length,
        avvisi: _edg.reduce((tot, n) => tot + ((n.avvisi || []).length), 0),
      };

      const regTessera = (area, n, unita, pieno, vuoto, tono, dove) => ({
        area, unita, go: go(dove),
        value: regCaricamento ? "—" : String(n),
        // "—" significa "non ancora saputo", "0" significa "non hai nulla":
        // sono due cose diverse e vanno distinte a schermo.
        hint: regCaricamento ? "caricamento…" : (n ? pieno : vuoto),
        bg: (!regCaricamento && n) ? tono.bg : "var(--surface2)",
        fg: (!regCaricamento && n) ? tono.fg : "var(--ink3)",
      });
      const regKpis = [
        regTessera("REMIT · BOZZE", nReg.bozze, "record", "da validare", "nessuna in lavorazione", WAIT, "remit"),
        regTessera("REMIT · XML", nReg.xml, "file", "validati XSD", "nessun file generato", OK, "remit"),
        regTessera("EDIG@S · GIORNO GAS", nReg.nomineOggi, "documenti", "NOMINT generati oggi", "nessuna nomina oggi", RUN, "nomine"),
        regTessera("EDIG@S · AVVISI", nReg.avvisi, "avvisi", "da leggere", "nessun avviso", WARN, "nomine"),
      ];

      const moduli = [
        { title: "Dashboard", desc: "Posizione del giorno gas, sbilanciamento e prezzi PSV a colpo d'occhio.", stat: "G+0", statLabel: "giorno gas corrente", primary: true, go: go("dash"), cursor: "pointer", border: "color-mix(in oklab, var(--prim) 40%, var(--line))" },
        { title: "Nomine & Programmazione", desc: "Nomine EDIG@S 6.1: NOMINT validato, risposta NOMRES del trasportatore e cicli di rinomina.", stat: String(nReg.nomineOggi), statLabel: "documenti del giorno gas", reale: true, primary: true, go: go("nomine"), cursor: "pointer", border: "var(--line)" },
        { title: "Bilanciamento", desc: "Posizione fisica e commerciale, esposizione e azioni correttive.", stat: "−312", statLabel: "MWh previsti", primary: true, go: go("bilancio"), cursor: "pointer", border: "var(--line)" },
        { title: "Capacità & Contratti", desc: "Capacità di trasporto conferite, contratti e scadenze.", stat: "8", statLabel: "contratti attivi", primary: true, go: go("capacita"), cursor: "pointer", border: "var(--line)" },
        { title: "Stoccaggio", desc: "Giacenza, iniezione ed erogazione sui servizi di stoccaggio.", stat: "61%", statLabel: "riempimento", primary: true, go: go("stoccaggio"), cursor: "pointer", border: "var(--line)" },
        { title: "Report & Analisi", desc: "Estrazioni, report regolatori e serie storiche esportabili.", stat: "12", statLabel: "report programmati", primary: true, go: go("report"), cursor: "pointer", border: "var(--line)" },
        { title: "REMIT · XML ACER", desc: "Bozze auditabili, validazione XSD, generatore UTI ed export per PDR.", stat: String(nReg.bozze), statLabel: "bozze da validare", reale: true, primary: true, go: go("remit"), cursor: "pointer", border: "var(--line)" },
        { title: "PDR · GME", desc: "Preflight, registro delle ricevute e requisiti di caricamento verso il GME.", stat: String(nReg.xml), statLabel: "file pronti al preflight", reale: true, primary: true, go: go("pdr"), cursor: "pointer", border: "var(--line)" },
      ];
      // Solo i numeri di scena vanno azzerati: quelli regolatori sono dati
      // veri dell'utente e restano visibili anche a portale pulito.
      if (!demoOn) for (const m of moduli) if (!m.reale) m.stat = "—";
      const off = this.state.dashOff;
      const fmtN = (n) => n.toLocaleString("it-IT");
      const dNom = 12480 + off * 260;
      const dAlloc = Math.round(dNom * 0.969);
      const dSbil = -312 + off * 45;
      const psvStr = (33.45 + off * 0.35).toFixed(2).replace(".", ",");
      // In demo il giorno gas resta ancorato al 17/07/2026 del canvas
      // (i grafici sono etichettati su quelle date); da pulito è oggi.
      const bd = demoOn ? new Date(2026, 6, 17 + off) : (() => { const t = new Date(); t.setDate(t.getDate() + off); return t; })();
      const fmtData = (d) => String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0") + "/" + d.getFullYear();
      const dashDate = fmtData(bd);
      const giornoGas = demoOn ? "17/07/2026" : fmtData(new Date());
      const kpis = demoOn ? [
        { label: "Nominato — giorno gas", value: fmtN(dNom), unit: "MWh", delta: "+3,2% vs G−1", dBg: OK.bg, dFg: OK.fg },
        { label: "Allocato — G−1", value: fmtN(dAlloc), unit: "MWh", delta: "96,9% del nominato", dBg: "var(--surface2)", dFg: "var(--ink2)" },
        { label: "Sbilanciamento previsto", value: (dSbil < 0 ? "−" : "+") + fmtN(Math.abs(dSbil)), unit: "MWh", delta: dSbil < 0 ? "Posizione corta" : "Posizione lunga", dBg: dSbil < 0 ? WARN.bg : OK.bg, dFg: dSbil < 0 ? WARN.fg : OK.fg },
        { label: "PSV Day-Ahead", value: psvStr, unit: "€/MWh", delta: "−0,8% vs ieri", dBg: NEG.bg, dFg: NEG.fg },
      ] : [
        { label: "Nominato — giorno gas", value: "—", unit: "MWh", ...ATTESA },
        { label: "Allocato — G−1", value: "—", unit: "MWh", ...ATTESA },
        { label: "Sbilanciamento previsto", value: "—", unit: "MWh", ...ATTESA },
        { label: "PSV Day-Ahead", value: "—", unit: "€/MWh", ...ATTESA },
      ];
      const dashCiclo = !demoOn ? { txt: "Nessuna programmazione attiva", bg: "var(--surface2)", fg: "var(--ink2)" }
        : off === 0 ? { txt: "Ciclo R3 in corso · chiude 14:00", bg: "color-mix(in oklab,var(--prim) 10%,transparent)", fg: "var(--primText)" }
        : off < 0 ? { txt: "Giorno chiuso · bilancio provvisorio", bg: "var(--surface2)", fg: "var(--ink2)" }
        : { txt: "Programmazione · nomine aperte", bg: RUN.bg, fg: RUN.fg };
      const raw = [["04", 62, 60], ["05", 65, 64], ["06", 58, 57], ["07", 71, 70], ["08", 74, 72], ["09", 69, 69], ["10", 77, 74], ["11", 60, 59], ["12", 55, 54], ["13", 79, 77], ["14", 83, 80], ["15", 76, 75], ["16", 87, 84], ["17", 89, 86]];
      const days = !demoOn ? [] : raw.map(([d, n, a], i) => ({ d, n: n + "%", a: a + "%", w: i === raw.length - 1 ? 700 : 500, lc: i === raw.length - 1 ? "var(--ink)" : "var(--ink3)" }));
      const cicli = !demoOn ? [] : [
        { name: "Nomina D−1", time: "ieri 14:00", stato: "Confermata", ...OK },
        { name: "Rinomina R1", time: "06:00", stato: "Confermata", ...OK },
        { name: "Rinomina R2", time: "10:00", stato: "Confermata", ...OK },
        { name: "Rinomina R3", time: "14:00", stato: "In corso", ...RUN },
        { name: "Rinomina R4", time: "17:00", stato: "In attesa", ...WAIT },
        { name: "Rinomina R5", time: "20:00", stato: "In attesa", ...WAIT },
      ];
      const rows = !demoOn ? [] : [
        { punto: "PSV", tipo: "Scambio virtuale", contratto: "CT-2026-114", nom: "4.850", alloc: "4.850", delta: "0,0%", dFg: "var(--ink2)", stato: "Confermata", sBg: OK.bg, sFg: OK.fg },
        { punto: "Passo Gries", tipo: "Import · Svizzera", contratto: "TR-2025-088", nom: "3.100", alloc: "3.010", delta: "−2,9%", dFg: NEG.fg, stato: "Allocata", sBg: RUN.bg, sFg: RUN.fg },
        { punto: "Mazara del Vallo", tipo: "Import · Algeria", contratto: "TR-2025-102", nom: "2.400", alloc: "2.355", delta: "−1,9%", dFg: NEG.fg, stato: "Allocata", sBg: RUN.bg, sFg: RUN.fg },
        { punto: "ReMi 34521301 · Milano Ovest", tipo: "Riconsegna", contratto: "DI-2026-021", nom: "1.480", alloc: "1.480", delta: "0,0%", dFg: "var(--ink2)", stato: "Confermata", sBg: OK.bg, sFg: OK.fg },
        { punto: "Stogit · Erogazione", tipo: "Stoccaggio", contratto: "ST-2026-007", nom: "650", alloc: "—", delta: "—", dFg: "var(--ink3)", stato: "In verifica", sBg: WARN.bg, sFg: WARN.fg },
      ];
      // Deviazione 6: l'effetto hover del mazzo è in CSS (:hover generato dal
      // builder), non in deckHover+setState come nel design: un re-render a
      // ogni hover spezzerebbe le transizioni. Qui restano i valori a riposo.
      const mkHub = (key, code, title, sub, target) => ({
        code, title, sub, go: go(target),
        t1: "rotate(-7deg) translate(-12px,2px)",
        t2: "rotate(4deg) translate(10px,-2px)",
        t3: "translateY(7px) rotate(-1deg)",
        top: "none",
        shadow: "var(--shadow)",
      });
      const hubCards = [mkHub("lg", "LG", "Logistica Gas", "Nomine EDIG@S · REMIT e PDR · Bilanciamento", "moduli"), mkHub("cfg", "CF", "Configurazione", "Utenti · Parametri · Notifiche", "config")];
      const cfgCards = [
        { title: "Sistema", desc: "Stato dei servizi collegati, ambiente e attività recenti.", stat: demoOn ? "3" : "0", statLabel: "servizi collegati", go: go("cfgSis") },
        { title: "Impostazioni", desc: "Anagrafica shipper, parametri di nomina, punti e notifiche.", stat: String(nAbil), statLabel: "punti abilitati", go: go("cfgImp") },
      ];
      const servizi = demoOn ? [
        { name: "API Snam Rete Gas", desc: "Nomine e rinomine", stato: "Operativo", ...OK },
        { name: "Feed PSV · GME", desc: "Prezzi day-ahead", stato: "Operativo", ...OK },
        { name: "SSO aziendale", desc: "Autenticazione utenti", stato: "Operativo", ...OK },
      ] : [
        { name: "API Snam Rete Gas", desc: "Nomine e rinomine", stato: "Da collegare", ...WAIT },
        { name: "Feed PSV · GME", desc: "Prezzi day-ahead", stato: "Da collegare", ...WAIT },
        { name: "SSO aziendale", desc: "Autenticazione utenti", stato: "Da collegare", ...WAIT },
      ];
      const logs = !demoOn ? [] : [
        { time: "11:42", who: "M. Rossi", txt: "Rinomina R2 inviata su PSV" },
        { time: "10:15", who: "Sistema", txt: "Allocazioni G−1 ricevute da Snam" },
        { time: "09:58", who: "L. Bianchi", txt: "Modificata tolleranza di sbilanciamento" },
        { time: "06:02", who: "Sistema", txt: "Apertura giorno gas 17/07/2026" },
      ];
      // Permessi di default degli utenti demo (nel canvas Bianchi/Verdi sono
      // in sola lettura); l'utente reale ('me') resta in lettura/scrittura.
      const DEMO_PERM = { mricci: "up", lbianchi: "ro", gverdi: "ro" };
      const permOpt = (uk, val, label) => { const cur = this.state.users[uk] ?? DEMO_PERM[uk] ?? "up"; return { label, go: () => this.setState((st) => ({ users: { ...st.users, [uk]: val } })), bg: cur === val ? "var(--surface)" : "transparent", fg: cur === val ? "var(--ink)" : "var(--ink2)", sh: cur === val ? "0 1px 2px rgba(16,34,45,.12)" : "none" }; };
      const baseU = demoOn
        ? [["mricci", "MR", "Marco Rossi", "m.rossi@azienda1.it"], ["lbianchi", "LB", "Laura Bianchi", "l.bianchi@azienda1.it"], ["gverdi", "GV", "Giulio Verdi", "g.verdi@azienda1.it"]]
        : [["me", ident.iniziali, ident.nome, this.state.utenteEmail || ""]];
      const utenti = [...baseU, ...this.state.extraUsers].map(([k, init, name, mail]) => {
        const offU = !!this.state.disabled[k];
        return {
          init, name, mail, opts: [permOpt(k, "ro", "Solo lettura"), permOpt(k, "up", "Lettura e scrittura")],
          remove: () => this.setState((st) => ({ extraUsers: st.extraUsers.filter((u) => u[0] !== k) })),
          removable: this.state.extraUsers.some((u) => u[0] === k),
          off: offU, rowOp: offU ? 0.45 : 1,
          togDis: () => this.setState((st) => ({ disabled: { ...st.disabled, [k]: !st.disabled[k] } })),
          disLabel: offU ? "Riabilita" : "Disabilita",
          disBg: offU ? "rgba(197,96,80,.14)" : "transparent",
          disFg: offU ? "#C05B4D" : "var(--ink2)",
          disBorder: offU ? "rgba(197,96,80,.4)" : "var(--line)",
        };
      });
      const addUser = () => this.setState({ wiz: { step: 1, nome: "", cognome: "", email: "", perm: "ro" } });
      const wiz = this.state.wiz;
      const wSet = (k) => (e) => this.setSilent((st) => ({ wiz: { ...st.wiz, [k]: e.target.value } }));
      const wStep = (d) => () => this.setState((st) => ({ wiz: { ...st.wiz, step: st.wiz.step + d } }));
      const wizSteps = wiz ? [["Anagrafica", 1], ["Privilegi", 2], ["Conferma", 3]].map(([label, n]) => ({
        n, label, bg: wiz.step >= n ? "var(--prim)" : "var(--surface2)", fg: wiz.step >= n ? "#fff" : "var(--ink3)",
        lc: wiz.step === n ? "var(--ink)" : "var(--ink3)", lw: wiz.step === n ? 600 : 500,
      })) : [];
      const wizPermOpts = wiz ? [["ro", "Solo lettura", "Consulta dashboard, nomine e report senza modificarli."], ["up", "Lettura e scrittura", "Può inviare nomine, caricare file e modificare i parametri."]].map(([v, t, d]) => ({
        t, d, go: () => this.setState((st) => ({ wiz: { ...st.wiz, perm: v } })),
        border: wiz.perm === v ? "color-mix(in oklab,var(--prim) 55%,var(--line))" : "var(--line)",
        bg: wiz.perm === v ? "color-mix(in oklab,var(--prim) 8%,var(--surface))" : "var(--surface)",
        dot: wiz.perm === v ? "var(--prim)" : "transparent",
      })) : [];
      const wName = wiz ? [wiz.nome, wiz.cognome].filter(Boolean).join(" ") : "";
      const wDominio = demoOn ? "azienda1.it" : ident.dominio;
      const wEmail = wiz ? (wiz.email || ((wiz.nome ? wiz.nome[0].toLowerCase() + "." : "") + (wiz.cognome || "utente").toLowerCase() + "@" + wDominio)) : "";
      const wizFinish = () => this.setState((st) => {
        if (st.extraUsers.length >= 200) return { wiz: null }; // tetto del backend
        const n = st.nextU, k = "wu" + n;
        const init = ((st.wiz.nome[0] || "N") + (st.wiz.cognome[0] || "U")).toUpperCase();
        return { extraUsers: [...st.extraUsers, [k, init, cap(wName || "Nuovo utente " + n, 160), cap(wEmail, 160)]], users: { ...st.users, [k]: st.wiz.perm }, nextU: n + 1, wiz: null };
      });
      const nomStatoC = {
        // "Registrata" è l'annotazione interna; "Inviata" resta per le righe
        // già salvate nei database dei clienti prima di questa distinzione.
        "Registrata": WAIT, "Confermata": OK, "In corso": RUN, "Inviata": RUN, "In attesa": WAIT,
      };
      // Le nomine demo compaiono DOPO quelle reali e non vengono mai salvate.
      const nomDemo = !demoOn ? [] : [
        { punto: "PSV", ciclo: "R3", qta: "4.850", stato: "In corso" },
        { punto: "Passo Gries", ciclo: "R2", qta: "3.100", stato: "Confermata" },
        { punto: "Mazara del Vallo", ciclo: "R2", qta: "2.400", stato: "Confermata" },
      ];
      const nomRows = [...this.state.nomList, ...nomDemo].map((r) => ({ ...r, bg: (nomStatoC[r.stato] || WAIT).bg, fg: (nomStatoC[r.stato] || WAIT).fg }));
      // Un doppio clic non deve creare due bozze, due nomine o due ricevute:
      // il secondo tentativo viene ignorato finché il primo non ha risposto.
      const unaVolta = (nome, azione) => async (...args) => {
        if (this._inCorso.has(nome)) return undefined;
        this._inCorso.add(nome);
        try {
          return await azione(...args);
        } finally {
          this._inCorso.delete(nome);
        }
      };

      const addNomina = () => this.setState((st) => ({ nomList: [{ punto: cap(st.nomPunto, 120), ciclo: cap(st.nomCiclo, 120), qta: cap(st.nomQta || "500", 120), stato: "Registrata" }, ...st.nomList].slice(0, 500), nomQta: "" }));

      // --- EDIG@S 6.1 ---------------------------------------------------
      // I ruoli delle due parti non si scelgono: li fissa il tipo di
      // documento secondo la decision table EASEE-gas.
      const EDG_RUOLI = {
        "01G": { emittente: "ZSH", destinatario: "ZSO", nomina: ["A01", "A02"], conControparti: true },
        "02G": { emittente: "ZSH", destinatario: "ZUK", nomina: ["A02"], conControparti: true },
        "03G": { emittente: "ZUM", destinatario: "ZUK", nomina: [], conControparti: true },
        "04G": { emittente: "ZSH", destinatario: "ZSO", nomina: [], conControparti: false },
      };
      const EDG_NOMI = { ZSH: "shipper", ZSO: "trasportatore", ZUK: "area coordinator", ZUM: "clearing responsible" };
      const edgRegole = EDG_RUOLI[this.state.edgTipo] || EDG_RUOLI["01G"];
      const edgRuoli = `Emittente ${edgRegole.emittente} (${EDG_NOMI[edgRegole.emittente]}) → destinatario ${edgRegole.destinatario} (${EDG_NOMI[edgRegole.destinatario]}).`;

      const setEdgTipo = (e) => {
        const tipo = e.target.value;
        const regole = EDG_RUOLI[tipo] || EDG_RUOLI["01G"];
        // il tipo di nomina si allinea da solo: fuori lista non è ammesso
        const attuale = this.state.edgTipoNomina;
        const nomina = regole.nomina.includes(attuale) ? attuale : (regole.nomina[0] || "");
        this.setState({ edgTipo: tipo, edgTipoNomina: nomina });
      };

      const edgPayload = () => {
        const st = this.state;
        const regole = EDG_RUOLI[st.edgTipo] || EDG_RUOLI["01G"];
        const base = {
          identificativo: cap(st.edgIdent, 35),
          versione: cap(st.edgVersione || "1", 3),
          tipo_documento: st.edgTipo,
          emittente_eic: cap(st.edgEmittente, 16),
          emittente_ruolo: regole.emittente,
          destinatario_eic: cap(st.edgDestinatario, 16),
          destinatario_ruolo: regole.destinatario,
          giorno_gas: cap(st.edgGiorno, 10),
          conto_interno: cap(st.edgConto, 35),
          punto_eic: cap(st.edgPunto, 35),
          unita: st.edgUnita,
        };
        if (st.edgTipoNomina) base.tipo_nomina = st.edgTipoNomina;
        if (!regole.conControparti) {
          base.direzione = st.edgDirezione;
          base.quantita_giornaliera = cap(st.edgQta, 40);
        } else {
          base.controparti = [{ conto: cap(st.edgControparte, 35), direzione: st.edgDirezione, quantita_giornaliera: cap(st.edgQta, 40) }];
        }
        return base;
      };

      const generaNomint = unaVolta("generaNomint", async () => {
        this.setState({ edgErrore: "", edgInfo: "", edgErroriCampo: [] });
        try {
          const esito = await this._json("/api/edigas/nomine", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(edgPayload()),
          });
          this.setState({
            edgInfo: `NOMINT ${esito.identificativo} v${esito.versione} generato e validato contro lo schema EDIG@S ${esito.versione_edigas}.`,
          });
          await this._caricaEdigas();
        } catch (error) {
          this.setState({
            edgErrore: `Nomina non generata: ${error.message}`,
            edgErroriCampo: error.dettagli || [],
          });
        }
      });

      const leggiNomres = async () => {
        this.setState({ edgRispostaErrore: "", edgRispostaInfo: "", edgScostamentiList: [] });
        const testo = (this.state.edgRisposta || "").trim();
        if (!testo) {
          this.setState({ edgRispostaErrore: "Incolla il contenuto del file NOMRES ricevuto." });
          return;
        }
        try {
          const esito = await this._json("/api/edigas/risposte", {
            method: "POST",
            headers: { "Content-Type": "application/xml" },
            body: testo,
          });
          const trovata = esito.nomina_trovata;
          const scost = esito.scostamenti || [];
          if (!esito.valido_xsd) {
            // Senza questo l'operatore leggerebbe "confermata per intero" su
            // un file che non rispetta lo schema e da cui non si è letto nulla.
            const dettaglio = (esito.errori_schema || [])[0] || "";
            this.setState({
              edgScostamentiList: scost,
              edgRispostaErrore: `Il file non è conforme allo schema EDIG@S e non può essere confrontato. ${dettaglio}`.trim(),
            });
            return;
          }
          this.setState({
            edgScostamentiList: scost,
            edgRispostaInfo: trovata
              ? (scost.length
                  ? `Risposta abbinata alla nomina ${esito.nomina_riferita.identificativo}: ${scost.length} scostamenti.`
                  : `Risposta abbinata alla nomina ${esito.nomina_riferita.identificativo}: confermata per intero.`)
              : `Risposta letta (${esito.righe.length} periodi), ma la nomina ${esito.nomina_riferita.identificativo} non è fra quelle generate qui: nessun confronto possibile.`,
          });
        } catch (error) {
          this.setState({ edgRispostaErrore: `Risposta non leggibile: ${error.message}${_dettagli(error)}` });
        }
      };

      // Stato del riscontro per nomina: senza questo l'operatore dovrebbe
      // incrociare a mano due elenchi per sapere quali documenti il
      // trasportatore ha preso in carico.
      const ESITI_ACK = {
        "accettato": { testo: "presa in carico", bg: "#D1FADF", fg: "#05603A" },
        "accettato con riserva": { testo: "con riserva", bg: "#FEF0C7", fg: "#B54708" },
        "respinto": { testo: "respinta", bg: "#FEE4E2", fg: "#B42318" },
      };

      const importaAck = unaVolta("importaAck", async () => {
        this.setState({ edgAckErrore: "", edgAckInfo: "" });
        const testo = (this.state.edgAck || "").trim();
        if (!testo) {
          this.setState({ edgAckErrore: "Incolla il contenuto del riscontro ACKNOW ricevuto." });
          return;
        }
        try {
          const esito = await this._json("/api/edigas/riscontri", {
            method: "POST",
            headers: { "Content-Type": "application/xml" },
            body: testo,
          });
          if (!esito.valido_xsd) {
            const dettaglio = (esito.errori_schema || [])[0] || "";
            this.setState({ edgAckErrore: `Il riscontro non è conforme allo schema EDIG@S. ${dettaglio}`.trim() });
            return;
          }
          const rif = esito.documento_riscontrato || {};
          const quale = rif.identificativo || rif.nome_file || "documento non identificato";
          const stato = esito.accettato ? "preso in carico" : "respinto";
          this.setState({
            edgAck: "",
            edgAckInfo: esito.gia_importato
              ? `Riscontro già archiviato in precedenza: ${quale} ${stato}.`
              : `${quale}: ${stato}${esito.nomina_trovata ? ", collegato alla nomina" : " (nomina non fra le tue)"}.`,
          });
          await this._caricaEdigas();
        } catch (error) {
          this.setState({ edgAckErrore: `Riscontro non importato: ${error.message}${_dettagli(error)}` });
        }
      });

      const edgRiscontri = (this.state.edgRiscontriList || []).map((r) => ({
        ...r,
        tipo: r.tipo_documento === "AMU" ? "Riscontro tecnico" : "Riscontro applicativo",
        dettaglio: (r.motivazioni || []).join(" · ") || "senza motivazione",
        esito: (ESITI_ACK[r.esito] || ESITI_ACK["respinto"]).testo,
        bg: (ESITI_ACK[r.esito] || ESITI_ACK["respinto"]).bg,
        fg: (ESITI_ACK[r.esito] || ESITI_ACK["respinto"]).fg,
      }));

      const EDG_ESITI = {
        ridotto: { bg: "#FEF0C7", fg: "#B54708" },
        aumentato: { bg: "#FEF0C7", fg: "#B54708" },
        "non nominato": { bg: "#FEE4E2", fg: "#B42318" },
        "senza risposta": { bg: "#FEE4E2", fg: "#B42318" },
      };
      const ackPerNomina = new Map();
      for (const r of this.state.edgRiscontriList || []) {
        // il più recente vince: l'elenco arriva già ordinato dal più nuovo
        if (r.nomina_id && !ackPerNomina.has(r.nomina_id)) ackPerNomina.set(r.nomina_id, r);
      }
      const edgRows = (this.state.edgNomine || []).map((n) => {
        const ack = ackPerNomina.get(n.id);
        const stato = ack ? (ESITI_ACK[ack.esito] || ESITI_ACK["respinto"]) : null;
        return {
          ...n,
          impronta: `SHA-256 ${String(n.sha256 || "").slice(0, 16)}…`,
          avvisi: (n.avvisi || []).map((testo) => ({ testo })),
          scarica: () => window.open(`/api/edigas/nomine/${n.id}/download`, "_blank"),
          ackEsito: stato ? stato.testo : "in attesa di riscontro",
          ackBg: stato ? stato.bg : "var(--surface2)",
          ackFg: stato ? stato.fg : "var(--ink3)",
          ackNota: ack ? (ack.motivazioni || []).join(" · ") : "",
        };
      });
      const edgScostamenti = (this.state.edgScostamentiList || []).map((s) => {
        const colori = EDG_ESITI[s.esito] || { bg: "var(--surface2)", fg: "var(--ink2)" };
        const nominato = s.nominato === null || s.nominato === undefined ? "—" : s.nominato;
        const confermato = s.quantita === null || s.quantita === undefined ? "—" : s.quantita;
        return { ...s, ...colori, etichetta: `${s.esito}: ${nominato} → ${confermato}` };
      });
      const oreSbil = !demoOn ? [] : [["04", 45], ["05", 28], ["06", -15], ["07", 62], ["08", 34], ["09", -48], ["10", 20], ["11", -25], ["12", 55], ["13", 12], ["14", -60], ["15", 38], ["16", -85], ["17", -87]].map(([h, v]) => ({ h, top: v > 0 ? Math.round(v * 0.9) + "%" : "0%", bot: v < 0 ? Math.round(-v * 0.9) + "%" : "0%", w: h === "17" ? 700 : 500, lc: h === "17" ? "var(--ink)" : "var(--ink3)" }));
      const bilKpis = demoOn ? [
        { label: "Posizione fisica prevista", value: "−312", unit: "MWh", delta: "Posizione corta", dBg: WARN.bg, dFg: WARN.fg },
        { label: "Posizione commerciale", value: "+95", unit: "MWh", delta: "Coperta su MGP", dBg: OK.bg, dFg: OK.fg },
        { label: "Esposizione stimata", value: "10.410", unit: "€", delta: "Prezzo sbil. 33,6 €/MWh", dBg: "var(--surface2)", dFg: "var(--ink2)" },
      ] : [
        { label: "Posizione fisica prevista", value: "—", unit: "MWh", ...ATTESA },
        { label: "Posizione commerciale", value: "—", unit: "MWh", ...ATTESA },
        { label: "Esposizione stimata", value: "—", unit: "€", ...ATTESA },
      ];
      const azioni = !demoOn ? [] : [
        { txt: "Acquisto 250 MWh su MGP-GAS", sub: "Sessione AGS · entro 12:30", stato: "Suggerita", ...RUN },
        { txt: "Rinomina R4 · +60 MWh su Gries", sub: "Apre alle 15:00", stato: "In valutazione", ...WAIT },
        { txt: "Erogazione stoccaggio +100 MWh", sub: "Confermata da Stogit", stato: "Eseguita", ...OK },
      ];
      const capRows = (!demoOn ? [] : [
        { punto: "Passo Gries", tipo: "Continuo · annuale", conf: "3.500 MWh/g", uso: 88, scad: "30/09/2026", stato: "Attivo", ...OK },
        { punto: "Mazara del Vallo", tipo: "Continuo · annuale", conf: "2.600 MWh/g", uso: 92, scad: "30/09/2026", stato: "Attivo", ...OK },
        { punto: "ReMi 34521301 · Milano Ovest", tipo: "Uscita · annuale", conf: "1.600 MWh/g", uso: 93, scad: "30/09/2026", stato: "Attivo", ...OK },
        { punto: "Tarvisio", tipo: "Continuo · trimestrale", conf: "1.200 MWh/g", uso: 0, scad: "01/10/2026", stato: "In firma", ...WARN },
        { punto: "Stogit", tipo: "Stoccaggio · anno termico", conf: "180 GWh", uso: 61, scad: "31/03/2027", stato: "Attivo", ...OK },
      ]).map((r) => ({ ...r, usoW: r.uso + "%", usoL: r.uso ? r.uso + "%" : "—" }));
      const capChip = capRows.length + " contratti";
      const scadenze = !demoOn ? [] : [
        { data: "05/08/2026", txt: "Asta PRISMA · capacità mensile settembre" },
        { data: "15/09/2026", txt: "Rinnovo capacità annuale Gries e Mazara" },
        { data: "30/09/2026", txt: "Chiusura anno termico 2025/26" },
      ];
      const stocKpis = demoOn ? [
        { label: "Spazio conferito", value: "180", unit: "GWh", delta: "Modulazione Uniforme", dBg: "var(--surface2)", dFg: "var(--ink2)" },
        { label: "Giacenza", value: "109,8", unit: "GWh", delta: "61% dello Spazio", dBg: OK.bg, dFg: OK.fg },
        { label: "Spazio residuo", value: "70,2", unit: "GWh", delta: "Disponibile per iniezione", dBg: OK.bg, dFg: OK.fg },
        { label: "Iniezione nominata · G+0", value: "+95", unit: "MWh", delta: "Ciclo diurno · conferma Stogit", dBg: RUN.bg, dFg: RUN.fg },
      ] : [
        { label: "Spazio conferito", value: "—", unit: "GWh", ...ATTESA },
        { label: "Giacenza", value: "—", unit: "GWh", ...ATTESA },
        { label: "Spazio residuo", value: "—", unit: "GWh", ...ATTESA },
        { label: "Iniezione nominata · G+0", value: "—", unit: "MWh", ...ATTESA },
      ];
      const stocCap = !demoOn ? [] : [
        { tipo: "Capacità di Iniezione", conf: "1.150", fatt: "0,82", disp: "943", dispW: "82%", note: "Limite: Spazio residuo 70,2 GWh", barCol: "var(--acc)" },
        { tipo: "Capacità di Erogazione", conf: "1.600", fatt: "0,64", disp: "1.024", dispW: "64%", note: "Limite: Giacenza 109,8 GWh", barCol: "var(--prim)" },
      ];
      const stocMov = !demoOn ? [] : [["Lun", 95], ["Mar", 80], ["Mer", -45], ["Gio", 120], ["Ven", 60], ["Sab", -30], ["Dom", -70]].map(([h, v]) => ({ h, top: v > 0 ? Math.round(v / 1.4) + "%" : "0%", bot: v < 0 ? Math.round(-v / 1.4) + "%" : "0%" }));
      const stocServ = !demoOn ? [] : [
        { name: "Modulazione Uniforme", sub: "Stogit · Spazio 180 GWh · asta annuale", val: "61%", stato: "Attivo", ...OK },
        { name: "Modulazione di punta", sub: "Stogit · CE aggiuntiva 40 MWh/g", val: "54%", stato: "Attivo", ...OK },
        { name: "Prestazioni costanti · fast-cycle", sub: "Stogit · richiesta in corso d'anno", val: "—", stato: "In richiesta", ...WAIT },
      ];
      const repKpis = demoOn ? [
        { label: "Invii programmati", value: "3", unit: "attivi", delta: "Prossimo 06:30", dBg: RUN.bg, dFg: RUN.fg },
        { label: "Obblighi regolatori", value: "2", unit: "in scadenza", delta: "REMIT · ARERA", dBg: WARN.bg, dFg: WARN.fg },
        { label: "Costo sbilancio · YTD", value: "84,2", unit: "k€", delta: "−18% vs 2025", dBg: OK.bg, dFg: OK.fg },
      ] : [
        { label: "Invii programmati", value: "0", unit: "attivi", ...ATTESA },
        { label: "Obblighi regolatori", value: "—", unit: "in scadenza", ...ATTESA },
        { label: "Costo sbilancio · YTD", value: "—", unit: "k€", ...ATTESA },
      ];
      const repCat = this.state.repCat;
      const repCats = [["tutti", "Tutti"], ["op", "Operativi"], ["reg", "Regolatori"], ["mkt", "Mercato"]].map(([k, label]) => ({
        label, go: () => this.setState({ repCat: k }),
        bg: repCat === k ? "var(--prim)" : "transparent", fg: repCat === k ? "#fff" : "var(--ink2)",
        bd: repCat === k ? "var(--prim)" : "var(--line)",
      }));
      const allRep = !demoOn ? [] : [
        { name: "Bilancio giornaliero shipper", tipo: "PDF · giornaliero", upd: "17/07 · 06:31", cat: "op", tag: "Operativo" },
        { name: "Estrazione nomine · luglio", tipo: "CSV · mensile", upd: "01/07 · 07:02", cat: "op", tag: "Operativo" },
        { name: "Sbilanci e corrispettivi · S29", tipo: "XLSX · settimanale", upd: "16/07 · 07:10", cat: "op", tag: "Operativo" },
        { name: "Utilizzo capacità per punto", tipo: "PDF · mensile", upd: "01/07 · 08:20", cat: "op", tag: "Operativo" },
        { name: "Report REMIT · settimana 29", tipo: "XML · settimanale", upd: "14/07 · 09:15", cat: "reg", tag: "Regolatorio" },
        { name: "Pacchetto ARERA · trimestre 2", tipo: "ZIP · trimestrale", upd: "05/07 · 11:00", cat: "reg", tag: "Regolatorio" },
        { name: "Serie storiche PSV", tipo: "CSV · on demand", upd: "12/07 · 16:44", cat: "mkt", tag: "Mercato" },
        { name: "Prezzi MGP-GAS vs sbilancio", tipo: "XLSX · settimanale", upd: "14/07 · 07:30", cat: "mkt", tag: "Mercato" },
      ];
      const repFiles = allRep.filter((r) => repCat === "tutti" || r.cat === repCat);
      const repProg = !demoOn ? [] : [["Bilancio giornaliero · 06:30", "rg"], ["Alert sbilanciamento", "rs"], ["Pacchetto regolatorio ARERA", "rr"]].map(([name, k]) => ({ name, go: () => this.setState((st) => ({ reps: { ...st.reps, [k]: !st.reps[k] } })), ...knob(this.state.reps[k]) }));
      const backMap = { moduli: "hub", dash: "moduli", config: "hub", cfgSis: "config", cfgImp: "config", nomine: "moduli", bilancio: "moduli", capacita: "moduli", stoccaggio: "moduli", report: "moduli", remit: "moduli", pdr: "moduli" };

      // --- REMIT: dominio server-side, auditabile e senza falsi invii ---
      const remStatoC = {
        bozza: WAIT,
        validata_localmente: OK,
        xml_validato_xsd: RUN,
      };
      const remStatoLabel = {
        bozza: "Bozza",
        validata_localmente: "Validata localmente",
        xml_validato_xsd: "XML validato XSD",
      };
      const remTipoLabel = {
        gas_standard: "Standard · gas",
        gas_nonstandard: "Non-standard · gas",
        gas_transport: "Trasporto gas",
      };
      const remRows = (this.state.remReports || []).map((r) => {
        const dati = r.data || r;
        const colore = remStatoC[r.status] || WAIT;
        const errori = Array.isArray(r.validation_errors) ? r.validation_errors : [];
        return {
          ...r,
          rif: dati.source_ref || "—",
          tipo: remTipoLabel[dati.report_kind] || "Da classificare",
          evento: dati.event_at || "—",
          qta: dati.quantity_mwh || "—",
          prezzo: dati.price_eur_mwh || "—",
          stato: remStatoLabel[r.status] || "Da verificare",
          bg: colore.bg,
          fg: colore.fg,
          errLabel: errori.length
            ? errori
                .map((e) => (e && e.message ? e.message : String(e)))
                .join(" · ")
            : "Controlli locali superati",
          errFg: errori.length ? NEG.fg : "var(--ink3)",
          canValidate: r.status === "bozza",
          canExport: r.status === "validata_localmente",
          // L'XML resta scaricabile anche dopo l'export: se il download del
          // browser fallisce o la scheda si chiude, l'artefatto va comunque
          // recuperato senza rigenerarlo.
          hasXml: !!r.xml_artifact_id,
          scaricaXml: () => scaricaArtifact({ id: r.xml_artifact_id, filename: r.xml_filename }),
          validate: () => validaRemit(r),
          export: () => esportaRemit(r),
          audit: () => apriAuditRemit(r),
          pdr: () => verificaPdr(r),
          receipts: () => apriRicevutePdr(r),
        };
      });
      const remN = (status) => remRows.filter((r) => r.status === status).length;
      const remKpis = [
        { label: "Bozze", value: String(remN("bozza")), unit: "da completare", delta: "nessun invio reale", dBg: WAIT.bg, dFg: WAIT.fg },
        { label: "Validate", value: String(remN("validata_localmente")), unit: "localmente", delta: "pronte per export", dBg: OK.bg, dFg: OK.fg },
        { label: "XML ACER", value: String(remN("xml_validato_xsd")), unit: "XSD validati", delta: "preflight PDR", dBg: RUN.bg, dFg: RUN.fg },
      ];
      // Calcola UTI e Contract ID con l'algoritmo ACER: per i contratti
      // bilaterali entrambe le parti devono riportare lo stesso codice.
      const generaUti = async () => {
        this.setState({ remErrore: "", remInfo: "" });
        try {
          const esito = await this._json("/api/remit/identificativi", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...remPayload(), progressivo: 1 }),
          });
          this.setState({
            remTransactionId: esito.uti,
            remInfo: `UTI calcolato con l'algoritmo ACER. La controparte, partendo dagli stessi dati, ottiene lo stesso codice.`,
          });
        } catch (error) {
          this.setState({ remErrore: `UTI non calcolabile: ${error.message}${_dettagli(error)}` });
        }
      };

      const remPayload = () => ({
        report_kind: this.state.remTipo,
        action: this.state.remAzione,
        source_ref: cap(this.state.remRif, 160),
        event_at: cap(this.state.remData, 10),
        delivery_point: cap(this.state.remPunto, 160),
        counterparty: cap(this.state.remControparte, 160),
        counterparty_scheme: this.state.remControparteTipo,
        side: this.state.remLato,
        quantity_mwh: cap(this.state.remQta, 40),
        quantity_unit: this.state.remUnita,
        price_eur_mwh: cap(this.state.remPrezzo, 40),
        price_currency: this.state.remValuta,
        // letto ORA e non dalla closure del render: i campi di testo usano
        // setSilent (per non perdere il fuoco) e la variabile catturata a
        // inizio renderVals resterebbe al valore precedente. Qui finirebbe un
        // codice sbagliato dentro reportingEntityID e nel nome file PDR.
        acer_code: cap(this.state.cfg.acer || "", 12),
        trading_capacity: this.state.remCapacita,
        contract_id: cap(this.state.remContractId, 50),
        contract_date: cap(this.state.remContractDate, 10),
        contract_type: this.state.remContractType,
        energy_commodity: this.state.remCommodity,
        delivery_start_date: cap(this.state.remDeliveryStart, 10),
        delivery_end_date: cap(this.state.remDeliveryEnd, 10),
        settlement_method: this.state.remSettlement,
        marketplace_scheme: this.state.remMarketplaceTipo,
        marketplace_id: cap(this.state.remMarketplaceId, 32),
        transaction_at: cap(this.state.remTransactionAt, 40),
        transaction_id: cap(this.state.remTransactionId, 100),
      });
      const sostituisciReport = (record) => this.setState((st) => ({
        remReports: (st.remReports || []).map((item) => item.id === record.id ? record : item),
      }));
      const addRem = unaVolta("addRem", async () => {
        this.setState({ remErrore: "", remInfo: "" });
        try {
          const record = await this._json("/api/remit/reports", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(remPayload()),
          });
          this.setState((st) => ({
            remReports: [record, ...(st.remReports || [])],
            remRif: "", remData: "", remPunto: "", remControparte: "", remQta: "", remPrezzo: "", remContractId: "", remContractDate: "", remDeliveryStart: "", remDeliveryEnd: "", remMarketplaceId: "", remTransactionAt: "", remTransactionId: "",
            remInfo: record.is_complete ? "Bozza salvata. Puoi eseguire la validazione locale." : "Bozza salvata: completa i campi richiesti e poi valida.",
          }));
        } catch (error) {
          this.setState({ remErrore: `Impossibile salvare la bozza: ${error.message}` });
        }
      });
      const validaRemit = async (record) => {
        this.setState({ remErrore: "", remInfo: "" });
        try {
          const updated = await this._json(`/api/remit/reports/${record.id}/validate`, {
            method: "POST", headers: { "Content-Type": "application/json", "If-Match": String(record.version) }, body: JSON.stringify({}),
          });
          sostituisciReport(updated);
          this.setState({ remInfo: updated.is_complete ? "Controlli di completezza completati: puoi generare l'XML ACER validato XSD." : "La bozza resta da correggere: consulta i campi segnalati." });
        } catch (error) {
          this.setState({ remErrore: `Validazione non completata: ${error.message}${_dettagli(error)}` });
        }
      };
      const scaricaArtifact = async (artifact) => {
        if (typeof document === "undefined" || typeof URL === "undefined" || !URL.createObjectURL) return;
        const response = await fetch(`/api/remit/artifacts/${artifact.id}`, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`download ${response.status}`);
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url; link.download = artifact.filename; link.style.display = "none";
        document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
      };
      const esportaRemit = async (record) => {
        this.setState({ remErrore: "", remInfo: "" });
        try {
          const result = await this._json(`/api/remit/reports/${record.id}/export`, {
            method: "POST", headers: { "If-Match": String(record.version) },
          });
          sostituisciReport(result.report);
          await scaricaArtifact(result.artifact);
          this.setState({ remInfo: `XML ${result.artifact.filename} validato XSD (SHA-256 ${result.artifact.sha256.slice(0, 12)}…) e scaricato. Non è stato inviato a GME/PDR o ACER.` });
        } catch (error) {
          this.setState({ remErrore: `Export non completato: ${error.message}` });
        }
      };
      const apriAuditRemit = async (record) => {
        this.setState({ remErrore: "", remAudit: { title: record.id, loading: true, events: [] } });
        try {
          const payload = await this._json(`/api/remit/reports/${record.id}/audit`);
          this.setState({ remAudit: { title: record.id, loading: false, events: payload.events || [] } });
        } catch (error) {
          this.setState({ remErrore: `Audit non disponibile: ${error.message}`, remAudit: null });
        }
      };
      const verificaPdr = async (record) => {
        this.setState({ screen: "pdr", pdrErrore: "", pdrInfo: "", pdrCheck: { report_id: record.id, loading: true, issues: [] } });
        try {
          const check = await this._json(`/api/pdr/reports/${record.id}/preflight`, { method: "POST" });
          this.setState({ pdrCheck: check });
        } catch (error) {
          this.setState({ pdrErrore: `Preflight PDR non disponibile: ${error.message}`, pdrCheck: null });
        }
      };
      const pdrProfile = this.state.pdr || {};
      const pdrSet = (key) => (event) => this.setSilent((st) => ({ pdr: { ...st.pdr, [key]: event.target.value } }));
      const pdrToggle = (key) => () => this.setState((st) => ({ pdr: { ...st.pdr, [key]: !st.pdr[key] } }));
      const salvaPdr = unaVolta("salvaPdr", async () => {
        this.setState({ pdrErrore: "", pdrInfo: "" });
        try {
          const result = await this._json("/api/pdr/profile", {
            method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(this.state.pdr || {}), // stato corrente, non lo snapshot del render
          });
          this.setState({ pdr: result.profile, pdrInfo: "Profilo PDR salvato. Nessuna credenziale è stata memorizzata." });
        } catch (error) {
          this.setState({ pdrErrore: `Profilo PDR non salvato: ${error.message}` });
        }
      });

      // --- Ricevute PDR: conservazione locale, senza fingere una verifica ---
      const receiptId = (receipt) => receipt?.id ?? receipt?.receipt_id ?? receipt?.receipt?.id ?? "";
      const receiptField = (receipt, ...keys) => {
        const candidates = [receipt, receipt?.metadata, receipt?.data, receipt?.receipt];
        for (const candidate of candidates) {
          if (!candidate || typeof candidate !== "object") continue;
          for (const key of keys) {
            if (candidate[key] != null && candidate[key] !== "") return candidate[key];
          }
        }
        return "";
      };
      const sameId = (left, right) => String(left ?? "") === String(right ?? "");
      const outcomeStyle = {
        pdr_accepted: { label: "PDR · Accept dichiarato", ...OK },
        pdr_rejected: { label: "PDR · Reject dichiarato", ...NEG },
        pdr_partial: { label: "PDR · Partial dichiarato", ...WARN },
        acer_transmitted: { label: "ACER · trasmissione dichiarata", ...RUN },
        acer_accepted: { label: "ACER · accettazione dichiarata", ...OK },
        acer_rejected: { label: "ACER · rifiuto dichiarato", ...NEG },
        technical_retry: { label: "Ritentare tecnicamente", ...WARN },
        unknown: { label: "Esito non classificato", ...WAIT },
      };
      const sourceLabel = {
        pdr: "PDR GME",
        acer: "Ritorno ACER",
        pdr_portal: "Portale PDR GME",
        pdr_web_service: "Web service PDR GME",
        manual: "Importazione manuale",
        other: "Altra fonte",
      };
      const listaRicevute = (payload) => {
        if (Array.isArray(payload)) return payload;
        if (Array.isArray(payload?.receipts)) return payload.receipts;
        if (Array.isArray(payload?.items)) return payload.items;
        return [];
      };
      const caricaRicevute = async (reportId) => {
        if (!reportId) {
          this.setState({ pdrReceipts: [], pdrReceiptReportId: "", pdrReceiptOpen: null });
          return;
        }
        this.setState({ pdrReceiptReportId: String(reportId), pdrReceiptCaricamento: true, pdrReceiptErrore: "", pdrReceiptInfo: "", pdrReceiptOpen: null });
        try {
          // Il filtro resta qui, in un punto solo, per agevolare un eventuale
          // adattamento dell'API senza toccare il flusso di import/download.
          const payload = await this._json(`/api/pdr/receipts?report_id=${encodeURIComponent(reportId)}`);
          this.setState({ pdrReceipts: listaRicevute(payload), pdrReceiptCaricamento: false });
        } catch (error) {
          this.setState({ pdrReceiptCaricamento: false, pdrReceiptErrore: `Ricevute PDR non disponibili: ${error.message}` });
        }
      };
      const selezionaRicevuteReport = (event) => caricaRicevute(event.target.value);
      const apriRicevutePdr = async (record) => {
        this.setState({ screen: "pdr" });
        await caricaRicevute(record.id);
      };
      const setPdrReceiptFile = (event) => {
        const file = event?.target?.files?.[0] || null;
        this.setState({ pdrReceiptFile: file, pdrReceiptFileName: file?.name || "", pdrReceiptErrore: "", pdrReceiptInfo: "" });
      };
      const pdrReceiptSet = (key) => (event) => this.setSilent({ [key]: event.target.value });
      const pdrReceiptSetLoadCode = (event) => this.setSilent({
        pdrReceiptLoadCode: String(event.target.value || "").replace(/\D/g, "").slice(0, 6),
      });
      const importaRicevuta = unaVolta("importaRicevuta", async () => {
        const reportId = this.state.pdrReceiptReportId;
        const file = this.state.pdrReceiptFile;
        if (!reportId) {
          this.setState({ pdrReceiptErrore: "Seleziona prima il record REMIT a cui associare la ricevuta." });
          return;
        }
        if (!file) {
          this.setState({ pdrReceiptErrore: "Seleziona il file della ricevuta PDR o ACER." });
          return;
        }
        if (!this.state.pdrReceiptReportedAt) {
          this.setState({ pdrReceiptErrore: "Indica la data-ora dichiarata nella ricevuta." });
          return;
        }
        if (this.state.pdrReceiptLoadCode && !/^\d{6}$/.test(this.state.pdrReceiptLoadCode)) {
          this.setState({ pdrReceiptErrore: "Il codice di carico PDR deve contenere esattamente 6 cifre." });
          return;
        }
        this.setState({ pdrReceiptErrore: "", pdrReceiptInfo: "", pdrReceiptImporting: true });
        try {
          const contentBase64 = await fileToBase64(file);
          const payload = await this._json("/api/pdr/receipts/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              report_id: reportId,
              outcome: this.state.pdrReceiptOutcome,
              source: this.state.pdrReceiptSource,
              load_code: cap(this.state.pdrReceiptLoadCode, 6),
              reported_at: localDateTimeToIso(this.state.pdrReceiptReportedAt),
              detail: cap(this.state.pdrReceiptDetailText, 4000),
              filename: cap(file.name || this.state.pdrReceiptFileName || "ricevuta-pdr", 255),
              // Alcuni browser non valorizzano File.type per XML/ZIP: il
              // contenuto resta comunque immutabile e l'API conserva un
              // media type esplicito senza rifiutare una ricevuta valida.
              mime_type: cap(file.type || "application/octet-stream", 120),
              content_base64: contentBase64,
            }),
          });
          const saved = payload.receipt || payload;
          this.setState((st) => ({
            pdrReceipts: [saved, ...(st.pdrReceipts || []).filter((item) => !sameId(receiptId(item), receiptId(saved)))],
            pdrReceiptImporting: false,
            pdrReceiptFile: null,
            pdrReceiptFileName: "",
            pdrReceiptOutcome: "unknown",
            pdrReceiptSource: "pdr",
            pdrReceiptLoadCode: "",
            pdrReceiptReportedAt: "",
            pdrReceiptDetailText: "",
            // Un import è una conservazione probatoria locale: non è la
            // conferma di un connettore PDR/ACER.
            pdrReceiptInfo: "Ricevuta importata: non verificata dal connettore.",
          }));
        } catch (error) {
          this.setState({ pdrReceiptImporting: false, pdrReceiptErrore: `Importazione ricevuta non completata: ${error.message}` });
        }
      });
      const apriRicevuta = async (receipt) => {
        const id = receiptId(receipt);
        if (!id) {
          this.setState({ pdrReceiptErrore: "Ricevuta senza identificativo locale: non posso aprirne il dettaglio." });
          return;
        }
        this.setState({ pdrReceiptErrore: "", pdrReceiptOpen: { id, loading: true, receipt: null } });
        try {
          const payload = await this._json(`/api/pdr/receipts/${encodeURIComponent(id)}`);
          const detailed = payload.receipt || payload;
          this.setState((st) => ({
            pdrReceiptOpen: { id, loading: false, receipt: detailed },
            pdrReceipts: (st.pdrReceipts || []).map((item) => sameId(receiptId(item), id) ? { ...item, ...detailed } : item),
          }));
        } catch (error) {
          this.setState({ pdrReceiptErrore: `Dettaglio ricevuta non disponibile: ${error.message}`, pdrReceiptOpen: null });
        }
      };
      const scaricaRicevuta = async (receipt) => {
        const id = receiptId(receipt);
        if (!id) {
          this.setState({ pdrReceiptErrore: "Ricevuta senza identificativo locale: download non disponibile." });
          return;
        }
        this.setState({ pdrReceiptErrore: "" });
        try {
          const response = await fetch(`/api/pdr/receipts/${encodeURIComponent(id)}/download`, { credentials: "same-origin" });
          if (!response.ok) throw new Error(`download ${response.status}`);
          // Nei test (senza DOM) la chiamata resta osservabile senza cercare di
          // creare un anchor; nel browser il file originale viene scaricato.
          if (typeof document !== "undefined" && typeof URL !== "undefined" && URL.createObjectURL) {
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = url;
            link.download = String(receiptField(receipt, "filename", "original_filename", "file_name") || "ricevuta-pdr");
            link.style.display = "none";
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
          }
          this.setState({ pdrReceiptInfo: "Download della ricevuta avviato. Il contenuto resta importato e non verificato dal connettore." });
        } catch (error) {
          this.setState({ pdrReceiptErrore: `Download ricevuta non completato: ${error.message}` });
        }
      };
      const pdrReceiptReportOptions = remRows.map((record) => ({ id: String(record.id), label: `${record.rif} · ${record.tipo}` }));
      const pdrReceiptReportId = this.state.pdrReceiptReportId || "";
      const pdrReceiptRows = (this.state.pdrReceipts || [])
        .filter((receipt) => {
          const linkedReport = receiptField(receipt, "report_id", "remit_report_id");
          return !linkedReport || !pdrReceiptReportId || sameId(linkedReport, pdrReceiptReportId);
        })
        .map((receipt) => {
          const outcome = String(receiptField(receipt, "outcome", "status") || "unknown");
          const visual = outcomeStyle[outcome] || outcomeStyle.unknown;
          return {
            ...receipt,
            id: receiptId(receipt),
            filename: String(receiptField(receipt, "filename", "original_filename", "file_name") || "ricevuta senza nome"),
            outcomeLabel: visual.label,
            outcomeBg: visual.bg,
            outcomeFg: visual.fg,
            source: sourceLabel[receiptField(receipt, "source")] || String(receiptField(receipt, "source") || "Fonte non indicata"),
            loadCode: String(receiptField(receipt, "load_code", "pdr_load_code", "reference") || "—"),
            reportedAt: String(receiptField(receipt, "reported_at", "published_at", "created_at") || "—"),
            detail: String(receiptField(receipt, "detail", "error_detail", "message", "notes") || "Nessun dettaglio dichiarato."),
            provenance: "Importata manualmente · non verificata dal connettore",
            view: () => apriRicevuta(receipt),
            download: () => scaricaRicevuta(receipt),
          };
        });
      const pdrReceiptOpenRow = this.state.pdrReceiptOpen?.receipt
        ? pdrReceiptRows.find((row) => sameId(row.id, receiptId(this.state.pdrReceiptOpen.receipt)))
          || (() => {
            const receipt = this.state.pdrReceiptOpen.receipt;
            const outcome = String(receiptField(receipt, "outcome", "status") || "unknown");
            const visual = outcomeStyle[outcome] || outcomeStyle.unknown;
            return {
              filename: String(receiptField(receipt, "filename", "original_filename", "file_name") || "ricevuta senza nome"),
              outcomeLabel: visual.label,
              outcomeBg: visual.bg,
              outcomeFg: visual.fg,
              source: sourceLabel[receiptField(receipt, "source")] || String(receiptField(receipt, "source") || "Fonte non indicata"),
              loadCode: String(receiptField(receipt, "load_code", "pdr_load_code", "reference") || "—"),
              reportedAt: String(receiptField(receipt, "reported_at", "published_at", "created_at") || "—"),
              detail: String(receiptField(receipt, "detail", "error_detail", "message", "notes") || "Nessun dettaglio dichiarato."),
            };
          })()
        : null;

      const doLogin = async () => {
        const email = (this.state.loginEmail || "").trim();
        await this._apriSessione(email);
      };
      const logout = async () => {
        // La coda di sincronizzazione va svuotata PRIMA di chiudere: il
        // debounce di 250 ms fa sì che l'ultima modifica sia spesso ancora in
        // attesa, e _reimpostaDopoLogout la butterebbe senza avvisare.
        try { await this._flush(); } catch (_) { /* si esce comunque */ }
        fetch("/api/logout", { method: "POST" }).catch(() => {});
        this._reimpostaDopoLogout();
      };

      return {
        hasBack: !!backMap[s], goBack: go(backMap[s] || "hub"), hubCards,
        regKpis,
        // Stati vuoti: una tabella vuota senza spiegazione fa sembrare rotta
        // un'applicazione che sta solo aspettando il primo dato.
        remVuoto: !remRows.length, edgVuoto: !edgRows.length,
        pdrVuoto: !remRows.length, goRemit: go("remit"),
        theme, themeLabel: theme === "dark" ? "chiaro" : "scuro",
        primC: p.colorePrimario ?? "#0E5A75", accC: p.coloreAccento ?? "#2FA37C",
        loggedIn: s !== "login", screenLogin: s === "login", screenHub: s === "hub", screenModuli: s === "moduli", screenDash: s === "dash", screenConfig: s === "config", screenCfgSis: s === "cfgSis", screenCfgImp: s === "cfgImp", screenNomine: s === "nomine", screenBilancio: s === "bilancio", screenCapacita: s === "capacita", screenStoccaggio: s === "stoccaggio", screenReport: s === "report", screenRemit: s === "remit", screenPdr: s === "pdr",
        remAcer: cfg.acer || "da configurare",
        remAcerVal: typeof cfg.acer === "string" ? cfg.acer : "",
        setRemAcer: (e) => this.setSilent((st) => ({ cfg: { ...st.cfg, acer: cap(e.target.value, 12) } })),
        remKpis, remRows, addRem, remCaricamento: !!this.state.remCaricamento, remErrore: this.state.remErrore, remInfo: this.state.remInfo,
        remAuditOpen: !!this.state.remAudit, remAuditTitle: this.state.remAudit?.title ?? "", remAuditLoading: !!this.state.remAudit?.loading, remAuditEvents: (this.state.remAudit?.events ?? []).map((ev) => ({
          quando: String(ev.occurred_at || "").replace("T", " ").slice(0, 19),
          azione: REM_EVENTI[ev.event_type] || ev.event_type,
          transizione: `${ev.from_status || "—"} → ${ev.to_status || "—"}`,
          attore: ev.actor || "—",
          // `detail` è un oggetto: interpolarlo stamperebbe [object Object]
          impronta: String(ev.hash || "").slice(0, 12) + "…",
        })), closeRemAudit: () => this.setState({ remAudit: null }),
        remTipo: this.state.remTipo, remAzione: this.state.remAzione, remRif: this.state.remRif, remData: this.state.remData, remPunto: this.state.remPunto, remControparte: this.state.remControparte, remControparteTipo: this.state.remControparteTipo, remLato: this.state.remLato, remQta: this.state.remQta, remUnita: this.state.remUnita, remPrezzo: this.state.remPrezzo, remValuta: this.state.remValuta, remCapacita: this.state.remCapacita,
        remContractId: this.state.remContractId, remContractDate: this.state.remContractDate, remContractType: this.state.remContractType, remCommodity: this.state.remCommodity, remDeliveryStart: this.state.remDeliveryStart, remDeliveryEnd: this.state.remDeliveryEnd, remSettlement: this.state.remSettlement, remMarketplaceTipo: this.state.remMarketplaceTipo, remMarketplaceId: this.state.remMarketplaceId, remTransactionAt: this.state.remTransactionAt, remTransactionId: this.state.remTransactionId,
        setRemTipo: (e) => this.setSilent({ remTipo: e.target.value }),
        setRemAzione: (e) => this.setSilent({ remAzione: e.target.value }),
        setRemRif: (e) => this.setSilent({ remRif: e.target.value }),
        setRemData: (e) => this.setSilent({ remData: e.target.value }),
        setRemPunto: (e) => this.setSilent({ remPunto: e.target.value }),
        setRemControparte: (e) => this.setSilent({ remControparte: e.target.value }),
        setRemControparteTipo: (e) => this.setSilent({ remControparteTipo: e.target.value }),
        setRemLato: (e) => this.setSilent({ remLato: e.target.value }),
        setRemQta: (e) => this.setSilent({ remQta: e.target.value }),
        setRemUnita: (e) => this.setSilent({ remUnita: e.target.value }),
        setRemPrezzo: (e) => this.setSilent({ remPrezzo: e.target.value }),
        setRemValuta: (e) => this.setSilent({ remValuta: e.target.value }),
        setRemCapacita: (e) => this.setSilent({ remCapacita: e.target.value }),
        setRemContractId: (e) => this.setSilent({ remContractId: e.target.value }),
        setRemContractDate: (e) => this.setSilent({ remContractDate: e.target.value }),
        setRemContractType: (e) => this.setSilent({ remContractType: e.target.value }),
        setRemCommodity: (e) => this.setSilent({ remCommodity: e.target.value }),
        setRemDeliveryStart: (e) => this.setSilent({ remDeliveryStart: e.target.value }),
        setRemDeliveryEnd: (e) => this.setSilent({ remDeliveryEnd: e.target.value }),
        setRemSettlement: (e) => this.setSilent({ remSettlement: e.target.value }),
        setRemMarketplaceTipo: (e) => this.setSilent({ remMarketplaceTipo: e.target.value }),
        setRemMarketplaceId: (e) => this.setSilent({ remMarketplaceId: e.target.value }),
        setRemTransactionAt: (e) => this.setSilent({ remTransactionAt: e.target.value }),
        setRemTransactionId: (e) => this.setSilent({ remTransactionId: e.target.value }),
        generaUti,
        doLogin, logout, goHub: go("hub"),
        loginEmail: this.state.loginEmail, loginPass: this.state.loginPass,
        loginErrore: this.state.loginErrore,
        setLoginEmail: (e) => this.setSilent({ loginEmail: e.target.value }),
        setLoginPass: (e) => this.setSilent({ loginPass: e.target.value }),
        loginKey: (e) => (e.key === "Enter" ? doLogin() : undefined),
        doSSO: () => { this.setState({ sso: "redirect" }); setTimeout(() => this.setState((st) => (st.sso ? { sso: "pick" } : {})), 1100); },
        ssoCancel: () => this.setState({ sso: null }),
        ssoPickMarco: async () => {
          if (!await this._apriSessione("m.rossi@azienda1.it")) return;
          this.setState({ sso: "auth" });
          this._timerSso = setTimeout(() => this.setState((st) => (st.sso ? { sso: null, screen: "hub" } : { sso: null })), 900);
        },
        ssoPickLaura: async () => {
          if (!await this._apriSessione("l.bianchi@azienda1.it")) return;
          this.setState({ sso: "auth" });
          this._timerSso = setTimeout(() => this.setState((st) => (st.sso ? { sso: null, screen: "hub" } : { sso: null })), 900);
        },
        ssoRedirect: this.state.sso === "redirect", ssoPickStep: this.state.sso === "pick", ssoAuth: this.state.sso === "auth", ssoOpen: !!this.state.sso,
        toggleTheme: () => { const t = theme === "dark" ? "light" : "dark"; store.setItem("vt-theme", t); this.setState({ theme: t }); },
        crumbs, moduli, kpis, days, cicli, rows, punti, notifiche, unitOpts, cicloOpts, cfgCards, servizi, logs,
        dashDate, dashTotNom: demoOn ? fmtN(dNom) : "0", dashCicloTxt: dashCiclo.txt, dashCicloBg: dashCiclo.bg, dashCicloFg: dashCiclo.fg,
        dashPrev: () => this.setState((st) => ({ dashOff: Math.max(st.dashOff - 1, -7) })),
        dashNext: () => this.setState((st) => ({ dashOff: Math.min(st.dashOff + 1, 7) })),
        dashNotToday: off !== 0, dashToday: () => this.setState({ dashOff: 0 }),
        saveConfig: () => this.setState({ saved: true }), savedOk: !!this.state.saved,
        utenti, addUser,
        wizOpen: !!wiz, wizStep1: wiz?.step === 1, wizStep2: wiz?.step === 2, wizStep3: wiz?.step === 3,
        wizSteps, wizPermOpts, wizClose: () => this.setState({ wiz: null }),
        wizNext: wStep(1), wizBack: wStep(-1), wizFinish, wizCanBack: wiz ? wiz.step > 1 : false,
        wizNome: wiz?.nome ?? "", wizCognome: wiz?.cognome ?? "", wizEmail: wiz?.email ?? "",
        wizSetNome: wSet("nome"), wizSetCognome: wSet("cognome"), wizSetEmail: wSet("email"),
        wizName: wName || "Nuovo utente", wizMail: wEmail, wizPermLabel: wiz?.perm === "up" ? "Lettura e scrittura" : "Solo lettura",
        wizInit: wiz ? ((wiz.nome[0] || "N") + (wiz.cognome[0] || "U")).toUpperCase() : "NU",
        addPunto, newPunto: this.state.newPunto,
        setNewPunto: (e) => this.setSilent({ newPunto: e.target.value }),
        newPuntoKey: (e) => { if (e.key === "Enter") addPunto(); },
        nomRows, addNomina, nomPunto: this.state.nomPunto, nomCiclo: this.state.nomCiclo, nomQta: this.state.nomQta,
        // silenziosi anche i select: la tendina mostra già il nuovo valore e
        // un re-render a metà interazione le farebbe perdere il focus
        setNomPunto: (e) => this.setSilent({ nomPunto: e.target.value }),
        edgTipo: this.state.edgTipo, edgTipoNomina: this.state.edgTipoNomina, edgRuoli,
        edgIdent: this.state.edgIdent, edgVersione: this.state.edgVersione,
        edgEmittente: this.state.edgEmittente, edgDestinatario: this.state.edgDestinatario,
        edgConto: this.state.edgConto, edgPunto: this.state.edgPunto, edgUnita: this.state.edgUnita,
        edgGiorno: this.state.edgGiorno, edgControparte: this.state.edgControparte,
        edgDirezione: this.state.edgDirezione, edgQta: this.state.edgQta,
        edgErrore: this.state.edgErrore, edgElencoErrore: this.state.edgElencoErrore,
        edgInfo: this.state.edgInfo, edgErrori: this.state.edgErroriCampo,
        edgRisposta: this.state.edgRisposta, edgRispostaErrore: this.state.edgRispostaErrore,
        edgRispostaInfo: this.state.edgRispostaInfo,
        edgRows, edgScostamenti, generaNomint, leggiNomres, setEdgTipo,
        edgRiscontri, importaAck, edgAck: this.state.edgAck,
        edgAckErrore: this.state.edgAckErrore, edgAckInfo: this.state.edgAckInfo,
        edgAckVuoto: !(this.state.edgRiscontriList || []).length,
        setEdgAck: (e) => this.setSilent({ edgAck: e.target.value }),
        setEdgTipoNomina: (e) => this.setSilent({ edgTipoNomina: e.target.value }),
        setEdgIdent: (e) => this.setSilent({ edgIdent: e.target.value }),
        setEdgVersione: (e) => this.setSilent({ edgVersione: e.target.value }),
        setEdgEmittente: (e) => this.setSilent({ edgEmittente: e.target.value }),
        setEdgDestinatario: (e) => this.setSilent({ edgDestinatario: e.target.value }),
        setEdgConto: (e) => this.setSilent({ edgConto: e.target.value }),
        setEdgPunto: (e) => this.setSilent({ edgPunto: e.target.value }),
        setEdgUnita: (e) => this.setSilent({ edgUnita: e.target.value }),
        setEdgGiorno: (e) => this.setSilent({ edgGiorno: e.target.value }),
        setEdgControparte: (e) => this.setSilent({ edgControparte: e.target.value }),
        setEdgDirezione: (e) => this.setSilent({ edgDirezione: e.target.value }),
        setEdgQta: (e) => this.setSilent({ edgQta: e.target.value }),
        setEdgRisposta: (e) => this.setSilent({ edgRisposta: e.target.value }),
        setNomCiclo: (e) => this.setSilent({ nomCiclo: e.target.value }),
        setNomQta: (e) => this.setSilent({ nomQta: e.target.value }),
        oreSbil, bilKpis, azioni, capRows, capChip, scadenze, stocKpis, stocCap, stocMov, stocServ, repFiles, repProg, repKpis, repCats,
        demoOn, demoToggle: () => this.setState((st) => ({ demoMode: !st.demoMode })),
        demoKBg: knob(demoOn).kBg, demoKX: knob(demoOn).kX,
        giornoGas, vuotoDash: !demoOn && this.state.nomList.length === 0,
        utenteNome: ident.nome, utenteIniziali: ident.iniziali, utenteAzienda: ident.azienda,
        saluto: (new Date().getHours() < 13 ? "Buongiorno" : new Date().getHours() < 18 ? "Buon pomeriggio" : "Buonasera") + ", " + ident.nomeSaluto,
        pdr: pdrProfile, pdrCaricamento: !!this.state.pdrCaricamento, pdrErrore: this.state.pdrErrore, pdrInfo: this.state.pdrInfo,
        pdrSetEnvironment: pdrSet("environment"), pdrSetChannel: pdrSet("channel"), pdrSetOperator: pdrSet("gme_operator_code"), pdrSetContract: pdrSet("pdr_contract_reference"), pdrSetRegisteredAcer: pdrSet("registered_acer_code"),
        pdrToggleTestAccess: pdrToggle("test_access_requested"), pdrToggleTwoFactor: pdrToggle("two_factor_ready"), salvaPdr,
        pdrEnvironment: pdrProfile.environment || "test", pdrChannel: pdrProfile.channel || "portal", pdrOperator: pdrProfile.gme_operator_code || "", pdrContract: pdrProfile.pdr_contract_reference || "", pdrRegisteredAcer: pdrProfile.registered_acer_code || "",
        pdrTestAccess: !!pdrProfile.test_access_requested, pdrTwoFactor: !!pdrProfile.two_factor_ready,
        pdrEndpoint: pdrProfile.environment === "production" ? "https://pdr.ipex.it" : "https://provepdr.ipex.it",
        pdrFeeAnnual: this.state.pdrFees ? `${this.state.pdrFees.external_upload_annual_eur} €/anno` : "—",
        pdrCheck: this.state.pdrCheck, pdrCheckOpen: !!this.state.pdrCheck, pdrCheckLoading: !!this.state.pdrCheck?.loading, pdrIssues: this.state.pdrCheck?.issues ?? [], pdrCheckReport: this.state.pdrCheck?.report_id ?? "",
        pdrRows: remRows, pdrSchemas: this.state.pdrSchemas || [], goPdr: go("pdr"),
        pdrReceiptReportOptions, pdrReceiptReportId, pdrReceiptRows,
        pdrReceiptCaricamento: !!this.state.pdrReceiptCaricamento, pdrReceiptImporting: !!this.state.pdrReceiptImporting,
        pdrReceiptErrore: this.state.pdrReceiptErrore, pdrReceiptInfo: this.state.pdrReceiptInfo,
        pdrReceiptFileName: this.state.pdrReceiptFileName || "Nessun file selezionato",
        pdrReceiptOutcome: this.state.pdrReceiptOutcome, pdrReceiptSource: this.state.pdrReceiptSource,
        pdrReceiptLoadCode: this.state.pdrReceiptLoadCode, pdrReceiptReportedAt: this.state.pdrReceiptReportedAt, pdrReceiptDetailText: this.state.pdrReceiptDetailText,
        pdrReceiptOutcomes: [
          { value: "unknown", label: "Non classificato" },
          { value: "pdr_accepted", label: "PDR · Accept" },
          { value: "pdr_rejected", label: "PDR · Reject" },
          { value: "pdr_partial", label: "PDR · Partial" },
          { value: "acer_transmitted", label: "ACER · trasmesso" },
          { value: "acer_accepted", label: "ACER · accettato" },
          { value: "acer_rejected", label: "ACER · respinto" },
          { value: "technical_retry", label: "Ritentare tecnicamente" },
        ],
        pdrReceiptSources: [
          { value: "pdr", label: "PDR GME" },
          { value: "acer", label: "Ritorno ACER" },
        ],
        pdrReceiptSetReport: selezionaRicevuteReport, pdrReceiptSetFile: setPdrReceiptFile,
        pdrReceiptSetOutcome: pdrReceiptSet("pdrReceiptOutcome"), pdrReceiptSetSource: pdrReceiptSet("pdrReceiptSource"),
        pdrReceiptSetLoadCode, pdrReceiptSetReportedAt: pdrReceiptSet("pdrReceiptReportedAt"), pdrReceiptSetDetail: pdrReceiptSet("pdrReceiptDetailText"),
        importaRicevuta, pdrReceiptEmpty: !!pdrReceiptReportId && !this.state.pdrReceiptCaricamento && pdrReceiptRows.length === 0,
        pdrReceiptOpen: !!this.state.pdrReceiptOpen, pdrReceiptOpenLoading: !!this.state.pdrReceiptOpen?.loading, pdrReceiptOpenRow, closePdrReceipt: () => this.setState({ pdrReceiptOpen: null }),
      };
    }
  }

  if (typeof window !== "undefined") window.VettoreApp = App;
  if (typeof module !== "undefined" && module.exports) module.exports = { App };

  if (typeof document !== "undefined" && document.getElementById("app")) {
    const app = new App();
    fetch("/api/state")
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((saved) => {
        const sessioneRipristinata = app.idrata(saved);
        VT.mount(document.getElementById("app"), document.getElementById("app-template"), app);
        if (sessioneRipristinata) app._caricaWorkspaceRegolatorio();
      });
  }
})();
