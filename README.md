# payout-bridge

From your ledger to the provider's report to the bank, with every difference named.

"The money doesn't add up" is rarely one problem. It is a dozen small ones with different causes: a
payment the shop never marked as paid, a refund made from the provider's dashboard, a dispute nobody
booked, a customer charged twice, an amount rounded differently, a fee above the contract, a payout the
bank shows minus a wire fee, or not at all. The total difference says nothing about which. This tool
reads three exports (the provider's report, your ledger, your bank statement) and builds a walk from
the first to the last. Every provider row lands on exactly one line of it, so the walk adds up to the
cent; whatever does not match is a finding, with the rows behind it.

Read-only: it reads CSV files and writes a report. Standard library only. Python 3.9+.

```
pip install git+https://github.com/terminalstate/payout-bridge
payout-bridge stripe-report.csv --provider stripe --ledger ledger.csv --bank bank.csv \
    --ledger-tz America/New_York --fee "2.9%+0.30" --json report.json
```

## What it prints

From the generated month in [`examples/stripe`](examples/stripe) ([full report](examples/stripe/report.md)):

| | amount | rows | class |
|---|---:|---:|---|
| **Your ledger, paid entries in the report's dates (19 in other currencies, at the provider's rate)** | **26,585.05** | | |
| &nbsp;&nbsp;payments in your ledger, not at the provider | −118.82 | 1 | not_at_provider |
| &nbsp;&nbsp;refunds in your ledger, not at the provider | +322.91 | 2 | not_at_provider |
| **Your ledger, matched to the provider** | **26,789.14** | 200 | |
| &nbsp;&nbsp;same payment, amounts differ by a rounding unit | 0.00 | 2 | rounding |
| &nbsp;&nbsp;your ledger booked the authorization, the provider captured less | −7.60 | 2 | partial_capture |
| &nbsp;&nbsp;same payment, different amount | −39.98 | 2 | amount_mismatch |
| &nbsp;&nbsp;captured at the provider, not paid in your ledger | +68.00 | 2 | pending_in_ledger |
| &nbsp;&nbsp;a second capture for an order your ledger has once | +325.95 | 1 | double_charge |
| &nbsp;&nbsp;payments at the provider, not in your ledger | +90.80 | 1 | not_in_ledger |
| &nbsp;&nbsp;refunds at the provider, not in your ledger | −268.58 | 2 | not_in_ledger |
| &nbsp;&nbsp;disputes at the provider, not in your ledger | −489.48 | 3 | not_in_ledger |
| &nbsp;&nbsp;payment reversals at the provider, not in your ledger | −19.00 | 1 | not_in_ledger |
| **Provider: payments, refunds, disputes (gross)** | **26,449.25** | 210 | |
| &nbsp;&nbsp;provider fees on those rows | −921.10 | 199 | |
| &nbsp;&nbsp;rows where net ≠ gross − fee | −0.50 | | row_arithmetic |
| **Provider: net** | **25,527.65** | | |
| **Payouts** | **25,527.65** | 30 | |
| &nbsp;&nbsp;payouts not on the bank statement | −1,007.83 | 2 | payout_not_in_bank |
| &nbsp;&nbsp;the bank received a different amount | −8.00 | 1 | bank_amount_differs |
| **Bank: deposits matched to payouts** | **24,511.82** | 28 | |
| &nbsp;&nbsp;provider deposits no payout explains (not in the total) | +4,660.80 | 1 | bank_deposit_unexplained |

Above the walk, the report says what is left out of it (here: 48 ledger entries dated outside the
report). Below it, each class gets a table of its rows: order, payment id, the ledger line, the report
line, the payout and the bank line, the amount, and what is wrong in words.

## Why the deposit doesn't match your orders

| class | what you see | usual cause |
|---|---|---|
| `pending_in_ledger` | the customer paid, the order still says pending | the callback with the final status never arrived or was never applied ([the seven places that happens](https://github.com/terminalstate/callback-audit/blob/main/docs/lost-callbacks.md)) |
| `not_in_ledger` | money moved that your books never saw | refunds made from the provider's dashboard, disputes and their fees, a settled payment the provider took back (a failed bank debit), payments whose order was never created |
| `double_charge` | one order, two captures | a retry that created a second payment instead of repeating the first: a double click, or a timeout the shop took for a failure |
| `not_at_provider` | your books have money the provider never took | a capture that failed after the order was marked paid; a refund marked done that was never sent |
| `duplicate_in_ledger` | one payment, two entries | the same callback applied twice |
| `amount_mismatch` | same payment, different amount | shipping or tax added on one side only, an edited order, a currency the row does not carry |
| `partial_capture` | the ledger has the authorization, the provider captured less | the order was booked at authorization time and not updated at capture |
| `rounding` | a cent here and there | amounts computed in floating point, or per line on one side and per order on the other |
| `row_arithmetic` | the report disagrees with itself | an export edited or re-saved by hand, a column read with the wrong decimal separator |
| `payout_sum` | a payout that is not the sum of its rows | an export filtered by type, so some rows of the batch are missing |
| `fee_above_schedule` | fees above the contract | another pricing tier, cross-border or currency surcharges, a plan that changed |
| `payout_not_in_bank` | a payout the bank never shows | failed payout, wrong bank account, or still in transit |
| `bank_amount_differs` | the payout arrived, smaller | an incoming-wire fee, an intermediary bank's charge |
| `bank_deposit_unexplained` | a deposit from the provider that no payout explains | another account or currency at the same provider, a payout from a period the report does not cover |
| `day_shift` (info) | daily totals disagree although every payment matches | each side counts days in its own time zone, or the payment straddles midnight |
| `outside_window` (info) | rows near the edges of the exports | the other side's export starts or ends there; not judged |

Errors and warnings are judged; `info` explains differences between totals that are not mistakes.

## How it matches

**Ledger and report: by reference, never by amount.** First the provider's payment id (payment intent
or charge id, Adyen's PSP reference), then your order reference. With subscriptions at a few fixed
prices, the same amount on the same day is the normal case, and matching by amount and date pairs one
customer's payment with another's. Where references disagree:

- An entry that is not paid (a failed attempt, a pending order) is matched by its own payment id only.
  If that attempt was captured after all and another payment of the order is matched, it is a
  `double_charge`; a capture for an order the ledger has only as unpaid is `pending_in_ledger`.
- An entry falls back to the order reference only when the report does not know its payment id at all.
  If the report knows it and that payment is already matched, the entry is a duplicate, not a match for
  some other payment of the same order.
- Several captures of one payment (same payment id: Adyen's partial captures, Stripe's multicapture)
  are compared as one when the ledger books that payment once or has an entry for their total, and one
  by one when it books each. A second capture under the same payment id is never a double charge.
- A second entry with a payment id that is already matched is `duplicate_in_ledger`, whatever its amount.
- A paid entry of zero (a free order) is not expected at the provider.

A Stripe partial-capture reversal is attached to its payment. A row in another currency is compared in
the currency the customer paid (Stripe's customer-facing amount, Adyen's gross currency), and the
difference is converted at the row's own rate.

**Report and bank: by reference, then exact amount, then a deduction.** A payout reference on the
statement (a payout id, Adyen's TX…XT) decides first. Then the same amount dated from a day before to
five days after the payout. Then a deposit whose description names the provider and that is smaller than
the payout by up to 10%: something was deducted on the way. A larger deposit is never explained that
way, and when the statement shows payout references at all, a deposit without this payout's reference
is not this payout. Only credits are deposits: the provider's own invoices debited from the account are
not reported, and a debit that carries a payout's reference (a wire fee, say) is not taken for the
payout. A payout in a currency the statement has no lines in is not judged.

**Windows.** An unmatched entry is judged only where the other side's export covers its date. Without
`--from`/`--to`, the report's dates come from its rows, and the two days at each edge are not judged
(`--edge-days`). A payout is judged missing only if the statement starts at least a day before it and
runs at least five days past it.

## Inputs

**Stripe**: the itemized payout reconciliation report, or another itemized balance report with the same
columns. Stripe's default columns have nothing that links a row to your order: download it with
`payment_intent_id` and/or `charge_id` (whichever your ledger keeps; `source_id` too if it keeps refund
ids) and
`payment_metadata[<your order key>]` added, and `customer_facing_amount`, `customer_facing_currency` if
you sell in more than one currency. Without a link column the tool stops and says which to add; without
the customer-facing columns, entries in other currencies are reported with a note saying so.

**Adyen**: the Settlement details report as is. Fees are read from Commission, or from Markup + Scheme
Fees + Interchange; the TimeZone column is honoured (`--report-tz` overrides it, and an ambiguous
abbreviation such as IST or CST is refused); a batch without a MerchantPayout row is treated as not paid
out yet; a batch number with rows in two currencies is two payouts.

**Any other provider**: one row per balance movement with `txn_id, kind, gross, fee, net, currency,
created_at, payment_ref, order_ref, payout_id, payout_date`, and `amount_tx, currency_tx` for foreign
currency. Kinds: `payment, refund, refund_failure, dispute, dispute_reversal, partial_capture_reversal,
payment_reversal, fee, adjustment, reserve, transfer, payout, payout_reversal, other`. A `payout` row
declares the payout's amount (negative, as money leaving the balance). Give `--bank-hint` too: there is
no provider name to default to.

**Ledger**: `id, kind, amount, currency, created_at, status, payment_ref, order_ref`, under these names
or common synonyms (`order_id`, `total`, `payment_intent_id`, `type`…), or mapped:
`--map-ledger "amount=total_amount,order_ref=order_no"`. A ledger without a kind column is read as
payments, and negative amounts as refunds. Statuses that mean paid: `--paid-statuses` (the default list
covers paid, succeeded, captured, completed, settled, refunded); an empty status always counts as paid.

**Bank statement**: date, amount (or credit and debit columns), currency, description (every
description-like column is joined, unless `--map-bank description=...` names one). The description
should keep whatever the bank prints about the sender: it is how a deposit is recognised as the
provider's (`--bank-hint`, the provider's name by default).

Amounts are read as text into integer minor units (ISO 4217 decimals; `--exponent` overrides). Text that
could mean two numbers is an error with its line number, not a guess: `1,234` in a three-decimal
currency, `1.234` with `--decimal-comma` there. Timestamps without an offset take `--report-tz` or
`--ledger-tz`: an IANA name, or an offset written with an equals sign (`--ledger-tz=-04:00`), since
argparse reads `-04:00` on its own as an option. On Windows, IANA names need `pip install tzdata`. CSV
files may be comma, semicolon or tab separated, UTF-8 (with or without BOM), UTF-16 or Windows-1252,
with any line endings.

## On a schedule

`--json` writes everything the Markdown has, plus amounts in minor units. The exit code is 0 when
nothing at `--fail-on` severity or above was found (default `error`), 1 when something was, 2 when an
input could not be read or the report could not be written. That is enough to run it after every
payout and look only when it fails.

## Tested on generated months

`payout_bridge.synth` writes a month of a made-up shop in Stripe's and in Adyen's format: a few hundred
orders, a third of them at three subscription prices, a tenth paid in EUR and settled in USD, refunds
booked by the shop, disputes nobody books, failed attempts in the ledger, daily payouts two days after
the payment, a ledger that writes local time without an offset, and a bank statement with other traffic
and the provider's own invoices. Some months the ledger keeps Stripe charge ids instead of payment
intents; some statements show payout references and some do not. Each month also gets a payment
captured in two parts and free orders, for which nothing should be reported. Into the middle of the
month it puts known cases of each class, and `truth.json` lists them. Half of the unexplained deposits
are the size of a payout, like another account's payout would be.

`python -m payout_bridge.evaluate --seeds 500` runs the tool over 500 months per format, through the CSV
files, and checks that every walk adds up (all 1,000 do):

| class | Stripe: put in / found / wrong | Adyen: put in / found / wrong |
|---|---:|---:|
| pending_in_ledger | 511 / 511 / 0 | 486 / 486 / 0 |
| not_in_ledger | 2232 / 2232 / 0 | 2210 / 2210 / 0 |
| double_charge | 753 / 753 / 0 | 755 / 755 / 0 |
| not_at_provider | 940 / 940 / 0 | 1029 / 1029 / 0 |
| duplicate_in_ledger | 486 / 486 / 0 | 493 / 493 / 0 |
| amount_mismatch | 528 / 528 / 0 | 1011 / 1011 / 0 |
| partial_capture | 503 / 503 / 0 | — (Adyen settles the captured amount: amount_mismatch) |
| rounding | 479 / 479 / 0 | 497 / 497 / 0 |
| row_arithmetic | 490 / 490 / 0 | 502 / 502 / 0 |
| payout_sum | — (Stripe payouts are sums by definition) | 500 / 500 / 0 |
| fee_above_schedule | 542 / 542 / 0 | 490 / 490 / 0 |
| payout_not_in_bank | 494 / 488 / 0 | 463 / 461 / 0 |
| bank_amount_differs | 493 / 493 / 6 | 516 / 516 / 2 |
| bank_deposit_unexplained | 489 / 483 / 0 | 489 / 487 / 0 |

All eight wrong answers are one case, on statements without payout references: a payout that never
arrived, and a deposit from another account a few percent smaller than it, within a few days. The tool
takes the deposit for that payout minus a bank fee, and reports `bank_amount_differs` instead of a
missing payout and an unexplained deposit. With references on the statement it does not happen:

| class | Stripe, with references | Stripe, without | Adyen, with | Adyen, without |
|---|---:|---:|---:|---:|
| payout_not_in_bank | 160 / 160 / 0 | 334 / 328 / 0 | 329 / 329 / 0 | 134 / 132 / 0 |
| bank_amount_differs | 169 / 169 / 0 | 324 / 324 / 6 | 365 / 365 / 0 | 151 / 151 / 2 |
| bank_deposit_unexplained | 167 / 167 / 0 | 322 / 316 / 0 | 359 / 359 / 0 | 130 / 128 / 0 |

Read the rest of the table as a regression test, not as accuracy on your data. The generator and the
checker have the same author, so it shows that the checker finds what its author thought of, in files
laid out as the providers document them. The readers were written from Stripe's and Adyen's
documentation and have not been run on a real export yet.

What the generated months did find were four wrong rules, each replaced by the one described above:

1. A failed attempt in the ledger was matched by order number to a second capture for the same order,
   and a double charge came out as `pending_in_ledger`.
2. With no payout reference on the statement, a payout that never arrived next to one that arrived minus
   a bank fee: matching the nearest amount gave the smaller deposit to the wrong payout. In one case the
   deposit was larger than the payout it was given to.
3. Deposits for next month's payouts, in the last days of the statement, were reported as unexplained.
   Deposits are now judged between the first and the last payout date of the report.
4. A deposit without a reference was taken for a payout on a statement that does show references.

Two reviews of the code before publishing found twenty-four more, among them a space in `--paid-statuses`
that made almost every entry unpaid, a captured timeout reported as pending instead of as a double
charge, Adyen's split captures reported as a double charge, `charge_id` ignored when `payment_intent_id`
was present, the provider's own invoices reported as deposits, and a bank-fee debit carrying the payout
id taken for the payout. Each is fixed and has a test, most of them in `tests/test_edges.py`.

And one that is not a mistake: in Adyen's format, a wrong gross column on a EUR row is both
`row_arithmetic` and `amount_mismatch`, because the EUR amount compared with the ledger comes from that
column.

## What it cannot tell apart

- Without payout references on the statement, a missing payout and another account's deposit a few
  percent smaller than it, within days: `bank_amount_differs`, as in the table above.
- Without your order reference in the report, a double charge is a payment not in your ledger.
- A ledger entry with a wrong payment id that the report does know: the entry is a duplicate or missing,
  and the payment it should have matched is not in your ledger.
- One fee rule for all payments. Interchange++ pricing, per-country or per-method rates are beyond it:
  use `--fee` as a ceiling.
- It is not a ledger: it posts nothing, knows nothing about tax, and does not move money.

## Tests

```
python -m unittest discover -s tests
```

91 tests: readers (including Adyen's own example rows), amounts and time zones, each class on small
hand-made cases, the cases from review, the walk adding up, the command line, and 40 generated months
per format.

## License

MIT
