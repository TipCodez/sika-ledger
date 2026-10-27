# Architecture and financial rules

The prototype has one FastAPI application, one relational database and a worker for payment verification and reminder processing. FastAPI serves both the web client and the JSON API.
Gemini and Paystack are called from the backend; credentials never reach the browser.

## Data ownership

Every merchant-owned record carries `merchant_id`. Authenticated handlers derive
the merchant from a server-side session and scope reads and writes accordingly.
The public debt and susu routes require a random 256-bit capability token. That token
grants access to one debt or savings page and its payment acknowledgements; share it privately.
It does not grant access to the merchant dashboard or other customers. Tokens
expire after 30 days and can be renewed by their owner. Renewal replaces the
token, invalidates the previous link and starts a fresh 30-day period. Existing
installations add the expiry columns at startup; older links receive 30 days
from their first upgraded startup.

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
columns remain unchanged. Version 0.3 adds:

| Table                | Purpose                                      | Relationship          |
| -------------------- | -------------------------------------------- | --------------------- |
| susu_links           | Rotatable private contribution token         | One savings account   |
| susu_payments        | Pending/confirmed test contribution intents  | One savings account   |
| sms_callback_inbox   | Deduplicated callback hints and lookup jobs  | One saved SMS attempt |
| sms_delivery_reports | Authenticated provider lookup result         | One saved SMS attempt |
| debt_changes         | Explained correction request and prior state | One debt              |
| debt_flags           | Explicit cancellation state                  | One debt              |
| credit_reviews       | Latest human review of unallocated credit    | One debt payment      |

These additions do not alter existing columns. The upgrade preservation test
starts with the v0.1 set of tables and checks the original balance and creation
of all later tables after current-version startup. Older savings accounts receive
a private link lazily when listed.

## Debt ledger

All stored financial amounts are integer pesewas. The balance is the sum of
signed debt ledger entries, never a value invented or written by an LLM.

| Entry                                | Sign     | Example         |
| ------------------------------------ | -------- | --------------- |
| Opening credit sale                  | Positive | +50,000 pesewas |
| Repayment applied to debt            | Negative | −20,000 pesewas |
| Reversal of repayment allocation     | Positive | +20,000 pesewas |
| Explained amount correction          | Either   | +5,000 pesewas  |
| Cancellation without payment history | Negative | −50,000 pesewas |

An initial payment is a distinct trader-reported payment and ledger entry.
The principal remains the original credit sale value. Adjusted credit is the
opening principal plus signed amount adjustments. Repayments equal adjusted
credit minus current balance, with cancelled debts explicitly reporting zero
collected. Cancellation requires no payment history or pending intents; it
appends an offsetting cancellation entry and retains the original record.
Corrections require an expected current balance and an idempotency key. Reasons
and before/after request data remain in `debt_changes`; reminder previews are
invalidated in the same transaction. Manual and simulation
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

1. The backend creates a pending intent linked to merchant and debt or savings account.
2. The provider handles test payment authorization; the app never requests a PIN.
3. A signed webhook or customer return requests server-side verification.
4. The backend checks success, expected reference, exact amount, currency,
   provider test domain and merchant/entity metadata, including susu contribution purpose.
5. The transaction allocates the repayment or savings contribution once and returns the new balance.

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
Optional callback reconciliation stores incoming SMS status claims as untrusted
hints. It resolves only known sandbox message IDs and uses an authenticated
Arkesel message-details lookup for the separate `sms_delivery_reports` record.
Unsupported response shapes, mismatched IDs/recipients and conflicting terminal
reports stop for review. Lookup failures retry with backoff, up to six attempts.
Callbacks cannot change ledger balances or resubmit SMS. An explained Settings
retry can recheck a completed lookup when the provider status changes. SMS
signature verification is not implemented because the exact signing contract
was not available; the IVR algorithm is not assumed equivalent. The message-details
response contract is mock-tested and still requires actual account validation.
Version 0.4 adds the owner-reviewed contact and consent workflow described below.

## Susu separation

Savings contributions increase the collector's recorded payout obligation;
completed manual payouts reduce it. A payout cannot exceed recorded contributions.
Neither event reduces a customer's trade debt. A private contribution link
creates test intents in `susu_payments`; confirmation creates exactly one positive
`susu_ledger` entry. Contribution confirmation and manual payout lock the same
savings account. The simulator and provider metadata enforce the account boundary.
Automatic real collection, scheduled debits, fees, savings disputes and provider
settlement reconciliation require a fuller savings-specific design.

## Repayment acknowledgement

A text download is generated from the existing payment and ledger records. The
merchant endpoint enforces tenant ownership; the public endpoint enforces the
current debt token and matching payment. Reversed payments remain visible with
an explicit reversal label. The document distinguishes trader-reported,
simulated and server-verified provider test transactions.

## Recovery and reporting

Payment recovery exposes the merchant's most recent 200 intents of each kind,
filtered to pending intents or debt excess credit. Reverification uses the same
server verification function as public returns and webhook processing. Credit
reviews append audit events and update a note; they do not move or clear credit.

Collection reports sum dated ledger entries for new credit, amount adjustments
and net recorded repayments. Cancellations are not repayments. Debt aging and
outstanding totals are calculated from current balances, independently of the
selected reporting period. Customer statements require merchant ownership and
include ledger history without claiming that manual or simulated records prove
bank settlement. Historical balance snapshots are not implemented.

## Version 0.4 ownership and additive tables

| Table               | Purpose                                                           |
| ------------------- | ----------------------------------------------------------------- |
| staff_accounts      | Owner/collector identities, password hashes and enabled state     |
| session_actors      | Maps a session to its individual staff identity                   |
| audit_actors        | Immutable actor name/role snapshot for a recorded audit event     |
| contact_preferences | Consent attestation for the currently verified phone              |
| contact_history     | Explained before/after phone and permission reviews               |
| repayment_plans     | Agreed baseline balance, idempotent request and agreement history |
| plan_instalments    | Ordered dated amounts belonging to a plan                         |
| business_branding   | Printable business identity, contact details and accent           |

Existing merchants become owners without changing their IDs or password hashes.
An old session without a session_actors row resolves only to that merchant's owner.
New sessions always bind a staff identity. Every authenticated request resolves
current active state from the database, and collector access uses a default-deny
route allowlist. The API separately rejects collector savings payouts. Password
resets and access changes delete that staff member's session mappings and sessions.
Owners cannot be demoted/disabled through the staff-management endpoints.

A middleware ContextVar carries the authenticated actor into sync handlers and
ledger audit calls. Actor snapshots are saved transactionally with audit events;
public-token actions and workers have no authenticated staff actor. Account
creation, staff management and correction still use merchant-scoped ownership
checks. Hiding buttons is not the authorization boundary.

## Instalment and contact invariants

An active plan's positive, ordered instalments sum exactly to its creation-time
outstanding balance. A plan changes no ledger amount. New recorded repayments
satisfy the oldest instalment first using baseline minus the current balance.
Reversals and debt corrections invalidate the agreement before that calculation
can misrepresent a new basis. The latest plan may be completed, replaced,
cancelled or need review. A review-required plan suppresses reminders. The
original due date remains stored; active collection dates drive reminders and
current aging. Aging buckets hold full debt balances grouped by the first unpaid
collection date; dashboard/Gemini overdue amounts count only past-due unpaid
instalments on active plans. A cancelled schedule resumes the original collection date.

Contact review requires the current saved phone as an optimistic concurrency
check. A changed phone rotates all associated public links, pauses debts and
invalidates reminder previews. Customer identity and financial history remain
unchanged; matching another customer's phone is a conflict, not a merge. Consent
is an owner attestation with evidence, bound to the current phone. Old contacts
start without an attestation. Dry runs can prepare fictional previews, but
sandbox queueing and final dispatch both enforce explicit permission. Revocation
sets contact suppression and invalidates queued previews; permission alone never
resolves a dispute or removes an existing pause.

## PDF and recovery boundaries

PDF downloads use bundled, licensed Unicode fonts and ReportLab. Owner branding
is escaped as text; no HTML, remote images or arbitrary URLs are executed.
Acknowledgements enforce merchant ownership or a matching current public token.
Their source/reversal labels and privacy rules remain the same as text downloads.
Statements separate debt and savings ledgers. Accent colours require readable
contrast on white. Representative one-page and multi-page output was rendered
for visual review; arbitrary future customer content still needs normal QA.

The backup CLI operates outside the web authorization model under filesystem
operator authority. No HTTP route exposes a multi-merchant database archive.
SQLite connections hold shared sidecar leases; restore requires an exclusive
lease, validation and a pre-restore safety snapshot. All installed app/worker
processes cooperate. External clients and old source versions must be closed.
Restoration revokes sessions and prevents queued work from replaying automatically.
See BACKUP_RECOVERY.md for the archive contract and operating procedure.

## Privacy and retention boundary

`GET /privacy` serves the public application shell; the frontend renders an
account-free privacy notice before requesting an authenticated session. Sign-in,
debt payment and susu contribution screens link to it. The notice documents the
prototype's stored data, test-provider disclosures, customer choices and
security limitations without claiming production compliance.

Sessions expire after 12 hours, saved assistant conversations after 24 hours,
and debt/susu private links after 30 days unless renewed. Financial, contact and
audit records have no automatic deletion lifecycle in this release. Production
operation therefore requires a defined controller/operator, lawful basis,
retention schedule and request-handling process outside the current codebase.

Private debt and susu API responses include the configured branding display name
and public contact for that merchant. They deliberately omit the branding address
and footer. If no branding contact exists, the response uses `null` rather than
exposing an account login or another internal identifier.
