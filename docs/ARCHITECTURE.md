# Architecture and financial rules

The prototype has one FastAPI application, one relational database and a worker for payment verification and reminder processing. FastAPI serves both the web client and the JSON API.
Gemini and Paystack are called from the backend; credentials never reach the browser.

## Data ownership

Every merchant-owned record carries `merchant_id`. Authenticated handlers derive
the merchant from a server-side session and scope reads and writes accordingly.
The public debt route requires a random 256-bit capability token. That token
grants access to one debt page and its payment acknowledgements; share it privately.
It does not grant access to the merchant dashboard or other customers. Tokens
can be replaced by their owner; replacement invalidates the previous link.
Automatic expiry remains a hardening item before real use.

## Tables and relationships

| Table           | Purpose                                                         | Key relationship                       |
| --------------- | --------------------------------------------------------------- | -------------------------------------- |
| merchants       | Business identity and password hash                             | Parent tenant                          |
| sessions        | Hashed session token and expiry                                 | One merchant                           |
| customers       | Name, normalized Ghana phone, contact suppression               | One merchant                           |
| debts           | Credit-sale principal, due date, private link, collection flags | One merchant and customer              |
| ledger          | Signed financial entries                                        | One debt                               |
| payments        | Intent, confirmation, amount allocation and excess credit       | One debt and unique provider/reference |
| disputes        | Customer report and recorded resolution                         | One debt                               |
| outbox          | Dated, deduplicated reminder preview                            | One debt per day                       |
| audit           | Application action history                                      | One merchant                           |
| agent_proposals | Expiring unconfirmed debt drafts                                | One merchant                           |
| susu_accounts   | Contributor savings record and daily target                     | One merchant and customer              |
| susu_ledger     | Contributions and manually completed payouts                    | One savings record                     |

Version 0.2 also adds `webhook_inbox` for durable verification jobs,
`sms_attempts` for submission outcomes, and `agent_conversations`/`agent_turns`
for expiring conversation history. These are additive tables; existing ledger
columns remain unchanged. The upgrade preservation test starts with the v0.1
set of tables and checks the original balance after v0.2 startup.

## Debt ledger

All stored financial amounts are integer pesewas. The balance is the sum of
signed debt ledger entries, never a value invented or written by an LLM.

| Entry                            | Sign     | Example         |
| -------------------------------- | -------- | --------------- |
| Opening credit sale              | Positive | +50,000 pesewas |
| Repayment applied to debt        | Negative | −20,000 pesewas |
| Reversal of repayment allocation | Positive | +20,000 pesewas |

An initial payment is a distinct trader-reported payment and ledger entry.
The principal remains the original credit sale value. Manual and simulation
payments are explicitly labelled and must not be presented as provider verification.

Confirmed provider payments allocate `min(current balance, confirmed amount)`.
Any excess stays attached to the payment as unallocated credit. This supports
the case where several checkout intents were opened before either settled.
Unallocated credit requires human review; there is no automatic refund or credit
transfer feature. Ledger reversals do not initiate refunds.

## Idempotency and transactions

Payment requests carry an idempotency key. Reusing a key for different amounts,
debts or payment sources produces a conflict. Provider references are globally
unique. A repeated confirmation does not create another journal entry.

SQLite uses `BEGIN IMMEDIATE` to serialize writers. PostgreSQL takes row locks
on the debt before allocating or reversing payments. Ledger writes, payment
state, outbox invalidation and action audit share one database transaction.
The concurrent confirmation test checks that five simultaneous confirmations
produce one allocation. PostgreSQL concurrency needs its own integration run
before claiming that database configuration has been tested.

## Payment boundary

1. The backend creates a pending intent linked to merchant and debt.
2. The provider handles test payment authorization; the app never requests a PIN.
3. A signed webhook or customer return requests server-side verification.
4. The backend checks success, expected reference, exact amount, currency,
   provider test domain and merchant/debt metadata.
5. The transaction allocates the repayment once and returns the new balance.

Webhook HMAC uses the raw body. A supported event is deduplicated by payment
reference and committed to `webhook_inbox` before acknowledgment. The worker
leases jobs, verifies provider status, and retries transient failures with bounded
exponential backoff. Invalid matching information and exhausted retries stop for
review. Lease expiry allows safe recovery because financial allocation is
idempotent. A reclaimed worker cannot overwrite a newer lease's completion.
The trader can inspect jobs and request explained retries from Settings.

The simulator is a separate, explicitly labelled pathway enabled by `DEMO_MODE`.
It confirms only simulation intents for the supplied debt token. Startup rejects
live payment keys. The shared test account arrangement is not an approved
multi-merchant settlement model.

## Gemini boundary

The conversation-aware agent produces a validated structured plan with an allowlisted
action: portfolio summary, customer lookup, debt proposal, reminder draft or
clarification. The authenticated merchant identity is injected by the backend,
never accepted from the model. Numeric summaries are calculated in application
code; reminder facts come from current ledger records. The model cannot choose
an arbitrary API endpoint, SQL query, destination account or executable command.

Debt proposals do not affect financial balances. Confirmation revalidates the
payload, checks tenant, expiry and one-time state, and creates the debt in one
transaction. Ambiguous/missing fields should lead to clarification; all extracted
values still need human review. Model instructions alone are not a security
boundary: authorization and financial validation are enforced in code.

Conversations and paired user/result turns are stored in `agent_conversations`
and `agent_turns`. The last six turns inform follow-ups; history is scoped to the
authenticated merchant and expires after 24 hours. A request ID deduplicates each
turn. A short busy lease prevents overlapping turns. Model failures release the
lease. Clearing history cancels unconfirmed proposals and erases their payloads;
confirmed financial entries remain in the separate ledger.

## Reminder and dispute rules

Generation is due-date based and respects settled debt, pause flags, active
disputes and wrong-number contact suppression. An existing preview is marked
stale after a repayment, reversal or contact-state change. A customer wrong-number
report suppresses all debts for that saved customer. Resolving a dispute leaves
reminders paused, and does not automatically clear wrong-number suppression.

The worker supports dry-run completion and Arkesel sandbox acceptance. Live SMS
is blocked. Preview/queued records are invalidated after balance/contact changes.
Immediately before submission the worker rechecks eligibility and rebuilds the
message. Each attempt is recorded in `sms_attempts`. Submission timeouts and
interrupted claims become unknown outcomes and are never automatically resent.
Provider acceptance is not a handset delivery report. The bounded submission
holds database locks during the network call; this limits pilot throughput.
Live delivery callbacks and opted-in contact correction remain separate work.

## Susu separation

Savings contributions increase the collector's recorded payout obligation;
completed manual payouts reduce it. A payout cannot exceed recorded contributions.
Neither event reduces a customer's trade debt. Contributions, schedules, payout
fees, disputes and provider reconciliation need a fuller savings-specific design
before enabling automatic susu payments.

## Repayment acknowledgement

A text download is generated from the existing payment and ledger records. The
merchant endpoint enforces tenant ownership; the public endpoint enforces the
current debt token and matching payment. Reversed payments remain visible with
an explicit reversal label. The document distinguishes trader-reported,
simulated and server-verified provider test transactions.
