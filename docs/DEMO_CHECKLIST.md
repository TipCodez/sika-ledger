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
- [ ] CSV and activity log work.
- [ ] Automated tests pass on the delivery machine.
- [ ] Source archive excludes `.env`, databases, sessions and API credentials.
- [ ] Known untested integrations and limitations are disclosed during the demo.
