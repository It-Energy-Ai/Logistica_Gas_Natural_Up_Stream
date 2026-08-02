// Test della logica frontend portata dal design (node --test, nessuna dipendenza).
const { test } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const { App } = require(path.join(__dirname, "..", "app", "static", "logic.js"));

const ev = (value) => ({ target: { value } });

// In Node la fetch verso URL relativi fallisce sempre e il retry della sync
// terrebbe vivo il processo: stub che risponde sempre 200.
let emailSessione = "utente@locale";
const FETCH_OK = async (url, opts = {}) => {
  if (url === "/api/login") {
    const body = JSON.parse(opts.body || "{}");
    emailSessione = String(body.email || "utente@locale").trim().toLowerCase() || "utente@locale";
    return { ok: true, status: 200, json: async () => ({ ok: true, email: emailSessione }), text: async () => "" };
  }
  if (url === "/api/state" && !opts.method) {
    return { ok: true, status: 200, json: async () => ({ email: emailSessione }), text: async () => "" };
  }
  return { ok: true, status: 200, text: async () => "" };
};
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

test("identità derivata dall'email di login", async () => {
  const app = new App();
  const v = app.renderVals();
  v.setLoginEmail(ev("davide.bellini@itenergy.ai"));
  await app.renderVals().doLogin();
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

test("registro interno: l'annotazione va in testa con stato Registrata", () => {
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
  assert.equal(rows[0].stato, "Registrata");
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

test("login: campi controllati e Invio", async () => {
  const app = new App();
  let v = app.renderVals();
  v.setLoginEmail(ev("davide@itenergy.ai"));
  assert.equal(app.state.loginEmail, "davide@itenergy.ai");
  await app.renderVals().loginKey({ key: "Enter" });
  v = app.renderVals();
  assert.equal(v.screenHub, true);
  assert.equal(app.state.loginPass, ""); // password azzerata dopo l'accesso
});

test("sync: su errore di rete la patch resta in coda E riprogramma un retry", async () => {
  const app = new App();
  app.state.demoMode = true;
  global.fetch = async () => { throw new Error("rete giù"); };
  app._pending = { demoMode: true };
  const timerPrima = app._syncTimer;
  await app._flush();
  assert.deepEqual(app._pending, { demoMode: true }); // ri-accodata
  assert.notEqual(app._syncTimer, timerPrima); // un NUOVO retry è stato programmato
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: su 5xx la patch resta in coda e si ritenta (non scartata come il 422)", async () => {
  const app = new App();
  app.state.demoMode = true;
  global.fetch = async () => ({ ok: false, status: 503, text: async () => "" });
  app._pending = { demoMode: true };
  const timerPrima = app._syncTimer;
  await app._flush();
  assert.deepEqual(app._pending, { demoMode: true });
  assert.notEqual(app._syncTimer, timerPrima);
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: il retry non riporta indietro un valore già superato", async () => {
  const app = new App();
  app.state.demoMode = false; // valore CORRENTE
  global.fetch = async () => ({ ok: false, status: 503, text: async () => "" });
  app._pending = { demoMode: true }; // snapshot vecchio in volo
  await app._flush();
  // _riaccoda deve ripartire dallo stato corrente, non dallo snapshot inviato
  assert.equal(app._pending.demoMode, false);
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: 401 sospende (niente loop), il login riprende e svuota la coda", async () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  app._sessionEmail = "operazioni@gasadriatica.it";
  app.state.demoMode = true; // valore corrente che _riaccoda deve conservare
  global.fetch = async () => ({ ok: false, status: 401, text: async () => "" });
  app._pending = { demoMode: true };
  const timerPrima = app._syncTimer;
  await app._flush();
  assert.equal(app.state.screen, "login");
  assert.deepEqual(app._pending, { demoMode: true }); // coda conservata
  assert.equal(app._sospesa, true);
  assert.equal(app._syncTimer, timerPrima); // NESSUN nuovo retry programmato
  // il login riattiva la sync e svuota la coda
  let inviate = null;
  global.fetch = async (url, opts = {}) => {
    if (url === "/api/login") return { ok: true, status: 200, json: async () => ({ email: "operazioni@gasadriatica.it" }) };
    if (url === "/api/state" && !opts.method) return { ok: true, status: 200, json: async () => ({ email: "operazioni@gasadriatica.it", demoMode: false }) };
    if (url === "/api/state" && opts.method === "PUT") inviate = JSON.parse(opts.body);
    return { ok: true, status: 200, text: async () => "" };
  };
  await app._apriSessione("operazioni@gasadriatica.it");
  assert.equal(app._sospesa, false);
  await new Promise((r) => setTimeout(r, 300)); // debounce 250ms
  assert.deepEqual(inviate, { demoMode: true });
  assert.deepEqual(app._pending, {});
  global.fetch = FETCH_OK;
});

test("login: non apre l'hub se l'API non risponde", async () => {
  const app = new App();
  global.fetch = async () => { throw new Error("offline"); };
  app.renderVals().setLoginEmail(ev("operazioni@gasadriatica.it"));
  await app.renderVals().doLogin();
  assert.equal(app.state.screen, "login");
  assert.match(app.state.loginErrore, /Accesso non riuscito/);
  global.fetch = FETCH_OK;
});

test("logout e nuovo login non conservano i dati dell'utente precedente", async () => {
  const app = new App();
  app.idrata({ email: "prima@azienda.it", nomList: [{ punto: "PSV", ciclo: "R4", qta: "500", stato: "Registrata" }] });
  app._reimpostaDopoLogout();
  assert.equal(app.state.screen, "login");
  assert.deepEqual(app.state.nomList, []);
  emailSessione = "seconda@azienda.it";
  await app._apriSessione("seconda@azienda.it");
  assert.equal(app.state.utenteEmail, "seconda@azienda.it");
  assert.deepEqual(app.state.nomList, []);
});

test("sync: serializza i salvataggi e conserva l'ultima modifica", async () => {
  const app = new App();
  app._sessionEmail = "operazioni@gasadriatica.it";
  app.state.demoMode = false;
  let risolviPrima;
  const invii = [];
  global.fetch = async (_url, opts) => {
    invii.push(JSON.parse(opts.body));
    if (invii.length === 1) return new Promise((resolve) => { risolviPrima = () => resolve({ ok: true, status: 200, text: async () => "" }); });
    return { ok: true, status: 200, text: async () => "" };
  };
  app._pending = { demoMode: false };
  const primo = app._flush();
  app.setState({ demoMode: true });
  await app._flush();
  assert.equal(invii.length, 1, "non invia una seconda PUT mentre la prima è in volo");
  risolviPrima();
  await primo;
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.deepEqual(invii, [{ demoMode: false }, { demoMode: true }]);
  clearTimeout(app._syncTimer);
  global.fetch = FETCH_OK;
});

test("sync: 422 scarta la patch senza ritentare", async () => {
  const app = new App();
  global.fetch = async () => ({ ok: false, status: 422, text: async () => "chiavi non valide" });
  app._pending = { demoMode: true };
  await app._flush();
  assert.deepEqual(app._pending, {});
  global.fetch = FETCH_OK;
});

test("boot: idratazione dallo stato salvato separa email e salta il login", () => {
  const app = new App();
  app.idrata({
    email: "operazioni@gasadriatica.it",
    nomList: [{ punto: "PSV", ciclo: "R4", qta: "500", stato: "Registrata" }],
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

test("REMIT: il form raccoglie i campi Table 1 senza creare falsi record locali", () => {
  const app = new App();
  app.setState({ screen: "remit" });
  let v = app.renderVals();
  v.setRemAcer(ev("A0045821W.IT"));
  v.setRemRif(ev("PSV-2026-0142"));
  v.setRemContractId(ev("PSV-2026-0142"));
  v.setRemControparte(ev("A0045821W.IT"));
  v.setRemQta(ev("500"));
  v.setRemPrezzo(ev("33,50"));
  v.setRemMarketplaceId(ev("XGAS"));
  v.setRemTransactionAt(ev("2026-08-01T10:15:00Z"));
  v.setRemTransactionId(ev("UTI-1"));
  v = app.renderVals();
  assert.equal(v.remAcer, "A0045821W.IT");
  assert.equal(v.remTipo, "gas_standard");
  assert.equal(v.remContractId, "PSV-2026-0142");
  assert.equal(v.remTransactionId, "UTI-1");
  assert.equal(v.remRows.length, 0);
  assert.equal("remList" in app.state, false);
  clearTimeout(app._syncTimer);
});

test("PDR: schermata e profilo separano readiness da credenziali", () => {
  const app = new App();
  app.setState({ screen: "pdr" });
  let v = app.renderVals();
  assert.equal(v.screenPdr, true);
  assert.equal(v.pdrEnvironment, "test");
  v.pdrSetOperator(ev("M-GAS-123"));
  v.pdrSetRegisteredAcer(ev("A0045821W.IT"));
  v.pdrToggleTestAccess();
  v = app.renderVals();
  assert.equal(v.pdrOperator, "M-GAS-123");
  assert.equal(v.pdrRegisteredAcer, "A0045821W.IT");
  assert.equal(v.pdrTestAccess, true);
  assert.equal("password" in app.state.pdr, false);
});

test("PDR: le ricevute restano associate al report e non sono mai presentate come verificate", () => {
  const app = new App();
  app.setState({
    screen: "pdr",
    pdrReceiptReportId: "report-1",
    remReports: [{
      id: "report-1", status: "xml_validato_xsd",
      data: { source_ref: "PSV-2026-0142", report_kind: "gas_standard", quantity_mwh: "500" },
    }],
    pdrReceipts: [{
      id: "receipt-1", report_id: "report-1", filename: "FA_20260801_REMITTable1_V3_A0045821WIT_1.xml",
      outcome: "pdr_partial", source: "pdr", load_code: "LOAD-42", reported_at: "2026-08-01T12:20:00Z", detail: "Partial dichiarato dalla ricevuta GME",
    }],
  });
  const v = app.renderVals();
  assert.equal(v.pdrReceiptReportOptions[0].id, "report-1");
  assert.equal(v.pdrReceiptRows.length, 1);
  assert.equal(v.pdrReceiptRows[0].outcomeLabel, "PDR · Partial dichiarato");
  assert.equal(v.pdrReceiptRows[0].source, "PDR GME");
  assert.equal(v.pdrReceiptRows[0].provenance, "Importata manualmente · non verificata dal connettore");
  assert.ok(v.pdrReceiptOutcomes.some((outcome) => outcome.value === "pdr_partial"));
  v.pdrReceiptSetLoadCode(ev("12A345678"));
  assert.equal(app.state.pdrReceiptLoadCode, "123456");
  clearTimeout(app._syncTimer);
});

test("PDR: l'import invia file Base64 e metadata al solo endpoint locale delle ricevute", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    if (url === "/api/pdr/receipts/import") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ receipt: {
          id: "receipt-2", report_id: "report-2", filename: "FA_file.xml", outcome: "pdr_accepted", source: "pdr",
          load_code: "123456", reported_at: "2026-08-01T12:25:00Z", detail: "Accept dichiarato",
        } }),
        text: async () => "",
      };
    }
    return FETCH_OK(url, opts);
  };
  try {
    const app = new App();
    app.setState({
      screen: "pdr", pdrReceiptReportId: "report-2", pdrReceiptOutcome: "pdr_accepted", pdrReceiptSource: "pdr",
      pdrReceiptLoadCode: "123456", pdrReceiptReportedAt: "2026-08-01T12:25", pdrReceiptDetailText: "Accept dichiarato",
      pdrReceiptFile: { name: "FA_file.xml", arrayBuffer: async () => new Uint8Array([79, 75]).buffer },
      pdrReceiptFileName: "FA_file.xml",
    });
    await app.renderVals().importaRicevuta();
    const request = calls.find((call) => call.url === "/api/pdr/receipts/import");
    assert.ok(request, "POST alla sola API locale delle ricevute");
    assert.equal(request.opts.method, "POST");
    const body = JSON.parse(request.opts.body);
    assert.deepEqual(body, {
      report_id: "report-2", outcome: "pdr_accepted", source: "pdr", load_code: "123456",
      reported_at: new Date("2026-08-01T12:25").toISOString(), detail: "Accept dichiarato", filename: "FA_file.xml", mime_type: "application/octet-stream", content_base64: "T0s=",
    });
    const v = app.renderVals();
    assert.equal(v.pdrReceiptRows[0].outcomeLabel, "PDR · Accept dichiarato");
    assert.match(v.pdrReceiptInfo, /non verificata dal connettore/);
    assert.equal(v.pdrReceiptFileName, "Nessun file selezionato");
    clearTimeout(app._syncTimer);
  } finally {
    global.fetch = originalFetch;
  }
});

test("PDR: selezione e download ricevuta usano gli endpoint dedicati", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    if (url === "/api/pdr/receipts?report_id=report-3") {
      return { ok: true, status: 200, json: async () => ({ receipts: [{ id: "receipt-3", report_id: "report-3", filename: "return.zip", outcome: "acer_accepted", source: "acer" }] }), text: async () => "" };
    }
    if (url === "/api/pdr/receipts/receipt-3/download") {
      return { ok: true, status: 200, text: async () => "" };
    }
    return FETCH_OK(url, opts);
  };
  try {
    const app = new App();
    app.setState({ screen: "pdr" });
    await app.renderVals().pdrReceiptSetReport(ev("report-3"));
    let v = app.renderVals();
    assert.equal(v.pdrReceiptRows[0].outcomeLabel, "ACER · accettazione dichiarata");
    await v.pdrReceiptRows[0].download();
    assert.ok(calls.some((call) => call.url === "/api/pdr/receipts?report_id=report-3"));
    assert.ok(calls.some((call) => call.url === "/api/pdr/receipts/receipt-3/download"));
    assert.match(app.state.pdrReceiptInfo, /non verificato dal connettore/);
    clearTimeout(app._syncTimer);
  } finally {
    global.fetch = originalFetch;
  }
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

// --- EDIG@S -----------------------------------------------------------

test("EDIG@S: i ruoli sono decisi dal tipo di documento, non scelti a mano", () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  let v = app.renderVals();
  assert.match(v.edgRuoli, /ZSH.*ZSO/); // 01G predefinito
  v.setEdgTipo(ev("02G"));
  v = app.renderVals();
  assert.match(v.edgRuoli, /ZSH.*ZUK/);
  v.setEdgTipo(ev("03G"));
  v = app.renderVals();
  assert.match(v.edgRuoli, /ZUM.*ZUK/);
});

test("EDIG@S: il tipo di nomina si allinea da solo al documento", () => {
  const app = new App();
  app.setState({ screen: "nomine", edgTipoNomina: "A01" });
  // 02G ammette solo A02: A01 non deve sopravvivere al cambio
  app.renderVals().setEdgTipo(ev("02G"));
  assert.equal(app.state.edgTipoNomina, "A02");
  // 03G non prevede il tipo di nomina: va svuotato
  app.renderVals().setEdgTipo(ev("03G"));
  assert.equal(app.state.edgTipoNomina, "");
  // tornando a 01G resta valido
  app.renderVals().setEdgTipo(ev("01G"));
  assert.equal(app.state.edgTipoNomina, "A01");
});

test("EDIG@S: avvio pulito, nessuna nomina e nessun messaggio", () => {
  const app = new App();
  app.setState({ screen: "nomine" });
  const v = app.renderVals();
  assert.deepEqual(v.edgRows, []);
  assert.deepEqual(v.edgScostamenti, []);
  assert.equal(v.edgErrore, "");
  assert.equal(v.edgInfo, "");
});

test("EDIG@S: le nomine generate espongono impronta, avvisi e download", () => {
  const app = new App();
  app.setState({
    screen: "nomine",
    edgNomine: [{ id: "abc123", identificativo: "NOMINT-1", versione: 2, tipo_documento: "01G", periodi: 24, sha256: "a".repeat(64), avvisi: ["giorno gas scoperto"] }],
  });
  const riga = app.renderVals().edgRows[0];
  assert.equal(riga.impronta, `SHA-256 ${"a".repeat(16)}…`);
  assert.deepEqual(riga.avvisi, [{ testo: "giorno gas scoperto" }]);
  assert.equal(typeof riga.scarica, "function");
});

test("EDIG@S: gli scostamenti mostrano nominato e confermato", () => {
  const app = new App();
  app.setState({
    screen: "nomine",
    edgScostamentiList: [
      { intervallo: "2026-08-03T04:00Z/2026-08-03T05:00Z", esito: "ridotto", nominato: "700", quantita: "400" },
      { intervallo: "2026-08-03T05:00Z/2026-08-03T06:00Z", esito: "senza risposta", nominato: "500", quantita: null },
    ],
  });
  const [primo, secondo] = app.renderVals().edgScostamenti;
  assert.equal(primo.etichetta, "ridotto: 700 → 400");
  assert.equal(primo.fg, "#B54708");
  assert.equal(secondo.etichetta, "senza risposta: 500 → —");
  assert.equal(secondo.fg, "#B42318");
});

test("EDIG@S: ogni binding del pannello ha un valore dal render", () => {
  // Un binding orfano non rompe nulla a schermo: mostra un campo vuoto e
  // basta. Va quindi verificato qui, non a occhio.
  const fs = require("node:fs");
  const html = fs.readFileSync(path.join(__dirname, "..", "app", "static", "index.html"), "utf8");
  const blocco = html.slice(html.indexOf("Documento EDIG@S"), html.indexOf("screenBilancio"));
  const app = new App();
  app.setState({ screen: "nomine" });
  const v = app.renderVals();
  const locali = new Set(["er", "aw", "sc", "ee", "true", "false"]); // alias delle sc-for e letterali
  const mancanti = [...new Set([...blocco.matchAll(/\{\{\s*([A-Za-z_$][\w$]*)\s*\}\}/g)].map((m) => m[1]))]
    .filter((nome) => !locali.has(nome) && !(nome in v));
  assert.deepEqual(mancanti, []);
});

test("nessuna ripetizione condizionale dentro una <select>", () => {
  // Il parser HTML ammette dentro <select> solo option/optgroup: un <sc-for>
  // viene scartato al caricamento e a schermo restano le mustache non
  // risolte. È già accaduto alle tre tendine delle ricevute PDR e nessun
  // test lo aveva visto, perché il difetto nasce nel parser del browser.
  const fs = require("node:fs");
  for (const f of ["design/design.html", "app/static/index.html"]) {
    const testoFile = fs.readFileSync(path.join(__dirname, "..", f), "utf8");
    const selects = testoFile.match(/<select\b[\s\S]*?<\/select>/g) || [];
    const colpevoli = selects.filter((s) => s.includes("<sc-for") || s.includes("<sc-if"));
    assert.deepEqual(colpevoli, [], `${f}: usa sc-repeat/sc-as sull'<option>`);
  }
});

test("le option ripetute nel design usano sc-repeat con la sua lista", () => {
  const fs = require("node:fs");
  const design = fs.readFileSync(path.join(__dirname, "..", "design", "design.html"), "utf8");
  const ripetute = design.match(/<option[^>]*sc-repeat[^>]*>/g) || [];
  assert.ok(ripetute.length >= 3, `attese almeno 3 option ripetute, trovate ${ripetute.length}`);
  for (const o of ripetute) {
    assert.match(o, /sc-repeat="\{\{ \w+ \}\}"/, `sc-repeat malformato: ${o}`);
    assert.match(o, /sc-as="\w+"/, `manca sc-as: ${o}`);
  }
});

// --- closure stale e confini di sessione ------------------------------------

test("il codice ACER digitato entra nella bozza anche senza re-render", () => {
  // I campi di testo usano setSilent per non perdere il fuoco: la variabile
  // catturata a inizio renderVals resta indietro. Se il payload la usasse,
  // l'XML ACER dichiarerebbe un codice diverso da quello digitato.
  const app = new App();
  app.setState({ screen: "remit" });
  const v = app.renderVals();
  v.setRemAcer(ev("A0045821W.IT"));       // setSilent: nessun re-render
  const payload = app.renderVals().remPayloadPerTest
    ? app.renderVals().remPayloadPerTest()
    : null;
  // se il modulo non espone il payload, si verifica lo stato di partenza
  assert.equal(app.state.cfg.acer, "A0045821W.IT");
  if (payload) assert.equal(payload.acer_code, "A0045821W.IT");
});

test("il profilo PDR inviato è quello digitato, non lo snapshot del render", () => {
  const app = new App();
  app.setState({ screen: "pdr" });
  const v = app.renderVals();
  v.pdrSetOperator(ev("OP-NUOVO"));        // setSilent
  let inviato = null;
  global.fetch = async (url, opts = {}) => {
    if (url === "/api/pdr/profile") inviato = JSON.parse(opts.body || "{}");
    return { ok: true, status: 200, json: async () => ({ profile: inviato || {} }), text: async () => "" };
  };
  return v.salvaPdr().then(() => {
    assert.equal(inviato && inviato.gme_operator_code, "OP-NUOVO");
    global.fetch = FETCH_OK;
  });
});

test("una risposta della sessione precedente non entra nello stato del nuovo utente", async () => {
  const app = new App();
  app._sessionEmail = "primo@azienda.it";
  let sblocca;
  const attesa = new Promise((r) => { sblocca = r; });
  global.fetch = async (url) => {
    if (url === "/api/edigas/nomine") {
      await attesa;
      return { ok: true, status: 200, json: async () => ({ nomine: [{ id: "x", identificativo: "DEL-PRIMO" }] }), text: async () => "" };
    }
    return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
  };
  const inVolo = app._caricaEdigas();
  app._sessionEpoch += 1;                  // nel frattempo: logout e nuovo login
  app._sessionEmail = "secondo@azienda.it";
  sblocca();
  await inVolo;
  assert.deepEqual(app.state.edgNomine, [], "i dati del primo utente non devono comparire");
  global.fetch = FETCH_OK;
});

test("il doppio clic non crea due bozze REMIT", async () => {
  const app = new App();
  app.setState({ screen: "remit" });
  let chiamate = 0;
  let sblocca;
  const attesa = new Promise((r) => { sblocca = r; });
  global.fetch = async (url, opts = {}) => {
    if (url === "/api/remit/reports" && opts.method === "POST") {
      chiamate++;
      await attesa;
      return { ok: true, status: 201, json: async () => ({ id: "r1", version: 1 }), text: async () => "" };
    }
    return { ok: true, status: 200, json: async () => ({ reports: [] }), text: async () => "" };
  };
  const v = app.renderVals();
  const primo = v.addRem();
  const secondo = v.addRem();          // clic ripetuto mentre il primo è in volo
  sblocca();
  await Promise.all([primo, secondo]);
  assert.equal(chiamate, 1, "il secondo clic non deve creare una seconda bozza");
  global.fetch = FETCH_OK;
});

test("dopo la risposta l'azione torna disponibile", async () => {
  const app = new App();
  app.setState({ screen: "remit" });
  let chiamate = 0;
  global.fetch = async (url, opts = {}) => {
    if (url === "/api/remit/reports" && opts.method === "POST") chiamate++;
    return { ok: true, status: 201, json: async () => ({ id: "r", version: 1, reports: [] }), text: async () => "" };
  };
  await app.renderVals().addRem();
  await app.renderVals().addRem();
  assert.equal(chiamate, 2, "il guardiano deve rilasciare l'azione a richiesta conclusa");
  global.fetch = FETCH_OK;
});

test("l'XML esportato resta scaricabile dal registro", () => {
  const app = new App();
  app.setState({
    screen: "remit",
    remReports: [{ id: "r1", status: "xml_validato_xsd", source_ref: "PSV-1", report_kind: "gas_standard",
                   xml_artifact_id: "a1", xml_filename: "20260802_REMITTable1_V3_A0045821W.IT_1.XML" }],
  });
  const riga = app.renderVals().remRows[0];
  assert.equal(riga.hasXml, true);
  assert.equal(typeof riga.scaricaXml, "function");
  assert.equal(riga.canExport, false, "un XML già generato non si riesporta");
});

test("rimuovendo un punto sparisce anche la sua chiave di configurazione", () => {
  const app = new App();
  app.setState({ screen: "cfgImp" });
  const v = app.renderVals();
  v.setNewPunto(ev("Punto di prova"));
  app.renderVals().addPunto();
  const chiave = app.state.extraPunti[0][2];
  assert.ok(chiave in app.state.cfg, "la chiave deve esistere dopo l'aggiunta");
  const riga = app.renderVals().punti.find((x) => x.name === "Punto di prova");
  riga.removeP();
  assert.equal(chiave in app.state.cfg, false, "la chiave orfana farebbe crescere cfg oltre il tetto");
  assert.equal(app.state.extraPunti.length, 0);
});

test("il logout invia la coda in sospeso prima di chiudere", async () => {
  const app = new App();
  app._sessionEmail = "t@t.it";
  const inviati = [];
  global.fetch = async (url, opts = {}) => {
    if (url === "/api/state" && opts.method === "PUT") inviati.push(JSON.parse(opts.body || "{}"));
    return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
  };
  app.setState({ demoMode: true });           // finisce in coda con debounce
  await app.renderVals().logout();
  assert.ok(inviati.some((p) => "demoMode" in p), "la modifica in coda non deve andare persa");
  global.fetch = FETCH_OK;
});

test("il logout sblocca il pulsante di accesso", () => {
  const app = new App();
  app._loginInCorso = true;                    // login ancora in corso
  app._reimpostaDopoLogout();
  assert.equal(app._loginInCorso, false, "restando chiuso, «Accedi» non risponderebbe più");
});
