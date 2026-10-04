import unittest
from datetime import date, datetime, timezone

from helpers import TmpDir

from payout_bridge.csvread import InputError
from payout_bridge.sources import load_adyen, load_bank, load_generic, load_ledger, load_stripe, parse_mapping, payouts_from

UTC = timezone.utc

STRIPE_HEADER = [
    "balance_transaction_id",
    "created_utc",
    "available_on",
    "currency",
    "gross",
    "fee",
    "net",
    "reporting_category",
    "automatic_payout_id",
    "automatic_payout_effective_at",
    "payment_intent_id",
    "payment_metadata[order_id]",
]

# The header and three example rows from Adyen's description of the Settlement details report.
ADYEN_HEADER = (
    "Company Account,Merchant Account,Psp Reference,Merchant Reference,Payment Method,Creation Date,TimeZone,Type,Modification Reference,"
    "Gross Currency,Gross Debit (GC),Gross Credit (GC),Exchange Rate,Net Currency,Net Debit (NC),Net Credit (NC),Commission (NC),Markup (NC),"
    "Scheme Fees (NC),Interchange (NC),Payment Method Variant,Modification Merchant Reference,Batch Number,Reserved4,Reserved5,Reserved6,"
    "Reserved7,Reserved8,Reserved9,Reserved10"
)
ADYEN_ROWS = [
    "TestCompany,TestMerchant,1813702551707653,3vpzf7,visa,2013-06-03 11:26:49,CET,Settled,1713702551795048,GBP,,5.00,1,GBP,,4.88,,0.04,0.01,0.07,visaclassic,,4,,,,,,,",
    "TestCompany,TestMerchant,,,,2013-06-11 04:55:40,CET,Fee,Transaction Fees May 2013,EUR,,,,GBP,1.46,,,,,,,,4,,,,,,,",
    'TestCompany,TestMerchant,,,,2013-06-11 04:55:40,CET,MerchantPayout,"TX4313726XT batch 4, TestMerchant batch 4",EUR,,,,GBP,3.42,,,,,,,,4,,,,,,,',
]


class Stripe(TmpDir):
    def test_rows(self):
        path = self.csv(
            "s.csv",
            STRIPE_HEADER + ["customer_facing_amount", "customer_facing_currency"],
            [
                [
                    "txn_1",
                    "2026-09-10 12:00:00",
                    "2026-09-12",
                    "usd",
                    "100.00",
                    "3.20",
                    "96.80",
                    "charge",
                    "po_1",
                    "2026-09-12 00:00:00",
                    "pi_1",
                    "ord_1",
                    "",
                    "",
                ],
                [
                    "txn_2",
                    "2026-09-11 09:00:00",
                    "2026-09-13",
                    "usd",
                    "-40.00",
                    "0.00",
                    "-40.00",
                    "refund",
                    "po_2",
                    "2026-09-13 00:00:00",
                    "pi_1",
                    "ord_1",
                    "",
                    "",
                ],
                [
                    "txn_3",
                    "2026-09-11 10:00:00",
                    "2026-09-13",
                    "usd",
                    "54.32",
                    "1.88",
                    "52.44",
                    "charge",
                    "po_2",
                    "2026-09-13 00:00:00",
                    "pi_3",
                    "ord_3",
                    "50.00",
                    "eur",
                ],
                ["txn_4", "2026-09-12 10:00:00", "2026-09-14", "usd", "-15.00", "0.00", "-15.00", "fee", "", "", "", "", "", ""],
                [
                    "txn_5",
                    "2026-09-12 11:00:00",
                    "2026-09-14",
                    "usd",
                    "-20.00",
                    "0.00",
                    "-20.00",
                    "refund",
                    "",
                    "",
                    "pi_3",
                    "ord_3",
                    "18.40",
                    "eur",
                ],
            ],
        )
        rows = load_stripe(path)
        a, r, fx, f, fxr = rows
        self.assertEqual((a.kind, a.gross, a.fee, a.net, a.currency), ("payment", 10000, 320, 9680, "USD"))
        self.assertEqual((a.payment_ref, a.order_ref, a.payout_id, a.payout_date), ("pi_1", "ord_1", "po_1", date(2026, 9, 12)))
        self.assertEqual(a.created, datetime(2026, 9, 10, 12, tzinfo=UTC))
        self.assertEqual((r.kind, r.gross), ("refund", -4000))
        self.assertEqual((fx.amount_tx, fx.currency_tx, fx.amount_in("EUR"), fx.amount_in("USD")), (5000, "EUR", 5000, 5432))
        self.assertEqual((fxr.amount_tx, fxr.currency_tx), (-1840, "EUR"))
        self.assertEqual((f.kind, f.payout_id, f.payment_ref), ("fee", None, None))
        payouts = payouts_from(rows)
        self.assertEqual(sorted(payouts), ["po_1", "po_2"])
        self.assertEqual(payouts["po_2"].amount, -4000 + 5244)
        self.assertFalse(payouts["po_2"].declared)

    def test_created_in_report_zone(self):
        header = [h if h != "created_utc" else "created" for h in STRIPE_HEADER]
        path = self.csv(
            "s.csv", header, [["txn_1", "2026-09-10 21:00:00", "2026-09-12", "usd", "1.00", "0.33", "0.67", "charge", "", "", "pi_1", "o"]]
        )
        (row,) = load_stripe(path, report_tz="+09:00")
        self.assertEqual(row.created, datetime(2026, 9, 10, 12, tzinfo=UTC))

    def test_default_columns_cannot_be_matched(self):
        path = self.csv(
            "s.csv",
            [
                "balance_transaction_id",
                "created",
                "available_on",
                "currency",
                "gross",
                "fee",
                "net",
                "reporting_category",
                "description",
                "automatic_payout_id",
                "automatic_payout_effective_at",
            ],
            [],
        )
        with self.assertRaisesRegex(InputError, "payment_intent_id"):
            load_stripe(path)

    def test_metadata_key(self):
        header = STRIPE_HEADER[:-1] + ["payment_metadata[order_id]", "payment_metadata[customer]"]
        path = self.csv(
            "s.csv", header, [["txn_1", "2026-09-10 12:00:00", "", "usd", "1.00", "0", "1.00", "charge", "", "", "pi", "ord_9", "c_1"]]
        )
        with self.assertRaisesRegex(InputError, "--order-key"):
            load_stripe(path)
        self.assertEqual(load_stripe(path, order_key="order_id")[0].order_ref, "ord_9")
        with self.assertRaisesRegex(InputError, "no column"):
            load_stripe(path, order_key="nope")

    def test_errors_carry_line_numbers(self):
        path = self.csv(
            "s.csv",
            STRIPE_HEADER,
            [
                ["txn_1", "2026-09-10 12:00:00", "", "usd", "1.005", "0", "1.00", "charge", "", "", "pi", "o"],
                ["txn_2", "yesterday", "", "usd", "1.00", "0", "1.00", "charge", "", "", "pi", "o"],
            ],
        )
        with self.assertRaises(InputError) as cm:
            load_stripe(path)
        self.assertIn("s.csv:2:", str(cm.exception))
        self.assertIn("s.csv:3:", str(cm.exception))


class Adyen(TmpDir):
    def test_documented_example(self):
        path = self.dir / "a.csv"
        path.write_text("\n".join([ADYEN_HEADER] + ADYEN_ROWS) + "\n")
        settled, fee, payout = load_adyen(path)
        self.assertEqual((settled.kind, settled.gross, settled.fee, settled.net, settled.currency), ("payment", 500, 12, 488, "GBP"))
        self.assertEqual((settled.payment_ref, settled.order_ref, settled.txn_id), ("1813702551707653", "3vpzf7", "1713702551795048"))
        self.assertEqual(settled.created, datetime(2013, 6, 3, 10, 26, 49, tzinfo=UTC))  # CET
        self.assertEqual(settled.gross_reported, 500)
        self.assertEqual((fee.kind, fee.gross, fee.fee, fee.net), ("fee", -146, 0, -146))
        self.assertEqual(
            (payout.kind, payout.net, payout.payout_tokens[0], payout.payout_date), ("payout", -342, "TX4313726XT", date(2013, 6, 11))
        )
        self.assertEqual(settled.payout_id, "batch 4")
        self.assertEqual(settled.payout_date, date(2013, 6, 11))
        (po,) = payouts_from([settled, fee, payout]).values()
        self.assertEqual((po.amount, po.declared, sum(r.net for r in po.rows)), (342, True, 342))

    def test_open_batch_is_not_paid_out(self):
        path = self.dir / "a.csv"
        path.write_text("\n".join([ADYEN_HEADER, ADYEN_ROWS[0].replace(",4,", ",5,")]) + "\n")
        (row,) = load_adyen(path)
        self.assertIsNone(row.payout_id)

    def test_fx_and_debits(self):
        rows = [
            "C,M,111,o1,visa,2026-09-10 10:00:00,UTC,Settled,m1,EUR,,100.00,1.1,USD,,107.50,2.50,,,,,,1,,,,,,,",
            "C,M,111,o1,visa,2026-09-11 10:00:00,UTC,Refunded,m2,EUR,40.00,,1.1,USD,44.00,,,,,,,,1,,,,,,,",
            "C,M,222,o2,visa,2026-09-11 10:00:00,UTC,Chargeback,m3,USD,20.00,,1,USD,35.00,,15.00,,,,,,1,,,,,,,",
            "C,M,,,,2026-09-12 04:00:00,UTC,MerchantPayout,TX1XT,USD,,,,USD,28.50,,,,,,,,1,,,,,,,",
        ]
        path = self.dir / "a.csv"
        path.write_text("\n".join([ADYEN_HEADER] + rows) + "\n")
        pay, ref, cb, po = load_adyen(path)
        self.assertEqual(
            (pay.gross, pay.fee, pay.net, pay.amount_tx, pay.currency_tx, pay.gross_reported, pay.fx),
            (11000, 250, 10750, 10000, "EUR", 11000, True),
        )
        self.assertEqual((ref.kind, ref.gross, ref.fee, ref.net, ref.amount_tx), ("refund", -4400, 0, -4400, -4000))
        self.assertEqual((cb.kind, cb.gross, cb.fee, cb.net), ("dispute", -2000, 1500, -3500))
        self.assertEqual(po.payout_tokens, ("TX1XT", "batch 1"))

    def test_merchant_accounts_keep_batches_apart(self):
        rows = [
            "C,M1,111,o1,visa,2026-09-10 10:00:00,UTC,Settled,m1,USD,,10.00,1,USD,,9.70,0.30,,,,,,1,,,,,,,",
            "C,M2,222,o2,visa,2026-09-10 10:00:00,UTC,Settled,m2,USD,,10.00,1,USD,,9.70,0.30,,,,,,1,,,,,,,",
        ]
        path = self.dir / "a.csv"
        path.write_text("\n".join([ADYEN_HEADER] + rows) + "\n")
        self.assertEqual({r.payout_id for r in load_adyen(path)}, {None})


class Generic(TmpDir):
    def test_rows_and_declared_payout(self):
        path = self.csv(
            "g.csv",
            ["txn_id", "kind", "gross", "fee", "currency", "created_at", "payment_ref", "order_ref", "payout_id", "payout_date"],
            [
                ["t1", "payment", "10.00", "0.30", "USD", "2026-09-10T10:00:00Z", "p1", "o1", "P1", "2026-09-12"],
                ["t2", "payout", "-9.70", "", "USD", "2026-09-12T00:00:00Z", "", "", "P1", "2026-09-12"],
            ],
        )
        rows = load_generic(path)
        self.assertEqual((rows[0].net, rows[1].kind), (970, "payout"))
        po = payouts_from(rows)["P1"]
        self.assertEqual((po.amount, po.declared, po.tokens), (970, True, ("P1", "t2")))

    def test_unknown_kind(self):
        path = self.csv("g.csv", ["id", "kind", "amount", "currency"], [["t1", "sale", "1", "USD"]])
        with self.assertRaisesRegex(InputError, "unknown kind"):
            load_generic(path)


class Ledger(TmpDir):
    def test_synonyms_signs_and_statuses(self):
        path = self.csv(
            "l.csv",
            ["order_id", "type", "total", "currency", "created", "state", "payment_intent", "id"],
            [
                ["o1", "charge", "10.00", "usd", "2026-09-10 21:00:00", "succeeded", "pi_1", "1"],
                ["o1", "refund", "4.00", "usd", "2026-09-11 19:00:00", "succeeded", "pi_1", "2"],
                ["o2", "chargeback", "-5.00", "usd", "2026-09-12 19:00:00", "", "pi_2", "3"],
                ["o3", "order", "7.00", "usd", "2026-09-12 19:00:00", "pending", "", "4"],
                ["o4", "payment", "-3.00", "usd", "2026-09-12 19:00:00", "paid", "", "5"],
            ],
        )
        e = load_ledger(path, tz="+09:00")
        self.assertEqual(
            [(x.kind, x.amount, x.paid) for x in e],
            [("payment", 1000, True), ("refund", -400, True), ("dispute", -500, True), ("payment", 700, False), ("refund", -300, True)],
        )
        self.assertEqual((e[0].payment_ref, e[0].order_ref, e[0].currency), ("pi_1", "o1", "USD"))
        self.assertEqual(e[0].created, datetime(2026, 9, 10, 12, tzinfo=UTC))

    def test_mapping_and_no_kind_column(self):
        path = self.csv("l.csv", ["no", "sum", "ref"], [["1", "10,50", "o1"], ["2", "-2,00", "o1"]])
        e = load_ledger(path, mapping=parse_mapping("id=no,amount=sum,order_ref=ref"), currency="eur", decimal_comma=True)
        self.assertEqual([(x.kind, x.amount, x.currency) for x in e], [("payment", 1050, "EUR"), ("refund", -200, "EUR")])

    def test_needs_a_key(self):
        path = self.csv("l.csv", ["id", "amount", "currency"], [["1", "1", "USD"]])
        with self.assertRaisesRegex(InputError, "reference"):
            load_ledger(path)
        with self.assertRaisesRegex(InputError, "mapped column"):
            load_ledger(path, mapping={"order_ref": "missing"})

    def test_custom_paid_statuses(self):
        path = self.csv(
            "l.csv",
            ["id", "amount", "currency", "status", "order_ref"],
            [["1", "1", "USD", "PAID_OUT", "o"], ["2", "1", "USD", "paid", "o"]],
        )
        self.assertEqual([x.paid for x in load_ledger(path, paid_statuses=["paid_out"])], [True, False])


class Bank(TmpDir):
    def test_credit_debit_and_descriptions(self):
        path = self.csv(
            "b.csv",
            ["Booking date", "Details", "Reference", "Credit", "Debit", "Currency"],
            [
                ["2026-09-12", "STRIPE", "ST-1", "1,234.50", "", "USD"],
                ["2026-09-13", "RENT", "", "", "500.00", "USD"],
            ],
        )
        a, b = load_bank(path)
        self.assertEqual((a.day, a.amount, a.description), (date(2026, 9, 12), 123450, "STRIPE | ST-1"))
        self.assertEqual(b.amount, -50000)

    def test_semicolons_and_bom(self):
        path = self.dir / "b.csv"
        path.write_bytes("﻿date;amount;description\n2026-09-12;12,50;ADYEN TX1XT\n".encode())
        (a,) = load_bank(path, currency="EUR", decimal_comma=True)
        self.assertEqual((a.amount, a.currency, a.description), (1250, "EUR", "ADYEN TX1XT"))

    def test_needs_currency(self):
        path = self.csv("b.csv", ["date", "amount"], [["2026-09-12", "1"]])
        with self.assertRaisesRegex(InputError, "bank-currency"):
            load_bank(path)


if __name__ == "__main__":
    unittest.main()
