# Manual demonstration checklist

Use fictional people and test credentials. Keep a separate empty database for
the final rehearsal. Do not delete valuable records to make room for samples.

## Start and isolation

- [ ] App starts using `start.ps1`; the page opens at the configured exact origin.
- [ ] Registration and login work; logout prevents access to private APIs.
- [ ] A second business sees none of the first business's customers or debts.
- [ ] Navigation and forms work at 375 px and desktop widths, including keyboard use.
- [ ] Server restart preserves debts and repayments.

## Debt and payments

- [ ] Record GH₵500 due on a chosen date.
- [ ] Starting GH₵200 checkout leaves the balance at GH₵500.
- [ ] Confirm the simulator: balance becomes GH₵300 and source says simulation.
- [ ] Replay confirmation: balance stays GH₵300.
- [ ] Pay the remainder: balance becomes zero and status says paid.
- [ ] Reverse a manual/simulation entry with a reason: history remains visible.
- [ ] Invalid, zero, negative and three-decimal-place amounts are rejected.
- [ ] Concurrent pending payments can produce visible excess credit, not a negative debt.
- [ ] If using Paystack, demonstrate real test checkout and server verification.
- [ ] A browser return without successful provider verification never marks paid.
- [ ] A signed notification enters the durable inbox before balance allocation.
- [ ] Worker processing verifies and allocates it once; transient failures retry.

## Customer page and reminders

- [ ] Debtor opens the private link without signing up.
- [ ] Another invalid link does not reveal account details.
- [ ] Wrong-number report suppresses contact across that saved customer's debts.
- [ ] Incorrect-balance report suspends reminders and new checkout.
- [ ] Resolution records a reason and leaves reminders paused.
- [ ] Reminder generation selects the correct due-date cases.
- [ ] After a partial repayment, regenerate a stale preview with the remaining balance.
- [ ] Settled debts generate no new reminder previews.
- [ ] Queue a reminder and observe dry_run_complete with an attempt record.
- [ ] With an Arkesel sandbox key, observe sandbox_accepted and the returned ID.
- [ ] An ambiguous timeout does not automatically resend.
- [ ] Dry-run/sandbox labels are visible; no claim of handset delivery is made.
- [ ] Replace a private link: the old URL stops working and the balance is preserved.
- [ ] Download an acknowledgement; source and current balance are clear.

## Corrections, recovery and reports

- [ ] Correct a due date with a reason; inspect the saved prior value through the API.
- [ ] Adjust GH₵500 upward by GH₵50: original sale remains 500, balance becomes 550, collected stays zero.
- [ ] Cancel a duplicate with no payment history: status is cancelled, not paid, and collected stays zero.
- [ ] Cancellation is blocked when any payment history or intent exists.
- [ ] A stale expected balance blocks the correction without changing the ledger.
- [ ] Payment recovery lists pending debt and savings test intents.
- [ ] Provider reverify cannot allocate a transaction twice.
- [ ] An excess-credit review saves an explanation without clearing or refunding credit.
- [ ] Report dates filter activity while current aging stays current.
- [ ] Customer statement includes corrections and labelled ledger entries.
- [ ] Browser printing works at the demonstration's page size.

## Arkesel callback reconciliation

- [ ] Enable callbacks only on a configured HTTPS test deployment and submit a fresh sandbox message.
- [ ] Callback intake stores known message/status hints without marking delivery.
- [ ] The worker looks up the saved message through the authenticated provider API.
- [ ] Validate the actual message-details JSON contract; unsupported shapes stop for review.
- [ ] A sandbox status is labelled separately from submission and is not handset-delivery evidence.
- [ ] Settings retry checks status again without submitting another SMS.
- [ ] Unknown IDs, mismatched recipients and unsupported statuses cannot update the report.

## Gemini

- [ ] No key: honest unavailable message, manual workflows still work.
- [ ] Valid key: structured plan is returned and tool trace is displayed.
- [ ] GH₵600 original sale, GH₵200 initial payment: proposed balance is GH₵400.
- [ ] No debt record exists before confirmation.
- [ ] Confirm once: record exists; confirm twice: conflict, no duplicate debt.
- [ ] Missing customer/date/amount prompts clarification, rather than assumed facts.
- [ ] Supply missing details in a follow-up; the completed draft uses recent context.
- [ ] Close and reopen the assistant to see saved history in the same session.
- [ ] Clear history: unconfirmed proposals can no longer be confirmed.
- [ ] Relative dates are resolved against today's date and reviewed before saving.
- [ ] Requests to mark an unpaid debt paid cannot execute a financial write tool.

## Susu and handoff

- [ ] Contribution increases savings liability without altering trade debt.
- [ ] Recorded payout reduces savings liability and cannot exceed it.
- [ ] No automated transfer is claimed by a manual savings entry.
- [ ] Open the private contribution link without a customer account.
- [ ] Starting a test contribution leaves savings unchanged; confirmation adds it once.
- [ ] Refresh/replay confirmation: no duplicate contribution and trade debt is unchanged.
- [ ] With provider test access, verify contribution purpose/account metadata and webhook replay.
- [ ] The contribution page shows no saved phone number or merchant login details.
- [ ] CSV and activity log work.
- [ ] Automated tests pass on the delivery machine.
- [ ] Source archive excludes `.env`, databases, sessions and API credentials.
- [ ] Known untested integrations and limitations are disclosed during the demo.

## Version 0.4 acceptance

- [ ] A savings-only customer appears in Customers.
- [ ] Open one profile and inspect separate debt/savings totals, payments, disputes and timeline.
- [ ] Agree instalments that total the current balance; no new financial entry is created.
- [ ] Record a partial payment and confirm oldest-instalment progress.
- [ ] Preview a scheduled reminder: instalment amount and total balance are accurate.
- [ ] Reverse a payment or correct the debt: the schedule needs review and reminders stop.
- [ ] Replace or cancel an agreement with a reason; history remains available through the API.
- [ ] Review contact consent with evidence; sandbox queue rejects a contact without permission.
- [ ] Correct a phone: all old private links fail and debts remain paused for review.
- [ ] Revoke permission after queueing: no sandbox submission occurs.
- [ ] Set printable branding and download a repayment PDF and customer PDF statement.
- [ ] Confirm reversed, manual, simulated and test sources stay explicitly labelled.
- [ ] Add a collector and sign in separately; permitted repayment/contribution workflows work.
- [ ] Collector API requests for corrections, payouts, contacts, plans and staff return 403.
- [ ] Owner audit events identify the collector responsible for a recorded repayment.
- [ ] Disable/reset collector: their existing session stops working immediately.
- [ ] Create and inspect a private SQLite backup while the app is running.
- [ ] Restore is refused until every app/worker connection is closed.
- [ ] Restore in a rehearsal database; sign in again and verify balances and safety archive.
- [ ] Review interrupted/queued work after restore; no automatic SMS replay occurs.
