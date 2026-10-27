const { JSDOM } = require("jsdom");
const path = require("path");
const project = path.resolve(__dirname, "..");
const { spawn } = require("child_process");
const fs = require("fs");
const assert = require("assert/strict");
const readline = require("readline");
const python =
  process.env.SIKA_TEST_PYTHON ||
  path.join(
    project,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const bridge = spawn(python, ["-u", path.join(__dirname, "ui_bridge.py")], {
  cwd: project,
  env: {
    ...process.env,
    PYTHONPATH: [project, process.env.PYTHONPATH]
      .filter(Boolean)
      .join(path.delimiter),
  },
  stdio: ["pipe", "pipe", "inherit"],
});
bridge.on("error", (e) => {
  console.error("Cannot start Python test transport:", e.message);
  process.exit(1);
});
let counter = 0;
const pending = new Map();
readline.createInterface({ input: bridge.stdout }).on("line", (line) => {
  const r = JSON.parse(line);
  pending.get(r.id)?.(r);
  pending.delete(r.id);
});
function request(path, method = "GET", body) {
  return new Promise((resolve) => {
    let id = ++counter;
    pending.set(id, resolve);
    bridge.stdin.write(JSON.stringify({ id, path, method, body }) + "\n");
  });
}
const source = fs.readFileSync(path.join(project, "app/static/app.js"), "utf8");
let errors = [];
function windowAt(path = "/") {
  const dom = new JSDOM(
    '<div id="app"></div><dialog id="modal"><div id="modal-content"></div></dialog><div id="toast"></div>',
    { url: "http://testserver" + path, runScripts: "outside-only" },
  );
  const w = dom.window;
  w.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  w.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  w.fetch = async (path, opts = {}) => {
    const r = await request(path, opts.method || "GET", opts.body);
    return { ok: r.status < 400, status: r.status, text: async () => r.text };
  };
  w.addEventListener("error", (e) => errors.push(e.message));
  w.eval(source);
  return w;
}
async function wait(fn, label) {
  const end = Date.now() + 5000;
  while (Date.now() < end) {
    if (fn()) return;
    await new Promise((r) => setTimeout(r, 15));
  }
  throw Error("Timeout: " + label);
}
function fill(w, id, data) {
  const form = w.document.querySelector(id);
  assert.ok(form, id);
  for (const [k, v] of Object.entries(data)) {
    const field = form.elements.namedItem(k);
    assert.ok(field, k);
    field.value = v;
  }
  form.dispatchEvent(
    new w.Event("submit", { bubbles: true, cancelable: true }),
  );
}
function click(w, s) {
  const el = w.document.querySelector(s);
  assert.ok(el, s);
  el.click();
}
(async () => {
  const w = windowAt();
  const doc = w.document;
  await wait(() => doc.querySelector("#auth-form"), "auth page");
  fill(w, "#auth-form", {
    name: "Raphael Demo Store",
    login: "raphael-ui",
    password: "UI-Prototype-Test-2026",
  });
  await wait(() => doc.querySelector("#seed"), "registered dashboard");
  click(w, "#seed");
  await wait(
    () => doc.querySelectorAll("tbody tr").length === 4,
    "sample data",
  );
  assert.ok(doc.body.textContent.includes("1,800.00"));
  click(w, "[data-new]");
  fill(w, "#debt-form", {
    name: "Efua Example",
    phone: "0249999999",
    amount: "500",
    initial_paid: "0",
    due_date: "2026-11-07",
    description: "Test purchase",
  });
  await wait(
    () =>
      !doc.querySelector("#modal").open &&
      doc.body.textContent.includes("Efua Example"),
    "new debt",
  );
  const row = [...doc.querySelectorAll("tbody tr")].find((r) =>
    r.textContent.includes("Efua Example"),
  );
  row.querySelector("[data-detail]").click();
  await wait(() => doc.querySelector("#record-payment"), "debt details");
  click(w, "#record-payment");
  fill(w, "#payment-form", {
    amount: "200",
    source: "cash",
    note: "Cash counted",
  });
  await wait(
    () =>
      doc
        .querySelector("#modal-content .detail-amount")
        ?.textContent.includes("300.00"),
    "manual partial payment",
  );
  doc.querySelector("#modal").close();
  for (const p of [
    "debts",
    "customers",
    "reminders",
    "disputes",
    "recovery",
    "reports",
    "audit",
    "settings",
    "team",
    "branding",
    "backups",
    "susu",
  ]) {
    click(w, `[data-page="${p}"]`);
    await wait(
      () =>
        doc.querySelector("#crumb")?.textContent ===
          {
            debts: "Debt book",
            customers: "Customers",
            reminders: "Reminders",
            disputes: "Disputes",
            recovery: "Payment recovery",
            reports: "Reports",
            audit: "Activity log",
            settings: "Settings",
            team: "Team",
            branding: "Business branding",
            backups: "Backup & recovery",
            susu: "Susu savings",
          }[p] && !doc.querySelector("#content .loading"),
      "nav " + p,
    );
    await new Promise((r) => setTimeout(r, 30));
  }
  await wait(() => doc.querySelector("#new-susu"), "susu screen");
  click(w, "#new-susu");
  fill(w, "#susu-form", {
    name: "Efua Example",
    phone: "0249999999",
    label: "Daily contributions",
    daily_target: "20",
  });
  await wait(
    () => !doc.querySelector("#modal").open && doc.querySelector("[data-susu]"),
    "susu created",
  );
  click(w, "[data-susu]");
  fill(w, "#susu-entry", {
    kind: "contribution",
    amount: "100",
    note: "Contribution received in cash",
  });
  await wait(
    () =>
      !doc.querySelector("#modal").open &&
      doc.querySelector("#content").textContent.includes("100.00"),
    "susu contribution",
  );
  click(w, "[data-agent]");
  await wait(() => doc.querySelector("#agent-form"), "agent dialog");
  doc.querySelector('[name="consent"]').checked = true;
  fill(w, "#agent-form", { message: "Yaw owes GH₵600 and paid GH₵200" });
  await wait(
    () =>
      doc
        .querySelector("#agent-output")
        ?.textContent.includes("provide the phone"),
    "agent clarification",
  );
  fill(w, "#agent-form", {
    message: "His number is 0246661111 and due 7 November 2026",
  });
  await wait(
    () => doc.querySelector("#confirm-proposal"),
    "agent followup proposal",
  );
  assert.ok(doc.querySelector(".proposal").textContent.includes("400.00"));
  click(w, "#confirm-proposal");
  await wait(() => !doc.querySelector("#modal").open, "proposal confirmed");
  const newRecords = JSON.parse((await request("/api/debts")).text);
  assert.equal(
    newRecords.find((d) => d.name === "Yaw Demo").balance_minor,
    40000,
  );
  click(w, "[data-agent]");
  await wait(
    () => doc.querySelector(".chat-user")?.textContent.includes("Yaw"),
    "chat restored",
  );
  click(w, "#clear-chat");
  await wait(
    () => doc.querySelectorAll(".chat-user").length === 0,
    "chat cleared",
  );
  doc.querySelector("#modal").close();
  click(w, '[data-page="debts"]');
  await wait(
    () =>
      [...doc.querySelectorAll("tbody tr")].some((r) =>
        r.textContent.includes("Yaw Demo"),
      ),
    "Yaw debt row",
  );
  [...doc.querySelectorAll("tbody tr")]
    .find((r) => r.textContent.includes("Yaw Demo"))
    .querySelector("[data-detail]")
    .click();
  await wait(() => doc.querySelector("#correct-debt"), "correction control");
  click(w, "#correct-debt");
  const changeKind = doc.querySelector("#change-kind");
  changeKind.value = "adjustment";
  changeKind.dispatchEvent(new w.Event("change"));
  fill(w, "#correction-form", {
    kind: "adjustment",
    direction: "increase",
    amount: "50",
    reason: "Correct omitted delivery charge",
  });
  await wait(
    () =>
      doc
        .querySelector("#modal-content .detail-amount")
        ?.textContent.includes("450.00"),
    "audited correction",
  );
  click(w, "#agree-plan");
  doc.querySelector('[name="agreed"]').checked = true;
  fill(w, "#plan-form", {
    reason: "Customer agrees two instalments after review",
  });
  await wait(
    () => doc.querySelector(".plan-panel .status.active"),
    "agreed repayment plan",
  );
  assert.equal(
    JSON.parse((await request("/api/debts")).text).find(
      (d) => d.name === "Yaw Demo",
    ).balance_minor,
    45000,
  );
  doc.querySelector("#modal").close();
  click(w, '[data-page="customers"]');
  await wait(() => doc.querySelector("[data-customer]"), "customer directory");
  const efuaRow = [...doc.querySelectorAll("tbody tr")].find((r) =>
    r.textContent.includes("Efua Example"),
  );
  efuaRow.querySelector("[data-customer]").click();
  await wait(
    () => doc.querySelector("#review-contact"),
    "complete customer profile",
  );
  assert.ok(doc.body.textContent.includes("Recorded savings"));
  assert.ok(doc.body.textContent.includes("Transaction timeline"));
  click(w, "#review-contact");
  fill(w, "#contact-form", {
    phone: "0249999999",
    sms_consent: "true",
    evidence: "Verified number and explicit test permission in person",
    reason: "Record customer permission before reminders",
  });
  await wait(
    () =>
      !doc.querySelector("#modal").open &&
      doc
        .querySelector("#content")
        .textContent.includes("Recorded for this number"),
    "contact review and permission",
  );
  click(w, '[data-page="branding"]');
  await wait(() => doc.querySelector("#branding-form"), "branding form");
  fill(w, "#branding-form", {
    display_name: "Raphael Demo Store",
    address: "Sunyani, Ghana",
    contact: "Public business contact",
    footer: "Thank you for keeping clear records.",
    accent: "#087f5b",
  });
  await wait(
    () => doc.querySelector("#toast").textContent.includes("Branding saved"),
    "branding saved",
  );
  click(w, '[data-page="backups"]');
  await wait(
    () =>
      doc.querySelector("#content").textContent.includes("app.backup restore"),
    "backup recovery instructions",
  );
  click(w, '[data-page="team"]');
  await wait(() => doc.querySelector("#new-collector"), "team management");
  click(w, "#new-collector");
  fill(w, "#staff-form", {
    name: "Ama Collector",
    login: "ama-ui-collector",
    password: "Collector-UI-Test-2026",
  });
  await wait(
    () =>
      !doc.querySelector("#modal").open &&
      doc.querySelector("#content").textContent.includes("Ama Collector"),
    "collector created",
  );
  click(w, '[data-page="reports"]');
  await wait(() => doc.querySelector("#report-range"), "collection report");
  fill(w, "#report-range", { start: "2026-01-01", end: "2026-12-31" });
  await wait(
    () =>
      doc.querySelector("#content").textContent.includes("Current debt aging"),
    "report date filter",
  );
  assert.ok(doc.querySelector('a[href*="/statement"]'));
  click(w, '[data-page="reminders"]');
  await wait(() => doc.querySelector("#preview-reminders"), "reminder page");
  click(w, "#preview-reminders");
  await wait(
    () => doc.querySelector("[data-queue-reminder]"),
    "reminder preview",
  );
  click(w, "[data-queue-reminder]");
  await wait(() => doc.querySelector(".status.queued"), "queued reminder");
  const debts = JSON.parse((await request("/api/debts")).text);
  const debt = debts.find((x) => x.name === "Efua Example");
  const savings = JSON.parse((await request("/api/susu")).text).find(
    (a) => a.name === "Efua Example",
  );
  const contributor = windowAt("/contribute/" + savings.public_token);
  await wait(
    () => contributor.document.querySelector("#contribution-form"),
    "private savings link",
  );
  fill(contributor, "#contribution-form", { amount: "20", mode: "simulation" });
  await wait(
    () => contributor.document.querySelector("#confirm-contribution"),
    "savings confirmation",
  );
  click(contributor, "#confirm-contribution");
  await wait(
    () =>
      !contributor.document.querySelector("#modal").open &&
      contributor.document
        .querySelector(".detail-amount")
        .textContent.includes("120.00"),
    "online savings credited",
  );
  assert.equal(
    JSON.parse((await request("/api/debts/" + debt.id)).text).balance_minor,
    30000,
  );
  await request(
    "/api/public/" + debt.public_token + "/checkout",
    "POST",
    JSON.stringify({
      amount: "10",
      mode: "simulation",
      idempotency_key: "ui-recovery-pending-001",
    }),
  );
  click(w, '[data-page="recovery"]');
  await wait(
    () => doc.querySelector("#content").textContent.includes("Efua Example"),
    "pending payment recovery",
  );
  const customer = windowAt("/pay/" + debt.public_token);
  const pd = customer.document;
  await wait(() => pd.querySelector("#checkout-form"), "public payment page");
  fill(customer, "#checkout-form", { amount: "100", mode: "simulation" });
  await wait(
    () => pd.querySelector("#simulate-confirm"),
    "simulation confirmation",
  );
  click(customer, "#simulate-confirm");
  await wait(
    () =>
      !pd.querySelector("#modal").open &&
      pd.querySelector(".detail-amount").textContent.includes("200.00"),
    "public partial payment",
  );
  click(customer, "#dispute-link");
  fill(customer, "#dispute-form", {
    reason: "incorrect_balance",
    message: "Please check the receipt.",
  });
  await wait(
    () =>
      !pd.querySelector("#modal").open && pd.querySelector(".status.disputed"),
    "public dispute",
  );
  assert.equal(pd.querySelector("#checkout-form"), null);
  click(w, "#logout");
  await wait(() => doc.querySelector("#auth-form"), "staff login screen");
  fill(w, "#auth-form", {
    login: "ama-ui-collector",
    password: "Collector-UI-Test-2026",
  });
  await wait(() => doc.querySelector("#logout"), "collector workspace");
  assert.equal(doc.querySelector('[data-page="team"]'), null);
  assert.equal(doc.querySelector('[data-page="settings"]'), null);
  assert.equal(doc.querySelector("[data-agent]"), null);
  click(w, '[data-page="debts"]');
  await wait(
    () =>
      [...doc.querySelectorAll("tbody tr")].some((r) =>
        r.textContent.includes("Yaw Demo"),
      ),
    "collector debt list",
  );
  [...doc.querySelectorAll("tbody tr")]
    .find((r) => r.textContent.includes("Yaw Demo"))
    .querySelector("[data-detail]")
    .click();
  await wait(
    () => doc.querySelector("#record-payment"),
    "collector payment permission",
  );
  assert.equal(doc.querySelector("#correct-debt"), null);
  assert.equal(doc.querySelector("#agree-plan"), null);
  click(w, "#record-payment");
  fill(w, "#payment-form", {
    amount: "25",
    source: "cash",
    note: "Collector received cash",
  });
  await wait(
    () =>
      doc
        .querySelector("#modal-content .detail-amount")
        ?.textContent.includes("425.00"),
    "collector repayment",
  );
  assert.deepEqual(errors, []);
  console.log(
    "PASS: DOM + real API workflows: registration, navigation, repayments, susu contributions, corrections, agreed instalments, complete profiles, contact consent, branding, backup guidance, staff creation/login and restricted collector repayment, reports, payment recovery, disputes, Gemini follow-up/confirmation and reminder queue. Browser layout not tested.",
  );
  w.close();
  customer.close();
  contributor.close();
  bridge.stdin.end();
})().catch((e) => {
  console.error(e);
  bridge.kill();
  process.exitCode = 1;
});
