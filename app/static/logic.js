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
 *      build_frontend.py) invece di deckHover+setState.
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
    "reps", "gmeAuto", "gmeOk", "imports", "imported",
  ];

  // Tronca ai limiti accettati dai validatori del backend: un valore fuori
  // misura farebbe respingere l'intera patch (422) e perdere la persistenza.
  const cap = (s, n) => String(s ?? "").slice(0, n);

  class App {
    constructor() {
      this.state = {
        screen: "login", theme: store.getItem("vt-theme"), sso: null, dashOff: 0,
        loginEmail: "", loginPass: "",
        saved: false, imported: false, gmeAuto: true, gmeOk: false,
        users: { mricci: "up", lbianchi: "ro", gverdi: "ro" }, extraUsers: [], nextU: 1,
        wiz: null, disabled: {}, extraPunti: [], nextP: 1, newPunto: "", repCat: "tutti",
        nomList: [
          { punto: "PSV", ciclo: "R3", qta: "4.850", stato: "In corso" },
          { punto: "Passo Gries", ciclo: "R2", qta: "3.100", stato: "Confermata" },
          { punto: "Mazara del Vallo", ciclo: "R2", qta: "2.400", stato: "Confermata" },
        ],
        nomPunto: "PSV", nomCiclo: "R4", nomQta: "",
        reps: { rg: true, rs: true, rr: false },
        imports: [
          { time: "17/07 · 06:15", file: "sbilancio_g20260716.xml", rec: "1.440", esito: "OK" },
          { time: "16/07 · 06:12", file: "sbilancio_g20260715.xml", rec: "1.440", esito: "OK" },
        ],
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
          this._pending = { ...patch, ...this._pending };
          console.warn("sessione scaduta: torno al login, modifiche in coda");
          this.setState({ screen: "login" });
          return;
        }
        if (r.status === 422) {
          console.error("sync respinta dal server (dati non validi), patch scartata:", await r.text());
          return;
        }
        this._pending = { ...patch, ...this._pending };
        this._scheduleSync(3000);
      } catch (e) {
        this._pending = { ...patch, ...this._pending };
        console.warn("sync fallita (rete), ritento tra 3s", e);
        this._scheduleSync(3000);
      }
    }

    async login(email) {
      try {
        await fetch("/api/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
      } catch (e) { console.warn("login API non raggiungibile", e); }
    }

    go(s) { return () => this.setState({ screen: s }); }

    renderVals() {
      const p = PROPS;
      const theme = this.state.theme ?? ((p.tema ?? "chiaro") === "scuro" ? "dark" : "light");
      const s = this.state.screen;
      const go = (x) => this.go(x);
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
        if (!name) return {};
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
      ];
      const off = this.state.dashOff;
      const fmtN = (n) => n.toLocaleString("it-IT");
      const dNom = 12480 + off * 260;
      const dAlloc = Math.round(dNom * 0.969);
      const dSbil = -312 + off * 45;
      const psvStr = (33.45 + off * 0.35).toFixed(2).replace(".", ",");
      const bd = new Date(2026, 6, 17 + off);
      const dashDate = String(bd.getDate()).padStart(2, "0") + "/" + String(bd.getMonth() + 1).padStart(2, "0") + "/" + bd.getFullYear();
      const kpis = [
        { label: "Nominato — giorno gas", value: fmtN(dNom), unit: "MWh", delta: "+3,2% vs G−1", dBg: OK.bg, dFg: OK.fg },
        { label: "Allocato — G−1", value: fmtN(dAlloc), unit: "MWh", delta: "96,9% del nominato", dBg: "var(--surface2)", dFg: "var(--ink2)" },
        { label: "Sbilanciamento previsto", value: (dSbil < 0 ? "−" : "+") + fmtN(Math.abs(dSbil)), unit: "MWh", delta: dSbil < 0 ? "Posizione corta" : "Posizione lunga", dBg: dSbil < 0 ? WARN.bg : OK.bg, dFg: dSbil < 0 ? WARN.fg : OK.fg },
        { label: "PSV Day-Ahead", value: psvStr, unit: "€/MWh", delta: "−0,8% vs ieri", dBg: NEG.bg, dFg: NEG.fg },
      ];
      const dashCiclo = off === 0 ? { txt: "Ciclo R3 in corso · chiude 14:00", bg: "color-mix(in oklab,var(--prim) 10%,transparent)", fg: "var(--primText)" }
        : off < 0 ? { txt: "Giorno chiuso · bilancio provvisorio", bg: "var(--surface2)", fg: "var(--ink2)" }
        : { txt: "Programmazione · nomine aperte", bg: RUN.bg, fg: RUN.fg };
      const raw = [["04", 62, 60], ["05", 65, 64], ["06", 58, 57], ["07", 71, 70], ["08", 74, 72], ["09", 69, 69], ["10", 77, 74], ["11", 60, 59], ["12", 55, 54], ["13", 79, 77], ["14", 83, 80], ["15", 76, 75], ["16", 87, 84], ["17", 89, 86]];
      const days = raw.map(([d, n, a], i) => ({ d, n: n + "%", a: a + "%", w: i === raw.length - 1 ? 700 : 500, lc: i === raw.length - 1 ? "var(--ink)" : "var(--ink3)" }));
      const cicli = [
        { name: "Nomina D−1", time: "ieri 14:00", stato: "Confermata", ...OK },
        { name: "Rinomina R1", time: "06:00", stato: "Confermata", ...OK },
        { name: "Rinomina R2", time: "10:00", stato: "Confermata", ...OK },
        { name: "Rinomina R3", time: "14:00", stato: "In corso", ...RUN },
        { name: "Rinomina R4", time: "17:00", stato: "In attesa", ...WAIT },
        { name: "Rinomina R5", time: "20:00", stato: "In attesa", ...WAIT },
      ];
      const rows = [
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
        enter: () => {}, leave: () => {},
        t1: "rotate(-7deg) translate(-12px,2px)",
        t2: "rotate(4deg) translate(10px,-2px)",
        t3: "translateY(7px) rotate(-1deg)",
        top: "none",
        shadow: "var(--shadow)",
      });
      const hubCards = [mkHub("lg", "LG", "Logistica Gas", "Nomine · Bilanciamento · Stoccaggio", "moduli"), mkHub("cfg", "CF", "Configurazione", "Utenti · Parametri · Notifiche", "config")];
      const cfgCards = [
        { title: "Sistema", desc: "Stato dei servizi collegati, ambiente e attività recenti.", stat: "3", statLabel: "servizi collegati", go: go("cfgSis") },
        { title: "Impostazioni", desc: "Anagrafica shipper, parametri di nomina, punti e notifiche.", stat: String(nAbil), statLabel: "punti abilitati", go: go("cfgImp") },
      ];
      const servizi = [
        { name: "API Snam Rete Gas", desc: "Nomine e rinomine", stato: "Operativo", ...OK },
        { name: "Feed PSV · GME", desc: "Prezzi day-ahead", stato: "Operativo", ...OK },
        { name: "SSO aziendale", desc: "Autenticazione utenti", stato: "Operativo", ...OK },
      ];
      const sysInfo = [["Versione", "4.2.1 · build 8842"], ["Ambiente", "Produzione"], ["Ultimo rilascio", "12/07/2026 · 03:40"], ["Regione dati", "EU-South · Milano"]].map(([k, v]) => ({ k, v }));
      const logs = [
        { time: "11:42", who: "M. Rossi", txt: "Rinomina R2 inviata su PSV" },
        { time: "10:15", who: "Sistema", txt: "Allocazioni G−1 ricevute da Snam" },
        { time: "09:58", who: "L. Bianchi", txt: "Modificata tolleranza di sbilanciamento" },
        { time: "06:02", who: "Sistema", txt: "Apertura giorno gas 17/07/2026" },
      ];
      const permOpt = (uk, val, label) => ({ label, go: () => this.setState((st) => ({ users: { ...st.users, [uk]: val } })), bg: this.state.users[uk] === val ? "var(--surface)" : "transparent", fg: this.state.users[uk] === val ? "var(--ink)" : "var(--ink2)", sh: this.state.users[uk] === val ? "0 1px 2px rgba(16,34,45,.12)" : "none" });
      const baseU = [["mricci", "MR", "Marco Rossi", "m.rossi@azienda1.it"], ["lbianchi", "LB", "Laura Bianchi", "l.bianchi@azienda1.it"], ["gverdi", "GV", "Giulio Verdi", "g.verdi@azienda1.it"]];
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
      const wEmail = wiz ? (wiz.email || ((wiz.nome ? wiz.nome[0].toLowerCase() + "." : "") + (wiz.cognome || "utente").toLowerCase() + "@azienda1.it")) : "";
      const wizFinish = () => this.setState((st) => {
        const n = st.nextU, k = "wu" + n;
        const init = ((st.wiz.nome[0] || "N") + (st.wiz.cognome[0] || "U")).toUpperCase();
        return { extraUsers: [...st.extraUsers, [k, init, cap(wName || "Nuovo utente " + n, 160), cap(wEmail, 160)]], users: { ...st.users, [k]: st.wiz.perm }, nextU: n + 1, wiz: null };
      });
      const impRows = this.state.imports.map((r) => ({ ...r, bg: OK.bg, fg: OK.fg }));
      const nomStatoC = { "Confermata": OK, "In corso": RUN, "Inviata": RUN, "In attesa": WAIT };
      const nomRows = this.state.nomList.map((r) => ({ ...r, bg: (nomStatoC[r.stato] || WAIT).bg, fg: (nomStatoC[r.stato] || WAIT).fg }));
      const addNomina = () => this.setState((st) => ({ nomList: [{ punto: cap(st.nomPunto, 120), ciclo: cap(st.nomCiclo, 120), qta: cap(st.nomQta || "500", 120), stato: "Inviata" }, ...st.nomList].slice(0, 500), nomQta: "" }));
      const oreSbil = [["04", 45], ["05", 28], ["06", -15], ["07", 62], ["08", 34], ["09", -48], ["10", 20], ["11", -25], ["12", 55], ["13", 12], ["14", -60], ["15", 38], ["16", -85], ["17", -87]].map(([h, v]) => ({ h, top: v > 0 ? Math.round(v * 0.9) + "%" : "0%", bot: v < 0 ? Math.round(-v * 0.9) + "%" : "0%", w: h === "17" ? 700 : 500, lc: h === "17" ? "var(--ink)" : "var(--ink3)" }));
      const bilKpis = [
        { label: "Posizione fisica prevista", value: "−312", unit: "MWh", delta: "Posizione corta", dBg: WARN.bg, dFg: WARN.fg },
        { label: "Posizione commerciale", value: "+95", unit: "MWh", delta: "Coperta su MGP", dBg: OK.bg, dFg: OK.fg },
        { label: "Esposizione stimata", value: "10.410", unit: "€", delta: "Prezzo sbil. 33,6 €/MWh", dBg: "var(--surface2)", dFg: "var(--ink2)" },
      ];
      const azioni = [
        { txt: "Acquisto 250 MWh su MGP-GAS", sub: "Sessione AGS · entro 12:30", stato: "Suggerita", ...RUN },
        { txt: "Rinomina R4 · +60 MWh su Gries", sub: "Apre alle 15:00", stato: "In valutazione", ...WAIT },
        { txt: "Erogazione stoccaggio +100 MWh", sub: "Confermata da Stogit", stato: "Eseguita", ...OK },
      ];
      const capRows = [
        { punto: "Passo Gries", tipo: "Continuo · annuale", conf: "3.500 MWh/g", uso: 88, scad: "30/09/2026", stato: "Attivo", ...OK },
        { punto: "Mazara del Vallo", tipo: "Continuo · annuale", conf: "2.600 MWh/g", uso: 92, scad: "30/09/2026", stato: "Attivo", ...OK },
        { punto: "ReMi 34521301 · Milano Ovest", tipo: "Uscita · annuale", conf: "1.600 MWh/g", uso: 93, scad: "30/09/2026", stato: "Attivo", ...OK },
        { punto: "Tarvisio", tipo: "Continuo · trimestrale", conf: "1.200 MWh/g", uso: 0, scad: "01/10/2026", stato: "In firma", ...WARN },
        { punto: "Stogit", tipo: "Stoccaggio · anno termico", conf: "180 GWh", uso: 61, scad: "31/03/2027", stato: "Attivo", ...OK },
      ].map((r) => ({ ...r, usoW: r.uso + "%", usoL: r.uso ? r.uso + "%" : "—" }));
      const scadenze = [
        { data: "05/08/2026", txt: "Asta PRISMA · capacità mensile settembre" },
        { data: "15/09/2026", txt: "Rinnovo capacità annuale Gries e Mazara" },
        { data: "30/09/2026", txt: "Chiusura anno termico 2025/26" },
      ];
      const stocKpis = [
        { label: "Spazio conferito", value: "180", unit: "GWh", delta: "Modulazione Uniforme", dBg: "var(--surface2)", dFg: "var(--ink2)" },
        { label: "Giacenza", value: "109,8", unit: "GWh", delta: "61% dello Spazio", dBg: OK.bg, dFg: OK.fg },
        { label: "Spazio residuo", value: "70,2", unit: "GWh", delta: "Disponibile per iniezione", dBg: OK.bg, dFg: OK.fg },
        { label: "Iniezione nominata · G+0", value: "+95", unit: "MWh", delta: "Ciclo diurno · conferma Stogit", dBg: RUN.bg, dFg: RUN.fg },
      ];
      const stocCap = [
        { tipo: "Capacità di Iniezione", conf: "1.150", fatt: "0,82", disp: "943", dispW: "82%", note: "Limite: Spazio residuo 70,2 GWh", barCol: "var(--acc)" },
        { tipo: "Capacità di Erogazione", conf: "1.600", fatt: "0,64", disp: "1.024", dispW: "64%", note: "Limite: Giacenza 109,8 GWh", barCol: "var(--prim)" },
      ];
      const stocMov = [["Lun", 95], ["Mar", 80], ["Mer", -45], ["Gio", 120], ["Ven", 60], ["Sab", -30], ["Dom", -70]].map(([h, v]) => ({ h, top: v > 0 ? Math.round(v / 1.4) + "%" : "0%", bot: v < 0 ? Math.round(-v / 1.4) + "%" : "0%" }));
      const stocServ = [
        { name: "Modulazione Uniforme", sub: "Stogit · Spazio 180 GWh · asta annuale", val: "61%", stato: "Attivo", ...OK },
        { name: "Modulazione di punta", sub: "Stogit · CE aggiuntiva 40 MWh/g", val: "54%", stato: "Attivo", ...OK },
        { name: "Prestazioni costanti · fast-cycle", sub: "Stogit · richiesta in corso d'anno", val: "—", stato: "In richiesta", ...WAIT },
      ];
      const repKpis = [
        { label: "Invii programmati", value: "3", unit: "attivi", delta: "Prossimo 06:30", dBg: RUN.bg, dFg: RUN.fg },
        { label: "Obblighi regolatori", value: "2", unit: "in scadenza", delta: "REMIT · ARERA", dBg: WARN.bg, dFg: WARN.fg },
        { label: "Costo sbilancio · YTD", value: "84,2", unit: "k€", delta: "−18% vs 2025", dBg: OK.bg, dFg: OK.fg },
      ];
      const repCat = this.state.repCat;
      const repCats = [["tutti", "Tutti"], ["op", "Operativi"], ["reg", "Regolatori"], ["mkt", "Mercato"]].map(([k, label]) => ({
        label, go: () => this.setState({ repCat: k }),
        bg: repCat === k ? "var(--prim)" : "transparent", fg: repCat === k ? "#fff" : "var(--ink2)",
        bd: repCat === k ? "var(--prim)" : "var(--line)",
      }));
      const allRep = [
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
      const repTrend = [["Feb", 46, 31], ["Mar", 52, 38], ["Apr", 44, 27], ["Mag", 58, 35], ["Giu", 49, 22], ["Lug", 61, 18]].map(([m, gen, costo]) => ({
        m, genH: Math.round((gen / 61) * 100) + "%", costoH: Math.round((costo / 61) * 100) + "%",
      }));
      const repProg = [["Bilancio giornaliero · 06:30", "rg"], ["Alert sbilanciamento", "rs"], ["Pacchetto regolatorio ARERA", "rr"]].map(([name, k]) => ({ name, go: () => this.setState((st) => ({ reps: { ...st.reps, [k]: !st.reps[k] } })), ...knob(this.state.reps[k]) }));
      const backMap = { moduli: "hub", dash: "moduli", config: "hub", cfgSis: "config", cfgImp: "config", nomine: "moduli", bilancio: "moduli", capacita: "moduli", stoccaggio: "moduli", report: "moduli" };

      const doLogin = () => {
        const email = (this.state.loginEmail || "").trim() || "m.rossi@azienda1.it";
        this.login(email);
        this.setState({ screen: "hub", loginPass: "" });
      };
      const logout = () => {
        fetch("/api/logout", { method: "POST" }).catch(() => {});
        this.setState({ screen: "login" });
      };

      return {
        hasBack: !!backMap[s], goBack: go(backMap[s] || "hub"), hubCards,
        theme, themeLabel: theme === "dark" ? "chiaro" : "scuro",
        primC: p.colorePrimario ?? "#0E5A75", accC: p.coloreAccento ?? "#2FA37C",
        loggedIn: s !== "login", screenLogin: s === "login", screenHub: s === "hub", screenModuli: s === "moduli", screenDash: s === "dash", screenConfig: s === "config", screenCfgSis: s === "cfgSis", screenCfgImp: s === "cfgImp", screenNomine: s === "nomine", screenBilancio: s === "bilancio", screenCapacita: s === "capacita", screenStoccaggio: s === "stoccaggio", screenReport: s === "report",
        doLogin, logout, goHub: go("hub"), goModuli: go("moduli"), goDash: go("dash"),
        loginEmail: this.state.loginEmail, loginPass: this.state.loginPass,
        setLoginEmail: (e) => this.setSilent({ loginEmail: e.target.value }),
        setLoginPass: (e) => this.setSilent({ loginPass: e.target.value }),
        loginKey: (e) => { if (e.key === "Enter") doLogin(); },
        doSSO: () => { this.setState({ sso: "redirect" }); setTimeout(() => this.setState((st) => (st.sso ? { sso: "pick" } : {})), 1100); },
        ssoCancel: () => this.setState({ sso: null }),
        ssoPick: () => { this.login("m.rossi@azienda1.it"); this.setState({ sso: "auth" }); setTimeout(() => this.setState({ sso: null, screen: "hub" }), 900); },
        ssoRedirect: this.state.sso === "redirect", ssoPickStep: this.state.sso === "pick", ssoAuth: this.state.sso === "auth", ssoOpen: !!this.state.sso,
        toggleTheme: () => { const t = theme === "dark" ? "light" : "dark"; store.setItem("vt-theme", t); this.setState({ theme: t }); },
        crumbs, moduli, kpis, days, cicli, rows, punti, notifiche, unitOpts, cicloOpts, cfgCards, servizi, sysInfo, logs,
        dashDate, dashTotNom: fmtN(dNom), dashCicloTxt: dashCiclo.txt, dashCicloBg: dashCiclo.bg, dashCicloFg: dashCiclo.fg,
        dashPrev: () => this.setState((st) => ({ dashOff: Math.max(st.dashOff - 1, -7) })),
        dashNext: () => this.setState((st) => ({ dashOff: Math.min(st.dashOff + 1, 7) })),
        dashNotToday: off !== 0, dashToday: () => this.setState({ dashOff: 0 }),
        saveConfig: () => this.setState({ saved: true }), savedOk: !!this.state.saved,
        utenti, addUser, impRows, importedOk: !!this.state.imported,
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
        oreSbil, bilKpis, azioni, capRows, scadenze, stocKpis, stocCap, stocMov, stocServ, repFiles, repProg, repKpis, repCats, repTrend,
        gmeOk: !!this.state.gmeOk, verifyGme: () => this.setState({ gmeOk: true }),
        gmeToggle: () => this.setState((st) => ({ gmeAuto: !st.gmeAuto })),
        gmeKBg: knob(this.state.gmeAuto).kBg, gmeKX: knob(this.state.gmeAuto).kX,
        importNow: () => { if (!this.state.imported) this.setState((st) => ({ imported: true, imports: [{ time: "17/07 · 11:58", file: "sbilancio_g20260717_prov.csv", rec: "288", esito: "OK" }, ...st.imports] })); },
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
        if (saved) app.state = { ...app.state, ...saved, screen: "hub" };
        VT.mount(document.getElementById("app"), document.getElementById("app-template"), app);
      });
  }
})();
