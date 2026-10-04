# stripe-report.csv against ledger.csv

212 provider rows, 267 ledger entries, 58 bank lines, 30 payouts.
Report covers 2026-08-30 07:43 – 2026-09-28 23:43 UTC; ledger export 2026-08-27 01:28 – 2026-10-02 17:36 UTC.

**Findings:** 2 pending_in_ledger, 7 not_in_ledger, 1 double_charge, 4 not_at_provider, 2 amount_mismatch, 2 partial_capture, 2 rounding, 2 row_arithmetic, 2 fee_above_schedule, 2 payout_not_in_bank, 1 bank_amount_differs, 1 bank_deposit_unexplained.

> 48 paid ledger entries (795.19 EUR, 6,960.72 USD) are outside the report's dates and not in the walk (outside_window).

## From your ledger to the bank, USD

| | amount | rows | class |
|---|---:|---:|---|
| **Your ledger, paid entries in the report's dates (19 in other currencies, at the provider's rate)** | **26,585.05** |  |  |
| &nbsp;&nbsp;payments in your ledger, not at the provider | −118.82 | 1 | not_at_provider |
| &nbsp;&nbsp;refunds in your ledger, not at the provider | +322.91 | 2 | not_at_provider |
| **Your ledger, matched to the provider** | **26,789.14** | 200 |  |
| &nbsp;&nbsp;same payment, amounts differ by a rounding unit | 0.00 | 2 | rounding |
| &nbsp;&nbsp;your ledger booked the authorization, the provider captured less | −7.60 | 2 | partial_capture |
| &nbsp;&nbsp;same payment, different amount | −39.98 | 2 | amount_mismatch |
| &nbsp;&nbsp;captured at the provider, not paid in your ledger | +68.00 | 2 | pending_in_ledger |
| &nbsp;&nbsp;a second capture for an order your ledger has once | +325.95 | 1 | double_charge |
| &nbsp;&nbsp;payments at the provider, not in your ledger | +90.80 | 1 | not_in_ledger |
| &nbsp;&nbsp;refunds at the provider, not in your ledger | −268.58 | 2 | not_in_ledger |
| &nbsp;&nbsp;disputes at the provider, not in your ledger | −489.48 | 3 | not_in_ledger |
| &nbsp;&nbsp;payment reversals at the provider, not in your ledger | −19.00 | 1 | not_in_ledger |
| **Provider: payments, refunds, disputes (gross)** | **26,449.25** | 210 |  |
| &nbsp;&nbsp;provider fees on those rows | −921.10 | 199 |  |
| &nbsp;&nbsp;rows where net ≠ gross − fee | −0.50 |  | row_arithmetic |
| **Provider: net** | **25,527.65** |  |  |
| **Payouts** | **25,527.65** | 30 |  |
| &nbsp;&nbsp;payouts not on the bank statement | −1,007.83 | 2 | payout_not_in_bank |
| &nbsp;&nbsp;the bank received a different amount | −8.00 | 1 | bank_amount_differs |
| **Bank: deposits matched to payouts** | **24,511.82** | 28 |  |
| &nbsp;&nbsp;provider deposits no payout explains (not in the total) | +4,660.80 | 1 | bank_deposit_unexplained |

## In your ledger only, EUR

| | amount | entries | class |
|---|---:|---:|---|
| in your ledger, not at the provider | −205.10 | 1 | not_at_provider |

## Findings

### pending_in_ledger — captured at the provider, not paid in your ledger

2 (error); total +68.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10277 | pi_100005521794 | L000290 (line 88) 19.00 USD pending | txn_100009628616 (line 68) payment 19.00 USD |  | +19.00 | payment captured at the provider; ledger status is pending |
| ord_10115 | pi_100002284390 | L000120 (line 92) 49.00 USD pending | txn_100007847968 (line 71) payment 49.00 USD |  | +49.00 | payment captured at the provider; ledger status is pending |

### not_in_ledger — at the provider, not in your ledger

7 (error); total −686.26 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10203 | pi_100004045780 |  | txn_100008827378 (line 62) payment 90.80 USD |  | +90.80 | payment at the provider with no ledger entry |
| ord_10129 | pi_100002567288 |  | txn_100010347935 (line 60) dispute −99.00 USD |  | −99.00 | dispute at the provider with no ledger entry |
| ord_10024 | pi_100000466825 |  | txn_100010458936 (line 95) refund −168.94 USD |  | −168.94 | refund at the provider with no ledger entry |
| ord_10228 | pi_100004546966 |  | txn_100010442086 (line 114) refund −99.64 USD |  | −99.64 | refund at the provider with no ledger entry |
| ord_10149 | pi_100002963998 |  | txn_100010369190 (line 129) dispute −331.40 USD |  | −331.40 | dispute at the provider with no ledger entry |
| ord_10227 | pi_100004523949 |  | txn_100010513827 (line 132) payment_reversal −19.00 USD |  | −19.00 | payment_reversal at the provider with no ledger entry |
| ord_10093 | pi_100001845344 |  | txn_100010302134 (line 153) dispute −59.08 USD |  | −59.08 | dispute at the provider with no ledger entry |

### double_charge — a second capture for an order your ledger has once

1 (error); total +325.95 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10042 | pi_100010468179 |  | txn_100010475140 (line 80) payment 325.95 USD |  | +325.95 | order ord_10042 has another captured payment that the ledger matches |

### not_at_provider — in your ledger, not at the provider

4 (error); total −205.10 EUR, +204.09 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10184 | ch_100003678633 | L000364 (line 110) −303.91 USD paid |  |  | +303.91 | refund in the ledger with no provider row |
| ord_10051 | ch_100001019942 | L000052 (line 125) 118.82 USD paid |  |  | −118.82 | payment in the ledger with no provider row |
| ord_10235 | ch_100004693869 | L000365 (line 132) −19.00 USD paid |  |  | +19.00 | refund in the ledger with no provider row |
| ord_10135 | ch_100002698307 | L000141 (line 142) 205.10 EUR paid |  |  | −205.10 | payment in the ledger with no provider row |

### amount_mismatch — same payment, different amount

2 (error); total −39.98 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10025 | pi_100000488602 | L000025 (line 93) 68.99 USD paid | txn_100006842761 (line 72) payment 49.00 USD |  | −19.99 | ledger 68.99, provider 49.00 USD |
| ord_10316 | pi_100006306432 | L000332 (line 120) 412.77 USD paid | txn_100010075233 (line 99) payment 392.78 USD |  | −19.99 | ledger 412.77, provider 392.78 USD |

### partial_capture — your ledger booked the authorization, the provider captured less

2 (error); total −7.60 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10266 | pi_100005304653 | L000279 (line 115) 49.00 USD paid | txn_100009515166 (line 93) payment 49.00 USD<br>txn_100010501690 (line 94) partial_capture_reversal −1.54 USD |  | −1.54 | ledger 49.00, provider 47.46 USD |
| ord_10029 | pi_100000567091 | L000029 (line 160) 49.00 USD paid | txn_100006884023 (line 135) payment 49.00 USD<br>txn_100010495105 (line 136) partial_capture_reversal −6.06 USD |  | −6.06 | ledger 49.00, provider 42.94 USD |

### row_arithmetic — the report's own row does not add up (net ≠ gross − fee)

2 (error); total −0.50 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10233 | pi_100004643382 |  | txn_100009149511 (line 131) payment 19.00 USD |  | −0.25 | net 18.15 ≠ gross 19.00 − fee 0.60 |
| ord_10318 | pi_100006342449 |  | txn_100010091236 (line 133) payment 191.41 USD |  | −0.25 | net 185.56 ≠ gross 191.41 − fee 5.60 |

### payout_not_in_bank — a payout the bank statement does not show

2 (error); total −1,007.83 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
|  |  |  |  | po_202609128910 2026-09-12 604.15 | −604.15 | payout of 2026-09-12 not on the statement |
|  |  |  |  | po_202609216700 2026-09-21 403.68 | −403.68 | payout of 2026-09-21 not on the statement |

### rounding — same payment, amounts differ by a rounding unit

2 (warning); total 0.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10035 | pi_100000685332 | L000036 (line 113) 175.39 USD paid | txn_100006964463 (line 91) payment 175.38 USD |  | −0.01 | ledger 175.39, provider 175.38 USD |
| ord_10196 | pi_100003903823 | L000205 (line 167) 12.97 USD paid | txn_100008736886 (line 143) payment 12.98 USD |  | +0.01 | ledger 12.97, provider 12.98 USD |

### fee_above_schedule — fee above the schedule you gave

2 (warning); total −7.35 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10267 | pi_100005327792 |  | txn_100009528399 (line 125) payment 99.00 USD |  | −1.49 | fee 4.66, schedule 2.9%+0.30 gives 3.17 |
| ord_10238 | pi_100004749340 |  | txn_100009216713 (line 146) payment 390.60 USD |  | −5.86 | fee 17.49, schedule 2.9%+0.30 gives 11.63 |

### bank_amount_differs — the bank received a different amount than the payout

1 (warning); total −8.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
|  |  |  |  | po_202609147841 2026-09-14 2,328.72<br>bank line 27 2026-09-15 2,320.72 “STRIPE TRANSFER ST-19245993” | −8.00 | payout 2,328.72, bank 2,320.72 |

### bank_deposit_unexplained — a deposit from the provider that no payout explains

1 (warning); total +4,660.80 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
|  |  |  |  | bank line 31 2026-09-17 4,660.80 “STRIPE TRANSFER ST-21165571” | +4,660.80 | names the provider; no payout in the report explains it |

### day_shift — same payment, booked on a different day by each side

24 (info).

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10087 | pi_100001721299 | L000091 (line 29) 281.85 USD paid | txn_100007546644 (line 8) payment 281.85 USD |  |  | ledger day 2026-08-30, provider day 2026-08-31 |
| ord_10058 | pi_100001146218 | L000061 (line 36) 49.00 USD paid | txn_100007232435 (line 15) payment 49.00 USD |  |  | ledger day 2026-08-31, provider day 2026-09-01 |
| ord_10127 | pi_100002524783 | L000132 (line 37) 70.46 USD paid | txn_100007966705 (line 16) payment 70.46 USD |  |  | ledger day 2026-08-31, provider day 2026-09-01 |
| … 21 more | | | | | | |

### outside_window — outside the other side's dates; not judged

51 (info).

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10139 | ch_100002773451 | L000145 (line 2) 6.30 USD paid |  |  | −6.30 | ledger entry outside the report's dates |
| ord_10275 | ch_100005491467 | L000288 (line 3) 252.69 USD paid |  |  | −252.69 | ledger entry outside the report's dates |
| ord_10163 | ch_100003255189 | L000170 (line 4) 101.80 USD paid |  |  | −101.80 | ledger entry outside the report's dates |
| … 48 more | | | | | | |
