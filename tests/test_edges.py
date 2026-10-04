"""Cases from review: each one was wrong once."""

import unittest

from helpers import TmpDir, bline, check_walk, entry, prow
from test_cli import call
from test_reconcile import background, classes, one
from test_sources import ADYEN_HEADER, STRIPE_HEADER

from payout_bridge.reconcile import Options, reconcile
from payout_bridge.sources import load_adyen, load_bank, load_generic, load_ledger, load_stripe, payouts_from


class Matching(unittest.TestCase):
    def test_timeout_then_retry_both_captured(self):
        # the ledger gave up on pi_1 after a timeout, the retry pi_2 succeeded, and the provider captured both
        rows, ledger = background()
        rows += [prow(gross=4900, ref="pi_1", order="o_1", day=12), prow(gross=4900, ref="pi_2", order="o_1", day=12, hour=13)]
        ledger += [
            entry(amount=4900, ref="pi_1", order="o_1", day=12, status="failed"),
            entry(amount=4900, ref="pi_2", order="o_1", day=12, hour=13),
        ]
        res = reconcile(rows, ledger)
        f = one(res, "double_charge")
        self.assertEqual((f.payment_ref, f.ledger[0].status), ("pi_1", "failed"))
        self.assertEqual(classes(res), ["double_charge"])
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_charge_id_in_the_ledger(self):
        rows, ledger = background()
        r = prow(gross=2500, ref="pi_1", order="o_1", day=12)
        r.alt_refs = ("ch_1",)
        rows.append(r)
        ledger.append(entry(amount=2500, ref="ch_1", order=None, day=12))
        self.assertEqual(classes(reconcile(rows, ledger)), [])

    def test_captures_of_one_payment(self):
        rows, ledger = background()
        rows += [prow(gross=6000, ref="psp_1", order="o_1", day=12), prow(gross=4000, ref="psp_1", order="o_1", day=13)]
        ledger.append(entry(amount=10000, ref="psp_1", order="o_1", day=12))
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), [])
        self.assertIsNone(check_walk(res.walks["USD"]))
        # a ledger that books every capture gets them one by one
        rows, ledger = background()
        rows += [prow(gross=6000, ref="psp_1", order="o_1", day=12), prow(gross=4000, ref="psp_1", order="o_1", day=13)]
        ledger += [entry(amount=6000, ref="psp_1", order="o_1", day=12), entry(amount=4000, ref="psp_1", order="o_1", day=13)]
        self.assertEqual(classes(reconcile(rows, ledger)), [])

    def test_free_order(self):
        rows, ledger = background()
        ledger.append(entry(amount=0, ref=None, order="o_free", day=12))
        self.assertEqual(classes(reconcile(rows, ledger)), [])

    def test_same_order_different_payment_id_is_not_a_duplicate(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12))
        ledger += [entry(amount=2500, ref="pi_1", order="o_1", day=12), entry(amount=2500, ref="pi_2", order="o_1", day=12, hour=14)]
        self.assertEqual(classes(reconcile(rows, ledger)), ["not_at_provider"])

    def test_second_payment_after_the_ledger_export(self):
        rows, ledger = background(n=8, day=1)  # ledger up to the 22nd
        rows.append(prow(gross=2500, ref="pi_1", order="bgo3", day=28))
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), [])
        self.assertEqual(one(res, "outside_window").payment_ref, "pi_1")

    def test_payment_reversal(self):
        rows, ledger = background()
        rows += [prow(gross=2500, ref="pi_1", order="o_1", day=12), prow("payment_reversal", -2500, fee=0, ref="pi_1", order="o_1", day=16)]
        ledger.append(entry(amount=2500, ref="pi_1", order="o_1", day=12))
        res = reconcile(rows, ledger)
        f = one(res, "not_in_ledger")
        self.assertEqual((f.kind, f.amount), ("payment_reversal", -2500))
        self.assertIsNone(check_walk(res.walks["USD"]))

    def test_notes(self):
        rows, ledger = background(n=5, day=10)
        ledger.append(entry(amount=777, ref="early", order="e", day=1))
        ledger.append(entry(amount=900, ref="eur", order="x", day=15, currency="EUR"))
        notes = reconcile(rows, ledger).notes
        self.assertTrue(notes[0].startswith("1 paid ledger entries (7.77 USD)"), notes)
        self.assertIn("in EUR", notes[1])


class Bank(unittest.TestCase):
    def test_provider_debits_are_not_deposits(self):
        rows, ledger = background(n=3, day=1)
        for r in rows:
            r.payout_id, r.payout_date = "po_1", None
        po = payouts_from(rows)["po_1"]
        po.day = None
        bank = [bline(1, 5, "x"), bline(12, -4900, "STRIPE BILLING INVOICE"), bline(25, 5, "x")]
        res = reconcile(rows, ledger, bank, Options(bank_hint="stripe"))
        self.assertNotIn("bank_deposit_unexplained", classes(res))

    def test_payout_currencies_kept_apart(self):
        rows = [
            prow(gross=1000, ref="a", payout="batch 1", payout_day=12),
            prow(gross=2000, ref="b", currency="EUR", payout="batch 1", payout_day=12),
        ]
        self.assertEqual(sorted(payouts_from(rows)), ["batch 1 EUR", "batch 1 USD"])
        res = reconcile(rows, [entry(amount=1000, ref="a", day=10), entry(amount=2000, ref="b", day=10, currency="EUR")])
        for cur in ("USD", "EUR"):
            self.assertIsNone(check_walk(res.walks[cur]))


class Readers(TmpDir):
    def test_paid_statuses_with_spaces_and_blank(self):
        path = self.csv(
            "l.csv", ["id", "amount", "currency", "status", "order_ref"], [["1", "1", "USD", "succeeded", "o"], ["2", "-1", "USD", "", "o"]]
        )
        self.assertEqual([x.paid for x in load_ledger(path, paid_statuses=" succeeded, paid".split(","))], [True, True])

    def test_mapped_description_wins(self):
        path = self.csv(
            "b.csv",
            ["date", "amount", "currency", "Reference", "Remittance information"],
            [["2026-09-12", "10.00", "USD", "NOTPROVIDED", "STRIPE TRANSFER po_123456789"]],
        )
        (b,) = load_bank(path, mapping={"description": "Remittance information"})
        self.assertEqual(b.description, "STRIPE TRANSFER po_123456789")

    def test_stripe_charge_ids_and_failures(self):
        path = self.csv(
            "s.csv",
            STRIPE_HEADER[:-1] + ["charge_id", "payment_metadata[order_id]"],
            [
                ["txn_1", "2026-09-10 12:00:00", "", "usd", "10.00", "0.59", "9.41", "charge", "", "", "pi_1", "ch_1", "o1"],
                ["txn_2", "2026-09-14 12:00:00", "", "usd", "-10.00", "0", "-10.00", "charge_failure", "", "", "pi_1", "ch_1", "o1"],
            ],
        )
        a, b = load_stripe(path)
        self.assertEqual((a.payment_ref, a.alt_refs), ("pi_1", ("ch_1",)))
        self.assertEqual(b.kind, "payment_reversal")

    def test_adyen_reversed_settlement(self):
        path = self.dir / "a.csv"
        path.write_text(
            ADYEN_HEADER + "\nC,M,111,o1,visa,2026-09-10 10:00:00,UTC,SettledReversed,m9,USD,10.00,,1,USD,10.00,,,,,,,,1,,,,,,,\n"
        )
        (r,) = load_adyen(path)
        self.assertEqual((r.kind, r.gross), ("payment_reversal", -1000))

    def test_generic_payout_date_from_its_rows(self):
        path = self.csv(
            "g.csv",
            ["txn_id", "kind", "gross", "fee", "currency", "created_at", "payment_ref", "payout_id", "payout_date"],
            [
                ["t1", "payment", "10.00", "0.30", "USD", "2026-09-10T10:00:00Z", "p1", "P1", "2026-09-12"],
                ["t2", "payout", "-9.70", "", "USD", "2026-09-12T00:00:00Z", "", "P1", ""],
            ],
        )
        self.assertEqual(str(payouts_from(load_generic(path))["P1"].day), "2026-09-12")

    def test_line_endings_and_encodings(self):
        cr = self.dir / "cr.csv"
        cr.write_bytes(b"date,amount,currency,description\r2026-09-12,1.00,USD,x\r2026-09-13,2.00,USD,y\r")
        self.assertEqual([b.amount for b in load_bank(cr)], [100, 200])
        u16 = self.dir / "u16.csv"
        u16.write_bytes("date,amount,currency,description\n2026-09-12,1.00,USD,x\n".encode("utf-16"))
        self.assertEqual([b.amount for b in load_bank(u16)], [100])


class CommandLine(TmpDir):
    def files(self):
        report = self.csv(
            "g.csv",
            ["txn_id", "kind", "gross", "fee", "currency", "created_at", "payment_ref"],
            [["t1", "payment", "10.00", "0.30", "USD", "2026-09-10T10:00:00Z", "p1"]],
        )
        ledger = self.csv(
            "l.csv", ["id", "amount", "currency", "created_at", "payment_ref"], [["1", "10.00", "USD", "2026-09-10T10:00:01Z", "p1"]]
        )
        bank = self.csv("b.csv", ["date", "amount", "currency", "description"], [["2026-09-12", "9.70", "USD", "PSP"]])
        return [str(report), "--provider", "generic", "--ledger", str(ledger), "--bank", str(bank)]

    def test_input_problems_exit_two(self):
        base = self.files()
        code, _, err = call(base + ["--bank-hint", "Stripe (US"])
        self.assertEqual(code, 2)
        self.assertIn("regular expression", err)
        code, _, err = call(base + ["--md", str(self.dir / "missing" / "r.md")])
        self.assertEqual(code, 2)
        code, _, err = call(base + ["--json", str(self.dir / "missing" / "r.json")])
        self.assertEqual(code, 2)
        nul = self.dir / "nul.csv"
        nul.write_bytes(b"date,amount\x00\n")
        code, _, err = call([str(nul), "--provider", "generic", "--ledger", str(nul)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()


class SecondReview(TmpDir):
    def test_reference_on_a_debit_is_not_the_deposit(self):
        rows = [prow(gross=7818, fee=30, ref="p1", order="o1", day=8, payout="po_ABCDEFGH123", payout_day=10)]
        rows[0].payout_tokens = ("po_ABCDEFGH123",)
        bank = [
            bline(1, 1, "x"),
            bline(10, -1500, "INCOMING WIRE FEE STRIPE po_ABCDEFGH123"),
            bline(11, 7788, "STRIPE TRANSFER po_ABCDEFGH123"),
            bline(25, 1, "x"),
        ]
        res = reconcile(rows, [entry(amount=7818, ref="p1", order="o1", day=8)], bank, Options(bank_hint="stripe"))
        self.assertEqual(res.bank_matches["po_ABCDEFGH123"].amount, 7788)
        self.assertEqual(classes(res), [])

    def test_currency_the_statement_does_not_carry(self):
        rows = [
            prow(gross=1000, ref="a", payout="batch 1", payout_day=12),
            prow(gross=2000, ref="b", currency="EUR", payout="batch 1", payout_day=12),
        ]
        ledger = [entry(amount=1000, ref="a", day=10), entry(amount=2000, ref="b", day=10, currency="EUR")]
        res = reconcile(rows, ledger, [bline(1, 1, "x"), bline(12, 970, "PSP"), bline(25, 1, "x")], Options())
        self.assertEqual(classes(res), [])
        self.assertIn("no EUR lines", one(res, "outside_window").message)

    def test_named_payment_id_wins_over_an_order_fallback(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12))
        ledger += [entry(amount=2600, ref="pi_1", order="o_1", day=12), entry(amount=2500, ref="pi_2", order="o_1", day=12, hour=13)]
        res = reconcile(rows, ledger)
        self.assertEqual(classes(res), ["amount_mismatch", "not_at_provider"])
        self.assertEqual(one(res, "not_at_provider").ledger[0].payment_ref, "pi_2")

    def test_captures_booked_one_by_one_without_ids(self):
        rows, ledger = background()
        rows += [prow(gross=6000, ref="psp_1", order="o_1", day=12), prow(gross=4000, ref="psp_1", order="o_1", day=13)]
        ledger += [entry(amount=6000, order="o_1", day=12), entry(amount=4000, order="o_1", day=13)]
        self.assertEqual(classes(reconcile(rows, ledger)), [])

    def test_second_capture_pending(self):
        rows, ledger = background()
        rows += [prow(gross=6000, ref="psp_1", order="o_1", day=12), prow(gross=4000, ref="psp_1", order="o_1", day=13)]
        ledger += [
            entry(amount=6000, ref="psp_1", order="o_1", day=12),
            entry(amount=4000, ref="psp_1", order="o_1", day=13, status="pending"),
        ]
        self.assertEqual(classes(reconcile(rows, ledger)), ["pending_in_ledger"])

    def test_split_payment_booked_twice(self):
        rows, ledger = background()
        rows += [prow(gross=6000, ref="psp_1", order="o_1", day=12), prow(gross=4000, ref="psp_1", order="o_1", day=13)]
        ledger += [entry(amount=10000, ref="psp_1", order="o_1", day=12), entry(amount=10000, ref="psp_1", order="o_1", day=12, hour=13)]
        self.assertEqual(classes(reconcile(rows, ledger)), ["duplicate_in_ledger"])

    def test_same_payment_id_different_amount_is_a_duplicate(self):
        rows, ledger = background()
        rows.append(prow(gross=2500, ref="pi_1", order="o_1", day=12))
        ledger += [entry(amount=2500, ref="pi_1", order="o_1", day=12), entry(amount=2700, ref="pi_1", order="o_1", day=12, hour=13)]
        res = reconcile(rows, ledger)
        self.assertIn("different amount", one(res, "duplicate_in_ledger").message)

    def test_stripe_refund_by_its_own_id(self):
        path = self.csv(
            "s.csv",
            STRIPE_HEADER[:-1] + ["source_id", "payment_metadata[order_id]"],
            [
                ["txn_1", "2026-09-10 12:00:00", "", "usd", "10.00", "0.59", "9.41", "charge", "", "", "pi_1", "ch_1", "o1"],
                ["txn_2", "2026-09-11 12:00:00", "", "usd", "-10.00", "0", "-10.00", "refund", "", "", "pi_1", "re_3", "o1"],
            ],
        )
        rows = load_stripe(path)
        ledger = [entry(amount=1000, ref="pi_1", order="o1", day=10), entry("refund", -1000, ref="re_3", order="o1", day=11)]
        self.assertEqual(classes(reconcile(rows, ledger)), [])

    def test_foreign_entries_without_amounts_are_not_counted_as_converted(self):
        rows, ledger = background()
        rows.append(prow(gross=5432, ref="pi_1", order="o_1", day=12))
        ledger.append(entry(amount=5000, ref="pi_1", order="o_1", day=12, currency="EUR"))
        res = reconcile(rows, ledger)
        self.assertNotIn("other currencies", res.walks["USD"][0].label)

    def test_small_readers(self):
        from payout_bridge.money import AmountError, parse_minor
        from payout_bridge.timeparse import TimeError, parse_datetime

        with self.assertRaises(AmountError):
            parse_minor("0,500", "USD")
        with self.assertRaises(AmountError):
            parse_minor("0.500", "USD", decimal_comma=True)
        with self.assertRaises(TimeError):
            parse_datetime("2026-09-14 10:00:00-30")
        self.assertEqual(str(parse_datetime("2026/09/14 10:00")), "2026-09-14 10:00:00+00:00")
        semi = self.dir / "b.csv"
        semi.write_text("\ndate;amount;currency;description\n2026-09-12;1.00;USD;x\n")
        self.assertEqual([b.amount for b in load_bank(semi)], [100])
        blank = self.csv("l.csv", ["id", "amount", "currency", "order_ref", "status"], [["1", "1.00", "", "o", "processing"]])
        with self.assertRaisesRegex(Exception, "empty currency"):
            load_ledger(blank)
        ok = self.csv("l2.csv", ["id", "amount", "currency", "order_ref", "status"], [["1", "1.00", "USD", "o", "processing"]])
        self.assertTrue(load_ledger(ok)[0].paid)
