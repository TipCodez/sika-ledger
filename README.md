# Sika Ledger working prototype — v0.4

A mobile-friendly debt book for Ghanaian traders, with partial repayments, a
traceable ledger, private customer payment pages, a bounded Gemini assistant,
saved Gemini conversations, susu test contribution links, audited corrections, customer profiles, agreed instalments, staff roles, PDF documents,
collection reports, durable payment verification, and reminder processing. **Sika Ledger is a provisional project name.**

Prepared for Raphael. Target demonstration date: **7 November 2026**.

## Start on Windows

Install Python 3.12 (3.11 or newer is required) and extract this entire folder.
Open PowerShell inside the extracted `sika-ledger` folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The script creates a virtual environment, installs or updates changed dependencies, copies
`.env.example` to `.env` if missing, and starts the app. Open
**http://127.0.0.1:8000**, or the local origin already set in `PUBLIC_BASE_URL`.
The launcher honors that local port (including 8010). Create your own business account; no preset credentials
are included. The password must have at least 10 characters.

To avoid running a PowerShell script, run these equivalent commands individually:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe dev_server.py
```

Copy the environment file only on first setup; preserve an existing `.env`.
Stop the server with Ctrl+C. To start again, run `start.ps1` again. The SQLite
database persists between restarts in `sika.db`.

## Start on macOS or Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/python dev_server.py
```

The default interface is plain HTML, CSS and JavaScript served by FastAPI.
There is no Node dependency, CDN requirement, or frontend build step. The brief
allows React **or a mobile-first web app**; this prototype uses the latter so
the complete local application starts with one Python server. The JSON API is
independent of the UI and can support a React client later.

## Five-minute demonstration

1. Create a business account and click **Load fictional sample data** on the
   empty Overview screen. Sample names and balances are fictional.
2. Open Abena Mensah's debt. Its original amount is GH₵500, GH₵200 is already
   recorded as paid, and GH₵300 remains outstanding.
3. Click **Open payment page**, enter GH₵100 and choose the demo simulator.
   Starting checkout alone does not change the balance. Confirm the simulation;
   the outstanding balance becomes GH₵200. No real money moves.
4. Refresh the trader's page. Check the financial history and repayment record.
   Record another payment manually or reverse a mistaken manual/simulated entry
   with an explanation. A reversal does not refund money.
5. Open a customer payment page and report an incorrect balance. Check the
   trader's Disputes screen. The report suspends reminders and new checkout.
6. Prepare reminder previews. Eligible records are selected by due date; settled,
   paused, disputed and opted-out records are excluded. **No SMS is sent.**
7. Open Susu savings, create a savings record, record a contribution and a payout.
   The savings liability stays separate from the debt ledger. These are manual
   records, not money transfers.
8. With Gemini configured, use the assistant example below, inspect the proposal,
   confirm it, and inspect the new debt and activity log.
9. Open a susu contribution link and confirm a test contribution; savings changes
   while trade debt stays unchanged. Inspect **Reports** and **Payment recovery**.
10. On a fictional debt, use **Correct record** with a reason and inspect the
    adjustment without changing the original sale.

## Configure Gemini

Get your own API key from Google AI Studio. Put it in the **server's** `.env`:

```dotenv
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.8-flash
```

Restart FastAPI. The Settings page shows connection configuration, not proof of
successful model access. An actual assistant request validates access and quota.
Choose another supported model ID if your account does not have this model.
The model ID is configurable; it is not embedded in the frontend.

Try:

> Record a sale of GH₵600 to Kofi Asante, phone 0240000002. He paid GH₵200
> already and the balance is due on 7 November 2026. Description: four bags of rice.

Also try “What is my total outstanding balance?” and “Draft a reminder for Kofi.”
If there are multiple matching debts, specify which record or use its identifier.

The agent selects an allowlisted action through a structured JSON plan. The
backend validates it, executes a scoped read tool or stores a draft proposal,
and shows the tool trace. New debts require a separate authenticated confirmation.
Proposals expire after 20 minutes and can be confirmed only once. The model
cannot change balances, approve a payment, send a message, or execute arbitrary
code or SQL. Conversations are scoped to a merchant and retained for 24 hours.
The most recent six turns are included with a follow-up, allowing the trader to
supply a missing phone number or date. A conversation supports up to 20 turns.
Reopening the assistant in the same browser session restores its history.
**Clear conversation** removes saved messages and cancels pending proposals.
Choosing **Edit details first** also cancels the original proposal before opening
the manual form, preventing the original draft from being confirmed afterward.
History expiration is enforced on access; the worker physically removes expired
conversation rows. Failed API calls do not store a completed turn or trap the
conversation in a busy state. Request IDs prevent duplicate proposals on retries.

Before each request the trader explicitly consents to sending the request and
recent conversation and scoped debt context (names, balances and dates) to Gemini. Saved phone numbers
and payment-link tokens are not included in that context. Phone numbers typed
in the request are sent as part of that request. Use fictional data while testing.
Gemini chooses reminder intent; the backend builds the factual reminder text
from ledger data using a deterministic template.

Without an API key the app reports that Gemini is unavailable. It does not
imitate a successful AI response. Manual workflows remain available.

## Configure Paystack test checkout

The built-in simulator works without provider credentials. To exercise the
external provider adapter, configure:

```dotenv
PAYSTACK_SECRET_KEY=sk_test_your_test_key
PAYSTACK_TEST_EMAIL=your_valid_test_email@example.com
```

The provider requires an email at initialization. This first prototype uses the
configured test email for all sandbox checkouts; debtor accounts still do not
require email. **Do not reuse this approach for a live multi-merchant service.**

Only `sk_test_` keys are accepted. Startup rejects live keys. Use only provider
test payment details. Test channel availability depends on the provider's test
environment; an unavailable MoMo test channel must not be treated as a real
MoMo integration success.

For provider callbacks, deploy the FastAPI app to an HTTPS-capable Python host,
set `PUBLIC_BASE_URL` to the exact public origin, set `COOKIE_SECURE=true`, and
set the provider's test webhook to:

```text
https://your-test-host.example/api/webhooks/paystack
```

Do not expose the development machine or real customer data just to test a webhook.
The webhook validates HMAC-SHA512 over the raw body, stores a deduplicated
verification job in a durable inbox, and acknowledges after that database commit.
The worker calls Paystack's verification endpoint and matches the reference,
amount, GHS currency, test domain, merchant and debt metadata before allocating
a repayment. Susu payments use the same verification pipeline, additionally
matching the savings account and contribution purpose before creating one
contribution entry. They never allocate against trade debt. Transient failures retry with backoff, up to eight attempts.
Mismatches stop for review. The Settings screen shows job states and provides
an explained retry action. Notification intake itself does not change balances. Returning from checkout alone
does not establish success. Duplicate confirmations do not allocate twice.
The public return page also calls server-side verification.

Two payments can be pending against the same balance. If both later succeed,
the remaining debt is paid down to zero and the excess becomes visible
unallocated credit for review. The app never silently makes a negative debt or
pretends that excess funds were refunded.

Provider-side refunds, chargeback handling, settlements and actual merchant
onboarding are not implemented. Manual reversal is blocked for provider test
transactions. Non-success payment states remain unallocated; the app can retry
verification without inventing a successful payment. Live payments are outside
this release.

## Reminder processing and SMS sandbox

The new `.env.example` enables the embedded worker:

```dotenv
RUN_BACKGROUND_WORKER=true
SMS_MODE=dry_run
AUTOMATIC_REMINDERS=false
REMINDER_START_HOUR=9
REMINDER_END_HOUR=18
```

It checks jobs every 10 seconds. Payment verification runs at all hours;
reminder generation and processing respect the configured Africa/Accra window.
Previews follow an active repayment plan when present. They are eligible one day before due, on the due day, and every third overdue
day. Settled, paused, disputed and opted-out records are excluded.

Click **Prepare today's previews**, then **Queue dry run** to exercise dispatch.
The worker rechecks the current balance and contact state before processing.
Dry-run completion makes no provider request. Arkesel sandbox queues additionally require recorded permission for the current number. The UI shows attempt history.
Set `AUTOMATIC_REMINDERS=true` to automatically queue eligible previews in the
configured test mode. A processed reminder is not regenerated that day.

To exercise the external adapter without delivering to a phone:

```dotenv
SMS_MODE=arkesel_sandbox
ARKESEL_API_KEY=your_arkesel_key
SMS_SENDER_ID=SikaLedger
```

The adapter posts to Arkesel SMS API v2 with **sandbox fixed to true**. A successful
response is recorded as `sandbox_accepted`. A separate sandbox status report can
be reconciled using the optional callback path below. Real handset sending,
billing confirmation and sender-ID approval are not part of this release.
Startup rejects any live SMS mode.

Timeouts and ambiguous responses become `unknown`. Interrupted sends also become
`unknown` after a recovery interval; neither case is automatically resent.
Inspect provider history before taking further action. In this prototype a
queued submission holds a bounded database transaction while contacting the
sandbox, serializing it against payments and disputes. This favors pilot
consistency over throughput and needs redesign for high-volume deployment.

For a standalone worker, disable the embedded worker and use a second terminal:

```powershell
.\.venv\Scripts\python.exe -m app.worker --loop
```

Docker Compose already disables the embedded worker and starts a separate worker.
For an existing `.env`, add the worker and SMS settings above, or run the
standalone worker. No credentials are overwritten by the launcher.

## Arkesel callback reconciliation

On a public HTTPS deployment, optionally add:

```dotenv
ARKESEL_CALLBACKS_ENABLED=true
```

New sandbox submissions attach this callback URL:

```text
https://your-test-host.example/api/webhooks/arkesel
```

The endpoint accepts `sms_id` and `status` query parameters with GET or POST.
Unknown message IDs and unsupported statuses are ignored; duplicates are stored
once. Callback payloads are **untrusted hints**. The worker calls Arkesel's
message-details endpoint using the server's API key and stores the returned
status separately from the original submission outcome. Settings exposes lookup
jobs and explained retries, including rechecking a processed nonterminal report.
A retry performs a status lookup and never resends the message.

The exact SMS webhook signing contract could not be verified, so this version
does not claim SMS signature verification. It does not reuse the different IVR
signing contract. The lookup adapter currently expects a success object containing
`data.status`, an optional matching `data.id`, and an optional `data.recipient`.
This contract is tested with mocks and still needs validation against your actual
Arkesel sandbox account. Unexpected response shapes stop for review; no callback
claim is substituted. A sandbox report labelled DELIVERED is not evidence of a
message reaching a handset. Callback lookups run outside reminder quiet hours.

## Susu test contribution links

Create a savings account under **Susu savings**, then open its contribution link.
The private, account-free page supports the demo simulator and configured
Paystack test checkout. Starting checkout leaves savings unchanged; successful
simulation confirmation or matching provider verification credits it once.
The page lists confirmed online contributions and their sources. It does not
expose the customer's phone number. Account owners can rotate links through
`POST /api/susu/{account_id}/rotate-link`; UI rotation is not yet exposed.

Manual contributions and externally completed payouts remain available. Payouts
cannot exceed the recorded savings balance. No automatic payout, scheduled debit,
or real fund collection is enabled. Savings entries remain separate from debts.

## Corrections, payment recovery and reports

Use **Correct record** in debt details to change a due date, increase/decrease the
amount owed, or cancel a duplicate record with no payment history or intents.
Every correction requires a reason and retains the prior state. An amount change
appends a ledger adjustment; it does not overwrite the original sale or invent a
repayment. Cancellation is separately labelled and does not count as collections.
A stale balance blocks a correction until the debt is reloaded.

**Payment recovery** lists recent pending debt/susu checkouts and excess debt
credit. Reverify a Paystack test reference through the provider API, or save an
explained review of unallocated credit. A review note does not allocate funds,
refund money or clear the credit. Simulations must be completed on their private
customer page. The view checks the most recent 200 intents of each kind.

**Reports** provides date-filtered credit, adjustments and net recorded repayment
activity, plus current debt aging and downloadable customer account statements.
Repayments include manual and simulated entries, net of reversals. Aging and the
outstanding total show today's balances, not a historical end-date snapshot.
The report has a browser print action; print layout still needs target-browser QA.
Statements are account records, not evidence of bank settlement.

## Customer profiles, repayment plans and staff

**Customers → Open profile** shows every customer, including savings-only
customers. A profile combines debts, repayment records, savings accounts, disputes
and a chronological transaction timeline. Trade debt and savings have separate
totals. Owner contact reviews and PDF statement downloads are available here.

Use **Agree repayment plan** in a debt's details to record up to 60 dated
instalments. Confirm that the customer agreed. Amounts must total the current
balance exactly; dates must be unique, increasing, and today or later. This
creates no financial entry. New repayments satisfy the oldest instalment first.
Reminder previews use the first unpaid instalment's date and scheduled unpaid
amount; the full balance is also stated. Debt aging follows the active plan's
collection date. Dashboard and Gemini past-due totals count the overdue instalment amount, not the whole remaining plan. Full repayment completes the plan. Corrections and repayment
reversals mark it **needs review** and suppress reminders until an owner records
a replacement agreement or explicitly cancels the schedule. Older agreements
remain in history. The original sale due date is retained.

**Team** lets the owner create collectors, disable/re-enable them, and reset their
passwords. A disable or password reset revokes existing sessions. Owners cannot
be disabled through staff management. Existing business accounts retain owner
access. Collector logins are globally unique and belong to one business.

| Capability                                                           | Owner                       | Collector     |
| -------------------------------------------------------------------- | --------------------------- | ------------- |
| View debts, customer profiles, savings, reports and statements       | Yes                         | Yes           |
| Record debts and received cash/direct-MoMo repayments                | Yes                         | Yes           |
| Record contributions to an existing savings account                  | Yes                         | Yes           |
| Prepare and queue eligible reminder tests                            | Yes                         | Yes           |
| Create savings accounts or record payouts                            | Yes                         | No            |
| Correct/reverse records, manage plans/disputes/contact/private links | Yes                         | No            |
| Gemini, recovery, CSV, activity log and integration controls         | Yes                         | No            |
| Staff and document branding                                          | Yes                         | No            |
| Whole-instance backup and restore                                    | Server operator through CLI | No web access |

Permissions are enforced by the API, as well as reflected in the interface.
New authenticated audit events identify the staff account and role responsible.
Worker/customer actions and earlier audit events have no staff attribution.

## Contact correction and explicit SMS permission

An owner reviews the verified number, consent evidence and reason through a
customer profile. Changing a number rotates all that customer's debt and savings
links and pauses their debts. Granting consent does not resolve disputes or
silently resume paused reminders. Number collisions are rejected without merging
customers; a stale saved number requires reloading before editing.

Old records have no explicit SMS consent until reviewed. Dry-run previews remain
available, but Arkesel sandbox queues and dispatch require permission recorded
for the current number. Revoking consent suppresses contact, pauses debts and
invalidates queued previews. The app records the owner's attestation and evidence;
it does not independently verify phone ownership or collect electronic consent.

## Branded PDFs and database recovery

**Business branding** sets the printable business name, address, public contact,
footer and readable accent colour. Repayment acknowledgements, online susu
contribution acknowledgements and customer statements are available as PDFs.
Statements contain debt and savings sections with separate totals. Public
acknowledgements require the matching current private link; they omit the saved
phone number and link token. Manual, simulated, provider-test and reversed
transactions retain explicit labels. Text downloads remain supported.

**Backup & recovery** explains the operator CLI. SQLite backups are consistent
snapshots with checksums, schema/integrity checks and a format version. They
include all businesses, so no tenant can download or restore them through the
website. Restoration requires every app/worker connection to be closed, creates
a safety backup, revokes sessions and stops stale or uncertain queued work.
Archives contain private records/password hashes and are not encrypted; protect
them with operating-system access controls and private storage. PostgreSQL uses
the database host's native recovery process. See `docs/BACKUP_RECOVERY.md`.

## Update an existing installation

Follow `docs/UPGRADING.md`. Back up the database with the app and worker stopped,
replace source files, preserve `.env` and the database, install requirements and
restart. Startup adds the new tables without replacing existing ledger columns.

## Payment acknowledgements and private links

Confirmed and reversed repayments have a downloadable text acknowledgement on
both trader and customer views. Each document identifies the payment source,
allocation, current balance and reversal status. A simulation cannot masquerade
as a provider-verified transaction. These are account acknowledgements, not tax
invoices. They do not include a customer's full phone number or private token.

Use **Replace private link** in the debt details to invalidate an earlier link.
Ledger entries and pending provider references remain intact and reconcilable.
Distribute the new link privately. Links do not yet expire automatically.

## Run automated checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

On macOS/Linux use `.venv/bin/python -m pytest -q`.
If Node is installed, `node --check app/static/app.js` checks frontend syntax.
For reproducible DOM interaction tests, use a Node version supported by jsdom
(the tested dependency requires Node 22.22.2, 24.15.0, or newer compatible releases),
then install the optional development dependency:

```bash
npm install
npm run test:ui
```

This uses the project `.venv` Python by default. Set `SIKA_TEST_PYTHON` if using
another interpreter. The UI test transport calls the real FastAPI app in-process
and mocks Gemini planning; it starts no web server and calls no external service.
Node/jsdom are test dependencies only; running the application needs only Python.
The backend's interactive API documentation is at http://127.0.0.1:8000/docs.

The supplied tests exercise local end-to-end API workflows, concurrent duplicate
confirmation, merchant isolation, financial invariants, dispute suppression,
CSV injection protection, and mocked external provider contracts. They are not
proof of actual Gemini, MoMo or SMS service access. PostgreSQL and Docker paths
are supplied but have not been exercised in this environment. Browser
visual and full-browser interaction QA could not be completed because the preview environment lacks
the Python runtime needed to launch FastAPI.

DOM-level interface interaction checks did pass against the actual in-process
FastAPI application, including registration, debt entry, manual repayment,
customer payment simulation, disputes, manual and online savings contributions,
audited corrections, collection report filters, pending payment recovery, Gemini
follow-ups and reminder queuing. These checks
do not validate visual layout or browser-specific behavior. See
`docs/VERIFICATION.md` for the complete verification boundary.

## Database and hosting

SQLite is the default and is suitable for this local demonstration. It uses WAL
and immediate write transactions. PostgreSQL support is configured with:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@host:5432/sika
```

Balance-consuming writes lock the relevant debt/account rows under PostgreSQL.
Schema creation is automatic. Versions 0.2, 0.3 and 0.4 add tables without changing
existing v0.1 columns, so startup creates them while retaining prior data.
General-purpose versioned migrations are not included. Do not point a later schema version at valuable data without a backup
and migration procedure. Keep the database outside transient deployment storage.

Optional local Docker setup, after copying `.env.example` to `.env`:

```bash
docker compose up --build
```

This starts the web app and dry-run worker with a persistent database volume.
The published port binds only to localhost. The environment's default
`PUBLIC_BASE_URL` must match the origin used in the browser; `localhost` and
`127.0.0.1` are different origins. Use 127.0.0.1 with the default configuration.

The Sites static/Worker host cannot execute FastAPI. This source project must
run locally or on a Python-capable host; no hosted deployment is included.

## Files and implementation notes

- `app/main.py`: authentication, scoped API, provider verification and web UI routes.
- `app/db.py`: schema and transaction boundaries.
- `app/domain.py`: money handling, ledger calculations, payment allocation and reversal.
- `app/agent.py`: Gemini structured planner and allowlisted tools.
- `app/jobs.py`: durable verification, reminder dispatch and conversation cleanup.
- `app/worker.py`: standalone worker entrypoint.
- `app/savings.py`: separate savings verification and contribution allocation.
- `app/sms_callbacks.py`: untrusted callback intake and authenticated status lookup.
- `app/operations.py`: audited corrections, payment recovery and reports.
- `app/workspace.py`: profiles, contacts, agreements, staff and branding.
- `app/access.py`: owner/collector permissions and request attribution.
- `app/plans.py`: ledger-derived instalment progress.
- `app/pdf_documents.py`: portable, branded PDF documents.
- `app/backup.py` and `app/maintenance_lock.py`: operator backup/recovery and connection leases.
- `app/static/`: responsive interface and customer payment page.
- `tests/`: behavioral and DOM interaction tests; external integrations are mocked.
- `docs/ARCHITECTURE.md`: data model, invariants and threat boundaries.
- `docs/DELIVERY_PLAN.md`: milestones and outstanding acceptance work.
- `docs/DEMO_CHECKLIST.md`: manual demonstration and verification steps.

Financial history is append-only through the application API, but the database
is not a tamper-evident audit system. Authentication uses scrypt password hashes,
hashed server-side session tokens, HttpOnly SameSite cookies and origin checks.
Rate limits are in-process and reset on restart; they are not a distributed abuse
control. Owner account recovery, independent email/phone verification, formal security
review and complete operational monitoring are not part of this prototype.

## Primary implementation references

Checked 26 October 2026. Recheck provider documentation before integrating.

- Gemini structured output: https://ai.google.dev/gemini-api/docs/generate-content/structured-output
- Gemini model IDs: https://ai.google.dev/gemini-api/docs/models
- Paystack initialize/verify transactions: https://paystack.com/docs/api/transaction/
- Paystack webhook verification: https://paystack.com/docs/payments/webhooks/
- Arkesel sandbox API: https://developers.arkesel.com/
- Arkesel integration guide: https://arkesel.com/bulk-sms-api-developers-ghana/
- Paystack test keys and environment limitations: https://paystack.com/docs/api/authentication/

The assignment's historical fee estimates and startup budget are not treated as
verified current prices in this implementation.
