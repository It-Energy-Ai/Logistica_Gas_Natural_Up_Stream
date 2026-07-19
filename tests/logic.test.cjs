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

test("sync: su errore di rete la patch resta in coda e si ritenta", async () => {
  const app = new App();
  global.fetch = async () => { throw new Error("rete giù"); };
  app._pending = { gmeOk: true };
  await app._flush();
  assert.deepEqual(app._pending, { gmeOk: true }); // ri-accodata
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: 401 riporta al login conservando la coda", async () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  global.fetch = async () => ({ ok: false, status: 401, text: async () => "" });
  app._pending = { gmeOk: true };
  await app._flush();
  assert.equal(app.state.screen, "login");
  assert.deepEqual(app._pending, { gmeOk: true });
  clearTimeout(app._syncTimer);
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

test("imported è tra le chiavi persistite", () => {
  const app = new App();
  app.renderVals().importNow();
  assert.equal(app.state.imported, true);
  assert.ok("imported" in app._pending && "imports" in app._pending);
  clearTimeout(app._syncTimer);
});
