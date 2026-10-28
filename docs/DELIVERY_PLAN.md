# Prototype delivery plan

Owner: Raphael. Target: **7 November 2026**, Africa/Accra.

This v0.4 bundle implements the prototype workflows and guarded Paystack live
collection, refund/dispute intake and settlement reconciliation foundations.
Provider approval and real-money acceptance testing remain operational prerequisites.
Dates below are proposed work milestones, not background jobs or
promises that unattended work has been scheduled.

| Dates         | Milestone                                           | Evidence required                                                                          |
| ------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 26–27 October | Review prototype and freeze demonstration scope     | App starts on Raphael's machine; agreed debt and susu scope                                |
| 28–29 October | Enable Gemini and test realistic entry requests     | Successful real API call; missing-field and ambiguous-date examples; confirmed proposals   |
| 30–31 October | Exercise provider sandbox end to end                | Test checkout, authenticated webhook, verification, duplicate replay and partial repayment |
| 1–2 November  | Validate reminder workflow and optional SMS sandbox | Correct schedule, suppression and updated remaining balance; explicit delivery status      |
| 3–4 November  | User workflow and responsive testing                | Desktop and low-cost Android-width checks; debtor can pay without an account               |
| 5 November    | Run regression and prepare demo data                | Tests pass; credentials excluded; known limitations recorded                               |
| 6 November    | Rehearse demonstration and prepare backup           | Repeatable demo script and database backup; local fallback works                           |
| 7 November    | Deliver working prototype                           | Source, setup guide, demonstration and evidence of integration status                      |

## Scope included now

- Business registration and sign-in; merchant-scoped debt records.
- Mobile-first web interface and private, account-free customer payment page.
- Multiple debts per customer, partial repayments, manual cash/direct-MoMo records.
- Append-only application ledger, reversals, idempotent allocation and excess credit.
- Paystack test-only initialization, webhook signature check and server verification.
- Gemini structured planner, read tools, debt proposals and explicit confirmation.
- Scheduled reminder dry runs and Arkesel sandbox adapter, dispute review and wrong-number suppression.
- Saved Gemini follow-ups, durable verification inbox, link replacement and downloadable acknowledgements.
- Automatic 30-day expiry and owner renewal for debt and susu private links.
- Public, account-free privacy notice linked from sign-in and private customer pages.
- Configured public business contact on matching debt and susu customer pages.
- Guarded live Paystack mode with owner-linked merchant subaccounts and masked settlement data.
- Idempotent Paystack refunds, durable dispute events and transaction-level settlement reconciliation.
- Guarded Arkesel live mode retaining consent, quiet-hour and unknown-outcome protections.
- Separate susu records, private simulator/Paystack test contribution links, and manual payouts.
- Audited due-date/amount corrections and cancellation of duplicate unpaid records.
- Pending-payment recovery and explained unallocated-credit review notes.
- Collection reports, current aging, customer statements and browser print action.
- Optional Arkesel callback hints with authenticated provider status lookup, durable jobs and explained retries.
- Complete customer profiles, including savings-only customers and transaction history.
- Agreed repayment instalments with ledger-derived progress and plan-aware reminders.
- Audited contact correction, explicit phone-bound SMS permission and link replacement.
- Branded PDF statements and repayment/online-contribution acknowledgements.
- Owner/collector API permissions, immediate session revocation and staff attribution.
- Operator SQLite backups, validation, offline restore and automatic safety snapshots.
- CSV export and action log.

## Evidence still needed for the final demonstration

- Real Gemini access, supported model and quota using Raphael's server credentials.
- Real Paystack **test** account access and supported Ghana test channel behavior.
- Successful provider test webhook from an HTTPS Python deployment, if external
  sandbox demonstration is required. A mocked contract test is not that evidence.
- Browser visual and interaction review. The current environment could not start
  the FastAPI process in its browser preview runtime.
- Actual Arkesel sandbox acceptance with Raphael's key. The adapter is implemented
  and contract-tested with mocks; live handset delivery remains outside this release.
- Actual Arkesel message-details response compatibility, callback arrival and
  authenticated lookup using Raphael's account. SMS signature validation is not
  claimed; unsupported response shapes stop for review.
- Susu test checkout, provider return and webhook replay against the actual test
  account; existing financial invariants are covered locally with mocks.
- Visual review of reports/print output and all new screens on the target browser.
- Windows launcher, Windows maintenance locking and backup/restore rehearsal.
- Owner/collector workflow rehearsal with two people and an actual backup location.
- PostgreSQL backup/recovery through the selected host, if PostgreSQL is selected.

## Acceptance scenario

A trader records GH₵500 owed by Abena. Abena pays GH₵200 using a clearly identified
simulator or verified provider test transaction. The outstanding balance becomes
GH₵300. Replaying the same confirmation leaves it at GH₵300. The next reminder
preview says GH₵300. Paying the remainder settles the debt and suppresses further
reminders. A second merchant cannot read or change the trader's records. A Gemini
debt proposal does not enter the ledger until the trader confirms it.

## Release boundary

Do not present this version as live MoMo collection, a regulated savings product,
a lending decision system, or a production-secure multi-merchant service. Live
settlement arrangements, operational safeguards and current legal requirements
must be established separately if the project moves beyond a prototype.
