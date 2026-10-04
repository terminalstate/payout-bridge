import unittest
from datetime import timezone

from helpers import at, bline, check_walk, entry, prow

from payout_bridge.money import FeeSchedule
from payout_bridge.reconcile import Options, reconcile

UTC = timezone.utc


def classes(res, *, info=False):
    return sorted(f.cls for f in res.findings if info or f.severity != "info")


def one(res, cls):
    fs = [f for f in res.findings if f.cls == cls]
    assert len(fs) == 1, (cls, [f.cls for f in res.findings])
    return fs[0]


def background(n=10, day=1):
    """Matched payments spread over the month, so the windows cover the middle of it."""
    rows, ledger = [], []
    for i in range(n):
        d = day + i * 3
        rows.append(prow(gross=1000 + i, ref=f"bg{i}", order=f"bgo{i}", day=d))
        ledger.append(entry(amount=1000 + i, ref=f"bg{i}", order=f"bgo{i}", day=d))
    return rows, ledger


class Matching(unittest.TestCase):
    def test_clean(self):
        rows, ledger = background()
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), [])
        self.assertEqual(len(res.pairs), 10)
        self.assertIsNone(check_walk(res.walks["USD"]))
        self.assertIsNone(res.worst())

    def test_never_by_amount(self):
        # two subscriptions at the same price, same minute: the references decide, not the amount
        rows = [prow(gross=4900, ref="pi_a", order="o_a", day=12), prow(gross=4900, ref="pi_b", order="o_b", day=12)]
        ledger = [entry(amount=4900, ref="pi_b", order="o_b", day=12), entry(amount=4900, ref="pi_a", order="o_a", day=12)]
        res = reconcile(rows, ledger)
        self.assertEqual({(p.entry.payment_ref, p.unit.payment_ref) for p in res.pairs}, {("pi_a", "pi_a"), ("pi_b", "pi_b")})
        # and an entry with no reference the report knows is not matched to a payment of the same amount
        rows, ledger = background()
        rows.append(prow(gross=4900, ref="pi_x", order="o_x", day=12))
        ledger.append(entry(amount=4900, ref="pi_y", order="o_y", day=12))
        self.assertEqual(classes(reconcile(rows, ledger)), ["not_at_provider", "not_in_ledger"])

    def test_by_order_when_the_report_has_no_payment_ids(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref=None, order="o_1", day=12))
        ledger.append(entry(amount=2500, ref="pi_1", order="o_1", day=12))
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), [])
        self.assertEqual(len(res.pairs), 11)

    def test_pending_in_ledger_by_reference(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12))
        ledger.append(entry(amount=2500, ref="pi_1", order="o_1", day=12, status="pending"))
        res = reconcile(rows, ledger)
        f = one(res, "pending_in_ledger")
        self.assertEqual((f.amount, f.ledger[0].status), (2500, "pending"))
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_pending_in_ledger_without_reference(self):
        # the callback never came, so the ledger never learned the payment's id
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12))
        ledger.append(entry(amount=2500, ref=None, order="o_1", day=12, status="awaiting_payment"))
        f = one(reconcile(rows, ledger), "pending_in_ledger")
        self.assertIn("awaiting_payment", f.message)

    def test_failed_attempt_does_not_take_a_capture(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_2", order="o_1", day=12))
        ledger += [
            entry(amount=2500, ref="pi_1", order="o_1", day=12, status="failed"),
            entry(amount=2500, ref="pi_2", order="o_1", day=12, hour=13),
        ]
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), [])

    def test_not_in_ledger_and_not_at_provider(self):
        rows, ledger = background()
        rows += [
            prow(gross=2500, ref="pi_1", order="o_1", day=12),
            prow("refund", -700, ref="pi_bg", order="o_9", day=14),
            prow("dispute", -1000, fee=1500, ref="pi_bg2", order="o_8", day=15),
        ]
        ledger += [entry(amount=3300, ref="pi_2", order="o_2", day=13), entry("refund", -500, ref="bg3", order="bgo3", day=16)]
        res = reconcile(rows, ledger)
        self.assertEqual(
            sorted((f.cls, f.kind) for f in res.findings if f.severity != "info"),
            [
                ("not_at_provider", "payment"),
                ("not_at_provider", "refund"),
                ("not_in_ledger", "dispute"),
                ("not_in_ledger", "payment"),
                ("not_in_ledger", "refund"),
            ],
        )
        walk = res.walks["USD"]
        self.assertIsNone(check_walk(walk))
        labels = {ln.label: ln.amount for ln in walk}
        self.assertEqual(labels["disputes at the provider, not in your ledger"], -1000)
        self.assertEqual(labels["payments in your ledger, not at the provider"], -3300)
        self.assertEqual(labels["refunds in your ledger, not at the provider"], 500)

    def test_duplicate_and_double_charge(self):
        rows, ledger = background()
        rows += [
            prow(gross=2500, ref="pi_1", order="o_1", day=12),
            prow(gross=4000, ref="pi_2", order="o_2", day=13),
            prow(gross=4000, ref="pi_3", order="o_2", day=13, hour=13),
        ]
        ledger += [
            entry(amount=2500, ref="pi_1", order="o_1", day=12),
            entry(amount=2500, ref="pi_1", order="o_1", day=12, hour=13),
            entry(amount=4000, ref="pi_2", order="o_2", day=13),
        ]
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), ["double_charge", "duplicate_in_ledger"])
        self.assertEqual(one(res, "double_charge").payment_ref, "pi_3")
        self.assertEqual(len(one(res, "duplicate_in_ledger").ledger), 2)
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_amounts(self):
        rows, ledger = background()
        rows += [
            prow(gross=2500, ref="pi_1", order="o_1", day=12),
            prow(gross=2500, ref="pi_2", order="o_2", day=12),
            prow(gross=5000, ref="pi_3", order="o_3", day=13),
            prow("partial_capture_reversal", -1200, fee=0, ref="pi_3", order="o_3", day=13),
        ]
        ledger += [
            entry(amount=2501, ref="pi_1", order="o_1", day=12),
            entry(amount=3000, ref="pi_2", order="o_2", day=12),
            entry(amount=5000, ref="pi_3", order="o_3", day=13),
        ]
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), ["amount_mismatch", "partial_capture", "rounding"])
        self.assertEqual(one(res, "rounding").amount, -1)
        self.assertEqual(one(res, "amount_mismatch").amount, -500)
        self.assertEqual(one(res, "partial_capture").amount, -1200)
        self.assertIsNone(check_walk(res.walks["USD"]))
        # booked at what was captured: nothing to say
        ledger[-1].amount = 3800
        self.assertEqual(classes(reconcile(rows, ledger)), ["amount_mismatch", "rounding"])

    def test_tolerance(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12))
        ledger.append(entry(amount=2503, ref="pi_1", order="o_1", day=12))
        self.assertEqual(classes(reconcile(rows, ledger)), ["amount_mismatch"])
        self.assertEqual(classes(reconcile(rows, ledger, opts=Options(tolerance=5))), ["rounding"])

    def test_foreign_currency_compared_in_the_customers_currency(self):
        rows, ledger = background()
        rows.append(prow(gross=5432, ref="pi_1", order="o_1", day=12, amount_tx=5000, currency_tx="EUR"))
        rows.append(prow(gross=2172, ref="pi_2", order="o_2", day=12, amount_tx=2000, currency_tx="EUR"))
        ledger.append(entry(amount=5000, ref="pi_1", order="o_1", day=12, currency="EUR"))
        ledger.append(entry(amount=2100, ref="pi_2", order="o_2", day=12, currency="EUR"))
        res = reconcile(rows, ledger)
        f = one(res, "amount_mismatch")
        self.assertEqual(f.detail["diff_minor"], -100)  # in EUR
        self.assertEqual(f.amount, -109)  # in USD at the row's own rate: 2172/2000
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_currency_the_row_does_not_have(self):
        rows, ledger = background()
        rows.append(prow(gross=5432, ref="pi_1", order="o_1", day=12))
        ledger.append(entry(amount=5000, ref="pi_1", order="o_1", day=12, currency="GBP"))
        res = reconcile(rows, ledger)
        self.assertEqual(one(res, "amount_mismatch").amount, 5432)
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_ledger_only_currency(self):
        rows, ledger = background()
        ledger.append(entry(amount=5000, ref="pi_9", order="o_9", day=12, currency="EUR"))
        res = reconcile(rows, ledger)
        self.assertEqual([ln.cls for ln in res.ledger_only["EUR"]], ["not_at_provider"])


class Windows(unittest.TestCase):
    def test_edges_are_not_judged(self):
        rows, ledger = background(n=10, day=1)  # rows from the 1st to the 28th
        ledger.append(entry(amount=777, ref="early", order="e", day=2))
        ledger.append(entry(amount=777, ref="late", order="l", day=28, hour=6))
        ledger.append(entry(amount=777, ref="mid", order="m", day=14))
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), ["not_at_provider"])
        self.assertEqual(sum(1 for f in res.findings if f.cls == "outside_window"), 2)
        # with the report's dates given, the edges are judged too
        res = reconcile(rows, ledger, opts=Options(window_from=at(1, 0), window_to=at(28, 23)))
        self.assertEqual(classes(res), ["not_at_provider"] * 3)

    def test_provider_rows_outside_the_ledger_export(self):
        rows, ledger = background(n=5, day=10)
        rows.append(prow(gross=999, ref="old", order="old", day=1))
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), [])
        f = one(res, "outside_window")
        self.assertEqual(f.detail["side"], "provider")
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_day_shift(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12, hour=20))
        ledger.append(entry(amount=2500, ref="pi_1", order="o_1", day=12, hour=20))
        from payout_bridge.timeparse import zone

        res = reconcile(rows, ledger, opts=Options(ledger_tz=zone("+09:00")))
        shifts = [f for f in res.findings if f.cls == "day_shift"]
        self.assertEqual([f.payment_ref for f in shifts], ["pi_1"])
        self.assertEqual(shifts[0].detail, {"ledger_day": "2026-09-13", "provider_day": "2026-09-12"})


class Report(unittest.TestCase):
    def test_row_arithmetic_and_fees(self):
        rows, ledger = background()
        rows.append(prow(gross=10000, fee=320, net=9600, ref="pi_1", order="o_1", day=12))
        rows.append(prow(gross=10000, fee=470, ref="pi_2", order="o_2", day=12))
        ledger += [entry(amount=10000, ref="pi_1", order="o_1", day=12), entry(amount=10000, ref="pi_2", order="o_2", day=12)]
        res = reconcile(rows, ledger, opts=Options(fee_schedule=FeeSchedule("2.9%+0.30")))
        self.assertEqual(classes(res), ["fee_above_schedule", "row_arithmetic"])
        self.assertEqual(one(res, "row_arithmetic").amount, -80)
        self.assertEqual(one(res, "fee_above_schedule").amount, -150)
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_gross_columns_against_net_and_fee(self):
        rows, ledger = background()
        r = prow(gross=11000, fee=250, ref="pi_1", order="o_1", day=12)
        r.gross_reported, r.fx = 11001, True
        r2 = prow(gross=5000, fee=0, ref="pi_2", order="o_2", day=12)
        r2.gross_reported = 5001
        rows += [r, r2]
        ledger += [entry(amount=11000, ref="pi_1", order="o_1", day=12), entry(amount=5000, ref="pi_2", order="o_2", day=12)]
        res = reconcile(rows, ledger)
        self.assertEqual([f.payment_ref for f in res.findings if f.cls == "row_arithmetic"], ["pi_2"])

    def test_payout_sum(self):
        rows, ledger = background(n=3, day=10)
        for r in rows:
            r.payout_id = "batch 7"
        payout = prow("payout", 0, fee=0, net=-(sum(r.net for r in rows) + 500), payout="batch 7", payout_day=20)
        res = reconcile(rows + [payout], ledger)
        f = one(res, "payout_sum")
        self.assertEqual(f.amount, 500)
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_unpaid_rows(self):
        rows, ledger = background(n=4, day=10)
        rows[0].payout_id = rows[1].payout_id = "po_1"
        res = reconcile(rows, ledger)
        labels = {ln.label: ln.amount for ln in res.walks["USD"]}
        self.assertEqual(labels["not in a payout yet"], -(rows[2].net + rows[3].net))
        self.assertEqual(labels["Payouts"], rows[0].net + rows[1].net)
        self.assertIsNone(check_walk(res.walks["USD"]))


def paid_out(days_amounts):
    """One payment per payout, so each payout has a known amount."""
    rows, ledger = [], []
    for i, (day, amount) in enumerate(days_amounts):
        rows.append(prow(gross=amount + 30, fee=30, ref=f"p{i}", order=f"o{i}", day=day - 2, payout=f"po_{day:02d}_{i}", payout_day=day))
        ledger.append(entry(amount=amount + 30, ref=f"p{i}", order=f"o{i}", day=day - 2))
    return rows, ledger


class BankSide(unittest.TestCase):
    def opts(self, **kw):
        return Options(bank_hint="stripe", **kw)

    def test_exact_and_reference(self):
        rows, ledger = paid_out([(5, 10000), (6, 20000), (7, 30000)])
        rows[1].payout_tokens = ("po_ref_000002",)
        bank = [bline(5, 10000, "STRIPE"), bline(7, 20000, "STRIPE po_ref_000002 "), bline(7, 30000, "STRIPE"), bline(20, 1, "INTEREST")]
        res = reconcile(rows, ledger, bank, self.opts())
        self.assertEqual(classes(res), [])
        self.assertEqual({pid: b.day.day for pid, b in res.bank_matches.items()}, {"po_05_0": 5, "po_06_1": 7, "po_07_2": 7})
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_reference_with_a_different_amount(self):
        rows, ledger = paid_out([(5, 10000)])
        rows[0].payout_tokens = ("TX4313726XT",)
        res = reconcile(rows, ledger, [bline(6, 9000, "ADYEN TX4313726XT")], Options())
        self.assertEqual(one(res, "bank_amount_differs").amount, -1000)

    def test_deducted_on_the_way(self):
        rows, ledger = paid_out([(5, 10000), (9, 20000)])
        bank = [bline(5, 10000, "STRIPE"), bline(10, 20000 - 1500, "STRIPE TRANSFER")]
        res = reconcile(rows, ledger, bank, self.opts())
        f = one(res, "bank_amount_differs")
        self.assertEqual((f.payout.payout_id, f.amount), ("po_09_1", -1500))
        self.assertIsNone(check_walk(res.walks["USD"]))
        # without the provider's name in the description, no guess is made
        bank = [bline(1, 5, "INTEREST"), bline(5, 10000, "STRIPE"), bline(10, 18500, "TRANSFER"), bline(20, 5, "INTEREST")]
        self.assertEqual(classes(reconcile(rows, ledger, bank, self.opts())), ["payout_not_in_bank"])

    def test_a_larger_deposit_is_not_a_payout_with_a_deduction(self):
        rows, ledger = paid_out([(5, 10000), (12, 20000), (13, 21000)])
        # the 12th never arrived; the 13th arrived minus a fee: 20,800 is nearer to the 12th's 20,000 but larger than it
        bank = [bline(5, 10000, "STRIPE"), bline(13, 20800, "STRIPE"), bline(25, 1, "STRIPE")]
        res = reconcile(rows, ledger, bank, self.opts())
        self.assertEqual(one(res, "payout_not_in_bank").payout.payout_id, "po_12_1")
        self.assertEqual(one(res, "bank_amount_differs").payout.payout_id, "po_13_2")

    def test_missing_unexplained_and_outside(self):
        rows, ledger = paid_out([(3, 10000), (10, 20000), (15, 30000), (29, 40000)])
        bank = [
            bline(3, 10000, "STRIPE"),
            bline(15, 30000, "STRIPE"),
            bline(12, 77777, "Stripe Payments"),
            bline(30, 55555, "STRIPE"),
            bline(1, 100, "card"),
        ]
        res = reconcile(rows, ledger, bank, self.opts())
        self.assertEqual(classes(res), ["bank_deposit_unexplained", "payout_not_in_bank"])
        self.assertEqual(one(res, "payout_not_in_bank").payout.payout_id, "po_10_1")
        self.assertEqual(one(res, "bank_deposit_unexplained").bank.amount, 77777)
        outside = [f for f in res.findings if f.cls == "outside_window"]
        self.assertEqual(
            sorted(f.detail["side"] for f in outside), ["bank", "bank_line"]
        )  # the 29th's payout may still arrive; the 30th's deposit is next period's
        walk = res.walks["USD"]
        self.assertIsNone(check_walk(walk))
        self.assertEqual(walk[-1].label, "provider deposits no payout explains (not in the total)")

    def test_currency_must_match(self):
        rows, ledger = paid_out([(5, 10000)])
        res = reconcile(rows, ledger, [bline(1, 1, "x"), bline(5, 10000, "STRIPE", currency="EUR"), bline(20, 1, "x")], self.opts())
        self.assertEqual(classes(res), ["bank_deposit_unexplained", "payout_not_in_bank"])

    def test_negative_payout(self):
        rows, ledger = paid_out([(5, 10000)])
        rows.append(prow("dispute", -50000, fee=1500, ref="p0", order="o0", day=8, payout="po_neg", payout_day=10))
        bank = [bline(5, 10000, "STRIPE"), bline(10, -51500, "STRIPE DEBIT"), bline(20, 1, "x")]
        res = reconcile(rows, ledger, bank, self.opts())
        self.assertEqual(res.bank_matches["po_neg"].amount, -51500)


if __name__ == "__main__":
    unittest.main()
