import contextlib
import io
import json
import unittest

from helpers import TmpDir, check_walk

from payout_bridge.cli import run
from payout_bridge.evaluate import SCORED, evaluate, run_one
from payout_bridge.synth import generate, write


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run(argv)
    return code, out.getvalue(), err.getvalue()


class Cli(TmpDir):
    def month(self, provider="stripe", seed=3):
        w = generate(provider, seed)
        return w, write(w, self.dir / provider)

    def test_stripe_month(self):
        w, p = self.month()
        code, md, err = call(
            [
                str(p["report"]),
                "--provider",
                "stripe",
                "--ledger",
                str(p["ledger"]),
                "--bank",
                str(p["bank"]),
                "--ledger-tz=-04:00",
                "--fee",
                "2.9%+0.30",
                "--json",
                str(self.dir / "out.json"),
            ]
        )
        self.assertEqual((code, err), (1, ""))
        self.assertIn("## From your ledger to the bank, USD", md)
        doc = json.loads((self.dir / "out.json").read_text())
        self.assertEqual(doc["worst"], "error")
        found = sorted(
            (f["class"], f["order_ref"])
            for f in doc["findings"]
            if f["class"] in SCORED
            and f["class"]
            not in (
                "row_arithmetic",
                "fee_above_schedule",
                "payout_not_in_bank",
                "bank_amount_differs",
                "payout_sum",
                "bank_deposit_unexplained",
            )
        )
        truth = sorted(
            (t["class"], t["key"])
            for t in w.truth
            if t["class"]
            not in (
                "row_arithmetic",
                "fee_above_schedule",
                "payout_not_in_bank",
                "bank_amount_differs",
                "payout_sum",
                "bank_deposit_unexplained",
            )
        )
        self.assertEqual(found, truth)

    def test_adyen_month_to_files(self):
        _, p = self.month("adyen", 5)
        md = self.dir / "r.md"
        code, out, err = call(
            [
                str(p["report"]),
                "--provider",
                "adyen",
                "--ledger",
                str(p["ledger"]),
                "--bank",
                str(p["bank"]),
                "--ledger-tz=-04:00",
                "--md",
                str(md),
                "--fail-on",
                "never",
            ]
        )
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertIn("## From your ledger to the bank, USD", md.read_text())

    def test_clean_exits_zero(self):
        path = self.csv(
            "g.csv",
            ["txn_id", "kind", "gross", "fee", "currency", "created_at", "payment_ref", "payout_id", "payout_date"],
            [["t1", "payment", "10.00", "0.30", "USD", "2026-09-10T10:00:00Z", "p1", "P1", "2026-09-12"]],
        )
        ledger = self.csv(
            "l.csv", ["id", "amount", "currency", "created_at", "payment_ref"], [["1", "10.00", "USD", "2026-09-10T10:00:01Z", "p1"]]
        )
        bank = self.csv("b.csv", ["date", "amount", "currency", "description"], [["2026-09-12", "9.70", "USD", "PSP payout"]])
        self.assertEqual(call([str(path), "--provider", "generic", "--ledger", str(ledger), "--bank", str(bank)])[0], 0)

    def test_bad_input_exits_two(self):
        bad = self.csv("s.csv", ["created", "gross"], [["x", "1"]])
        code, out, err = call([str(bad), "--provider", "stripe", "--ledger", str(bad)])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("missing column", err)
        _, p = self.month()
        code, _, err = call([str(p["report"]), "--provider", "stripe", "--ledger", str(p["ledger"]), "--from", "2026-09-01"])
        self.assertEqual(code, 2)
        self.assertIn("--from and --to", err)
        code, _, err = call([str(p["report"]), "--provider", "stripe", "--ledger", str(p["ledger"]), "--fee", "2.9"])
        self.assertEqual(code, 2)

    def test_fail_on_warning(self):
        path = self.csv(
            "g.csv",
            ["txn_id", "kind", "gross", "fee", "currency", "created_at", "payment_ref"],
            [["t1", "payment", "10.00", "0.30", "USD", "2026-09-10T10:00:00Z", "p1"]],
        )
        ledger = self.csv(
            "l.csv", ["id", "amount", "currency", "created_at", "payment_ref"], [["1", "10.01", "USD", "2026-09-10T10:00:01Z", "p1"]]
        )
        base = [str(path), "--provider", "generic", "--ledger", str(ledger)]
        self.assertEqual(call(base)[0], 0)
        self.assertEqual(call(base + ["--fail-on", "warning"])[0], 1)


class Generated(unittest.TestCase):
    def test_every_injected_case_and_nothing_else(self):
        from payout_bridge.evaluate import BANK
        from payout_bridge.synth import generate as gen

        table, misses, by_statement, broken = evaluate(seeds=40)
        self.assertEqual(broken, [])
        # the only disagreements allowed: the bank side, on statements without payout references
        for provider, seed, cls, *_ in misses:
            self.assertIn(cls, BANK)
            self.assertFalse(gen(provider, seed).flags["bank_has_payout_refs"], (provider, seed, cls))
        for (provider, refs, cls), c in by_statement.items():
            if refs:
                self.assertEqual((c["fp"], c["fn"]), (0, 0), (provider, cls))
        for (provider, cls), c in table.items():
            if cls == "partial_capture" and provider == "adyen" or cls == "payout_sum" and provider == "stripe":
                continue
            self.assertGreater(c["tp"], 0, (provider, cls))

    def test_walks_add_up(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            for provider in ("stripe", "adyen"):
                for seed in range(1, 11):
                    res, _, _, _ = run_one(provider, seed, Path(tmp))
                    for cur, lines in res.walks.items():
                        self.assertIsNone(check_walk(lines), (provider, seed, cur))

    def test_deterministic(self):
        a, b = generate("stripe", 9), generate("stripe", 9)
        self.assertEqual(a.truth, b.truth)
        self.assertEqual([t.txn_id for t in a.report], [t.txn_id for t in b.report])


if __name__ == "__main__":
    unittest.main()
