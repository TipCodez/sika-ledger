"use strict";
const $ = (s, r = document) => r.querySelector(s);
const app = $("#app"),
  modal = $("#modal"),
  mc = $("#modal-content");
let user = null,
  config = {},
  records = [],
  summary = {},
  page = "overview",
  filter = "all",
  query = "";
const icons = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  debts:
    '<path d="M5 3h12a2 2 0 0 1 2 2v16H7a3 3 0 0 1-3-3V4a1 1 0 0 1 1-1Z"/><path d="M4 17h15M8 7h7M8 11h7"/>',
  customers:
    '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-3-5"/>',
  reminders:
    '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  susu: '<rect x="3" y="6" width="18" height="15" rx="2"/><path d="M3 10h18M7 6V3h10v3M8 15h8"/>',
  disputes:
    '<path d="M21 11.5a8.5 8.5 0 0 1-9 8.5H3l2-4a8.5 8.5 0 1 1 16-4.5Z"/><path d="M12 6v6M12 15h.01"/>',
  audit: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  settings:
    '<path d="m12 3 3 2 4 1 1 4-1 4-3 4-4 3-4-3-3-4-1-4 1-4 4-1Z"/><circle cx="12" cy="11" r="3"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  logout: '<path d="M9 4H4v16h5M9 12h12m-5-5 5 5-5 5"/>',
  wallet:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 9h18M17 14h4M7 5V3h10"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
};
const icon = (n) =>
  `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[n] || icons.debts}</svg>`;
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const cash = (n) =>
  (Number(n || 0) / 100).toLocaleString("en-GH", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const initials = (n) =>
  n
    .split(/\s+/)
    .filter(Boolean)
    .map((x) => x[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
const fmtdate = (d) =>
  new Date(d.length === 10 ? d + "T12:00:00Z" : d).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
const badge = (s) =>
  `<span class="status ${esc(s)}"><span class="dot"></span>${esc(s.replaceAll("_", " "))}</span>`;
const brand = () =>
  '<div class="brand"><div class="brand-mark">s</div><span>sika<span>ledger</span></span></div>';
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("visible"), 4200);
}
async function api(path, method = "GET", body) {
  const r = await fetch("/api" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const t = await r.text();
  let d;
  try {
    d = JSON.parse(t);
  } catch {
    d = { detail: t };
  }
  if (!r.ok) {
    if (r.status === 401 && user) {
      user = null;
      auth();
    }
    let msg = d.detail || "Request failed";
    if (Array.isArray(msg))
      msg = msg.map((e) => `${e.loc?.slice(1).join(".")}: ${e.msg}`).join("\n");
    throw Error(msg);
  }
  return d;
}
function openModal(title, body) {
  mc.innerHTML = `<div class="modal-head"><h2>${esc(title)}</h2><button data-close aria-label="Close dialog">×</button></div><div class="modal-body">${body}</div>`;
  if (!modal.open) modal.showModal();
  $("[data-close]", mc).onclick = () => modal.close();
}
modal.addEventListener("click", (e) => {
  if (e.target === modal) modal.close();
});
function bindForm(id, fn) {
  const form = $(id);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", form);
    const error = $(".form-error", form);
    if (error) error.textContent = "";
    button.disabled = true;
    try {
      await fn(Object.fromEntries(new FormData(form)));
    } catch (err) {
      if (error) error.textContent = err.message;
      else toast(err.message);
    } finally {
      button.disabled = false;
    }
  });
}
function field(label, name, type = "text", value = "", extra = "") {
  return `<div class="field"><label for="f-${name}">${esc(label)}</label><input id="f-${name}" name="${name}" type="${type}" value="${esc(value)}" ${extra}></div>`;
}
function auth(register = false) {
  page = "overview";
  app.innerHTML = `<div class="auth"><section class="auth-art">${brand()}<div><div class="eyebrow">Made for everyday business</div><h1>Your debt book.<br>A little smarter.</h1><p>Keep track of what you’re owed, follow up kindly, and see every repayment in one place.</p><div class="auth-orbit"><div class="row"><span class="eyebrow">An example account</span>${badge("paid")}</div><div class="row"><strong>GH₵ 500.00</strong><span>✓</span></div><p>A clear record. A friendly reminder. One less thing to remember.</p></div></div><div class="auth-bottom"><span>Built for Ghanaian businesses</span><span>Prototype · 2026</span></div></section><main class="auth-main"><div class="auth-box"><span class="mode">WORKING PROTOTYPE · TEST PAYMENTS</span><h2 class="auth-title">${register ? "Start your debt book" : "Welcome back"}</h2><p>${register ? "Create a business account to get started." : "Sign in to see how your business is doing."}</p><form id="auth-form">${register ? field("Business name", "name", "text", "", 'required maxlength="100" autocomplete="organization"') : ""}${field("Login name", "login", "text", "", 'required minlength="3" maxlength="100" autocomplete="username"')}${field("Password", "password", "password", "", 'required minlength="10" maxlength="128" autocomplete="' + (register ? "new-password" : "current-password") + '"')}<div class="form-error" role="alert"></div><button type="submit" class="btn">${register ? "Create business account" : "Sign in"} ${icon("arrow")}</button></form><div class="auth-toggle">${register ? "Already have an account?" : "New here?"} <button class="text-btn" id="auth-toggle">${register ? "Sign in" : "Create an account"}</button></div><div class="info small">Use fictional customer data while testing. This prototype uses test payments and dry-run or sandbox SMS.</div></div></main></div>`;
  $("#auth-toggle").onclick = () => auth(!register);
  bindForm("#auth-form", async (data) => {
    user = await api(
      "/auth/" + (register ? "register" : "login"),
      "POST",
      data,
    );
    await refresh();
    shell();
  });
}
async function refresh() {
  [records, summary] = await Promise.all([api("/debts"), api("/dashboard")]);
}
const navNames = {
  overview: "Overview",
  debts: "Debt book",
  customers: "Customers",
  reminders: "Reminders",
  susu: "Susu savings",
  disputes: "Disputes",
  audit: "Activity log",
  settings: "Settings",
};
function shell() {
  app.innerHTML = `<div class="shell"><aside class="sidebar">${brand()}<div class="nav-label">Workspace</div><nav class="nav" aria-label="Main navigation">${Object.entries(
    navNames,
  )
    .filter(([k]) => k !== "settings")
    .map(
      ([k, v]) =>
        `<button data-page="${k}" class="${page === k ? "active" : ""}">${icon(k)}${v}${k === "debts" ? `<span class="count">${records.filter((d) => d.balance_minor > 0).length}</span>` : ""}</button>`,
    )
    .join(
      "",
    )}</nav><div class="side-bottom"><div class="side-note"><h3>Meet your assistant <span>✧</span></h3><p>Turn a quick note into a debt record with Gemini.</p><button class="text-btn" data-agent>Try the assistant →</button></div><nav class="nav"><button data-page="settings">${icon("settings")}Settings</button></nav><div class="profile"><div class="avatar">${esc(initials(user.name))}</div><div><strong class="small">${esc(user.name)}</strong><div class="subtle small">Business account</div></div><button id="logout" aria-label="Sign out">${icon("logout")}</button></div></div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb"><button class="mobile-menu" aria-label="Open navigation">${icon("menu")}</button><span>Workspace</span><span>/</span><strong id="crumb">${navNames[page]}</strong></div><div class="top-right"><span class="mode">PROTOTYPE · NO LIVE MONEY</span><span class="small subtle">${new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</span></div></header><main class="content" id="content"></main></div></div>`;
  $$("[data-page]").forEach(
    (b) =>
      (b.onclick = () => {
        page = b.dataset.page;
        filter = "all";
        query = "";
        shell();
      }),
  );
  $$("[data-agent]").forEach((b) => (b.onclick = () => agentDialog()));
  $("#logout").onclick = async () => {
    await api("/auth/logout", "POST", {});
    user = null;
    auth();
  };
  $(".mobile-menu").onclick = () => $(".sidebar").classList.toggle("open");
  renderPage().catch((e) => toast(e.message));
}
function $$(s, r = document) {
  return [...r.querySelectorAll(s)];
}
function heading(title, description, buttons = "") {
  return `<div class="page-head"><div><h1>${title}</h1><p class="subtle">${description}</p></div><div class="button-row">${buttons}</div></div>`;
}
const addButton = () =>
  `<button class="btn" data-new>${icon("plus")}Record a debt</button>`;
function stats() {
  return `<section class="stats" aria-label="Business totals"><article class="stat primary"><div class="stat-top">Total outstanding ${icon("wallet")}</div><div class="number"><small>GH₵</small>${cash(summary.outstanding_minor)}</div><div class="note">Across ${summary.open_debts} unsettled debts</div></article><article class="stat"><div class="stat-top">Repayments recorded ${icon("overview")}</div><div class="number"><small>GH₵</small>${cash(summary.collected_minor)}</div><div class="note">Net of recorded reversals · all time</div></article><article class="stat overdue"><div class="stat-top">Overdue balance ${icon("reminders")}</div><div class="number"><small>GH₵</small>${cash(summary.overdue_minor)}</div><div class="note">Unsettled debts past their due dates</div></article><article class="stat"><div class="stat-top">Your customers ${icon("customers")}</div><div class="number">${summary.customers}</div><div class="note">People in your business records</div></article></section>`;
}
function debtTable(ds, tools = false) {
  return `<section class="panel"><div class="panel-head"><div><h2>${tools ? "Your debt book" : "Recent debts"}</h2><p class="subtle">A clear picture of who owes what.</p></div>${!tools ? '<button class="text-btn" data-view-all>View all debts →</button>' : ""}</div>${tools ? `<div class="table-tools"><div class="search"><input id="search" aria-label="Search customers" placeholder="Search name, number or description…" value="${esc(query)}"></div><div class="filters">${["all", "open", "overdue", "paid", "disputed"].map((s) => `<button data-filter="${s}" class="${s === filter ? "active" : ""}">${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}</div></div>` : ""}<div id="debt-table-rows">${tableRows(ds)}</div></section>`;
}
function tableRows(ds) {
  return ds.length
    ? `<div class="table-scroll"><table><thead><tr><th>Customer</th><th>Original debt</th><th>Outstanding</th><th>Due date</th><th>Status</th><th><span class="small">Action</span></th></tr></thead><tbody>${ds.map((d) => `<tr><td><div class="person"><div class="avatar">${esc(initials(d.name))}</div><div><strong>${esc(d.name)}</strong><small>${esc(d.phone)}</small></div></div></td><td>GH₵ ${cash(d.principal_minor)}</td><td class="money-cell">GH₵ ${cash(d.balance_minor)}</td><td>${fmtdate(d.due_date)}</td><td>${badge(d.status)}</td><td><button class="text-btn" data-detail="${d.id}" aria-label="View debt for ${esc(d.name)}">View →</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty"><h3>${records.length ? "No debts match this view" : "Your next chapter starts here"}</h3><p>${records.length ? "Try another search or status filter." : "Record a credit sale to start tracking repayments."}</p>${!records.length ? `<div class="button-row empty-actions">${addButton()}${config.demo_mode ? '<button class="btn secondary" id="seed">Load fictional sample data</button>' : ""}</div>` : ""}</div>`;
}
function filtered() {
  const q = query.toLowerCase();
  return records.filter(
    (d) =>
      (filter === "all" ||
        (filter === "open" ? d.balance_minor > 0 : d.status === filter)) &&
      `${d.name} ${d.phone} ${d.description}`.toLowerCase().includes(q),
  );
}
function wireTable() {
  $$("[data-detail]").forEach(
    (b) => (b.onclick = () => showDebt(b.dataset.detail)),
  );
  $$("[data-new]").forEach((b) => (b.onclick = newDebt));
  $$("[data-view-all]").forEach(
    (b) =>
      (b.onclick = () => {
        page = "debts";
        shell();
      }),
  );
  if ($("#seed"))
    $("#seed").onclick = async () => {
      try {
        await api("/demo/seed", "POST", {});
        await refresh();
        shell();
        toast("Fictional sample data loaded.");
      } catch (e) {
        toast(e.message);
      }
    };
  if ($("#search"))
    $("#search").oninput = (e) => {
      query = e.target.value;
      $("#debt-table-rows").innerHTML = tableRows(filtered());
      wireRowButtons();
    };
  $$("[data-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        filter = b.dataset.filter;
        renderPage();
      }),
  );
}
function wireRowButtons() {
  $$("[data-detail]").forEach(
    (b) => (b.onclick = () => showDebt(b.dataset.detail)),
  );
}
async function renderPage() {
  const c = $("#content");
  if (!c) return;
  $("#crumb").textContent = navNames[page];
  if (page === "overview") {
    const due = records
      .filter((d) => d.balance_minor > 0 && !d.disputed)
      .sort((a, b) => a.due_date.localeCompare(b.due_date))
      .slice(0, 3);
    const groups = ["Overdue", "Due today", "Due later", "Settled"];
    const today = new Date().toISOString().slice(0, 10);
    const vals = [
      summary.overdue_minor,
      records
        .filter((d) => d.due_date === today)
        .reduce((s, d) => s + d.balance_minor, 0),
      records
        .filter((d) => d.due_date > today)
        .reduce((s, d) => s + d.balance_minor, 0),
      summary.collected_minor,
    ];
    const max = Math.max(1, ...vals);
    c.innerHTML =
      heading(
        "Your business at a glance",
        "Less chasing. More time for your business.",
        `<a class="btn secondary" href="/api/export/debts.csv">Export records</a>${addButton()}`,
      ) +
      stats() +
      `<div class="grid-main"><div class="stack"><section class="panel"><div class="panel-head"><div><h2>Where things stand</h2><p class="subtle">Current balances and net repayments, in Ghana cedis.</p></div><span class="legend"><span class="dot"></span>From your ledger</span></div><div class="panel-body"><div class="collection-chart">${groups.map((g, i) => `<div class="chart-column"><span class="chart-value">${cash(vals[i])}</span><div class="chart-bar" data-height="${Math.max(2, Math.round((vals[i] / max) * 105))}"></div><span>${g}</span></div>`).join("")}</div></div></section>${debtTable(records.slice(0, 5))}</div><div class="stack side-stack"><section class="panel agent-card"><div class="panel-body agent-body"><div class="agent-glyph">✧</div><div class="eyebrow">Gemini assistant</div><h2>A note is all it takes.</h2><p>Describe a credit sale. Review the details. Add it to your debt book.</p><div class="agent-example">“Kofi bought GH₵600 of rice, paid GH₵200, and will pay the rest next Friday.”</div><button class="btn" data-agent>Ask your assistant ${icon("arrow")}</button><p class="small">${config.gemini_configured ? "Connected · confirmation required" : "API key needed · manual entry available"}</p></div></section><section class="panel"><div class="panel-head"><div><h2>Coming up</h2><p class="subtle">Upcoming and overdue repayments</p></div>${icon("reminders")}</div><div class="panel-body mini-list">${due.length ? due.map((d) => `<div class="mini-item"><div class="avatar">${esc(initials(d.name))}</div><div><strong class="small">${esc(d.name)}</strong><br><small>${fmtdate(d.due_date)}</small></div><div class="amount">GH₵ ${cash(d.balance_minor)}</div></div>`).join("") : '<p class="subtle small">Your repayment schedule will appear here.</p>'}</div></section></div></div><div class="footer-note"><span>Every repayment has a record. Every balance has a history.</span><span>GHS · Africa/Accra · Prototype v0.2</span></div>`;
    $$("[data-height]").forEach(
      (b) => (b.style.height = b.dataset.height + "px"),
    );
    $$("[data-agent]").forEach((b) => (b.onclick = () => agentDialog()));
    wireTable();
    return;
  }
  if (page === "debts") {
    c.innerHTML =
      heading(
        "Your debt book",
        "Record credit sales and follow each repayment.",
        `<a class="btn secondary" href="/api/export/debts.csv">Export CSV</a>${addButton()}`,
      ) + debtTable(filtered(), true);
    wireTable();
    return;
  }
  if (page === "customers") {
    const grouped = {};
    records.forEach((d) => {
      const x = (grouped[d.customer_id] ??= {
        name: d.name,
        phone: d.phone,
        balance: 0,
        count: 0,
      });
      x.balance += d.balance_minor;
      x.count++;
    });
    c.innerHTML =
      heading(
        "Your customers",
        "A customer can have several separate credit purchases.",
        addButton(),
      ) +
      `<section class="panel">${
        Object.values(grouped).length
          ? `<div class="table-scroll"><table><thead><tr><th>Customer</th><th>Phone</th><th>Purchases</th><th>Outstanding</th></tr></thead><tbody>${Object.values(
              grouped,
            )
              .map(
                (x) =>
                  `<tr><td>${esc(x.name)}</td><td>${esc(x.phone)}</td><td>${x.count}</td><td class="money-cell">GH₵ ${cash(x.balance)}</td></tr>`,
              )
              .join("")}</tbody></table></div>`
          : '<div class="empty">Customers appear when you record their first debt.</div>'
      }</section>`;
    wireTable();
    return;
  }
  if (page === "reminders") {
    const rows = await api("/reminders");
    c.innerHTML =
      heading(
        "Friendly follow-ups",
        "Prepare and track reminder processing around repayment dates.",
        '<button class="btn" id="preview-reminders">Prepare today’s previews</button>',
      ) +
      `<div class="info warning">No real SMS is sent. Choose a dry run or the configured provider sandbox. Previews are generated one day before the due date, on the due date, and every third overdue day. Settled, paused, disputed and opted-out records are excluded.</div><section class="panel">${rows.length ? rows.map((r) => `<article class="record-card"><div class="record-meta"><span>${fmtdate(r.created_at)}</span>${badge(r.status)}</div><p>${esc(r.body)}</p><span class="small subtle">${r.status === "stale" ? "Balance or contact state changed. Regenerate the preview." : "Not a handset delivery confirmation"}</span>${r.status === "preview" ? `<div class="section-gap"><button class="btn secondary tiny" data-queue-reminder="${r.id}">${config.sms_mode === "dry_run" ? "Queue dry run" : "Queue sandbox submission"}</button></div>` : ""}${(r.attempts || []).map((a) => `<div class="small subtle section-gap">${esc(a.mode)} · ${esc(a.status)}${a.provider_id ? ` · ID ${esc(a.provider_id)}` : ""}${a.error ? `<br>${esc(a.error)}` : ""}</div>`).join("")}</article>`).join("") : '<div class="empty"><h3>No reminder previews yet</h3><p>Prepare today’s previews to see eligible messages.</p></div>'}</section>`;
    $$("[data-queue-reminder]").forEach(
      (b) =>
        (b.onclick = async () => {
          b.disabled = true;
          try {
            await api(
              "/reminders/" + b.dataset.queueReminder + "/queue",
              "POST",
              {},
            );
            await renderPage();
            toast(
              "Queued. The worker will process it during reminder hours. No real SMS is sent.",
            );
          } catch (e) {
            toast(e.message);
            b.disabled = false;
          }
        }),
    );
    $("#preview-reminders").onclick = async () => {
      try {
        const r = await api("/reminders/preview", "POST", {});
        await renderPage();
        toast(`${r.created} preview(s) prepared. No SMS sent.`);
      } catch (e) {
        toast(e.message);
      }
    };
    return;
  }
  if (page === "disputes") {
    const rows = await api("/disputes");
    c.innerHTML =
      heading(
        "Disputes & corrections",
        "Review customer reports before resuming follow-ups.",
      ) +
      `<section class="panel">${rows.length ? rows.map((r) => `<article class="record-card"><div class="record-meta"><strong>${esc(r.reason.replaceAll("_", " "))}</strong>${badge(r.status)}</div><p>${esc(r.message)}</p>${r.resolution ? `<p class="subtle">Resolution: ${esc(r.resolution)}</p>` : ""}<div class="button-row"><button class="btn tiny secondary" data-detail="${r.debt_id}">View debt</button>${r.status === "open" ? `<button class="btn tiny" data-resolve="${r.id}">Record resolution</button>` : ""}</div></article>`).join("") : '<div class="empty"><h3>No disputes to review</h3><p>Customers can report an incorrect balance or wrong number from their payment link.</p></div>'}</section>`;
    wireRowButtons();
    $$("[data-resolve]").forEach(
      (b) =>
        (b.onclick = () =>
          reasonDialog(
            "Resolve dispute",
            "Explain how you checked and resolved this report. Reminders will remain paused.",
            async (reason) => {
              await api("/disputes/" + b.dataset.resolve + "/resolve", "POST", {
                reason,
              });
              await refresh();
              shell();
            },
          )),
    );
    return;
  }
  if (page === "audit") {
    const rows = await api("/audit");
    c.innerHTML =
      heading(
        "Activity log",
        "A traceable history of changes in your business account.",
      ) +
      `<section class="panel">${rows.length ? rows.map((r) => `<div class="record-card"><div class="record-meta"><strong>${esc(r.action.replaceAll(".", " · "))}</strong><span>${fmtdate(r.created_at)} ${new Date(r.created_at).toLocaleTimeString("en-GB")}</span></div><p class="mono">${esc(r.entity_id)}</p>${r.detail ? `<span class="small subtle">${esc(r.detail)}</span>` : ""}</div>`).join("") : '<div class="empty">No activity yet.</div>'}</section>`;
    return;
  }
  if (page === "settings") {
    c.innerHTML =
      heading(
        "Your workspace settings",
        "Integration status and prototype boundaries.",
      ) +
      `<div class="status-grid"><section class="panel"><div class="panel-head"><h2>Connections</h2></div><div class="panel-body"><div class="setting-line"><div><h3>Gemini agent</h3><p>${esc(config.gemini_model)} · server-side API</p></div>${badge(config.gemini_configured ? "configured" : "not_configured")}</div><div class="setting-line"><div><h3>Paystack test checkout</h3><p>Test keys only. Live payments are blocked.</p></div>${badge(config.paystack_test_configured ? "configured" : "not_configured")}</div><div class="setting-line"><div><h3>SMS reminders</h3><p>Sandbox acceptance is not handset delivery.</p></div>${badge(config.sms_mode)}</div><div class="setting-line"><div><h3>Background processing</h3><p>Durable verification and reminder jobs.</p></div>${badge(config.background_worker ? "enabled" : "external_worker_needed")}</div><div class="setting-line"><div><h3>Demo payment simulator</h3><p>For fictional records only.</p></div>${badge(config.demo_mode ? "enabled" : "disabled")}</div></div></section><section class="panel"><div class="panel-head"><h2>Set up your integrations</h2></div><div class="panel-body"><p class="subtle small">Add credentials to the server’s <code>.env</code> file and restart. Keep API keys out of frontend code.</p><div class="info">Gemini: <code>GEMINI_API_KEY</code><br>Test payments: <code>PAYSTACK_SECRET_KEY</code> and <code>PAYSTACK_TEST_EMAIL</code><br>SMS sandbox: <code>ARKESEL_API_KEY</code>, <code>SMS_MODE=arkesel_sandbox</code><br>Worker: <code>RUN_BACKGROUND_WORKER=true</code></div><p class="small subtle">For shared deployment, configure HTTPS, secure cookies, a matching public URL, and a persistent database. See the included setup guide.</p><div class="info warning">Unallocated customer credit: <strong>GH₵ ${cash(summary.unallocated_minor)}</strong>. This can arise if two pending payments settle against the same debt. Review it before any manual refund.</div></div></section></div>`;
    const jobs = await api("/integrations/webhooks");
    c.insertAdjacentHTML(
      "beforeend",
      `<section class="panel section-gap"><div class="panel-head"><div><h2>Payment notification inbox</h2><p class="subtle">Stored notifications are verified by the worker before balances change.</p></div></div>${jobs.length ? jobs.map((j) => `<article class="record-card"><div class="record-meta"><strong class="mono">${esc(j.reference)}</strong>${badge(j.status)}</div><p class="small subtle">${j.attempts} verification attempt(s)${j.last_error ? ` · ${esc(j.last_error)}` : ""}</p>${["retry", "needs_review"].includes(j.status) ? `<button class="btn secondary tiny" data-retry-job="${j.id}">Request verification retry</button>` : ""}</article>`).join("") : '<div class="empty">No payment notifications received yet.</div>'}</section>`,
    );
    $$("[data-retry-job]").forEach(
      (b) =>
        (b.onclick = () =>
          reasonDialog(
            "Retry payment verification",
            "Explain what you checked before retrying this notification.",
            async (reason) => {
              await api(
                "/integrations/webhooks/" + b.dataset.retryJob + "/retry",
                "POST",
                { reason },
              );
              await renderPage();
            },
          )),
    );
    return;
  }
  if (page === "susu") {
    const rows = await api("/susu");
    c.innerHTML =
      heading(
        "Susu savings",
        "Track contributions and payout obligations separately from debt.",
        '<button class="btn" id="new-susu">' +
          icon("plus") +
          "Open savings record</button>",
      ) +
      `<div class="info warning">Manual savings records only. Recording a contribution or payout does not transfer money. Automated susu payment links and collection fees are outside this first release.</div><section class="panel">${
        rows.length
          ? rows
              .map(
                (r) =>
                  `<article class="record-card"><div class="record-meta"><h3>${esc(r.name)} · ${esc(r.label)}</h3><strong>GH₵ ${cash(r.balance_minor)}</strong></div><p class="subtle">Daily target GH₵ ${cash(r.daily_target_minor)} · ${r.entries.length} recorded entries</p><button class="btn secondary tiny" data-susu="${r.id}">Record contribution / payout</button>${r.entries
                    .slice(0, 4)
                    .map(
                      (e) =>
                        `<div class="ledger-item"><div><p>${esc(e.kind)} · ${esc(e.note)}</p><small>${fmtdate(e.created_at)}</small></div><span class="amount">${e.amount_minor < 0 ? "−" : "+"} GH₵ ${cash(Math.abs(e.amount_minor))}</span></div>`,
                    )
                    .join("")}</article>`,
              )
              .join("")
          : '<div class="empty"><h3>No savings records yet</h3><p>Open a record for a contributor to begin.</p></div>'
      }</section>`;
    $("#new-susu").onclick = newSusu;
    $$("[data-susu]").forEach(
      (b) => (b.onclick = () => susuEntry(b.dataset.susu)),
    );
    return;
  }
}
function newDebt(prefill = {}) {
  const due = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10);
  openModal(
    "Record a credit sale",
    `<p class="subtle small">Enter the original sale amount. Any initial payment is recorded separately in the ledger.</p><form id="debt-form"><div class="split">${field("Customer name", "name", "text", prefill.name || "", 'required maxlength="100"')}${field("Ghana phone number", "phone", "tel", prefill.phone || "", 'required placeholder="0241234567"')}</div><div class="split">${field("Original amount (GHS)", "amount", "number", prefill.amount || "", 'required min="0.01" max="1000000" step="0.01"')}${field("Already paid (GHS)", "initial_paid", "number", prefill.initial_paid || "0", 'required min="0" max="1000000" step="0.01"')}</div>${field("Repayment due date", "due_date", "date", prefill.due_date || due, "required")}${field("What was sold?", "description", "text", prefill.description || "", 'maxlength="300" placeholder="e.g. Four bags of rice"')}<div class="form-error" role="alert"></div><div class="modal-footer"><button class="btn" type="submit">Save debt record</button></div></form>`,
  );
  bindForm("#debt-form", async (data) => {
    await api("/debts", "POST", data);
    modal.close();
    await refresh();
    shell();
    toast("Debt recorded with a traceable opening balance.");
  });
}
async function showDebt(id) {
  try {
    const d = await api("/debts/" + id);
    openModal(
      d.name,
      `<div class="record-meta"><span>${esc(d.description)}</span>${badge(d.status)}</div><div class="detail-amount"><small>GH₵</small> ${cash(d.balance_minor)}</div><p class="subtle small">Outstanding balance</p><dl class="detail-list"><div><dt>Original sale</dt><dd>GH₵ ${cash(d.principal_minor)}</dd></div><div><dt>Repayments, net of reversals</dt><dd>GH₵ ${cash(d.paid_minor)}</dd></div><div><dt>Due date</dt><dd>${fmtdate(d.due_date)}</dd></div><div><dt>Customer phone</dt><dd>${esc(d.phone)}</dd></div></dl><div class="button-row">${d.balance_minor > 0 ? '<button class="btn" id="record-payment">Record repayment</button>' : ""}<a class="btn secondary" target="_blank" rel="noopener" href="/pay/${d.public_token}">Open payment page ↗</a><button class="btn secondary" id="copy-link">Copy link</button><button class="text-btn" id="rotate-link">Replace private link</button><button class="text-btn" id="pause-debt">${d.paused ? "Resume reminders" : "Pause reminders"}</button></div>${d.disputed ? '<div class="info warning">Customer dispute is open. Reminders and new checkout are suspended.</div>' : ""}${d.do_not_contact ? '<div class="info warning">This customer is opted out after a wrong-number report.</div>' : ""}<div class="info small">Cash and direct MoMo entries are trader-reported. Only transactions labelled “Paystack test” are verified against a payment provider.</div><h3>Financial history</h3>${d.ledger.map((e) => `<div class="ledger-item"><div><p><strong>${esc(e.kind)}</strong> · ${esc(e.note)}</p><small>${fmtdate(e.created_at)}</small></div><span class="amount">${e.amount_minor < 0 ? "−" : "+"} GH₵ ${cash(Math.abs(e.amount_minor))}</span></div>`).join("")}<h3 class="section-gap">Repayment records</h3>${d.payments.length ? d.payments.map((p) => `<div class="ledger-item"><div><p>GH₵ ${cash(p.amount_minor)} · ${esc(p.source.replaceAll("_", " "))}</p><small>${esc(p.status)}${p.unallocated_minor ? ` · GH₵ ${cash(p.unallocated_minor)} unallocated` : ""}</small>${p.status === "confirmed" && p.source !== "paystack_test" ? `<div><button class="text-btn" data-reverse="${p.id}">Reverse ledger entry</button></div>` : ""}</div><div><small class="mono">${esc(p.id.slice(0, 10))}</small>${["confirmed", "reversed"].includes(p.status) ? `<br><a class="muted-link" href="/api/payments/${p.id}/receipt">Download acknowledgement</a>` : ""}</div></div>`).join("") : '<p class="small subtle">No repayments yet.</p>'}`,
    );
    if ($("#record-payment"))
      $("#record-payment").onclick = () => manualPayment(d);
    $("#rotate-link").onclick = async () => {
      if (
        !confirm(
          "Replace this debt’s private link? The previous link will stop working. Existing payments remain recorded.",
        )
      )
        return;
      try {
        await api("/debts/" + id + "/rotate-link", "POST", {});
        await refresh();
        await showDebt(id);
        toast("Private link replaced. Share the new link privately.");
      } catch (e) {
        toast(e.message);
      }
    };
    $("#copy-link").onclick = async () => {
      try {
        await navigator.clipboard.writeText(
          location.origin + "/pay/" + d.public_token,
        );
        toast("Payment link copied. Share privately with the customer.");
      } catch {
        toast("Open the payment page and copy its address.");
      }
    };
    $("#pause-debt").onclick = async () => {
      await api("/debts/" + id + "/pause", "POST", { paused: !d.paused });
      await refresh();
      await showDebt(id);
      toast(
        d.paused
          ? "Reminder pause removed. Dispute and contact restrictions still apply."
          : "Reminders paused.",
      );
    };
    $$("[data-reverse]", mc).forEach(
      (b) =>
        (b.onclick = () =>
          reasonDialog(
            "Reverse a ledger entry",
            "This restores the applied debt balance. It does not refund or transfer money.",
            async (reason) => {
              await api("/payments/" + b.dataset.reverse + "/reverse", "POST", {
                reason,
              });
              await refresh();
              shell();
              await showDebt(id);
            },
          )),
    );
  } catch (e) {
    toast(e.message);
  }
}
function manualPayment(d) {
  openModal(
    "Record a repayment",
    `<p class="subtle small">${esc(d.name)} · Outstanding GH₵ ${cash(d.balance_minor)}</p><div class="info warning">Use this for cash or direct MoMo already received and checked by the trader. This action does not verify a MoMo transaction.</div><form id="payment-form">${field("Amount received (GHS)", "amount", "number", "", 'required min="0.01" step="0.01" max="' + d.balance_minor / 100 + '"')}<div class="field"><label for="source">Payment method</label><select name="source" id="source"><option value="cash">Cash received</option><option value="direct_momo">Direct MoMo — checked manually</option></select></div>${field("Reference or explanation", "note", "text", "", 'required maxlength="300"')}<div class="form-error" role="alert"></div><button class="btn" type="submit">Record received payment</button></form>`,
  );
  const key = crypto.randomUUID();
  bindForm("#payment-form", async (data) => {
    await api("/debts/" + d.id + "/payments", "POST", {
      ...data,
      idempotency_key: key,
    });
    await refresh();
    shell();
    await showDebt(d.id);
    toast("Repayment recorded. Balance updated.");
  });
}
function reasonDialog(title, help, fn) {
  openModal(
    title,
    `<p class="subtle small">${esc(help)}</p><form id="reason-form"><div class="field section-gap"><label for="reason">Explanation</label><textarea id="reason" name="reason" required minlength="5" maxlength="500"></textarea></div><div class="form-error" role="alert"></div><button class="btn" type="submit">Confirm</button></form>`,
  );
  bindForm("#reason-form", async (d) => {
    await fn(d.reason);
    if (modal.open) modal.close();
    toast("Change recorded in the activity log.");
  });
}
async function agentDialog(message = "") {
  const key = "sika-conversation-" + user.id;
  let cid = sessionStorage.getItem(key),
    history = [];
  openModal(
    "Your Gemini assistant",
    `<p class="subtle small">Continue a draft by supplying missing details. Confirm every new debt before it enters the ledger.</p>${!config.gemini_configured ? '<div class="info warning">Configure GEMINI_API_KEY on the server to enable the agent.</div>' : ""}<div class="chat-actions"><span class="small subtle">History expires after 24 hours.</span><button class="text-btn" id="clear-chat">Clear conversation</button></div><div id="agent-output" class="chat-history" aria-live="polite"></div><form id="agent-form"><div class="field section-gap"><label for="agent-message">Your message</label><textarea id="agent-message" name="message" required minlength="3" maxlength="2000" placeholder="Record GH₵600 owed by Kofi; he already paid GH₵200.">${esc(message)}</textarea></div><label class="check"><input type="checkbox" name="consent" value="yes" required><span>Send this request, recent conversation and scoped debt context to Gemini. Use fictional records for testing.</span></label><div class="form-error" role="alert"></div><button class="btn" type="submit" ${config.gemini_configured ? "" : "disabled"}>Ask Gemini ✧</button></form>`,
  );
  const output = $("#agent-output");
  function renderHistory() {
    output.innerHTML = history
      .map(
        (t, i) =>
          `<div class="chat-user">${esc(t.message)}</div><div class="agent-result">${esc(t.result.reply)}</div>${i === history.length - 1 && t.result.proposal ? proposalHtml(t.result.proposal) : ""}<div class="trace">${(t.result.trace || []).map((x) => `✓ ${esc(x.tool)} · ${esc(x.status)}`).join("<br>")}</div>`,
      )
      .join("");
    const p = history.at(-1)?.result.proposal;
    if (p) {
      $("#confirm-proposal", output).onclick = async (e) => {
        e.target.disabled = true;
        try {
          await api("/agent/proposals/" + p.id + "/confirm", "POST", {});
          modal.close();
          await refresh();
          shell();
          toast("Confirmed debt added to your ledger.");
        } catch (e) {
          toast(e.message);
        }
      };
      $("#edit-proposal", output).onclick = async () => {
        try {
          await api("/agent/proposals/" + p.id + "/cancel", "POST", {});
          newDebt(p);
        } catch (e) {
          toast(e.message);
        }
      };
    }
    output.scrollTop = output.scrollHeight;
  }
  try {
    if (cid) history = (await api("/agent/conversations/" + cid)).turns;
    renderHistory();
  } catch (e) {
    cid = null;
    sessionStorage.removeItem(key);
    toast(
      "Start a new conversation: the previous conversation is unavailable.",
    );
  }
  if (!modal.open || !$("#agent-form")) return;
  $("#clear-chat").onclick = async (e) => {
    e.target.disabled = true;
    try {
      if (cid) await api("/agent/conversations/" + cid, "DELETE");
      cid = null;
      history = [];
      sessionStorage.removeItem(key);
      renderHistory();
      toast("Conversation cleared. Pending proposals were cancelled.");
    } catch (err) {
      toast(err.message);
    } finally {
      e.target.disabled = false;
    }
  };
  let retryId = null,
    retryMessage = null;
  bindForm("#agent-form", async (data) => {
    if (!cid) {
      cid = (await api("/agent/conversations", "POST", {})).id;
      sessionStorage.setItem(key, cid);
    }
    if (retryMessage !== data.message) {
      retryId = crypto.randomUUID();
      retryMessage = data.message;
    }
    const result = await api("/agent", "POST", {
      message: data.message,
      consent: data.consent === "yes",
      conversation_id: cid,
      request_id: retryId,
    });
    history.push({ message: data.message, result });
    retryId = null;
    retryMessage = null;
    $("#agent-message").value = "";
    renderHistory();
  });
}
function proposalHtml(p) {
  return `<div class="proposal"><h3>Review proposed debt</h3><dl><dt>Customer</dt><dd>${esc(p.name)}</dd><dt>Phone</dt><dd>${esc(p.phone)}</dd><dt>Original sale</dt><dd>GH₵ ${esc(p.amount)}</dd><dt>Already paid</dt><dd>GH₵ ${esc(p.initial_paid)}</dd><dt>Outstanding</dt><dd>GH₵ ${cash(p.balance_minor)}</dd><dt>Due date</dt><dd>${esc(p.due_date)}</dd><dt>Description</dt><dd>${esc(p.description)}</dd></dl><div class="button-row"><button class="btn tiny" id="confirm-proposal">Confirm and record</button><button class="btn secondary tiny" id="edit-proposal">Edit details first</button></div><p class="small subtle section-gap">Proposal expires in 20 minutes. No financial record exists until confirmation.</p></div>`;
}
function newSusu() {
  openModal(
    "Open a savings record",
    `<form id="susu-form">${field("Contributor name", "name", "text", "", "required")}${field("Ghana phone number", "phone", "tel", "", "required")}${field("Savings record label", "label", "text", "Daily savings", "required")}${field("Daily target (GHS)", "daily_target", "number", "", 'required min="0.01" step="0.01"')}<div class="form-error" role="alert"></div><button class="btn" type="submit">Create savings record</button></form>`,
  );
  bindForm("#susu-form", async (data) => {
    await api("/susu", "POST", data);
    modal.close();
    await refresh();
    shell();
  });
}
function susuEntry(id) {
  openModal(
    "Record savings activity",
    `<div class="info warning">Record a contribution or payout already completed outside this application. No transfer will be initiated.</div><form id="susu-entry"><div class="field"><label for="kind">Activity</label><select name="kind" id="kind"><option value="contribution">Contribution received</option><option value="payout">Payout completed</option></select></div>${field("Amount (GHS)", "amount", "number", "", 'required min="0.01" step="0.01"')}${field("Explanation / external reference", "note", "text", "", 'required minlength="3" maxlength="300"')}<div class="form-error" role="alert"></div><button class="btn" type="submit">Record activity</button></form>`,
  );
  const ref = crypto.randomUUID();
  bindForm("#susu-entry", async (data) => {
    await api("/susu/" + id + "/entries", "POST", { ...data, reference: ref });
    modal.close();
    shell();
    toast("Savings activity recorded. No funds transferred.");
  });
}
async function paymentPage(token) {
  const d = await api("/public/" + encodeURIComponent(token));
  app.innerHTML = `<main class="public-wrap">${brand()}<article class="public-card"><div class="record-meta"><span class="eyebrow">Private payment page</span>${badge(d.status)}</div><h1>${esc(d.business_name)}</h1><p class="subtle small">For ${esc(d.name)} · ${esc(d.description)}</p><div class="detail-amount"><small>GH₵</small> ${cash(d.balance_minor)}</div><p class="subtle small">Outstanding · Due ${fmtdate(d.due_date)}</p><dl class="detail-list section-gap"><div><dt>Original sale</dt><dd>GH₵ ${cash(d.principal_minor)}</dd></div><div><dt>Repayments recorded</dt><dd>GH₵ ${cash(d.paid_minor)}</dd></div></dl><div class="info warning">Test environment. No real money moves through this prototype. Never share a MoMo PIN, password or OTP here.</div>${d.disputed ? '<div class="info">This balance is under review. New checkout and reminders are suspended.</div>' : d.balance_minor > 0 ? `<form id="checkout-form">${field("How much would you like to pay? (GHS)", "amount", "number", String(d.balance_minor / 100), 'required min="0.01" step="0.01" max="' + d.balance_minor / 100 + '"')}<div class="field"><label for="pay-mode">Test payment method</label><select id="pay-mode" name="mode">${config.demo_mode ? '<option value="simulation">Demo simulator — no money moves</option>' : ""}${config.paystack_test_configured ? '<option value="paystack_test">Paystack TEST MoMo checkout</option>' : ""}</select></div><div class="form-error" role="alert"></div><button type="submit" class="btn" ${!config.demo_mode && !config.paystack_test_configured ? "disabled" : ""}>${config.demo_mode ? "Start test repayment" : "Continue to test checkout"} ${icon("arrow")}</button></form>` : '<div class="info">This debt is settled. Thank you.</div>'}<button class="text-btn section-gap" id="dispute-link">Incorrect balance or wrong number?</button><h3 class="section-gap">Repayment acknowledgements</h3>${d.receipts.length ? d.receipts.map((p) => `<div class="ledger-item"><div><p>GH₵ ${cash(p.amount_minor)} · ${esc(p.source.replaceAll("_", " "))}</p><small>${esc(p.status)} · ${p.date ? fmtdate(p.date) : ""}</small>${p.unallocated_minor ? `<p class="small">GH₵ ${cash(p.unallocated_minor)} credit requires business review.</p>` : ""}</div><div><span class="mono">${esc(p.id.slice(0, 8))}</span><br><a class="muted-link" href="/api/public/${token}/receipts/${p.id}">Download acknowledgement</a></div></div>`).join("") : '<p class="small subtle">Your acknowledgement will appear after a payment is recorded or verified.</p>'}</article><p class="public-footer">A private link for this debt only. No account or app installation required.<br>Sika Ledger prototype · Ghana cedis (GHS)</p></main>`;
  if ($("#checkout-form")) {
    const key = crypto.randomUUID();
    bindForm("#checkout-form", async (data) => {
      const result = await api("/public/" + token + "/checkout", "POST", {
        ...data,
        idempotency_key: key,
      });
      if (result.authorization_url) {
        sessionStorage.setItem("sika-last-" + token, result.reference);
        location.href = result.authorization_url;
      } else if (result.mode === "simulation") {
        openModal(
          "Simulate a repayment",
          `<div class="info warning">This is a demonstration. No MoMo request is sent and no money will move.</div><p>Apply a simulated payment of <strong>GH₵ ${esc(data.amount)}</strong> to this test debt?</p><div class="modal-footer"><button class="btn" id="simulate-confirm">Confirm simulation</button></div>`,
        );
        $("#simulate-confirm").onclick = async (e) => {
          e.target.disabled = true;
          try {
            await api(
              "/public/" + token + "/simulate/" + result.reference,
              "POST",
              {},
            );
            modal.close();
            await paymentPage(token);
            toast("Simulated repayment recorded. No real money moved.");
          } catch (err) {
            toast(err.message);
            e.target.disabled = false;
          }
        };
      } else
        toast(
          "Checkout already exists. Return from the provider to verify it.",
        );
    });
  }
  $("#dispute-link").onclick = () => {
    openModal(
      "Report an issue",
      `<p class="subtle small">This report pauses collection reminders while the business reviews it.</p><form id="dispute-form"><div class="field section-gap"><label for="reason">What is wrong?</label><select name="reason" id="reason"><option value="incorrect_balance">The balance is incorrect</option><option value="wrong_number">This message reached the wrong number</option><option value="other">Another issue</option></select></div><div class="field"><label for="message">Explain the issue</label><textarea name="message" id="message" required minlength="3" maxlength="500"></textarea></div><div class="form-error" role="alert"></div><button class="btn" type="submit">Submit report</button></form>`,
    );
    bindForm("#dispute-form", async (data) => {
      await api("/public/" + token + "/dispute", "POST", data);
      modal.close();
      await paymentPage(token);
      toast("Report received. Reminders suspended for review.");
    });
  };
  const url = new URL(location.href);
  const ref =
    url.searchParams.get("reference") || url.searchParams.get("trxref");
  if (ref) {
    history.replaceState({}, "", location.pathname);
    try {
      const r = await api(
        "/public/" + token + "/verify/" + encodeURIComponent(ref),
        "POST",
        {},
      );
      toast(
        r.balance_unchanged
          ? "Payment is not yet successful. Check again shortly."
          : "Test transaction verified by server.",
      );
      await paymentPage(token);
    } catch (e) {
      toast(e.message);
    }
  }
}
async function boot() {
  try {
    config = await api("/config");
    const match = location.pathname.match(/^\/pay\/([^/]+)$/);
    if (match) {
      await paymentPage(match[1]);
      return;
    }
    try {
      user = await api("/me");
    } catch {
      auth(true);
      return;
    }
    await refresh();
    shell();
  } catch (e) {
    app.innerHTML = `<div class="public-wrap">${brand()}<div class="public-card"><h2>Unable to open this page</h2><p class="subtle section-gap">${esc(e.message)}</p><a class="btn secondary section-gap" href="/">Go to workspace</a></div></div>`;
  }
}
boot();
