# Verification record

Date: 27 October 2026. Version: 0.4.0.

## Passed

- **131 pytest cases**, all passed. The suite includes parametrized invalid-money
  inputs, partial repayments, duplicate requests, reversals, concurrent duplicate
  confirmation, excess credit, tenant isolation, public-link scope, disputes,
  reminder invalidation, susu payout limits, CSV formula escaping, origin checks,
  Gemini consent and confirmation, provider signature/matching and live-key rejection.
  New cases cover durable notifications, retry/backoff, quiet-hour independence,
  interrupted sandbox submission, follow-up history and request idempotency,
  receipt scope, link replacement and an additive v0.1 database upgrade. Version
  0.3 cases cover separate savings checkouts, concurrent contribution confirmation,
  account metadata and webhook replay, public-link privacy, audited adjustments,
  cancellation without fictitious collections, stale-balance rejection, recovery
  and statement isolation, unchanged excess credit after review, report dates,
  callback deduplication, authenticated lookup, terminal status regression,
  recipient matching, lookup failures and explained retry without SMS resend.
- Version 0.4 cases cover customer/savings aggregation and tenant isolation,
  agreement totals/date validation/idempotency, oldest-instalment allocation,
  plan-aware worker/Gemini reminders and dashboard overdue totals, schedule invalidation/completion/history, phone-bound
  permission, consent withdrawal, private-link rotation, default-deny collector
  permissions, staff session revocation, audit attribution, owner migration,
  labelled/private PDF documents, consistent online SQLite snapshots, checksums,
  restore lock enforcement, safety backups, session revocation and stopped
  in-flight jobs after restore. Private-link cases cover debt and susu expiry,
  blocked public checkout, owner renewal and additive legacy-database columns.
  Privacy-notice cases cover unauthenticated page access, links from all public
  entry points and disclosure of the prototype's retention limitation. Public
  business-contact cases cover debt and susu pages, branding-name fallback and
  non-disclosure of the configured address and document footer.
  PostgreSQL is explicitly rejected by the SQLite
  recovery tool.
- JavaScript syntax check completed successfully.
- Interface interaction checks completed using a DOM environment connected to
  the actual FastAPI application through its in-process HTTP test transport.
  Registration, sample loading, navigation, debt creation, manual partial payment,
  savings contribution, public simulated repayment and dispute reporting passed.
  The extended checks also exercised Gemini clarification/follow-up with a mocked
  planner, proposal confirmation, history restoration/clearing, and reminder
  queuing. Version 0.3 also exercises audited amount correction, report/date-filter
  rendering, pending payment recovery and an account-free simulated online susu
  contribution that leaves trade debt unchanged. There were no captured
  JavaScript error events in these flows. The v0.4 interface tests add complete
  customer profiles, contact consent, agreed instalments, branding, backup guidance,
  collector creation/login and a restricted collector repayment.
  The scripts are included under `tests/ui_bridge.py` and `tests/ui_workflows.cjs`.
- A generated repayment PDF and both pages of a generated customer statement
  were rendered to images and visually inspected. Text, labels, table wrapping,
  repeated headers, separate savings totals and page footers were readable.
- The reminder worker's one-shot dry-run entrypoint executed successfully.

The test run emitted one dependency deprecation warning: the installed Starlette
test client still supports the supplied `httpx` transport but recommends its
newer transport package. It did not cause any test failures.

## Not verified

- Actual Gemini API access, quota, extraction quality or model response behavior.
  Tests used mocked HTTP/model responses; no API key was available.
- Actual Paystack test checkout or external webhook delivery. HTTP contracts were
  mocked. No real or provider-sandbox funds were collected in this session.
- Actual Arkesel sandbox acceptance, callback arrival or message-details JSON
  compatibility. Submission and status lookup are HTTP-contract-tested with mocks;
  callback claims alone never establish a status. The full SMS signing contract
  was unavailable, so SMS signature verification is not implemented. Live SMS is
  blocked, and sandbox reports do not prove handset delivery.
- Browser rendering, responsive visual layout, browser cookie policies and
  cross-tab behavior. The managed preview environment could not launch the Python
  interpreter required by FastAPI. DOM checks do not verify these properties.
- PostgreSQL, Windows launcher execution, Windows LockFileEx behavior, Docker
  build or hosted deployment. SQLite backup/restore and POSIX locking were tested
  on Linux. No Task Scheduler or other scheduled backup was installed.
  Launcher scripts still require confirmation on Raphael's Windows machine.
- Browser print output, responsive layout and actual provider test contribution
  returns on the new savings page.
- Load testing, formal security audit, disaster recovery on the delivery host, or production readiness.

Use DEMO_CHECKLIST.md for manual checks on the target machine. Preserve these
distinctions in the project demonstration: tests that mock an external service
do not establish that the external integration works with your credentials.
