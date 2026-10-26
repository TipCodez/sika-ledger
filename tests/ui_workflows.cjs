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
    "audit",
    "settings",
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
            audit: "Activity log",
            settings: "Settings",
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
  assert.deepEqual(errors, []);
  console.log(
    "PASS: DOM + real API workflows: registration, seed, navigation, debt entry, manual repayment, susu contribution, public simulation, dispute, Gemini follow-up and confirmation, saved chat and queue controls. Browser layout not tested.",
  );
  w.close();
  customer.close();
  bridge.stdin.end();
})().catch((e) => {
  console.error(e);
  bridge.kill();
  process.exitCode = 1;
});
