# adyen-report.csv against ledger.csv

250 provider rows, 273 ledger entries, 60 bank lines, 30 payouts.
Report covers 2026-08-29 23:04 – 2026-09-28 19:44 UTC; ledger export 2026-08-27 01:28 – 2026-10-02 17:03 UTC.

**Findings:** 1 pending_in_ledger, 3 not_in_ledger, 3 double_charge, 2 rounding, 2 row_arithmetic, 1 payout_sum, 1 fee_above_schedule, 1 bank_amount_differs.

> 39 paid ledger entries (389.72 EUR, 5,283.52 USD) are outside the report's dates and not in the walk (outside_window).

## From your ledger to the bank, USD

| | amount | rows | class |
|---|---:|---:|---|
| **Your ledger, paid entries in the report's dates (19 in other currencies, at the provider's rate)** | **30,733.39** |  |  |
| **Your ledger, matched to the provider** | **30,733.39** | 213 |  |
| &nbsp;&nbsp;same payment, amounts differ by a rounding unit | +0.02 | 2 | rounding |
| &nbsp;&nbsp;captured at the provider, not paid in your ledger | +49.00 | 1 | pending_in_ledger |
| &nbsp;&nbsp;a second capture for an order your ledger has once | +790.41 | 3 | double_charge |
| &nbsp;&nbsp;disputes at the provider, not in your ledger | −127.19 | 2 | not_in_ledger |
| &nbsp;&nbsp;payment reversals at the provider, not in your ledger | −19.00 | 1 | not_in_ledger |
| **Provider: payments, refunds, disputes (gross)** | **31,426.63** | 220 |  |
| &nbsp;&nbsp;provider fees on those rows | −1,049.83 | 207 |  |
| **Provider: net** | **30,376.80** |  |  |
| &nbsp;&nbsp;payouts that differ from the sum of their rows | −50.00 |  | payout_sum |
| **Payouts** | **30,326.80** | 30 |  |
| &nbsp;&nbsp;the bank received a different amount | −25.00 | 1 | bank_amount_differs |
| **Bank: deposits matched to payouts** | **30,301.80** | 30 |  |

## Findings

### pending_in_ledger — captured at the provider, not paid in your ledger

1 (error); total +49.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10002 | 4900896756852776 | L000002 (line 169) 49.00 USD pending | 4028355743239511 (line 161) payment 49.00 USD |  | +49.00 | payment captured at the provider; ledger status is pending |

### not_in_ledger — at the provider, not in your ledger

3 (error); total −146.19 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10104 | 1138757141145335 |  | 8675707771140356 (line 138) dispute −103.29 USD |  | −103.29 | dispute at the provider with no ledger entry |
| ord_10047 | 1173552068983580 |  | 6086175104336752 (line 140) dispute −23.90 USD |  | −23.90 | dispute at the provider with no ledger entry |
| ord_10063 | 3986852487272425 |  | 1184472638171120 (line 187) payment_reversal −19.00 USD |  | −19.00 | payment_reversal at the provider with no ledger entry |

### double_charge — a second capture for an order your ledger has once

3 (error); total +790.41 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10323 | 5791350215900975 | L000350 (line 165) 282.10 USD failed | 6287471424785607 (line 156) payment 282.10 USD |  | +282.10 | captured although the ledger has this attempt as failed; another payment of the order is matched |
| ord_10197 | 8312462706063995 |  | 4688298984700947 (line 71) payment 177.01 USD |  | +177.01 | order ord_10197 has another captured payment that the ledger matches |
| ord_10172 | 1145690889578401 |  | 1798611337688656 (line 128) payment 331.30 USD |  | +331.30 | order ord_10172 has another captured payment that the ledger matches |

### row_arithmetic — the report's own row does not add up (net ≠ gross − fee)

2 (error); total 0.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10120 | 9095653277380229 |  | 3224043355627222 (line 73) payment 99.00 USD |  | 0.00 | gross columns give 99.25, net + fee give 99.00 |
| ord_10114 | 8644565206649534 |  | 3725918959378759 (line 181) payment 17.88 USD |  | 0.00 | gross columns give 18.13, net + fee give 17.88 |

### payout_sum — a payout is not the sum of its rows

1 (error); total −50.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
|  |  |  |  | batch 19 2026-09-11 692.33 | −50.00 | payout 692.33, its rows add up to 742.33 |

### rounding — same payment, amounts differ by a rounding unit

2 (warning); total +0.02 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10076 | 6885088957486984 | L000085 (line 132) 204.93 USD paid | 5969044983574412 (line 118) payment 204.94 USD |  | +0.01 | ledger 204.93, provider 204.94 USD |
| ord_10050 | 9583065733697895 | L000057 (line 156) 118.81 USD paid | 8534881427804908 (line 146) payment 118.82 USD |  | +0.01 | ledger 118.81, provider 118.82 USD |

### fee_above_schedule — fee above the schedule you gave

1 (warning); total −3.78 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10167 | 1954253903498003 |  | 5496611741123553 (line 114) payment 252.15 USD |  | −3.78 | fee 11.39, schedule 2.9%+0.30 gives 7.61 |

### bank_amount_differs — the bank received a different amount than the payout

1 (warning); total −25.00 USD.

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
|  |  |  |  | batch 15 2026-09-07 461.20<br>bank line 15 2026-09-07 436.20 “ADYEN N.V. TX4313824XT” | −25.00 | payout 461.20, bank 436.20 |

### day_shift — same payment, booked on a different day by each side

56 (info).

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10238 | 5045555504669061 | L000260 (line 25) 49.00 USD paid | 3822585564665516 (line 2) payment 49.00 USD |  |  | ledger day 2026-08-29, provider day 2026-08-30 |
| ord_10098 | 8401951317490394 | L000108 (line 26) 41.89 USD paid | 5951989276359346 (line 3) payment 41.89 USD |  |  | ledger day 2026-08-29, provider day 2026-08-30 |
| ord_10123 | 1938425576242082 | L000137 (line 36) 70.46 USD paid | 3568398988697250 (line 15) payment 70.46 USD |  |  | ledger day 2026-08-31, provider day 2026-09-01 |
| … 53 more | | | | | | |

### outside_window — outside the other side's dates; not judged

42 (info).

| order | payment | ledger | provider | payout / bank | effect | note |
|---|---|---|---|---|---:|---|
| ord_10135 | 4204006818772049 | L000150 (line 2) 6.30 USD paid |  |  | −6.30 | ledger entry outside the report's dates |
| ord_10160 | 9745276730067298 | L000177 (line 3) 101.80 USD paid |  |  | −101.80 | ledger entry outside the report's dates |
| ord_10220 | 4127504339237836 | L000242 (line 4) 277.50 USD paid |  |  | −277.50 | ledger entry outside the report's dates |
| … 39 more | | | | | | |
