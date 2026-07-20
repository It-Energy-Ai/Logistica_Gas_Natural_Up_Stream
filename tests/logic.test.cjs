// Test della logica frontend portata dal design (node --test, nessuna dipendenza).
const { test } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const { App } = require(path.join(__dirname, "..", "app", "static", "logic.js"));

const ev = (value) => ({ target: { value } });

// In Node la fetch verso URL relativi fallisce sempre e il retry della sync
// terrebbe vivo il processo: stub che risponde sempre 200.
const FETCH_OK = async () => ({ ok: true, status: 200, text: async () => "" });
global.fetch = FETCH_OK;

test("stato iniziale: schermata di login", () => {
  const app = new App();
  const v = app.renderVals();
  assert.equal(v.screenLogin, true);
  assert.equal(v.loggedIn, false);
  assert.equal(v.primC, "#0E5A75");
});

test("avvio pulito: niente scenografia, data reale, banner attivo", () => {
  const app = new App();
  app.setState({ screen: "dash" });
  const v = app.renderVals();
  assert.equal(v.demoOn, false);
  assert.equal(v.kpis[0].value, "—");
  assert.deepEqual(v.days, []);
  assert.deepEqual(v.rows, []);
  assert.equal(v.nomRows.length, 0);
  assert.equal(v.vuotoDash, true);
  const oggi = new Date();
  const atteso = String(oggi.getDate()).padStart(2, "0") + "/" + String(oggi.getMonth() + 1).padStart(2, "0") + "/" + oggi.getFullYear();
  assert.equal(v.giornoGas, atteso);
  assert.equal(v.dashDate, atteso);
  assert.equal(v.capChip, "0 contratti");
  assert.ok(v.servizi.every((sv) => sv.stato === "Da collegare"));
  assert.equal(v.dashTotNom, "0"); // regressione: il totale demo non deve trapelare
});

test("modalità demo: l'interruttore popola la scenografia del canvas", () => {
  const app = new App();
  app.setState({ screen: "dash" });
  app.renderVals().demoToggle();
  const v = app.renderVals();
  assert.equal(v.demoOn, true);
  assert.equal(v.kpis[0].value, "12.480");
  assert.equal(v.days.length, 14);
  assert.equal(v.rows.length, 5);
  assert.equal(v.nomRows.length, 3); // nomine di scena, non salvate
  assert.equal(v.giornoGas, "17/07/2026");
  assert.equal(v.vuotoDash, false);
  assert.equal(v.capChip, "5 contratti");
  assert.ok("demoMode" in app._pending, "demoMode persistito");
  clearTimeout(app._syncTimer);
  // le nomine demo NON entrano nello stato reale
  assert.equal(app.state.nomList.length, 0);
});

test("identità derivata dall'email di login", () => {
  const app = new App();
  const v = app.renderVals();
  v.setLoginEmail(ev("davide.bellini@itenergy.ai"));
  app.renderVals().doLogin();
  const v2 = app.renderVals();
  assert.equal(v2.utenteNome, "Davide Bellini");
  assert.equal(v2.utenteIniziali, "DB");
  assert.equal(v2.utenteAzienda, "Itenergy");
  assert.ok(v2.saluto.endsWith("Davide"));
  clearTimeout(app._syncTimer);
});

test("navigazione e breadcrumb", () => {
  const app = new App();
  app.setState({ screen: "dash" });
  const v = app.renderVals();
  assert.equal(v.screenDash, true);
  assert.equal(v.loggedIn, true);
  assert.deepEqual(v.crumbs.map((c) => c.label), ["Moduli", "Logistica Gas", "Dashboard"]);
  assert.equal(v.hasBack, true);
  v.goBack();
  assert.equal(app.renderVals().screenModuli, true);
});

test("dashboard: giorno gas e KPI si muovono con offset", () => {
  const app = new App();
  app.setState({ screen: "dash", demoMode: true });
  let v = app.renderVals();
  assert.equal(v.dashDate, "17/07/2026");
  assert.equal(v.dashNotToday, false);
  assert.equal(v.kpis[0].value, "12.480");
  v.dashPrev();
  v = app.renderVals();
  assert.equal(v.dashDate, "16/07/2026");
  assert.equal(v.dashNotToday, true);
  assert.equal(v.kpis[0].value, "12.220");
  v.dashToday();
  assert.equal(app.renderVals().dashDate, "17/07/2026");
  // il limite è ±7 giorni
  for (let i = 0; i < 20; i++) app.renderVals().dashNext();
  assert.equal(app.state.dashOff, 7);
});

test("nomina: invio aggiunge in testa con stato Inviata e sincronizza", () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  let v = app.renderVals();
  v.setNomPunto(ev("Passo Gries"));
  v = app.renderVals();
  v.setNomQta(ev("750"));
  app.renderVals().addNomina();
  const rows = app.renderVals().nomRows;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].punto, "Passo Gries");
  assert.equal(rows[0].qta, "750");
  assert.equal(rows[0].stato, "Inviata");
  assert.ok("nomList" in app._pending, "nomList in attesa di sync col backend");
  assert.equal(app.state.nomQta, "");
});

test("wizard utente: anagrafica, privilegi, conferma", () => {
  const app = new App();
  app.setState({ screen: "cfgSis", demoMode: true });
  app.renderVals().addUser();
  let v = app.renderVals();
  assert.equal(v.wizOpen, true);
  assert.equal(v.wizStep1, true);
  v.wizSetNome(ev("Anna"));
  app.renderVals().wizSetCognome(ev("Ferrari"));
  v = app.renderVals();
  assert.equal(v.wizMail, "a.ferrari@azienda1.it"); // email generata
  v.wizNext();
  v = app.renderVals();
  assert.equal(v.wizStep2, true);
  v.wizPermOpts[1].go(); // Lettura e scrittura
  app.renderVals().wizNext();
  v = app.renderVals();
  assert.equal(v.wizStep3, true);
  assert.equal(v.wizName, "Anna Ferrari");
  assert.equal(v.wizPermLabel, "Lettura e scrittura");
  v.wizFinish();
  v = app.renderVals();
  assert.equal(v.wizOpen, false);
  const nuovo = v.utenti.find((u) => u.name === "Anna Ferrari");
  assert.ok(nuovo, "utente aggiunto alla lista");
  assert.equal(app.state.users.wu1, "up");
});

test("punti di consegna: aggiunta, spegnimento, eliminazione", () => {
  const app = new App();
  app.setState({ screen: "cfgImp" });
  let v = app.renderVals();
  assert.equal(v.punti.length, 6);
  v.setNewPunto(ev("ReMi 34521405 · Brescia Est"));
  app.renderVals().addPunto();
  v = app.renderVals();
  assert.equal(v.punti.length, 7);
  assert.equal(v.punti[6].name, "ReMi 34521405 · Brescia Est");
  assert.equal(v.punti[6].on, true);
  // spegni il PSV
  v.punti[0].go();
  v = app.renderVals();
  assert.equal(v.punti[0].on, false);
  // elimina un punto base -> finisce tra i nascosti
  v.punti[1].removeP();
  v = app.renderVals();
  assert.equal(v.punti.length, 6);
  assert.deepEqual(app.state.hiddenPunti, ["gries"]);
  // elimina il punto aggiunto -> rimosso dagli extra
  const extra = v.punti.find((p) => p.name.includes("Brescia"));
  extra.removeP();
  assert.equal(app.renderVals().punti.length, 5);
  assert.equal(app.state.extraPunti.length, 0);
});

test("configurazione: toggle, segmenti e salvataggio", () => {
  const app = new App();
  app.setState({ screen: "cfgImp" });
  let v = app.renderVals();
  v.notifiche[2].go(); // attiva Report giornaliero PDF
  v = app.renderVals();
  assert.equal(v.notifiche[2].on, true);
  assert.equal(v.savedOk, false);
  v.unitOpts[1].go(); // Smc
  v = app.renderVals();
  assert.equal(app.state.cfg.unit, "Smc");
  v.saveConfig();
  assert.equal(app.renderVals().savedOk, true);
});

test("report: filtro per categoria", () => {
  const app = new App();
  app.setState({ screen: "report", demoMode: true });
  let v = app.renderVals();
  assert.equal(v.repFiles.length, 8);
  v.repCats[2].go(); // Regolatori
  v = app.renderVals();
  assert.equal(v.repFiles.length, 2);
  assert.ok(v.repFiles.every((f) => f.tag === "Regolatorio"));
});

test("GME: verifica credenziali e download automatico", () => {
  const app = new App();
  app.setState({ screen: "cfgSis" });
  let v = app.renderVals();
  assert.equal(v.gmeOk, false);
  v.verifyGme();
  v = app.renderVals();
  assert.equal(v.gmeOk, true);
  v.gmeToggle();
  assert.equal(app.state.gmeAuto, false);
  assert.ok("gmeOk" in app._pending && "gmeAuto" in app._pending);
});

test("tema: toggle chiaro/scuro", () => {
  const app = new App();
  let v = app.renderVals();
  assert.equal(v.theme, "light");
  v.toggleTheme();
  v = app.renderVals();
  assert.equal(v.theme, "dark");
  assert.equal(v.themeLabel, "chiaro");
});

test("utenti: privilegi e disabilitazione", () => {
  const app = new App();
  app.setState({ screen: "cfgSis", demoMode: true });
  let v = app.renderVals();
  assert.equal(v.utenti.length, 3);
  v.utenti[2].opts[0].go(); // Giulio Verdi -> solo lettura
  assert.equal(app.state.users.gverdi, "ro");
  app.renderVals().utenti[2].togDis();
  v = app.renderVals();
  assert.equal(v.utenti[2].disLabel, "Riabilita");
  assert.equal(v.utenti[2].rowOp, 0.45);
});

test("sync: solo chiavi persistite finiscono in _pending", () => {
  const app = new App();
  app.setState({ screen: "dash" }); // screen non è persistito
  assert.deepEqual(app._pending, {});
  app.renderVals().dashPrev(); // dashOff non è persistito
  assert.deepEqual(app._pending, {});
});

test("login: campi controllati e Invio", () => {
  const app = new App();
  let v = app.renderVals();
  v.setLoginEmail(ev("davide@itenergy.ai"));
  assert.equal(app.state.loginEmail, "davide@itenergy.ai");
  app.renderVals().loginKey({ key: "Enter" });
  v = app.renderVals();
  assert.equal(v.screenHub, true);
  assert.equal(app.state.loginPass, ""); // password azzerata dopo l'accesso
});

test("sync: su errore di rete la patch resta in coda E riprogramma un retry", async () => {
  const app = new App();
  app.state.gmeOk = true;
  global.fetch = async () => { throw new Error("rete giù"); };
  app._pending = { gmeOk: true };
  const timerPrima = app._syncTimer;
  await app._flush();
  assert.deepEqual(app._pending, { gmeOk: true }); // ri-accodata
  assert.notEqual(app._syncTimer, timerPrima); // un NUOVO retry è stato programmato
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: su 5xx la patch resta in coda e si ritenta (non scartata come il 422)", async () => {
  const app = new App();
  app.state.gmeOk = true;
  global.fetch = async () => ({ ok: false, status: 503, text: async () => "" });
  app._pending = { gmeOk: true };
  const timerPrima = app._syncTimer;
  await app._flush();
  assert.deepEqual(app._pending, { gmeOk: true });
  assert.notEqual(app._syncTimer, timerPrima);
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: il retry non riporta indietro un valore già superato", async () => {
  const app = new App();
  app.state.gmeOk = false; // valore CORRENTE
  global.fetch = async () => ({ ok: false, status: 503, text: async () => "" });
  app._pending = { gmeOk: true }; // snapshot vecchio in volo
  await app._flush();
  // _riaccoda deve ripartire dallo stato corrente, non dallo snapshot inviato
  assert.equal(app._pending.gmeOk, false);
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: 401 sospende (niente loop), il login riprende e svuota la coda", async () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  app.state.gmeOk = true; // valore corrente che _riaccoda deve conservare
  global.fetch = async () => ({ ok: false, status: 401, text: async () => "" });
  app._pending = { gmeOk: true };
  const timerPrima = app._syncTimer;
  await app._flush();
  assert.equal(app.state.screen, "login");
  assert.deepEqual(app._pending, { gmeOk: true }); // coda conservata
  assert.equal(app._sospesa, true);
  assert.equal(app._syncTimer, timerPrima); // NESSUN nuovo retry programmato
  // il login riattiva la sync e svuota la coda
  let inviate = null;
  global.fetch = async (url, opts) => {
    if (url === "/api/state" && opts && opts.method === "PUT") inviate = JSON.parse(opts.body);
    return { ok: true, status: 200, text: async () => "" };
  };
  await app.login("operazioni@gasadriatica.it");
  assert.equal(app._sospesa, false);
  await new Promise((r) => setTimeout(r, 300)); // debounce 250ms
  assert.deepEqual(inviate, { gmeOk: true });
  assert.deepEqual(app._pending, {});
  global.fetch = FETCH_OK;
});

test("sync: 422 scarta la patch senza ritentare", async () => {
  const app = new App();
  global.fetch = async () => ({ ok: false, status: 422, text: async () => "chiavi non valide" });
  app._pending = { gmeOk: true };
  await app._flush();
  assert.deepEqual(app._pending, {});
  global.fetch = FETCH_OK;
});

test("boot: idratazione dallo stato salvato separa email e salta il login", () => {
  const app = new App();
  app.idrata({
    email: "operazioni@gasadriatica.it",
    nomList: [{ punto: "PSV", ciclo: "R4", qta: "500", stato: "Inviata" }],
    demoMode: true,
  });
  assert.equal(app.state.screen, "hub"); // sessione presente → niente login
  assert.equal(app.state.utenteEmail, "operazioni@gasadriatica.it");
  assert.equal(app.state.nomList.length, 1);
  assert.equal(app.state.demoMode, true);
  assert.equal("email" in app.state, false); // 'email' non finisce nello stato
  const v = app.renderVals();
  assert.equal(v.utenteAzienda, "Gasadriatica");
  assert.equal(v.demoOn, true);
});

test("boot: senza sessione (payload nullo) resta al login", () => {
  const app = new App();
  app.idrata(null);
  assert.equal(app.state.screen, "login");
});

test("demo: i permessi di scena di Bianchi e Verdi sono sola lettura", () => {
  const app = new App();
  app.setState({ screen: "cfgSis", demoMode: true });
  const u = app.renderVals().utenti;
  const bianchi = u.find((x) => x.name === "Laura Bianchi");
  const verdi = u.find((x) => x.name === "Giulio Verdi");
  // opts[0] = Solo lettura, opts[1] = Lettura e scrittura; il "cur" evidenzia il default
  assert.equal(bianchi.opts[0].bg, "var(--surface)"); // sola lettura attiva
  assert.equal(bianchi.opts[1].bg, "transparent");
  assert.equal(verdi.opts[0].bg, "var(--surface)");
});

test("REMIT pulito: registro vuoto, KPI a zero, codice da configurare", () => {
  const app = new App();
  app.setState({ screen: "remit" });
  const v = app.renderVals();
  assert.equal(v.screenRemit, true);
  assert.equal(v.remRows.length, 0);
  assert.deepEqual(v.remKpis.map((k) => k.value), ["0", "0", "0"]);
  assert.equal(v.remAcer, "da configurare");
});

test("REMIT: registra una segnalazione, poi segnala l'invio (persistito)", () => {
  const app = new App();
  app.setState({ screen: "remit" });
  let v = app.renderVals();
  v.setRemRif(ev("PSV-2026-0142"));
  app.renderVals().setRemQta(ev("500"));
  app.renderVals().setRemPrezzo(ev("33,50"));
  app.renderVals().addRem();
  v = app.renderVals();
  assert.equal(v.remRows.length, 1);
  assert.equal(v.remRows[0].stato, "Da inviare");
  assert.equal(v.remRows[0].daInviare, true);
  assert.equal(v.remKpis[0].value, "1");
  assert.ok("remList" in app._pending, "remList persistita");
  v.remRows[0].invia();
  v = app.renderVals();
  assert.equal(v.remRows[0].stato, "Inviata");
  assert.equal(v.remKpis[1].value, "1");
  clearTimeout(app._syncTimer);
});

test("REMIT demo: scenografia in coda alle righe reali, mai salvata", () => {
  const app = new App();
  app.setState({ screen: "remit", demoMode: true });
  let v = app.renderVals();
  assert.equal(v.remRows.length, 4); // solo scena
  assert.equal(v.remAcer, "A0045821W.IT");
  assert.deepEqual(v.remKpis.map((k) => k.value), ["1", "2", "1"]);
  // una riga reale si mette DAVANTI alla scena
  v.setRemRif(ev("REALE-01"));
  app.renderVals().addRem();
  v = app.renderVals();
  assert.equal(v.remRows.length, 5);
  assert.equal(v.remRows[0].rif, "REALE-01");
  assert.equal(app.state.remList.length, 1); // la scena non entra nello stato
  clearTimeout(app._syncTimer);
});

test("REMIT: il codice ACER si salva in cfg e compare nel chip", () => {
  const app = new App();
  app.setState({ screen: "remit" });
  app.renderVals().setRemAcer(ev("A0099999X.IT"));
  const v = app.renderVals();
  assert.equal(app.state.cfg.acer, "A0099999X.IT");
  assert.equal(v.remAcer, "A0099999X.IT");
  assert.ok("cfg" in app._pending);
  clearTimeout(app._syncTimer);
});

test("limiti: nomina e punto troncati ai cap del backend", () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  app.renderVals().setNomQta(ev("9".repeat(300)));
  app.renderVals().addNomina();
  assert.equal(app.state.nomList[0].qta.length, 120);
  app.setState({ screen: "cfgImp" });
  app.renderVals().setNewPunto(ev("R".repeat(300)));
  app.renderVals().addPunto();
  assert.equal(app.state.extraPunti[0][0].length, 160);
});

