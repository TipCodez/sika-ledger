# Verification record

Date: 26 October 2026. Version: 0.2.0.

## Passed

- **57 pytest cases**, all passed. The suite includes parametrized invalid-money
  inputs, partial repayments, duplicate requests, reversals, concurrent duplicate
  confirmation, excess credit, tenant isolation, public-link scope, disputes,
  reminder invalidation, susu payout limits, CSV formula escaping, origin checks,
  Gemini consent and confirmation, provider signature/matching and live-key rejection.
  New cases cover durable notifications, retry/backoff, quiet-hour independence,
  interrupted sandbox submission, follow-up history and request idempotency,
  receipt scope, link replacement and an additive v0.1 database upgrade.
- JavaScript syntax check completed successfully.
- Interface interaction checks completed using a DOM environment connected to
  the actual FastAPI application through its in-process HTTP test transport.
  Registration, sample loading, navigation, debt creation, manual partial payment,
  savings contribution, public simulated repayment and dispute reporting passed.
  The extended checks also exercised Gemini clarification/follow-up with a mocked
  planner, proposal confirmation, history restoration/clearing, and reminder
  queuing. There were no captured JavaScript error events in these flows.
  The scripts are included under `tests/ui_bridge.py` and `tests/ui_workflows.cjs`.
- The reminder worker's one-shot dry-run entrypoint executed successfully.

The test run emitted one dependency deprecation warning: the installed Starlette
test client still supports the supplied `httpx` transport but recommends its
newer transport package. It did not cause any test failures.

## Not verified

- Actual Gemini API access, quota, extraction quality or model response behavior.
  Tests used mocked HTTP/model responses; no API key was available.
- Actual Paystack test checkout or external webhook delivery. HTTP contracts were
  mocked. No real or provider-sandbox funds were collected in this session.
- Actual Arkesel sandbox acceptance or handset delivery. The sandbox adapter is
  implemented and HTTP-contract-tested with mocks; live SMS is blocked.
- Browser rendering, responsive visual layout, browser cookie policies and
  cross-tab behavior. The managed preview environment could not launch the Python
  interpreter required by FastAPI. DOM checks do not verify these properties.
- PostgreSQL, Windows launcher, Docker build or hosted deployment.
- Load testing, formal security audit, data recovery or production readiness.

Use DEMO_CHECKLIST.md for manual checks on the target machine. Preserve these
distinctions in the project demonstration: tests that mock an external service
do not establish that the external integration works with your credentials.
