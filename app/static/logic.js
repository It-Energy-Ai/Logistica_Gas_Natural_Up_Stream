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

  const store = typeof localStorage !== "undefined" ? localStorage : { getItem: () => null, setItem: () => {} };

  // Chiavi di stato persistite sul backend (il resto è scenografia demo).
  const PERSIST = [
    "nomList", "cfg", "hiddenPunti", "extraPunti", "nextP",
    "users", "extraUsers", "disabled", "nextU",
    "reps", "gmeAuto", "gmeOk", "demoMode", "remList",
  ];

  // Tronca ai limiti accettati dai validatori del backend: un valore fuori
  // misura farebbe respingere l'intera patch (422) e perdere la persistenza.
  const cap = (s, n) => String(s ?? "").slice(0, n);

  class App {
    constructor() {
      this.state = {
        screen: "login", theme: store.getItem("vt-theme"), sso: null, dashOff: 0,
        loginEmail: "", loginPass: "", utenteEmail: "", demoMode: false,
        saved: false, gmeAuto: true, gmeOk: false,
        users: {}, extraUsers: [], nextU: 1,
        wiz: null, disabled: {}, extraPunti: [], nextP: 1, newPunto: "", repCat: "tutti",
        nomList: [],
        nomPunto: "PSV", nomCiclo: "R4", nomQta: "",
        remList: [], remTipo: "Standard · mercato organizzato", remRif: "", remQta: "", remPrezzo: "",
        reps: { rg: true, rs: true, rr: false },
        cfg: {
          psv: true, gries: true, mazara: true, remi: true, stogit: true, cavarzere: false,
          nEmail: true, nAlert: true, nReport: false, unit: "MWh", ciclo: "Intraday",
        },
      };
      this._pending = {};
      this._syncTimer = null;
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
      const patch = this._pending;
      this._pending = {};
      try {
        const r = await fetch("/api/state", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
        if (r.ok) return;
        if (r.status === 401) {
          // sospende la sync (niente loop di retry contro un 401 permanente):
          // login() la riattiva e svuota la coda conservata
          this._sospesa = true;
          this._riaccoda(patch);
          console.warn("sessione scaduta: torno al login, modifiche in coda");
          this.setState({ screen: "login" });
          return;
        }
        if (r.status === 422) {
          console.error("sync respinta dal server (dati non validi), patch scartata:", await r.text());
          return;
        }
        this._riaccoda(patch);
        this._scheduleSync(3000);
      } catch (e) {
        this._riaccoda(patch);
        console.warn("sync fallita (rete), ritento tra 3s", e);
        this._scheduleSync(3000);
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

    // Ritorna l'email COME NORMALIZZATA DAL SERVER (fonte di verità): è quella
    // che il boot rileggerà da /api/state, quindi l'identità mostrata resta
    // coerente dopo un refresh anche se l'input era vuoto o malformato.
    async login(email) {
      try {
        const r = await fetch("/api/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        this._sospesa = false;
        this._scheduleSync(); // svuota la coda accumulata durante la sessione scaduta
        const dati = await r.json().catch(() => ({}));
        return dati.email || email;
      } catch (e) {
        console.warn("login API non raggiungibile", e);
        return email;
      }
    }

    go(s) { return () => this.setState({ screen: s }); }

    // Idrata lo stato dal payload di /api/state: separa l'identità (email)
    // dallo stato persistito e, se c'è una sessione, salta il login.
    idrata(saved) {
      if (!saved) return;
      const { email, ...stato } = saved;
      this.state = { ...this.state, ...stato, utenteEmail: email || "", screen: "hub" };
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
        nomine: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Nomine & Programmazione" }],
        bilancio: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Bilanciamento" }],
        capacita: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Capacità & Contratti" }],
        stoccaggio: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Stoccaggio" }],
        report: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "Report & Analisi" }],
        remit: [{ label: "Moduli", t: "hub" }, { label: "Logistica Gas", t: "moduli" }, { label: "REMIT" }],
      })[s] || [];
      const crumbs = trail.map((c, i) => ({
        label: c.label, pre: i ? "›" : "",
        go: c.t ? go(c.t) : () => {}, cur: c.t ? "pointer" : "default",
        col: i === trail.length - 1 ? "var(--ink)" : "var(--ink2)",
        fw: i === trail.length - 1 ? 600 : 400,
      }));
      const OK = { bg: "rgba(47,163,124,.15)", fg: "#259372" };
      const RUN = { bg: "rgba(62,150,180,.16)", fg: "#3E96B4" };
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
        removeP: () => this.setState((st) => st.extraPunti.some((pp) => pp[2] === k)
          ? { extraPunti: st.extraPunti.filter((pp) => pp[2] !== k), saved: false }
          : { hiddenPunti: [...(st.hiddenPunti || []), k], saved: false }),
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
      const moduli = [
        { title: "Dashboard", desc: "Posizione del giorno gas, sbilanciamento e prezzi PSV a colpo d'occhio.", stat: "G+0", statLabel: "giorno gas corrente", primary: true, go: go("dash"), cursor: "pointer", border: "color-mix(in oklab, var(--prim) 40%, var(--line))" },
        { title: "Nomine & Programmazione", desc: "Invio e monitoraggio di nomine e rinomine sui punti della rete.", stat: "5", statLabel: "cicli oggi", primary: true, go: go("nomine"), cursor: "pointer", border: "var(--line)" },
        { title: "Bilanciamento", desc: "Posizione fisica e commerciale, esposizione e azioni correttive.", stat: "−312", statLabel: "MWh previsti", primary: true, go: go("bilancio"), cursor: "pointer", border: "var(--line)" },
        { title: "Capacità & Contratti", desc: "Capacità di trasporto conferite, contratti e scadenze.", stat: "8", statLabel: "contratti attivi", primary: true, go: go("capacita"), cursor: "pointer", border: "var(--line)" },
        { title: "Stoccaggio", desc: "Giacenza, iniezione ed erogazione sui servizi di stoccaggio.", stat: "61%", statLabel: "riempimento", primary: true, go: go("stoccaggio"), cursor: "pointer", border: "var(--line)" },
        { title: "Report & Analisi", desc: "Estrazioni, report regolatori e serie storiche esportabili.", stat: "12", statLabel: "report programmati", primary: true, go: go("report"), cursor: "pointer", border: "var(--line)" },
        { title: "REMIT · Segnalazioni", desc: "Registro delle transazioni e obblighi di reporting verso ACER.", stat: "1", statLabel: "da inviare", primary: true, go: go("remit"), cursor: "pointer", border: "var(--line)" },
      ];
      if (!demoOn) for (const m of moduli) m.stat = "—"; // i numeri delle card sono scenografia
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
      const hubCards = [mkHub("lg", "LG", "Logistica Gas", "Nomine · Bilanciamento · Stoccaggio", "moduli"), mkHub("cfg", "CF", "Configurazione", "Utenti · Parametri · Notifiche", "config")];
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
      const nomStatoC = { "Confermata": OK, "In corso": RUN, "Inviata": RUN, "In attesa": WAIT };
      // Le nomine demo compaiono DOPO quelle reali e non vengono mai salvate.
      const nomDemo = !demoOn ? [] : [
        { punto: "PSV", ciclo: "R3", qta: "4.850", stato: "In corso" },
        { punto: "Passo Gries", ciclo: "R2", qta: "3.100", stato: "Confermata" },
        { punto: "Mazara del Vallo", ciclo: "R2", qta: "2.400", stato: "Confermata" },
      ];
      const nomRows = [...this.state.nomList, ...nomDemo].map((r) => ({ ...r, bg: (nomStatoC[r.stato] || WAIT).bg, fg: (nomStatoC[r.stato] || WAIT).fg }));
      const addNomina = () => this.setState((st) => ({ nomList: [{ punto: cap(st.nomPunto, 120), ciclo: cap(st.nomCiclo, 120), qta: cap(st.nomQta || "500", 120), stato: "Inviata" }, ...st.nomList].slice(0, 500), nomQta: "" }));
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
      const backMap = { moduli: "hub", dash: "moduli", config: "hub", cfgSis: "config", cfgImp: "config", nomine: "moduli", bilancio: "moduli", capacita: "moduli", stoccaggio: "moduli", report: "moduli", remit: "moduli" };

      // --- REMIT: registro reale dell'utente + scenografia demo separata ---
      const remStatoC = { "Accettata": OK, "Inviata": RUN, "Da inviare": WAIT, "Respinta": NEG };
      const remDemo = !demoOn ? [] : [
        { rif: "MGP-GAS 17/07 · lotto 42", tipo: "Standard", qta: "250", prezzo: "33,45", stato: "Accettata" },
        { rif: "PSV-2026-0138 · bilaterale", tipo: "Non-standard", qta: "1.500", prezzo: "32,80", stato: "Inviata" },
        { rif: "MGP-GAS 16/07 · lotto 18", tipo: "Standard", qta: "400", prezzo: "33,61", stato: "Respinta" },
        { rif: "PSV-2026-0141 · bilaterale", tipo: "Non-standard", qta: "800", prezzo: "33,10", stato: "Da inviare" },
      ];
      const colora = (r) => ({ ...r, bg: (remStatoC[r.stato] || WAIT).bg, fg: (remStatoC[r.stato] || WAIT).fg });
      // le righe reali hanno l'azione "Segna inviata"; quelle demo sono solo scena
      const remRows = [
        ...this.state.remList.map((r, i) => ({
          ...colora(r), daInviare: r.stato === "Da inviare",
          invia: () => this.setState((st) => ({ remList: st.remList.map((x, j) => (j === i ? { ...x, stato: "Inviata" } : x)) })),
        })),
        ...remDemo.map((r) => ({ ...colora(r), daInviare: r.stato === "Da inviare", invia: () => {} })),
      ];
      const remN = (s2) => remRows.filter((r) => r.stato === s2).length;
      const remKpis = [
        { label: "Da inviare", value: String(remN("Da inviare")), unit: "in coda", delta: "standard: T+2", dBg: WAIT.bg, dFg: WAIT.fg },
        { label: "Inviate · mese", value: String(remN("Inviata") + remN("Accettata")), unit: "tramite RRM", delta: demoOn ? "flusso regolare" : "dal tuo registro", dBg: RUN.bg, dFg: RUN.fg },
        { label: "Respinte", value: String(remN("Respinta")), unit: "da correggere", delta: demoOn ? "verifica i campi" : "nessun esito negativo", dBg: remN("Respinta") ? NEG.bg : "var(--surface2)", dFg: remN("Respinta") ? NEG.fg : "var(--ink3)" },
      ];
      const addRem = () => this.setState((st) => ({
        remList: [{
          rif: cap((st.remRif || "").trim() || "(senza riferimento)", 120),
          tipo: st.remTipo.startsWith("Standard") ? "Standard" : "Non-standard",
          qta: cap(st.remQta || "\u2014", 120), prezzo: cap(st.remPrezzo || "\u2014", 120),
          stato: "Da inviare",
        }, ...st.remList].slice(0, 500),
        remRif: "", remQta: "", remPrezzo: "",
      }));

      const doLogin = () => {
        const email = (this.state.loginEmail || "").trim();
        // l'identità mostrata è quella normalizzata dal server (fonte di verità),
        // così un refresh la ripesca identica da /api/state
        this.login(email).then((confermata) => this.setState({ utenteEmail: confermata }));
        this.setState({ screen: "hub", loginPass: "", utenteEmail: email });
      };
      const logout = () => {
        fetch("/api/logout", { method: "POST" }).catch(() => {});
        this.setState({ screen: "login" });
      };

      return {
        hasBack: !!backMap[s], goBack: go(backMap[s] || "hub"), hubCards,
        theme, themeLabel: theme === "dark" ? "chiaro" : "scuro",
        primC: p.colorePrimario ?? "#0E5A75", accC: p.coloreAccento ?? "#2FA37C",
        loggedIn: s !== "login", screenLogin: s === "login", screenHub: s === "hub", screenModuli: s === "moduli", screenDash: s === "dash", screenConfig: s === "config", screenCfgSis: s === "cfgSis", screenCfgImp: s === "cfgImp", screenNomine: s === "nomine", screenBilancio: s === "bilancio", screenCapacita: s === "capacita", screenStoccaggio: s === "stoccaggio", screenReport: s === "report", screenRemit: s === "remit",
        remAcer: demoOn ? "A0045821W.IT" : (cfg.acer || "da configurare"),
        remAcerVal: typeof cfg.acer === "string" ? cfg.acer : "",
        setRemAcer: (e) => this.setSilent((st) => ({ cfg: { ...st.cfg, acer: cap(e.target.value, 63) } })),
        remKpis, remRows, addRem,
        remTipo: this.state.remTipo, remRif: this.state.remRif, remQta: this.state.remQta, remPrezzo: this.state.remPrezzo,
        setRemTipo: (e) => this.setSilent({ remTipo: e.target.value }),
        setRemRif: (e) => this.setSilent({ remRif: e.target.value }),
        setRemQta: (e) => this.setSilent({ remQta: e.target.value }),
        setRemPrezzo: (e) => this.setSilent({ remPrezzo: e.target.value }),
        doLogin, logout, goHub: go("hub"),
        loginEmail: this.state.loginEmail, loginPass: this.state.loginPass,
        setLoginEmail: (e) => this.setSilent({ loginEmail: e.target.value }),
        setLoginPass: (e) => this.setSilent({ loginPass: e.target.value }),
        loginKey: (e) => { if (e.key === "Enter") doLogin(); },
        doSSO: () => { this.setState({ sso: "redirect" }); setTimeout(() => this.setState((st) => (st.sso ? { sso: "pick" } : {})), 1100); },
        ssoCancel: () => this.setState({ sso: null }),
        ssoPick: () => { this.login("m.rossi@azienda1.it"); this.setState({ sso: "auth", utenteEmail: "m.rossi@azienda1.it" }); setTimeout(() => this.setState({ sso: null, screen: "hub" }), 900); },
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
        setNomCiclo: (e) => this.setSilent({ nomCiclo: e.target.value }),
        setNomQta: (e) => this.setSilent({ nomQta: e.target.value }),
        oreSbil, bilKpis, azioni, capRows, capChip, scadenze, stocKpis, stocCap, stocMov, stocServ, repFiles, repProg, repKpis, repCats,
        demoOn, demoToggle: () => this.setState((st) => ({ demoMode: !st.demoMode })),
        demoKBg: knob(demoOn).kBg, demoKX: knob(demoOn).kX,
        giornoGas, vuotoDash: !demoOn && this.state.nomList.length === 0,
        utenteNome: ident.nome, utenteIniziali: ident.iniziali, utenteAzienda: ident.azienda,
        saluto: (new Date().getHours() < 13 ? "Buongiorno" : new Date().getHours() < 18 ? "Buon pomeriggio" : "Buonasera") + ", " + ident.nomeSaluto,
        gmeOk: !!this.state.gmeOk, verifyGme: () => this.setState({ gmeOk: true }),
        gmeToggle: () => this.setState((st) => ({ gmeAuto: !st.gmeAuto })),
        gmeKBg: knob(this.state.gmeAuto).kBg, gmeKX: knob(this.state.gmeAuto).kX,
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
        app.idrata(saved);
        VT.mount(document.getElementById("app"), document.getElementById("app-template"), app);
      });
  }
})();
