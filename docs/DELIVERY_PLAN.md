# Prototype delivery plan

Owner: Raphael. Target: **7 November 2026**, Africa/Accra.

This bundle is a runnable first implementation, not a completed live payment
operation. Dates below are proposed work milestones, not background jobs or
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
- Separate basic susu contribution and payout records; CSV export and action log.

## Evidence still needed for the final demonstration

- Real Gemini access, supported model and quota using Raphael's server credentials.
- Real Paystack **test** account access and supported Ghana test channel behavior.
- Successful provider test webhook from an HTTPS Python deployment, if external
  sandbox demonstration is required. A mocked contract test is not that evidence.
- Browser visual and interaction review. The current environment could not start
  the FastAPI process in its browser preview runtime.
- Actual Arkesel sandbox acceptance with Raphael's key. The adapter is implemented
  and contract-tested with mocks; live handset delivery remains outside this release.
- A decision on whether automatic susu payment links are required on 7 November.
  The current savings module records externally completed activity only.

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
