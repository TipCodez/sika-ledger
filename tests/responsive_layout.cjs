const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const chromePath =
  process.env.CHROME_PATH ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const appUrl = process.env.APP_URL || "http://127.0.0.1:8000";
const debugPort = Number(process.env.CHROME_DEBUG_PORT || 9333);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "sika-layout-"));
const screenshots = path.join(os.tmpdir(), "sika-layout-screens");
fs.mkdirSync(screenshots, { recursive: true });

const chrome = spawn(
  chromePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${profile}`,
    "--window-size=1440,1000",
    appUrl,
  ],
  { stdio: "ignore" },
);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForTarget() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(
        (response) => response.json(),
      );
      const target = targets.find((item) => item.type === "page");
      if (target) return target;
    } catch (_) {
      // Chrome may still be starting.
    }
    await sleep(100);
  }
  throw new Error("Headless Chrome did not expose a page target.");
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result || {});
  });
  return {
    socket,
    call(method, params = {}) {
      sequence += 1;
      const id = sequence;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

async function evaluate(call, expression) {
  const result = await call("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Browser evaluation failed.");
  }
  return result.result?.value;
}

async function waitFor(call, expression, label) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (await evaluate(call, expression)) return;
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function main() {
  const target = await waitForTarget();
  const { socket, call } = await connect(target.webSocketDebuggerUrl);
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Page.navigate", { url: appUrl });
  await waitFor(call, "document.readyState === 'complete'", "initial page");

  const account = await evaluate(
    call,
    `(async () => {
      const suffix = Date.now();
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: 'Responsive Audit Store',
          login: 'responsive-audit-' + suffix,
          password: 'Responsive-Audit-2026'
        })
      });
      if (!response.ok) return {ok: false, status: response.status, body: await response.text()};
      const seed = await fetch('/api/demo/seed', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}'
      });
      return {ok: seed.ok, status: seed.status, body: await seed.text()};
    })()`,
  );
  if (!account?.ok) throw new Error(`Could not prepare audit account: ${JSON.stringify(account)}`);

  await call("Page.reload", { ignoreCache: true });
  await waitFor(call, "Boolean(document.querySelector('.shell'))", "authenticated shell");

  const pages = [
    "overview",
    "debts",
    "customers",
    "reminders",
    "susu",
    "disputes",
    "audit",
    "recovery",
    "reports",
    "team",
    "branding",
    "backups",
    "settings",
    "customer-profile",
  ];
  const viewports = [
    { name: "phone", width: 375, height: 812, mobile: true },
    { name: "landscape", width: 812, height: 375, mobile: true },
    { name: "tablet", width: 768, height: 1024, mobile: true },
    { name: "tablet-wide", width: 1024, height: 768, mobile: false },
    { name: "compact-desktop", width: 1100, height: 800, mobile: false },
    { name: "desktop", width: 1440, height: 1000, mobile: false },
    { name: "wide", width: 1920, height: 1080, mobile: false },
  ];
  const failures = [];
  const results = [];

  for (const viewport of viewports) {
    await call("Emulation.setDeviceMetricsOverride", {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: viewport.mobile,
    });
    for (const page of pages) {
      await evaluate(
        call,
        `(() => {
          if (${JSON.stringify(page)} === 'customer-profile') {
            const customers = document.querySelector('[data-page="customers"]');
            if (!customers) return false;
            customers.click();
            return true;
          }
          const button = document.querySelector('[data-page="${page}"]');
          if (button) {
            button.click();
            return true;
          }
          if (${JSON.stringify(page)} === 'backups') {
            page = 'backups';
            shell();
            return true;
          }
          return false;
        })()`,
      );
      await waitFor(
        call,
        `document.querySelector('#crumb')?.textContent === ${JSON.stringify(
          {
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
            "customer-profile": "Customers",
          }[page],
        )}`,
        `${viewport.name}/${page}`,
      );
      if (page === "customer-profile") {
        await waitFor(call, "Boolean(document.querySelector('[data-customer]'))", `${viewport.name}/customer list`);
        await evaluate(call, "document.querySelector('[data-customer]').click()");
        await waitFor(call, "Boolean(document.querySelector('.profile-content'))", `${viewport.name}/customer profile`);
      }
      await sleep(180);
      const audit = await evaluate(
        call,
        `(() => {
          const visible = (element) => {
            const style = getComputedStyle(element);
            return style.display !== 'none' && style.visibility !== 'hidden' && style.position !== 'fixed';
          };
          const offenders = [...document.body.querySelectorAll('*')]
            .filter(visible)
            .filter((element) => {
              const head = element.closest('thead');
              return !head || getComputedStyle(head).position !== 'absolute';
            })
            .map((element) => ({element, rect: element.getBoundingClientRect()}))
            .filter(({rect}) => rect.width > 0 && (rect.right > innerWidth + 1 || rect.left < -1))
            .slice(0, 8)
            .map(({element, rect}) => ({
              tag: element.tagName.toLowerCase(),
              className: String(element.className || '').slice(0, 80),
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              width: Math.round(rect.width)
            }));
          const main = document.querySelector('.content')?.getBoundingClientRect();
          const sidebar = document.querySelector('.sidebar');
          return {
            viewport: innerWidth,
            documentWidth: document.documentElement.scrollWidth,
            bodyWidth: document.body.scrollWidth,
            mainWidth: main ? Math.round(main.width) : 0,
            bodyFont: getComputedStyle(document.body).fontSize,
            sidebarDisplay: sidebar ? getComputedStyle(sidebar).display : 'missing',
            sidebarScrolls: sidebar ? sidebar.scrollHeight > sidebar.clientHeight + 1 : false,
            offenders
          };
        })()`,
      );
      const row = { viewportName: viewport.name, page, ...audit };
      results.push(row);
      if (
        audit.documentWidth > viewport.width + 1 ||
        audit.bodyWidth > viewport.width + 1 ||
        audit.offenders.length ||
        (viewport.width <= 480 && Number.parseFloat(audit.bodyFont) < 16) ||
        (viewport.width > 1024 && audit.sidebarScrolls)
      ) {
        failures.push(row);
      }
    }

    if (["phone", "tablet", "wide"].includes(viewport.name)) {
      await evaluate(call, "document.querySelector('[data-page=customers]').click()");
      await waitFor(call, "Boolean(document.querySelector('[data-customer]'))", `${viewport.name}/customer screenshot list`);
      await evaluate(call, "document.querySelector('[data-customer]').click()");
      await waitFor(call, "Boolean(document.querySelector('.profile-content'))", `${viewport.name}/customer screenshot profile`);
      await sleep(250);
      const customerImage = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      fs.writeFileSync(path.join(screenshots, `${viewport.name}-customer.png`), customerImage.data, "base64");

      await evaluate(call, "document.querySelector('[data-page=overview]').click()");
      await sleep(250);
      const image = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      fs.writeFileSync(path.join(screenshots, `${viewport.name}-overview.png`), image.data, "base64");
    }

    if (viewport.width <= 1024) {
      const drawer = await evaluate(
        call,
        `(() => {
          document.querySelector('.mobile-menu').click();
          const sidebar = document.querySelector('.sidebar');
          const scrim = document.querySelector('.sidebar-scrim');
          return {
            expanded: document.querySelector('.mobile-menu').getAttribute('aria-expanded'),
            sidebar: getComputedStyle(sidebar).display,
            scrim: getComputedStyle(scrim).display,
            bodyLocked: document.body.classList.contains('nav-open')
          };
        })()`,
      );
      if (
        drawer.expanded !== "true" ||
        drawer.sidebar !== "flex" ||
        drawer.scrim !== "block" ||
        !drawer.bodyLocked
      ) {
        failures.push({ viewportName: viewport.name, page: "mobile-drawer", ...drawer });
      }
      await evaluate(call, "document.querySelector('.sidebar-scrim').click()");
    }
  }

  const report = process.env.RESPONSIVE_AUDIT_VERBOSE
    ? { failures, results, screenshots }
    : { checked: results.length, failures, screenshots };
  console.log(JSON.stringify(report, null, 2));
  socket.close();
  if (failures.length) process.exitCode = 1;
}

main()
  .catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 1;
  })
  .finally(async () => {
    chrome.kill();
    await sleep(150);
    fs.rmSync(profile, { recursive: true, force: true });
  });
