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
const paymentMethod = (source) => {
  if (String(source).startsWith("paystack_") || source === "simulation")
    return "Online payment";
  if (source === "direct_momo") return "Mobile money";
  if (source === "cash") return "Cash";
  return String(source || "Payment").replaceAll("_", " ");
};
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
  app.innerHTML = `<div class="auth"><section class="auth-art">${brand()}<div><div class="eyebrow">Made for everyday business</div><h1>Your debt book.<br>A little smarter.</h1><p>Keep track of what you’re owed, follow up kindly, and see every repayment in one place.</p><div class="auth-orbit"><div class="row"><span class="eyebrow">An example account</span>${badge("paid")}</div><div class="row"><strong>GH₵ 500.00</strong><span>✓</span></div><p>A clear record. A friendly reminder. One less thing to remember.</p></div></div><div class="auth-bottom"><span>Built for Ghanaian businesses</span><span>Ghana cedis · GHS</span></div></section><main class="auth-main"><div class="auth-box"><h2 class="auth-title">${register ? "Start your debt book" : "Welcome back"}</h2><p>${register ? "Create a business account to get started." : "Sign in to see how your business is doing."}</p><form id="auth-form">${register ? field("Business name", "name", "text", "", 'required maxlength="100" autocomplete="organization"') : ""}${field("Login name", "login", "text", "", 'required minlength="3" maxlength="100" autocomplete="username"')}${field("Password", "password", "password", "", 'required minlength="10" maxlength="128" autocomplete="' + (register ? "new-password" : "current-password") + '"')}<div class="form-error" role="alert"></div><button type="submit" class="btn">${register ? "Create business account" : "Sign in"} ${icon("arrow")}</button></form><div class="auth-toggle">${register ? "Already have an account?" : "New here?"} <button class="text-btn" id="auth-toggle">${register ? "Sign in" : "Create an account"}</button></div></div></main></div>`;
  $(".auth-box").insertAdjacentHTML(
    "beforeend",
    '<p class="privacy-link"><a class="muted-link" href="/privacy">Read the privacy notice</a></p>',
  );
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
let profileId = null;
const isOwner = () => user?.role === "owner";
const navNames = {
  overview: "Overview",
  debts: "Debt book",
  customers: "Customers",
  reminders: "Reminders",
  susu: "Susu savings",
  disputes: "Disputes",
  audit: "Activity log",
  recovery: "Payment review",
  reports: "Reports",
  team: "Team",
  branding: "Business branding",
  backups: "Backup & recovery",
  settings: "Settings",
};
function shell() {
  app.innerHTML = `<div class="shell"><aside class="sidebar">${brand()}<div class="nav-label">Workspace</div><nav class="nav" aria-label="Main navigation">${Object.entries(
    navNames,
  )
    .filter(
      ([k]) =>
        !["settings", "backups"].includes(k) &&
        (isOwner() ||
          !["audit", "recovery", "team", "branding", "backups"].includes(k)),
    )
    .map(
      ([k, v]) =>
        `<button data-page="${k}" class="${page === k ? "active" : ""}">${icon(k)}${v}${k === "debts" ? `<span class="count">${records.filter((d) => d.balance_minor > 0).length}</span>` : ""}</button>`,
    )
    .join(
      "",
    )}</nav><div class="side-bottom"><div class="side-note"><h3>Meet your assistant <span>✧</span></h3><p>Turn a quick note into a debt record with Gemini.</p><button class="text-btn" data-agent>Try the assistant →</button></div><nav class="nav"><button data-page="settings">${icon("settings")}Settings</button></nav><div class="profile"><div class="avatar">${esc(initials(user.name))}</div><div><strong class="small">${esc(user.actor_name || user.name)}</strong><div class="subtle small">${esc(user.role)}</div></div><button id="logout" aria-label="Sign out">${icon("logout")}</button></div></div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb"><button class="mobile-menu" aria-label="Open navigation">${icon("menu")}</button><span>Workspace</span><span>/</span><strong id="crumb">${navNames[page]}</strong></div><div class="top-right"><span class="mode">PROTOTYPE · NO LIVE MONEY</span><span class="small subtle">${new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</span></div></header><main class="content" id="content"></main></div></div>`;
  $(".top-right .mode")?.remove();
  $(".sidebar").insertAdjacentHTML(
    "afterbegin",
    '<button class="sidebar-close" aria-label="Close navigation">×</button>',
  );
  $$("[data-page]").forEach(
    (b) =>
      (b.onclick = () => {
        page = b.dataset.page;
        profileId = null;
        filter = "all";
        query = "";
        shell();
      }),
  );
  if (!isOwner()) {
    $$('.side-note, [data-agent], [data-page="settings"]').forEach((b) =>
      b.remove(),
    );
  }
  $$("[data-agent]").forEach((b) => (b.onclick = () => agentDialog()));
  $("#logout").onclick = async () => {
    await api("/auth/logout", "POST", {});
    user = null;
    auth();
  };
  $(".mobile-menu").onclick = () => $(".sidebar").classList.toggle("open");
  $(".sidebar-close").onclick = () => $(".sidebar").classList.remove("open");
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
function scheduledAmount(d, today, later) {
  if (d.plan?.status === "active")
    return d.plan.instalments
      .filter((i) => (later ? i.due_date > today : i.due_date === today))
      .reduce((s, i) => s + i.remaining_minor, 0);
  return (
    later ? d.collection_due_date > today : d.collection_due_date === today
  )
    ? d.balance_minor
    : 0;
}
function stats() {
  return `<section class="stats" aria-label="Business totals"><article class="stat primary"><div class="stat-top">Total outstanding ${icon("wallet")}</div><div class="number"><small>GH₵</small>${cash(summary.outstanding_minor)}</div><div class="note">Across ${summary.open_debts} unsettled debts</div></article><article class="stat"><div class="stat-top">Repayments recorded ${icon("overview")}</div><div class="number"><small>GH₵</small>${cash(summary.collected_minor)}</div><div class="note">Net of recorded reversals · all time</div></article><article class="stat overdue"><div class="stat-top">Overdue balance ${icon("reminders")}</div><div class="number"><small>GH₵</small>${cash(summary.overdue_minor)}</div><div class="note">Past-due instalments or unscheduled balances</div></article><article class="stat"><div class="stat-top">Your customers ${icon("customers")}</div><div class="number">${summary.customers}</div><div class="note">People in your business records</div></article></section>`;
}
function debtTable(ds, tools = false) {
  return `<section class="panel"><div class="panel-head"><div><h2>${tools ? "Your debt book" : "Recent debts"}</h2><p class="subtle">A clear picture of who owes what.</p></div>${!tools ? '<button class="text-btn" data-view-all>View all debts →</button>' : ""}</div>${tools ? `<div class="table-tools"><div class="search"><input id="search" aria-label="Search customers" placeholder="Search name, number or description…" value="${esc(query)}"></div><div class="filters">${["all", "open", "overdue", "paid", "disputed"].map((s) => `<button data-filter="${s}" class="${s === filter ? "active" : ""}">${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}</div></div>` : ""}<div id="debt-table-rows">${tableRows(ds)}</div></section>`;
}
function tableRows(ds) {
  return ds.length
    ? `<div class="table-scroll"><table class="responsive-table"><thead><tr><th>Customer</th><th>Original debt</th><th>Outstanding</th><th>Collection due</th><th>Status</th><th><span class="small">Action</span></th></tr></thead><tbody>${ds.map((d) => `<tr><td data-label="Customer"><div class="person"><div class="avatar">${esc(initials(d.name))}</div><div><strong>${esc(d.name)}</strong><small>${esc(d.phone)}</small></div></div></td><td data-label="Original debt">GH₵ ${cash(d.principal_minor)}</td><td data-label="Outstanding" class="money-cell">GH₵ ${cash(d.balance_minor)}</td><td data-label="Collection due">${fmtdate(d.collection_due_date)}${d.plan?.status === "active" ? '<small class="subtle"> · plan</small>' : ""}</td><td data-label="Status">${badge(d.status)}</td><td data-label="Action"><button class="text-btn" data-detail="${d.id}" aria-label="View debt for ${esc(d.name)}">View →</button></td></tr>`).join("")}</tbody></table></div>`
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
  if (!isOwner())
    $$(
      '.agent-card, [data-agent], #seed, a[href="/api/export/debts.csv"]',
    ).forEach((b) => b.remove());
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
      .sort((a, b) =>
        a.collection_due_date.localeCompare(b.collection_due_date),
      )
      .slice(0, 3);
    const groups = ["Overdue", "Due today", "Due later", "Settled"];
    const today = new Date().toISOString().slice(0, 10);
    const vals = [
      summary.overdue_minor,
      records.reduce((sum, d) => sum + scheduledAmount(d, today, false), 0),
      records.reduce((sum, d) => sum + scheduledAmount(d, today, true), 0),
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
      `<div class="grid-main"><div class="stack"><section class="panel"><div class="panel-head"><div><h2>Where things stand</h2><p class="subtle">Current balances and net repayments, in Ghana cedis.</p></div><span class="legend"><span class="dot"></span>From your ledger</span></div><div class="panel-body"><div class="collection-chart">${groups.map((g, i) => `<div class="chart-column"><span class="chart-value">${cash(vals[i])}</span><div class="chart-bar" data-height="${Math.max(2, Math.round((vals[i] / max) * 105))}"></div><span>${g}</span></div>`).join("")}</div></div></section>${debtTable(records.slice(0, 5))}</div><div class="stack side-stack"><section class="panel agent-card"><div class="panel-body agent-body"><div class="agent-glyph">✧</div><div class="eyebrow">Gemini assistant</div><h2>Add a credit sale faster</h2><p>Describe the sale, review the details, then save it.</p><div class="agent-example">“Kofi bought GH₵600 of rice, paid GH₵200, and will pay the rest next Friday.”</div><button class="btn" data-agent>Ask your assistant ${icon("arrow")}</button><p class="small">${config.gemini_configured ? "Ready to use · confirmation required" : "Assistant unavailable · manual entry available"}</p></div></section><section class="panel"><div class="panel-head"><div><h2>Coming up</h2><p class="subtle">Upcoming and overdue repayments</p></div>${icon("reminders")}</div><div class="panel-body mini-list">${due.length ? due.map((d) => `<div class="mini-item"><div class="avatar">${esc(initials(d.name))}</div><div><strong class="small">${esc(d.name)}</strong><br><small>${fmtdate(d.collection_due_date)}</small></div><div class="amount">GH₵ ${cash(d.plan?.status === "active" ? d.plan.next_due_minor : d.balance_minor)}</div></div>`).join("") : '<p class="subtle small">Your repayment schedule will appear here.</p>'}</div></section></div></div><div class="footer-note"><span>Balances update from your ledger.</span><span>GHS · Africa/Accra</span></div>`;
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
    if (profileId) {
      await showCustomer(profileId);
      return;
    }
    const rows = await api("/customers");
    c.innerHTML =
      heading(
        "Your customers",
        "Debts, savings and account history in one profile.",
        addButton(),
      ) +
      `<section class="panel">${rows.length ? `<div class="table-scroll"><table class="responsive-table"><thead><tr><th>Customer</th><th>Phone</th><th>Trade debt</th><th>Savings</th><th></th></tr></thead><tbody>${rows.map((x) => `<tr><td data-label="Customer">${esc(x.name)}</td><td data-label="Phone">${esc(x.phone)}</td><td data-label="Trade debt">GH₵ ${cash(x.outstanding_minor)}</td><td data-label="Savings">GH₵ ${cash(x.savings_minor)}</td><td data-label="Action"><button class="text-btn" data-customer="${x.id}">Open profile →</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">Customers appear after a debt or savings account is created.</div>'}</section>`;
    $$("[data-customer]").forEach(
      (b) =>
        (b.onclick = () => {
          profileId = b.dataset.customer;
          showCustomer(profileId).catch((e) => toast(e.message));
        }),
    );
    wireTable();
    return;
  }
  if (page === "team") {
    await showTeam();
    return;
  }
  if (page === "branding") {
    await showBranding();
    return;
  }
  if (page === "backups") {
    await showBackups();
    return;
  }
  if (page === "reminders") {
    const rows = await api("/reminders");
    c.innerHTML =
      heading(
        "Friendly follow-ups",
        "Prepare and send reminders around repayment dates.",
        '<button class="btn" id="preview-reminders">Prepare today’s previews</button>',
      ) +
      `<p class="small subtle page-note">Messages are prepared only for customers who have granted SMS permission.</p><section class="panel">${rows.length ? rows.map((r) => `<article class="record-card"><div class="record-meta"><span>${fmtdate(r.created_at)}</span>${badge(r.status)}</div><p>${esc(r.body)}</p><span class="small subtle">${r.status === "stale" ? "Customer details changed. Prepare a new preview." : config.sms_mode === "arkesel_live" ? "Delivery updates appear here." : "Ready to review"}</span>${r.status === "preview" ? `<div class="section-gap"><button class="btn secondary tiny" data-queue-reminder="${r.id}">${config.sms_mode === "arkesel_live" ? "Send SMS" : "Save reminder"}</button></div>` : ""}${(r.attempts || []).map((a) => `<div class="small subtle section-gap">${a.delivery_report ? `<strong>Delivery: ${esc(a.delivery_report.status)}</strong>` : esc(a.status)}${a.error ? "<br>Could not send. Please try again." : ""}</div>`).join("")}</article>`).join("") : '<div class="empty"><h3>No reminders prepared yet</h3><p>Prepare today’s reminders to see eligible messages.</p></div>'}</section>`;
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
              config.sms_mode === "arkesel_live"
                ? "Queued for SMS delivery during reminder hours."
                : config.sms_mode === "arkesel_sandbox"
                  ? "Reminder saved for review."
                  : "Reminder saved. No SMS was sent.",
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
      `<section class="panel">${rows.length ? rows.map((r) => `<div class="record-card"><div class="record-meta"><strong>${esc(r.action.replaceAll(".", " · ").replaceAll("_", " "))}</strong><span>${fmtdate(r.created_at)} ${new Date(r.created_at).toLocaleTimeString("en-GB")}</span></div>${r.actor_name ? `<p class="small subtle">By ${esc(r.actor_name)} · ${esc(r.actor_role)}</p>` : '<p class="small subtle">Automatic update</p>'}</div>`).join("") : '<div class="empty">No activity yet.</div>'}</section>`;
    return;
  }
  if (page === "recovery") {
    const rows = await api("/payment-recovery");
    c.innerHTML =
      heading(
        "Payment review",
        "Review pending payments and customer credit.",
      ) +
      `<section class="panel">${rows.length ? rows.map((p) => `<article class="record-card"><div class="record-meta"><h3>${esc(p.name)}</h3>${badge(p.status)}</div><p><strong>GH₵ ${cash(p.amount_minor)}</strong> · ${p.source.startsWith("paystack_") ? "Online payment" : "Recorded payment"}</p>${p.unallocated_minor ? `<p>Customer credit: <strong>GH₵ ${cash(p.unallocated_minor)}</strong></p>` : ""}${p.review ? `<p class="small subtle">Review: ${esc(p.review.note)}</p>` : ""}<div class="button-row">${p.source.startsWith("paystack_") ? `<button class="btn tiny" data-reverify="${p.kind}/${p.id}">Check payment status</button>` : '<span class="small subtle">Awaiting completion from the customer payment page.</span>'}${p.unallocated_minor ? `<button class="btn secondary tiny" data-review-credit="${p.id}">Record credit review</button>` : ""}</div></article>`).join("") : '<div class="empty"><h3>No payments need review</h3></div>'}</section>`;
    $$("[data-reverify]").forEach(
      (b) =>
        (b.onclick = async () => {
          b.disabled = true;
          try {
            const r = await api(
              "/payment-recovery/" + b.dataset.reverify + "/verify",
              "POST",
              {},
            );
            await refresh();
            await renderPage();
            toast(
              r.balance_unchanged
                ? "Payment status: " + r.status.replaceAll("_", " ")
                : "Payment confirmed and balance updated.",
            );
          } catch (e) {
            toast(e.message);
            b.disabled = false;
          }
        }),
    );
    $$("[data-review-credit]").forEach(
      (b) =>
        (b.onclick = () =>
          reasonDialog(
            "Review unallocated credit",
            "Record what you checked. The amount remains unallocated and no refund is initiated.",
            async (reason) => {
              await api(
                "/payment-recovery/debt/" + b.dataset.reviewCredit + "/review",
                "POST",
                { reason },
              );
              await renderPage();
            },
          )),
    );
    return;
  }
  if (page === "reports") {
    await showReports();
    return;
  }
  if (page === "settings") {
    c.innerHTML =
      heading(
        "Your workspace settings",
        "Manage the services used by your business.",
      ) +
      `<div class="status-grid"><section class="panel"><div class="panel-head"><h2>Business services</h2></div><div class="panel-body"><div class="setting-line"><div><h3>Gemini assistant</h3><p>${config.gemini_configured ? "Ready to help record debts" : "Not available"}</p></div>${badge(config.gemini_configured ? "ready" : "unavailable")}</div><div class="setting-line"><div><h3>Online payments</h3><p>${config.paystack_test_configured || config.paystack_live_configured ? "Ready to accept payments" : "Not available"}</p></div>${badge(config.paystack_test_configured || config.paystack_live_configured ? "ready" : "unavailable")}</div><div class="setting-line"><div><h3>SMS reminders</h3><p>${config.sms_mode === "arkesel_live" ? "Ready to send reminders" : "Preview mode"}</p></div>${badge(config.sms_mode === "arkesel_live" ? "ready" : "preview")}</div></div></section>${summary.unallocated_minor ? `<section class="panel"><div class="panel-head"><h2>Needs review</h2></div><div class="panel-body"><div class="info warning">Customer credit awaiting review: <strong>GH₵ ${cash(summary.unallocated_minor)}</strong>.</div><a class="btn secondary tiny" href="#" data-nav="recovery">Review payments</a></div></section>` : ""}</div>`;
    $("[data-nav=recovery]", c)?.addEventListener("click", (event) => {
      event.preventDefault();
      page = "recovery";
      shell();
    });
    if (config.paystack_test_configured || config.paystack_live_configured) {
      const settlement = await api("/integrations/paystack/subaccount");
      c.insertAdjacentHTML(
        "beforeend",
        settlement.account
          ? `<section class="panel section-gap"><div class="panel-head"><div><h2>Settlement account</h2><p class="subtle">Online payments are sent to this account.</p></div>${badge(settlement.account.status)}</div><div class="record-card"><p><strong>${esc(settlement.account.business_name)}</strong></p><p class="small subtle">${esc(settlement.account.account_name)} · ${esc(settlement.account.settlement_bank)} · ending ${esc(settlement.account.account_last4)}</p><button class="btn secondary tiny" id="refresh-subaccount">Check account status</button></div></section>`
          : `<section class="panel form-panel section-gap"><div class="panel-head"><div><h2>Add settlement account</h2><p class="subtle">Choose where your online payments should be received.</p></div></div><form id="subaccount-form">${field("Business name", "business_name", "text", user.name, 'required minlength="2" maxlength="100"')}${field("Bank or mobile money provider code", "settlement_bank", "text", "", 'required minlength="2" maxlength="30" pattern="[A-Za-z0-9_-]+"')}${field("Account or mobile money number", "account_number", "text", "", 'required minlength="6" maxlength="30" inputmode="numeric" pattern="[0-9]+" autocomplete="off"')}${field("Contact name", "contact_name", "text", user.name, 'maxlength="100"')}${field("Contact email", "contact_email", "email", "", 'maxlength="254" autocomplete="email"')}${field("Contact phone", "contact_phone", "tel", "", 'maxlength="30" autocomplete="tel"')}<label class="check"><input type="checkbox" name="confirmed" value="true" required><span>I have checked these account details and approve this settlement destination.</span></label><div class="form-error" role="alert"></div><button class="btn" type="submit">Save settlement account</button></form></section>`,
      );
      if ($("#subaccount-form"))
        bindForm("#subaccount-form", async (data) => {
          data.confirmed = data.confirmed === "true";
          await api("/integrations/paystack/subaccount", "POST", data);
          toast("Settlement account saved.");
          await renderPage();
        });
      if ($("#refresh-subaccount"))
        $("#refresh-subaccount").onclick = async () => {
          await api("/integrations/paystack/subaccount/refresh", "POST", {});
          toast("Account status updated.");
          await renderPage();
        };
    }
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
      `<p class="small subtle page-note">Use the private link for online contributions, or record completed activity manually.</p><section class="panel">${
        rows.length
          ? rows
              .map(
                (r) =>
                  `<article class="record-card"><div class="record-meta"><h3>${esc(r.name)} · ${esc(r.label)}</h3><strong>GH₵ ${cash(r.balance_minor)}</strong></div><p class="subtle">Daily target GH₵ ${cash(r.daily_target_minor)} · ${r.entries.length} recorded entries · Link expires ${fmtdate(r.link_expires_at)}</p><div class="button-row"><button class="btn secondary tiny" data-susu="${r.id}">${isOwner() ? "Record contribution / payout" : "Record contribution"}</button><a class="btn secondary tiny" href="/contribute/${r.public_token}" target="_blank" rel="noopener">Open contribution link ↗</a>${isOwner() ? `<button class="text-btn" data-rotate-susu="${r.id}">Renew private link</button>` : ""}</div>${r.entries
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
    if (!isOwner()) $("#new-susu").remove();
    else $("#new-susu").onclick = newSusu;
    $$("[data-susu]").forEach(
      (b) => (b.onclick = () => susuEntry(b.dataset.susu)),
    );
    $$("[data-rotate-susu]").forEach(
      (b) =>
        (b.onclick = async () => {
          if (!confirm("Renew this contribution link? The previous link will stop working.")) return;
          try {
            await api("/susu/" + b.dataset.rotateSusu + "/rotate-link", "POST", {});
            await renderPage();
            toast("Contribution link renewed for 30 days.");
          } catch (e) {
            toast(e.message);
          }
        }),
    );
    return;
  }
}
async function showReports(start = "", end = "") {
  let q = new URLSearchParams();
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  const r = await api("/reports/collections?" + q.toString());
  const unique = [
    ...new Map(
      r.debts.map((d) => [d.customer_id, { id: d.customer_id, name: d.name }]),
    ).values(),
  ];
  $("#content").innerHTML =
    heading(
      "Collection reports",
      "Period activity and current debt aging.",
      '<button class="btn secondary" id="print-report">Print report</button>',
    ) +
    `<form id="report-range" class="button-row panel report-range">${field("From", "start", "date", r.start, "required")}${field("To", "end", "date", r.end, "required")}<button type="submit" class="btn">Apply dates</button><div class="form-error" role="alert"></div></form><section class="stats"><article class="stat"><div class="stat-top">Credit recorded in period</div><div class="number"><small>GH₵</small>${cash(r.new_credit_minor)}</div></article><article class="stat"><div class="stat-top">Net recorded repayments</div><div class="number"><small>GH₵</small>${cash(r.net_recorded_repayments_minor)}</div></article><article class="stat"><div class="stat-top">Period adjustments</div><div class="number"><small>GH₵</small>${cash(r.adjustments_minor)}</div></article><article class="stat primary"><div class="stat-top">Outstanding now</div><div class="number"><small>GH₵</small>${cash(r.outstanding_now_minor)}</div></article></section><p class="small subtle page-note">Repayments are shown net of reversals. Aging reflects balances on ${esc(r.aging_as_of)}.</p><div class="split"><section class="panel"><div class="panel-head"><h2>Current debt aging</h2></div><table class="responsive-table"><thead><tr><th>Age</th><th>Outstanding</th></tr></thead><tbody>${Object.entries(
      r.aging,
    )
      .map(([k, v]) => `<tr><td data-label="Age">${esc(k)}</td><td data-label="Outstanding">GH₵ ${cash(v)}</td></tr>`)
      .join(
        "",
      )}</tbody></table></section><section class="panel"><div class="panel-head"><h2>Customer statements</h2></div>${unique.length ? unique.map((c) => `<article class="record-card"><div class="record-meta"><strong>${esc(c.name)}</strong><a class="muted-link" href="/api/reports/customers/${c.id}/statement.pdf">Download PDF statement</a></div></article>`).join("") : '<div class="empty">No customer debts recorded.</div>'}</section></div>`;
  bindForm("#report-range", (data) => showReports(data.start, data.end));
  $("#print-report").onclick = () => window.print();
}
function correctionDialog(d) {
  openModal(
    "Correct debt record",
    `<p class="small subtle">${esc(d.name)} · Current balance GH₵ ${cash(d.balance_minor)}. Corrections preserve financial history.</p><form id="correction-form"><div class="field section-gap"><label for="change-kind">Correction type</label><select id="change-kind" name="kind"><option value="due_date">Change due date</option><option value="adjustment">Adjust amount owed</option><option value="cancel">Cancel duplicate debt without payment history</option></select></div><div id="change-date">${field("New due date", "due_date", "date", d.due_date, "required")}</div><div id="change-amount" hidden><div class="field"><label for="direction">Adjustment</label><select id="direction" name="direction"><option value="increase">Increase amount owed</option><option value="decrease">Decrease amount owed</option></select></div>${field("Adjustment amount (GHS)", "amount", "number", "", 'min="0.01" step="0.01"')}</div><div class="field"><label for="change-reason">Reason</label><textarea name="reason" id="change-reason" required minlength="5" maxlength="500"></textarea></div><div class="form-error" role="alert"></div><button class="btn" type="submit">Save audited correction</button></form>`,
  );
  $("#change-kind").onchange = (e) => {
    $("#change-date").hidden = e.target.value !== "due_date";
    $("#f-due_date").required = e.target.value === "due_date";
    $("#change-amount").hidden = e.target.value !== "adjustment";
    $("#f-amount").required = e.target.value === "adjustment";
  };
  const key = crypto.randomUUID();
  bindForm("#correction-form", async (data) => {
    const body = {
      kind: data.kind,
      reason: data.reason,
      request_key: key,
      expected_balance_minor: d.balance_minor,
    };
    if (data.kind === "due_date") body.due_date = data.due_date;
    if (data.kind === "adjustment") {
      body.amount = data.amount;
      body.direction = data.direction;
    }
    await api("/debts/" + d.id + "/changes", "POST", body);
    await refresh();
    shell();
    await showDebt(d.id);
    toast("Correction saved with its reason and prior state.");
  });
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
      `<div class="record-meta"><span>${esc(d.description)}</span>${badge(d.status)}</div><div class="detail-amount"><small>GH₵</small> ${cash(d.balance_minor)}</div><p class="subtle small">Outstanding balance${d.adjustments_minor ? ` · Corrections: GH₵ ${cash(d.adjustments_minor)}` : ""}</p><dl class="detail-list"><div><dt>Original sale</dt><dd>GH₵ ${cash(d.principal_minor)}</dd></div><div><dt>Repayments, net of reversals</dt><dd>GH₵ ${cash(d.paid_minor)}</dd></div><div><dt>Original due date</dt><dd>${fmtdate(d.due_date)}</dd></div><div><dt>Customer phone</dt><dd>${esc(d.phone)}</dd></div></dl><div class="button-row">${d.balance_minor > 0 ? '<button class="btn" id="record-payment">Record repayment</button>' : ""}<a class="btn secondary" target="_blank" rel="noopener" href="/pay/${d.public_token}">Open payment page ↗</a><button class="btn secondary" id="copy-link">Copy link</button>${isOwner() ? '<button class="text-btn" id="rotate-link">Replace private link</button>' : ""}${isOwner() && !d.cancelled ? '<button class="text-btn" id="correct-debt">Correct record</button>' : ""}${isOwner() ? `<button class="text-btn" id="pause-debt">${d.paused ? "Resume reminders" : "Pause reminders"}</button>` : ""}${isOwner() && d.balance_minor > 0 && !d.cancelled ? '<button class="btn secondary tiny" id="agree-plan">Agree repayment plan</button>' : ""}</div>${d.disputed ? '<div class="info warning">Customer dispute is open. Reminders and new checkout are suspended.</div>' : ""}${d.do_not_contact ? '<div class="info warning">This customer is opted out after a wrong-number report.</div>' : ""}<h3>Financial history</h3>${d.ledger.map((e) => `<div class="ledger-item"><div><p><strong>${esc(e.kind.replaceAll("_", " "))}</strong> · ${esc(e.note)}</p><small>${fmtdate(e.created_at)}</small></div><span class="amount">${e.amount_minor < 0 ? "−" : "+"} GH₵ ${cash(Math.abs(e.amount_minor))}</span></div>`).join("")}<h3 class="section-gap">Repayment records</h3>${d.payments.length ? d.payments.map((p) => `<div class="ledger-item"><div><p>GH₵ ${cash(p.amount_minor)} · ${esc(paymentMethod(p.source))}</p><small>${esc(p.status)}${p.unallocated_minor ? ` · GH₵ ${cash(p.unallocated_minor)} customer credit` : ""}</small>${isOwner() && p.status === "confirmed" && !p.source.startsWith("paystack_") ? `<div><button class="text-btn" data-reverse="${p.id}">Reverse ledger entry</button></div>` : ""}</div><div>${["confirmed", "reversed"].includes(p.status) ? `<a class="muted-link" href="/api/payments/${p.id}/receipt.pdf">Download PDF acknowledgement</a>` : ""}</div></div>`).join("") : '<p class="small subtle">No repayments yet.</p>'}`,
    );
    $(".modal-body", mc).insertAdjacentHTML(
      "beforeend",
      `<p class="small subtle section-gap">Private payment link expires ${fmtdate(d.link_expires_at)}. Replace it to issue a fresh 30-day link.</p>`,
    );
    $(".modal-body", mc).insertAdjacentHTML("beforeend", planPanel(d.plan));
    if ($("#agree-plan")) $("#agree-plan").onclick = () => planDialog(d);
    if ($("#cancel-plan"))
      $("#cancel-plan").onclick = () =>
        reasonDialog(
          "Cancel repayment plan",
          "The ledger balance stays unchanged; the original due-date schedule resumes.",
          async (reason) => {
            await api(`/debts/${d.id}/plans/${d.plan.id}/cancel`, "POST", {
              reason,
            });
            await refresh();
            shell();
            await showDebt(d.id);
          },
        );
    if ($("#record-payment"))
      $("#record-payment").onclick = () => manualPayment(d);
    if ($("#rotate-link"))
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
          toast("Private link renewed for 30 days. Share the new link privately.");
        } catch (e) {
          toast(e.message);
        }
      };
    if ($("#correct-debt"))
      $("#correct-debt").onclick = () => correctionDialog(d);
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
    if ($("#pause-debt"))
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
    `<p class="subtle small">${esc(d.name)} · Outstanding GH₵ ${cash(d.balance_minor)}</p><p class="small subtle page-note">Record only payments you have already received and checked.</p><form id="payment-form">${field("Amount received (GHS)", "amount", "number", "", 'required min="0.01" step="0.01" max="' + d.balance_minor / 100 + '"')}<div class="field"><label for="source">Payment method</label><select name="source" id="source"><option value="cash">Cash received</option><option value="direct_momo">Direct MoMo (checked manually)</option></select></div>${field("Reference or explanation", "note", "text", "", 'required maxlength="300"')}<div class="form-error" role="alert"></div><button class="btn" type="submit">Record received payment</button></form>`,
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
    `<p class="subtle small">Continue a draft by supplying missing details. Confirm every new debt before it enters the ledger.</p>${!config.gemini_configured ? '<p class="small subtle page-note">Gemini is not connected.</p>' : ""}<div class="chat-actions"><span class="small subtle">History expires after 24 hours.</span><button class="text-btn" id="clear-chat">Clear conversation</button></div><div id="agent-output" class="chat-history" aria-live="polite"></div><form id="agent-form"><div class="field section-gap"><label for="agent-message">Your message</label><textarea id="agent-message" name="message" required minlength="3" maxlength="2000" placeholder="Record GH₵600 owed by Kofi; he already paid GH₵200.">${esc(message)}</textarea></div><label class="check"><input type="checkbox" name="consent" value="yes" required><span>Send this request, recent conversation and relevant debt context to Gemini.</span></label><div class="form-error" role="alert"></div><button class="btn" type="submit" ${config.gemini_configured ? "" : "disabled"}>Ask Gemini ✧</button></form>`,
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
    `<p class="small subtle page-note">Record only activity already completed outside Sika Ledger.</p><form id="susu-entry"><div class="field"><label for="kind">Activity</label><select name="kind" id="kind"><option value="contribution">Contribution received</option>${isOwner() ? '<option value="payout">Payout completed</option>' : ""}</select></div>${field("Amount (GHS)", "amount", "number", "", 'required min="0.01" step="0.01"')}${field("Explanation / external reference", "note", "text", "", 'required minlength="3" maxlength="300"')}<div class="form-error" role="alert"></div><button class="btn" type="submit">Record activity</button></form>`,
  );
  const ref = crypto.randomUUID();
  bindForm("#susu-entry", async (data) => {
    await api("/susu/" + id + "/entries", "POST", { ...data, reference: ref });
    modal.close();
    shell();
    toast("Savings activity recorded. No funds transferred.");
  });
}
function addLiveCheckout(selectId, buttonSelector, liveLabel) {
  if (!config.paystack_live_configured) return;
  const select = $(selectId);
  if (!select) return;
  select.insertAdjacentHTML(
    "beforeend",
    '<option value="paystack_live">Secure online payment: card, bank or MoMo</option>',
  );
  select.closest(".field").insertAdjacentHTML(
    "afterend",
    field(
      "Email for the Paystack receipt",
      "email",
      "email",
      "",
      'maxlength="254" autocomplete="email"',
    ),
  );
  const email = $("#f-email");
  const group = email.closest(".field");
  const button = $(buttonSelector);
  const sync = () => {
    const live = select.value === "paystack_live";
    group.hidden = !live;
    email.disabled = !live;
    email.required = live;
    if (button)
      button.innerHTML = live
        ? `${liveLabel} ${icon("arrow")}`
        : button.dataset.defaultLabel;
  };
  if (button) button.dataset.defaultLabel = button.innerHTML;
  select.addEventListener("change", sync);
  sync();
}
function describePaystackCheckout(selectId) {
  const select = $(selectId);
  if (!select) return;
  const testOption = select.querySelector('option[value="paystack_test"]');
  if (testOption)
    testOption.textContent =
      "Online payment: card, bank or MoMo";
  const label = select.closest(".field")?.querySelector("label");
  if (label) label.textContent = "Payment method";
  if (testOption || config.paystack_live_configured)
    select.closest(".field").insertAdjacentHTML(
      "beforeend",
      '<p class="small subtle">Choose card, bank or mobile money on the secure payment page.</p>',
    );
}
async function paymentPage(token) {
  const d = await api("/public/" + encodeURIComponent(token));
  app.innerHTML = `<main class="public-wrap">${brand()}<article class="public-card"><div class="record-meta"><span class="eyebrow">Private payment page</span>${badge(d.status)}</div><h1>${esc(d.business_name)}</h1><p class="subtle small">For ${esc(d.name)} · ${esc(d.description)}</p><div class="detail-amount"><small>GH₵</small> ${cash(d.balance_minor)}</div><p class="subtle small">Outstanding · Original due date ${fmtdate(d.due_date)}</p><dl class="detail-list section-gap"><div><dt>Original sale</dt><dd>GH₵ ${cash(d.principal_minor)}</dd></div><div><dt>Repayments recorded</dt><dd>GH₵ ${cash(d.paid_minor)}</dd></div></dl>${d.disputed ? '<div class="info">This balance is under review. New checkout and reminders are suspended.</div>' : d.balance_minor > 0 ? `<form id="checkout-form">${field("How much would you like to pay? (GHS)", "amount", "number", String(d.balance_minor / 100), 'required min="0.01" step="0.01" max="' + d.balance_minor / 100 + '"')}<div class="field"><label for="pay-mode">${config.paystack_live_configured ? "Payment method" : "Test payment method"}</label><select id="pay-mode" name="mode">${config.paystack_test_configured ? '<option value="paystack_test">Paystack TEST checkout</option>' : ""}</select></div><div class="form-error" role="alert"></div><button type="submit" class="btn" ${!config.paystack_test_configured && !config.paystack_live_configured ? "disabled" : ""}>Continue to checkout ${icon("arrow")}</button></form>` : '<div class="info">This debt is settled. Thank you.</div>'}<button class="text-btn section-gap" id="dispute-link">Incorrect balance or wrong number?</button><h3 class="section-gap">Repayment acknowledgements</h3>${d.receipts.length ? d.receipts.map((p) => `<div class="ledger-item"><div><p>GH₵ ${cash(p.amount_minor)} · ${esc(p.source.replaceAll("_", " "))}</p><small>${esc(p.status)} · ${p.date ? fmtdate(p.date) : ""}</small>${p.unallocated_minor ? `<p class="small">GH₵ ${cash(p.unallocated_minor)} credit requires business review.</p>` : ""}</div><div><span class="mono">${esc(p.id.slice(0, 8))}</span><br><a class="muted-link" href="/api/public/${token}/receipts/${p.id}/pdf">Download PDF acknowledgement</a></div></div>`).join("") : '<p class="small subtle">Your acknowledgement will appear after a payment is recorded or verified.</p>'}</article><p class="public-footer">A private link for this debt only. No account or app installation required.<br>Sika Ledger prototype · Ghana cedis (GHS)</p></main>`;
  $$(".ledger-item", app).forEach((row, index) => {
    const line = $("p", row);
    if (line && d.receipts[index])
      line.firstChild.nodeValue = `GH₵ ${cash(d.receipts[index].amount_minor)} · ${paymentMethod(d.receipts[index].source)}`;
  });
  if (d.business_contact) {
    $(".public-card h1").insertAdjacentHTML(
      "afterend",
      `<p class="business-contact"><strong>Business contact:</strong> ${esc(d.business_contact)}</p>`,
    );
  }
  describePaystackCheckout("#pay-mode");
  if (config.paystack_live_configured && $("#checkout-form"))
    $("#checkout-form").insertAdjacentHTML(
      "beforebegin",
      '<div class="info warning">Paystack LIVE moves real money. Enter payment details only on Paystack’s secure checkout. Sika Ledger never asks for your PIN, CVV or OTP.</div>',
    );
  addLiveCheckout(
    "#pay-mode",
    "#checkout-form button[type=submit]",
    "Continue to secure Paystack checkout",
  );
  $(".public-footer").insertAdjacentHTML(
    "beforeend",
    '<br><a href="/privacy">Privacy notice</a>',
  );
  for (const node of $(".public-footer").childNodes)
    if (node.nodeType === Node.TEXT_NODE)
      node.nodeValue = node.nodeValue.replace("Sika Ledger prototype", "Sika Ledger");
  $(".public-card").insertAdjacentHTML("beforeend", planPanel(d.plan, true));
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
          `<p class="small subtle page-note">Demo only: no money moves.</p><p>Apply a simulated payment of <strong>GH₵ ${esc(data.amount)}</strong> to this test debt?</p><div class="modal-footer"><button class="btn" id="simulate-confirm">Confirm simulation</button></div>`,
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
          : "Payment confirmed.",
      );
      await paymentPage(token);
    } catch (e) {
      toast(e.message);
    }
  }
}
async function contributionPage(token) {
  const d = await api("/susu-public/" + token);
  app.innerHTML = `<main class="public-wrap">${brand()}<article class="public-card"><span class="eyebrow">Private susu contribution page</span><h1>${esc(d.business_name)}</h1><p class="subtle small">${esc(d.name)} · ${esc(d.label)}</p><div class="detail-amount"><small>GH₵</small> ${cash(d.balance_minor)}</div><p class="subtle small">Recorded savings balance · Daily target GH₵ ${cash(d.daily_target_minor)}</p><form id="contribution-form">${field("Contribution amount (GHS)", "amount", "number", String(d.daily_target_minor / 100), 'required min="0.01" max="1000000" step="0.01"')}<div class="field"><label for="contribution-mode">${config.paystack_live_configured ? "Payment method" : "Test payment method"}</label><select id="contribution-mode" name="mode">${config.paystack_test_configured ? '<option value="paystack_test">Paystack TEST checkout</option>' : ""}</select></div><div class="form-error" role="alert"></div><button class="btn" type="submit" ${!config.paystack_test_configured && !config.paystack_live_configured ? "disabled" : ""}>Continue to checkout</button></form><h3 class="section-gap">Confirmed contribution acknowledgements</h3>${d.receipts.length ? d.receipts.map((p) => `<div class="ledger-item"><div><p>GH₵ ${cash(p.amount_minor)} · ${esc(p.source.replaceAll("_", " "))}</p><small>${p.date ? fmtdate(p.date) : ""}</small></div><div><span class="mono">${esc(p.id.slice(0, 8))}</span><br><a class="muted-link" href="/api/susu-public/${token}/receipts/${p.id}.pdf">Download PDF</a></div></div>`).join("") : '<p class="small subtle">No online contributions recorded yet.</p>'}</article><p class="public-footer">Share this link privately. No account is required.</p></main>`;
  $$(".ledger-item", app).forEach((row, index) => {
    const line = $("p", row);
    if (line && d.receipts[index])
      line.textContent = `GH₵ ${cash(d.receipts[index].amount_minor)} · ${paymentMethod(d.receipts[index].source)}`;
  });
  if (d.business_contact) {
    $(".public-card h1").insertAdjacentHTML(
      "afterend",
      `<p class="business-contact"><strong>Business contact:</strong> ${esc(d.business_contact)}</p>`,
    );
  }
  describePaystackCheckout("#contribution-mode");
  if (config.paystack_live_configured && $("#contribution-form"))
    $("#contribution-form").insertAdjacentHTML(
      "beforebegin",
      '<div class="info warning">A live contribution moves real money and increases this savings record; it does not repay trade debt. Enter payment details only on Paystack’s secure checkout.</div>',
    );
  addLiveCheckout(
    "#contribution-mode",
    "#contribution-form button[type=submit]",
    "Continue to secure Paystack contribution",
  );
  $(".public-footer").insertAdjacentHTML(
    "beforeend",
    '<br><a href="/privacy">Privacy notice</a>',
  );
  const key = crypto.randomUUID();
  bindForm("#contribution-form", async (data) => {
    const result = await api("/susu-public/" + token + "/checkout", "POST", {
      ...data,
      idempotency_key: key,
    });
    if (result.authorization_url) {
      location.href = result.authorization_url;
      return;
    }
    if (result.mode === "simulation") {
      openModal(
        "Simulate a susu contribution",
        `<p class="small subtle page-note">Demo only: no money moves.</p><p>Add GH₵ ${esc(data.amount)} to this savings record?</p><div class="modal-footer"><button class="btn" id="confirm-contribution">Confirm simulation</button></div>`,
      );
      $("#confirm-contribution").onclick = async (e) => {
        e.target.disabled = true;
        try {
          await api(
            "/susu-public/" + token + "/simulate/" + result.reference,
            "POST",
            {},
          );
          modal.close();
          await contributionPage(token);
          toast("Simulated contribution recorded once.");
        } catch (err) {
          toast(err.message);
          e.target.disabled = false;
        }
      };
    } else
      toast(
        "An existing checkout was found. Return from the provider to verify it.",
      );
  });
  const url = new URL(location.href),
    ref = url.searchParams.get("reference") || url.searchParams.get("trxref");
  if (ref) {
    history.replaceState({}, "", location.pathname);
    try {
      const r = await api(
        "/susu-public/" + token + "/verify/" + encodeURIComponent(ref),
        "POST",
        {},
      );
      toast(
        r.balance_unchanged
          ? "Contribution is still pending."
          : "Contribution confirmed.",
      );
      await contributionPage(token);
    } catch (e) {
      toast(e.message);
    }
  }
}
function planPanel(plan, publicView = false) {
  if (!plan) return "";
  return `<section class="section-gap plan-panel"><div class="record-meta"><h3>Agreed repayment plan</h3>${badge(plan.status)}</div>${plan.status === "needs_review" ? '<div class="info warning">A correction or reversal changed the agreement basis. Reminders are suspended until the business reviews the plan.</div>' : ""}<div class="table-scroll"><table class="responsive-table"><thead><tr><th>Due</th><th>Agreed</th><th>Remaining</th><th>Status</th></tr></thead><tbody>${plan.instalments.map((i) => `<tr><td data-label="Due">${fmtdate(i.due_date)}</td><td data-label="Agreed">GH₵ ${cash(i.amount_minor)}</td><td data-label="Remaining">${plan.progress_available ? "GH₵ " + cash(i.remaining_minor) : "See current ledger"}</td><td data-label="Status">${badge(i.status)}</td></tr>`).join("")}</tbody></table></div>${!publicView && isOwner() && ["active", "needs_review"].includes(plan.status) ? '<button class="text-btn section-gap" id="cancel-plan">Cancel this schedule</button>' : ""}<p class="small subtle section-gap">Payments apply to the oldest instalment first. This schedule does not add interest or change the ledger balance.</p></section>`;
}
function planDialog(d) {
  const dateAt = (n) =>
    new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
  openModal(
    "Agree a repayment plan",
    `<p class="small subtle">${esc(d.name)} · Schedule exactly GH₵ ${cash(d.balance_minor)}. Saving replaces an earlier active agreement and preserves its history.</p><form id="plan-form"><div id="instalment-rows"></div><button type="button" class="text-btn" id="add-instalment">+ Add instalment</button><div class="field section-gap"><label for="plan-reason">Agreement notes</label><textarea id="plan-reason" name="reason" required minlength="5" maxlength="500"></textarea></div><label class="check"><input type="checkbox" name="agreed" required value="yes"><span>I reviewed and agreed these amounts and dates with the customer.</span></label><div class="form-error" role="alert"></div><button class="btn" type="submit">Save agreed schedule</button></form>`,
  );
  let sequence = 0;
  function addRow(minor = 0) {
    const n = ++sequence;
    $("#instalment-rows").insertAdjacentHTML(
      "beforeend",
      `<div class="instalment-row split" data-instalment>${field("Due date", "date_" + n, "date", dateAt(n * 7), "required")}${field("Amount (GHS)", "amount_" + n, "number", minor ? String(minor / 100) : "", 'required min="0.01" step="0.01"')}<button type="button" class="text-btn" data-remove-instalment>Remove</button></div>`,
    );
    $$("[data-remove-instalment]").forEach(
      (b) =>
        (b.onclick = () => {
          if ($$("[data-instalment]").length > 1)
            b.closest("[data-instalment]").remove();
        }),
    );
  }
  const first = Math.ceil(d.balance_minor / 2);
  addRow(first);
  if (d.balance_minor - first > 0) addRow(d.balance_minor - first);
  $("#add-instalment").onclick = () => {
    if ($$("[data-instalment]").length < 60) addRow();
  };
  const key = crypto.randomUUID();
  bindForm("#plan-form", async (data) => {
    const instalments = $$("[data-instalment]").map((r) => ({
      due_date: $("input[type=date]", r).value,
      amount: $("input[type=number]", r).value,
    }));
    await api("/debts/" + d.id + "/plans", "POST", {
      expected_balance_minor: d.balance_minor,
      request_key: key,
      reason: data.reason,
      agreement_confirmed: data.agreed === "yes",
      instalments,
    });
    await refresh();
    shell();
    await showDebt(d.id);
    toast("Agreement saved; ledger balance is unchanged.");
  });
}
async function showCustomer(cid) {
  const r = await api("/customers/" + cid),
    c = r.customer;
  const history = isOwner()
    ? await api("/customers/" + cid + "/contact-history")
    : [];
  $("#content").innerHTML =
    heading(
      esc(c.name),
      "Complete customer account profile.",
      `<button class="btn secondary" id="back-customers">All customers</button><a class="btn secondary" href="/api/reports/customers/${cid}/statement.pdf">Download PDF statement</a>${isOwner() ? '<button class="btn" id="review-contact">Review contact & consent</button>' : ""}`,
    ) +
    `<div class="info">${esc(c.phone)} · SMS permission: <strong>${c.sms_consent ? "Recorded for this number" : "Not granted"}</strong>${c.do_not_contact ? " · Contact suppressed" : ""}. Trade debt and savings remain separate.</div><section class="stats"><article class="stat"><div class="stat-top">Trade debt outstanding</div><div class="number"><small>GH₵</small>${cash(r.outstanding_minor)}</div></article><article class="stat"><div class="stat-top">Recorded savings</div><div class="number"><small>GH₵</small>${cash(r.savings_minor)}</div></article></section><div class="split"><section class="panel"><div class="panel-head"><h2>Debts & repayment plans</h2></div>${r.debts.length ? r.debts.map((d) => `<article class="record-card"><div class="record-meta"><strong>${esc(d.description)}</strong>${badge(d.status)}</div><p>GH₵ ${cash(d.balance_minor)} outstanding · ${d.plan ? "Plan: " + esc(d.plan.status) : "No repayment plan"}</p><button class="text-btn" data-detail="${d.id}">View debt →</button></article>`).join("") : '<div class="empty">No trade debts.</div>'}</section><section class="panel"><div class="panel-head"><h2>Savings accounts</h2></div>${r.savings.length ? r.savings.map((a) => `<article class="record-card"><h3>${esc(a.label)}</h3><p>GH₵ ${cash(a.balance_minor)} · ${a.entries.length} entries</p></article>`).join("") : '<div class="empty">No savings accounts.</div>'}</section></div><section class="panel section-gap"><div class="panel-head"><h2>Repayment records</h2></div>${r.payments.length ? r.payments.map((p) => `<article class="record-card"><div class="record-meta"><strong>GH₵ ${cash(p.amount_minor)} · ${esc(p.source.replaceAll("_", " "))}</strong>${badge(p.status)}</div>${p.unallocated_minor ? `<p>Unallocated credit GH₵ ${cash(p.unallocated_minor)}</p>` : ""}${["confirmed", "reversed"].includes(p.status) ? `<a class="muted-link" href="/api/payments/${p.id}/receipt.pdf">Download PDF acknowledgement</a>` : ""}</article>`).join("") : '<div class="empty">No repayment records.</div>'}</section><section class="panel section-gap"><div class="panel-head"><h2>Disputes</h2></div>${r.disputes.length ? r.disputes.map((d) => `<article class="record-card"><div class="record-meta"><strong>${esc(d.reason.replaceAll("_", " "))}</strong>${badge(d.status)}</div><p>${esc(d.message)}</p>${d.resolution ? `<p class="small subtle">Resolution: ${esc(d.resolution)}</p>` : ""}</article>`).join("") : '<div class="empty">No disputes.</div>'}</section><section class="panel section-gap"><div class="panel-head"><h2>Transaction timeline</h2></div>${r.timeline.length ? r.timeline.map((e) => `<article class="ledger-item"><div><p><strong>${esc(e.kind.replaceAll("_", " "))}</strong> · ${esc(e.note)}</p><small>${fmtdate(e.created_at)}</small></div><span class="amount">${e.amount_minor < 0 ? "−" : "+"} GH₵ ${cash(Math.abs(e.amount_minor))}</span></article>`).join("") : '<div class="empty">No ledger activity.</div>'}</section>${
      isOwner()
        ? `<section class="panel section-gap"><div class="panel-head"><h2>Contact review history</h2></div>${
            history.length
              ? history
                  .map((h) => {
                    const d = JSON.parse(h.payload);
                    return `<article class="record-card"><strong>${esc(d.before.phone)} → ${esc(d.after.phone)}</strong><p>${esc(h.reason)}</p><p class="small subtle">${fmtdate(h.created_at)} · SMS consent ${d.after.sms_consent ? "granted" : "revoked"} · ${esc(d.after.evidence)}</p></article>`;
                  })
                  .join("")
              : '<div class="empty">No contact reviews recorded.</div>'
          }</section>`
        : ""
    }`;
  const customerSummary = $("#content > .info");
  if (customerSummary) customerSummary.className = "small subtle page-note";
  $("#back-customers").onclick = () => {
    profileId = null;
    renderPage().catch((e) => toast(e.message));
  };
  if ($("#review-contact"))
    $("#review-contact").onclick = () => contactDialog(c);
  wireTable();
}
function contactDialog(c) {
  openModal(
    "Review contact & SMS permission",
    `<div class="info warning">Changing the number replaces private debt/savings links and pauses every debt for this customer. Existing disputes stay open. Grant permission only after verifying the number with the customer.</div><form id="contact-form">${field("Verified Ghana phone number", "phone", "tel", c.phone, "required")}<div class="field"><label for="contact-consent">SMS permission</label><select name="sms_consent" id="contact-consent"><option value="false">Not granted / revoke permission</option><option value="true">Customer explicitly granted permission for this number</option></select></div><div class="field"><label for="contact-evidence">Verification and permission evidence</label><textarea name="evidence" id="contact-evidence" maxlength="500" placeholder="How and when did you verify the number and obtain permission?"></textarea></div><div class="field"><label for="contact-reason">Reason for this review</label><textarea name="reason" id="contact-reason" required minlength="5" maxlength="500"></textarea></div><div class="form-error" role="alert"></div><button class="btn" type="submit">Save contact review</button></form>`,
  );
  $("#contact-consent").value = c.sms_consent ? "true" : "false";
  bindForm("#contact-form", async (data) => {
    await api("/customers/" + c.id + "/contact", "POST", {
      ...data,
      expected_phone: c.phone,
      sms_consent: data.sms_consent === "true",
    });
    modal.close();
    await refresh();
    await showCustomer(c.id);
    toast(
      "Contact reviewed. Check disputes and paused debts before resuming reminders.",
    );
  });
}
async function showTeam() {
  const rows = await api("/team");
  $("#content").innerHTML =
    heading(
      "Your team",
      "Owners manage the business. Collectors record debts, received repayments and savings contributions.",
      '<button class="btn" id="new-collector">Add collector</button>',
    ) +
    `<div class="info">Collectors can view customer accounts, statements and reminder history. They cannot reverse payments, record payouts, change consent, manage plans, access integration settings or manage staff.</div><section class="panel">${rows.map((a) => `<article class="record-card"><div class="record-meta"><h3>${esc(a.name)}</h3>${badge(a.active ? a.role : "disabled")}</div><p class="small subtle">Login: ${esc(a.login)}</p>${a.role === "collector" ? `<div class="button-row"><button class="btn secondary tiny" data-staff-active="${a.id}" data-next="${!a.active}">${a.active ? "Disable access" : "Enable access"}</button><button class="text-btn" data-reset-staff="${a.id}">Reset password</button></div>` : ""}</article>`).join("")}</section>`;
  const teamSummary = $("#content > .info");
  if (teamSummary) {
    teamSummary.className = "small subtle page-note";
    teamSummary.textContent =
      "Collectors can manage customer records and reminders. Only owners can change payments, consent, staff and business settings.";
  }
  $("#new-collector").onclick = () => {
    openModal(
      "Add collector",
      `<form id="staff-form">${field("Staff name", "name", "text", "", 'required minlength="2" maxlength="100"')}${field("Unique login", "login", "text", "", 'required minlength="3" maxlength="100" pattern="[A-Za-z0-9_.-]+"')}${field("Initial password", "password", "password", "", 'required minlength="10" maxlength="128" autocomplete="new-password"')}<p class="small subtle">Share credentials privately. This creates a collector account for this business only.</p><div class="form-error" role="alert"></div><button class="btn" type="submit">Create collector</button></form>`,
    );
    bindForm("#staff-form", async (data) => {
      await api("/team", "POST", data);
      modal.close();
      await showTeam();
      toast("Collector created.");
    });
  };
  $$("[data-staff-active]").forEach(
    (b) =>
      (b.onclick = () =>
        reasonDialog(
          "Change staff access",
          "Existing sessions are revoked immediately.",
          async (reason) => {
            await api("/team/" + b.dataset.staffActive + "/active", "POST", {
              reason,
              active: b.dataset.next === "true",
            });
            await showTeam();
          },
        )),
  );
  $$("[data-reset-staff]").forEach(
    (b) =>
      (b.onclick = () => {
        openModal(
          "Reset collector password",
          `<form id="staff-password-form">${field("New password", "password", "password", "", 'required minlength="10" maxlength="128" autocomplete="new-password"')}${field("Reason", "reason", "text", "", 'required minlength="5" maxlength="500"')}<div class="form-error" role="alert"></div><button class="btn" type="submit">Reset & revoke sessions</button></form>`,
        );
        bindForm("#staff-password-form", async (data) => {
          await api(
            "/team/" + b.dataset.resetStaff + "/password",
            "POST",
            data,
          );
          modal.close();
          toast("Password reset; previous sessions revoked.");
        });
      }),
  );
}
async function showBranding() {
  const b = await api("/branding");
  $("#content").innerHTML =
    heading(
      "Business branding",
      "Customize PDF acknowledgements and customer account statements.",
    ) +
    `<section class="panel form-panel"><form id="branding-form">${field("Business display name", "display_name", "text", b.display_name, 'required minlength="2" maxlength="100"')}${field("Business address", "address", "text", b.address, 'maxlength="200"')}${field("Public business contact", "contact", "text", b.contact, 'maxlength="100"')}${field("Footer message", "footer", "text", b.footer, 'maxlength="200"')}${field("Accent colour", "accent", "color", b.accent, "required")}<p class="small subtle">These details appear on customer documents. Keep private credentials out of these fields.</p><div class="form-error" role="alert"></div><button class="btn" type="submit">Save document branding</button></form></section>`;
  bindForm("#branding-form", async (data) => {
    await api("/branding", "PUT", data);
    toast("Branding saved for new document downloads.");
  });
}
async function showBackups() {
  const info = await api("/backup-info");
  $("#content").innerHTML =
    heading(
      "Backup & recovery",
      "Server-operator tools preserve the complete database and validate restoration.",
    ) +
    `<section class="panel"><div class="panel-head"><h2>${info.engine === "sqlite" ? "SQLite backup workflow" : "PostgreSQL recovery"}</h2></div><div class="record-card"><p>Full-instance backups include every business and account on this server. They are available to the server operator through the terminal, with no tenant download or restore endpoint.</p>${info.engine === "sqlite" ? `<ol class="workflow-steps"><li>Create a new archive in the project terminal:<pre class="code-block">.\\.venv\\Scripts\\python.exe -m app.backup create --output sika-backup.sikabackup</pre></li><li>Validate its checksum, schema, ledger database integrity and foreign keys:<pre class="code-block">.\\.venv\\Scripts\\python.exe -m app.backup inspect sika-backup.sikabackup</pre></li><li>Stop every app and worker process. Restore only after checking the destination filename:<pre class="code-block">.\\.venv\\Scripts\\python.exe -m app.backup restore sika-backup.sikabackup --confirm sika.db</pre></li><li>Restart and sign in. The tool preserves a safety backup before replacing an existing database. Restored sessions are revoked and queued work requires fresh review.</li></ol><p class="small subtle">Commands use DATABASE_URL from .env. Replace sika.db with its actual database filename. Archives contain personal records and password hashes; store them privately. See docs/BACKUP_RECOVERY.md for recovery and scheduled-backup setup.</p>` : "<p>Use your PostgreSQL host’s native backup and restore workflow, with the app and workers stopped for restoration. The SQLite archive tool rejects PostgreSQL URLs. See docs/BACKUP_RECOVERY.md.</p>"}</div></section>`;
}
function privacyPage() {
  document.title = "Privacy notice | Sika Ledger";
  app.innerHTML = `<main class="public-wrap privacy-wrap">${brand()}<article class="public-card privacy-card"><span class="eyebrow">Privacy notice</span><h1>How Sika Ledger handles information</h1><p class="subtle small">Last updated 27 October 2026</p><p>Sika Ledger helps businesses record customer debt, repayments and susu savings. The business that records your information is responsible for using it fairly and keeping it accurate.</p><h2>Information recorded</h2><ul><li>Business and staff account details.</li><li>Customer names, phone numbers, purchases, amounts, dates, repayment plans and savings records.</li><li>Payments, receipts, reminders, contact permission and disputes.</li></ul><h2>How it is used</h2><p>This information is used to maintain balances, prepare receipts, send permitted reminders, prevent duplicate payments and resolve customer concerns.</p><h2>Services used</h2><p>Paystack may handle online payments, Arkesel may send SMS reminders and Gemini may help prepare debt records. Information is shared with Gemini only after the user agrees. Payment details are entered on Paystack’s secure payment page.</p><h2>How long information is kept</h2><ul><li>Sign-in sessions expire after 12 hours.</li><li>Assistant conversation history is kept for 24 hours.</li><li>Private payment and contribution links expire after 30 days unless renewed.</li><li>Financial and activity records are kept until the service operator removes them.</li></ul><h2>Your choices</h2><p>Contact the business that sent you a private link to correct your details, dispute a balance, report a wrong number or withdraw SMS permission. The business can explain how to request access to or deletion of your information.</p><h2>Keeping your information safe</h2><p>Private links should not be shared. Sika Ledger will never ask for your mobile money PIN, password or one-time code.</p><h2>Legal responsibilities</h2><p>Businesses using Sika Ledger must provide appropriate contact details, follow a lawful retention policy and comply with Ghana’s Data Protection Act, 2012 (Act 843). This notice is not legal advice.</p></article><p class="public-footer"><a href="/">Return to Sika Ledger</a></p></main>`;
}
async function boot() {
  try {
    config = await api("/config");
    if (location.pathname === "/privacy") {
      privacyPage();
      return;
    }
    const contribution = location.pathname.match(/^\/contribute\/([^/]+)$/);
    if (contribution) {
      await contributionPage(contribution[1]);
      return;
    }
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
