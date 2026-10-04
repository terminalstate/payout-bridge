"""A month of a made-up merchant, with known discrepancies put in on purpose.

    python -m payout_bridge.synth --provider stripe --seed 7 --out demo/

writes the provider's report (Stripe or Adyen format), a ledger export, a bank statement and truth.json:
the list of discrepancies that were put in. The same generator drives the tests: whatever the checker
reports must be exactly that list. This tests the checker against its author's idea of the problems,
not against real data.

The merchant: a few hundred orders a month, a third of them at one of three subscription prices (so the
same amount on the same day is common), a tenth paid in EUR and settled in USD, refunds booked by the
shop, disputes nobody books, daily payouts two days after the payment, a ledger that writes local time
without an offset.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .money import round_minor

UTC = timezone.utc
LEDGER_TZ = timezone(timedelta(hours=-4))  # the shop's books: local time, no offset written
ADYEN_TZ = timezone(timedelta(hours=2))  # the Adyen account's report zone, written as CEST
SUBSCRIPTION_PRICES = (1900, 4900, 9900)
SCHEDULE = "2.9%+0.30"


@dataclass
class Txn:
    txn_id: str
    kind: str  # payment, refund, dispute, partial_capture_reversal, fee
    created: datetime
    gross: int  # USD minor, signed
    fee: int
    payment_ref: str
    order_ref: str
    amount_tx: int | None = None  # EUR minor, signed
    rate: Decimal = Decimal(1)
    payout_day: date | None = None
    payout_id: str | None = None
    fee_written: int | None = None  # row_arithmetic: what the export says the fee was
    gross_gc_bump: int = 0  # row_arithmetic (Adyen): what the export says the gross was, minus the truth
    hidden: bool = False  # left out of the export (payout_sum)
    charge_ref: str = ""  # Stripe: the charge id next to the payment intent


@dataclass
class Order:
    ref: str
    created: datetime
    currency: str
    amount: int  # in currency
    payment_ref: str
    gross_usd: int
    rate: Decimal
    busy: bool = False  # has a refund, dispute or an injection already
    attempts: bool = False  # the ledger has a failed attempt for it
    charge_ref: str = ""
    attempt_ref: str = ""  # the failed attempt's payment intent …
    attempt_charge: str = ""  # … and charge


@dataclass
class World:
    provider: str
    start: date
    end: date  # last day of the month
    txns: list[Txn] = field(default_factory=list)
    ledger: list[dict] = field(default_factory=list)
    bank: list[dict] = field(default_factory=list)
    payouts: dict = field(default_factory=dict)
    truth: list[dict] = field(default_factory=list)
    report: list[Txn] = field(default_factory=list)  # what the provider's export shows
    report_payouts: list[dict] = field(default_factory=list)
    ledger_export: list[dict] = field(default_factory=list)
    flags: dict = field(default_factory=dict)  # how this month's exports look


def _usd(amount_eur: int, rate: Decimal) -> int:
    return round_minor(Decimal(amount_eur) * rate)


def _fee(gross: int) -> int:
    return round_minor(Decimal(gross) * Decimal("0.029")) + 30


def generate(provider: str = "stripe", seed: int = 1, start: date = date(2026, 9, 1)) -> World:
    if provider not in ("stripe", "adyen"):
        raise ValueError("provider must be stripe or adyen")
    rng = random.Random(seed)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    w = World(provider, start, end)
    t0 = datetime(start.year, start.month, start.day, tzinfo=UTC)
    t_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    seq = iter(range(10_000_000, 99_999_999))
    # how this month's exports look: a ledger that stores Stripe charge ids instead of payment intents; a bank
    # statement that carries the payout reference (Stripe: the payout id, Adyen: the TX…XT token)
    w.flags = {
        "ledger_has_charge_ids": provider == "stripe" and rng.random() < 0.3,
        "bank_has_payout_refs": rng.random() < (0.35 if provider == "stripe" else 0.7),
    }

    def ref(prefix: str) -> str:
        n = next(seq)
        if provider == "adyen" and prefix in ("pi", "txn"):
            return f"{rng.randint(10**15, 10**16 - 1)}"
        return f"{prefix}_{n:08d}{rng.randint(1000, 9999)}"

    def rate_on(day: datetime) -> Decimal:
        return Decimal("1.0800") + Decimal(rng.randint(0, 400)) / Decimal(10000)

    # ---------------------------------------------------------------- orders
    orders: list[Order] = []
    n_orders = rng.randint(260, 420)
    span = (t_end - t0) + timedelta(days=20)
    for i in range(n_orders):
        created = t0 - timedelta(days=10) + timedelta(seconds=rng.randint(0, int(span.total_seconds())))
        currency = "EUR" if rng.random() < 0.1 else "USD"
        amount = rng.choice(SUBSCRIPTION_PRICES) if rng.random() < 0.35 else rng.randint(500, 40000)
        rate = rate_on(created) if currency == "EUR" else Decimal(1)
        gross = _usd(amount, rate) if currency == "EUR" else amount
        orders.append(Order(f"ord_{10001 + i}", created, currency, amount, ref("pi"), gross, rate, charge_ref=ref("ch")))

    def mid(o: Order, margin_after: int = 8) -> bool:
        return t0 + timedelta(days=5) <= o.created <= t_end - timedelta(days=margin_after)

    def ledger_entry(o: Order, kind: str, amount: int, created: datetime, status: str = "paid", **kw) -> dict:
        e = {
            "id": f"L{len(w.ledger) + 1:06d}",
            "kind": kind,
            "amount": amount,
            "currency": o.currency,
            "created": created,
            "status": status,
            "payment_ref": o.charge_ref if w.flags["ledger_has_charge_ids"] else o.payment_ref,
            "order_ref": o.ref,
        }
        e.update(kw)
        w.ledger.append(e)
        return e

    def payment(
        o: Order, gross: int | None = None, payment_ref: str | None = None, amount_tx: int | None = None, created=None, charge_ref=None
    ) -> Txn:
        g = o.gross_usd if gross is None else gross
        t = Txn(
            ref("txn"),
            "payment",
            created or o.created + timedelta(seconds=rng.randint(2, 40)),
            g,
            _fee(g),
            payment_ref or o.payment_ref,
            o.ref,
            (o.amount if amount_tx is None else amount_tx) if o.currency == "EUR" else None,
            o.rate,
            charge_ref=charge_ref or (o.charge_ref if payment_ref is None else ref("ch")),
        )
        w.txns.append(t)
        return t

    def refund(o: Order, amount: int, when: datetime) -> Txn:
        rate = rate_on(when) if o.currency == "EUR" else Decimal(1)
        gross = -(_usd(amount, rate) if o.currency == "EUR" else amount)
        t = Txn(
            ref("txn"),
            "refund",
            when,
            gross,
            0,
            o.payment_ref,
            o.ref,
            -amount if o.currency == "EUR" else None,
            rate,
            charge_ref=o.charge_ref,
        )
        w.txns.append(t)
        return t

    # every order: a payment at the provider and a paid entry in the ledger; some failed attempts first
    pay: dict[str, Txn] = {}
    entry: dict[str, dict] = {}
    for o in orders:
        if rng.random() < 0.05:
            o.attempt_ref, o.attempt_charge = ref("pi"), ref("ch")
            ledger_entry(
                o,
                "payment",
                o.amount,
                o.created - timedelta(minutes=rng.randint(1, 30)),
                "failed",
                payment_ref=o.attempt_charge if w.flags["ledger_has_charge_ids"] else o.attempt_ref,
            )
            o.attempts = True
        pay[o.ref] = payment(o)
        entry[o.ref] = ledger_entry(o, "payment", o.amount, o.created)

    # refunds booked by the shop; disputes nobody books
    for o in orders:
        r = rng.random()
        if r < 0.06:
            amount = o.amount if rng.random() < 0.6 else max(100, o.amount // 3)
            when = o.created + timedelta(days=rng.uniform(0.5, 6))
            refund(o, amount, when)
            ledger_entry(o, "refund", amount, when + timedelta(seconds=rng.randint(1, 20)))
            o.busy = True
        elif r < 0.075:
            when = o.created + timedelta(days=rng.uniform(5, 12))
            if t0 + timedelta(days=4) <= when <= t_end - timedelta(days=7):
                rate = rate_on(when) if o.currency == "EUR" else Decimal(1)
                gross = -(_usd(o.amount, rate) if o.currency == "EUR" else o.amount)
                w.txns.append(
                    Txn(
                        ref("txn"),
                        "dispute",
                        when,
                        gross,
                        1500,
                        o.payment_ref,
                        o.ref,
                        -o.amount if o.currency == "EUR" else None,
                        rate,
                        charge_ref=o.charge_ref,
                    )
                )
                w.truth.append({"class": "not_in_ledger", "key": o.ref, "kind": "dispute"})
                o.busy = True

    # ---------------------------------------------------------------- injections into the middle of the month
    def free(
        margin_after: int = 8, currency: str | None = None, min_gross: int = 0, attempts: bool = True, need_attempts: bool = False
    ) -> Order | None:
        cands = [
            o
            for o in orders
            if not o.busy
            and mid(o, margin_after)
            and (currency is None or o.currency == currency)
            and o.gross_usd >= min_gross
            and (attempts or not o.attempts)
            and (o.attempts or not need_attempts)
        ]
        if not cands:
            return None
        o = rng.choice(cands)
        o.busy = True
        return o

    def times(lo: int = 0, hi: int = 2) -> int:
        return rng.randint(lo, hi)

    for _ in range(times()):
        o = free()
        if o:
            entry[o.ref]["status"] = "pending"
            w.truth.append({"class": "pending_in_ledger", "key": o.ref})
    for _ in range(times()):
        o = free(attempts=False)  # an order with a failed attempt in the ledger would be pending_in_ledger
        if o:
            w.ledger.remove(entry[o.ref])
            w.truth.append({"class": "not_in_ledger", "key": o.ref, "kind": "payment"})
    for _ in range(times()):
        o = free()
        if o:
            refund(o, o.amount, o.created + timedelta(days=rng.uniform(0.5, 3)))
            w.truth.append({"class": "not_in_ledger", "key": o.ref, "kind": "refund"})
    for _ in range(times()):
        o = free()
        if o:
            w.txns.remove(pay[o.ref])
            w.truth.append({"class": "not_at_provider", "key": o.ref, "kind": "payment"})
    for _ in range(times()):
        o = free()
        if o:
            ledger_entry(o, "refund", o.amount, o.created + timedelta(days=rng.uniform(0.5, 3)))
            w.truth.append({"class": "not_at_provider", "key": o.ref, "kind": "refund"})
    for _ in range(times()):
        o = free()
        if o:
            dup = dict(entry[o.ref])
            dup["id"] = f"L{len(w.ledger) + 1:06d}"
            dup["created"] = o.created + timedelta(seconds=rng.randint(1, 5))
            w.ledger.append(dup)
            w.truth.append({"class": "duplicate_in_ledger", "key": o.ref})
    for _ in range(times()):
        o = free()
        if o:
            payment(o, payment_ref=ref("pi"), created=o.created + timedelta(seconds=rng.randint(45, 120)))
            w.truth.append({"class": "double_charge", "key": o.ref})
    for _ in range(times()):
        o = free(currency="USD")
        if o:
            entry[o.ref]["amount"] = o.amount + rng.choice([500, 1000, -250, 1999])
            w.truth.append({"class": "amount_mismatch", "key": o.ref})
    for _ in range(times()):
        o = free(currency="USD", min_gross=2000)
        if o:
            short = rng.randint(100, o.amount // 4)
            if provider == "stripe":
                # the charge row carries the authorization, a second row gives back what was not captured
                w.txns.append(
                    Txn(
                        ref("txn"), "partial_capture_reversal", pay[o.ref].created, -short, 0, o.payment_ref, o.ref, charge_ref=o.charge_ref
                    )
                )
                pay[o.ref].fee = _fee(o.amount - short)
                w.truth.append({"class": "partial_capture", "key": o.ref})
            else:
                # Adyen settles what was captured; the ledger still has the authorization
                pay[o.ref].gross = o.amount - short
                pay[o.ref].fee = _fee(o.amount - short)
                w.truth.append({"class": "amount_mismatch", "key": o.ref})
    for _ in range(times()):
        o = free(currency="USD")
        if o:
            entry[o.ref]["amount"] = o.amount + rng.choice([1, -1])
            w.truth.append({"class": "rounding", "key": o.ref})
    for _ in range(times()):
        o = free(min_gross=1000)
        if o:
            pay[o.ref].fee += round_minor(Decimal(pay[o.ref].gross) * Decimal("0.015"))
            w.truth.append({"class": "fee_above_schedule", "key": pay[o.ref].txn_id})
    for _ in range(times()):
        o = free(currency="USD")  # a wrong gross column in EUR would also make the EUR comparison with the ledger wrong
        if o:
            t = pay[o.ref]
            if provider == "stripe":
                t.fee_written = t.fee - rng.choice([10, 25, 100])
            else:
                t.gross_gc_bump = rng.choice([5, 25, 100])
            w.truth.append({"class": "row_arithmetic", "key": t.txn_id})
    for _ in range(times(0, 1)):
        # a timeout and a retry: the attempt the ledger marked failed was captured as well
        o = free(need_attempts=True)
        if o:
            payment(o, payment_ref=o.attempt_ref, charge_ref=o.attempt_charge, created=o.created - timedelta(seconds=rng.randint(10, 50)))
            w.truth.append({"class": "double_charge", "key": o.ref})
    for _ in range(times(0, 1)):
        # a payment the provider took back after settling it (a failed bank debit)
        o = free(currency="USD")
        if o:
            w.txns.append(
                Txn(
                    ref("txn"),
                    "payment_reversal",
                    o.created + timedelta(days=rng.uniform(3, 5)),
                    -o.amount,
                    0,
                    o.payment_ref,
                    o.ref,
                    charge_ref=o.charge_ref,
                )
            )
            w.truth.append({"class": "not_in_ledger", "key": o.ref, "kind": "payment_reversal"})
    # not discrepancies, and nothing should be reported for them:
    for _ in range(times(0, 1)):
        # one payment captured in two parts, booked once
        o = free(currency="USD", min_gross=2000)
        if o:
            first = rng.randint(o.amount // 4, o.amount * 3 // 4)
            pay[o.ref].gross, pay[o.ref].fee = first, _fee(first)
            payment(
                o,
                gross=o.amount - first,
                payment_ref=o.payment_ref,
                charge_ref=o.charge_ref,
                created=o.created + timedelta(hours=rng.randint(2, 20)),
            )
    for k in range(times(0, 2)):
        # a free order
        day = t0 + timedelta(days=rng.randint(6, 20), seconds=rng.randint(0, 86399))
        w.ledger.append(
            {
                "id": f"L{len(w.ledger) + 1:06d}",
                "kind": "payment",
                "amount": 0,
                "currency": "USD",
                "created": day,
                "status": "paid",
                "payment_ref": "",
                "order_ref": f"ord_free_{k + 1}",
            }
        )

    # ---------------------------------------------------------------- payouts: daily, two days after the row
    report_tz = ADYEN_TZ if provider == "adyen" else UTC
    for t in w.txns:
        t.payout_day = t.created.astimezone(report_tz).date() + timedelta(days=2)
    by_day: dict[date, list[Txn]] = {}
    for t in sorted(w.txns, key=lambda t: t.created):
        by_day.setdefault(t.payout_day, []).append(t)
    tx_seq = 4313726
    for day in sorted(by_day):
        members = by_day[day]
        pid = f"po_{day:%Y%m%d}{rng.randint(1000, 9999)}" if provider == "stripe" else f"batch {len(w.payouts) + 1}"
        token = f"TX{tx_seq + len(w.payouts) * 7}XT"
        for t in members:
            t.payout_id = pid
        w.payouts[pid] = {"id": pid, "day": day, "amount": sum(t.gross - t.fee for t in members), "token": token, "members": members}

    in_report = {pid for pid, p in w.payouts.items() if start <= p["day"] <= end}
    mid_payouts = [
        pid
        for pid in sorted(in_report)
        if start + timedelta(days=4) <= w.payouts[pid]["day"] <= end - timedelta(days=8) and w.payouts[pid]["amount"] > 30000
    ]

    if provider == "adyen":
        for _ in range(times()):
            if not mid_payouts:
                break
            pid = rng.choice(mid_payouts)
            p = w.payouts[pid]
            fee_row = Txn(
                f"Transaction Fees {p['day']:%B %Y}",
                "fee",
                datetime.combine(p["day"], datetime.min.time(), ADYEN_TZ).astimezone(UTC),
                -5000,
                0,
                "",
                "",
                payout_day=p["day"],
                payout_id=pid,
                hidden=True,
            )
            w.txns.append(fee_row)
            p["members"].append(fee_row)
            p["amount"] += fee_row.gross
            mid_payouts.remove(pid)
            w.truth.append({"class": "payout_sum", "key": pid})

    # ---------------------------------------------------------------- the bank: one deposit per payout
    hint = "STRIPE TRANSFER" if provider == "stripe" else "ADYEN N.V."
    bank_end = end + timedelta(days=3)
    skip, bank_fee = set(), {}
    for _ in range(times()):
        cands = [pid for pid in mid_payouts if pid not in skip and pid not in bank_fee]
        if cands:
            pid = rng.choice(cands)
            skip.add(pid)
            w.truth.append({"class": "payout_not_in_bank", "key": pid})
    for _ in range(times()):
        cands = [pid for pid in mid_payouts if pid not in skip and pid not in bank_fee]
        if cands:
            pid = rng.choice(cands)
            bank_fee[pid] = rng.choice([1500, 2500, 800])
            w.truth.append({"class": "bank_amount_differs", "key": pid})
    lines: list[dict] = []
    amounts = set()
    for pid, p in sorted(w.payouts.items(), key=lambda kv: kv[1]["day"]):
        if not (start <= p["day"] <= bank_end) or pid in skip:
            continue
        late = 1 if (pid in in_report and pid in mid_payouts and rng.random() < 0.2) else 0
        if w.flags["bank_has_payout_refs"]:
            desc = f"{hint} {p['token']}" if provider == "adyen" else f"{hint} {pid}"
        else:
            desc = f"{hint} PAYOUT" if provider == "adyen" else f"{hint} ST-{rng.randint(10**7, 10**8 - 1)}"
        amount = p["amount"] - bank_fee.get(pid, 0)
        lines.append({"date": p["day"] + timedelta(days=late), "amount": amount, "description": desc})
        amounts.add(amount)
    sizes = sorted(p["amount"] for p in w.payouts.values())
    for _ in range(times()):
        day = start + timedelta(days=rng.randint(5, (end - start).days - 8))
        if rng.random() < 0.5:
            amount = sizes[-1] * 2 + rng.randint(1, 9999)  # far from every payout
        else:
            # the size of a payout: another account's payout, the hard case without a reference on the statement
            amount = round_minor(Decimal(sizes[len(sizes) // 2]) * Decimal(rng.uniform(0.85, 1.15)))
        if w.flags["bank_has_payout_refs"]:
            desc = f"{hint} po_{rng.randint(10**11, 10**12 - 1)}" if provider == "stripe" else f"{hint} TX{rng.randint(10**6, 10**7)}XT"
        else:
            desc = f"{hint} PAYOUT" if provider == "adyen" else f"{hint} ST-{rng.randint(10**7, 10**8 - 1)}"
        if amount in amounts:
            continue
        lines.append({"date": day, "amount": amount, "description": desc, "truth": "bank_deposit_unexplained"})
    for _ in range(times(0, 2)):
        # the provider's own invoice, debited: not a deposit
        day = start + timedelta(days=rng.randint(0, (end - start).days))
        lines.append(
            {
                "date": day,
                "amount": -rng.randint(1000, 20000),
                "description": "STRIPE BILLING INVOICE" if provider == "stripe" else "ADYEN N.V. INVOICE",
            }
        )
    for _ in range(rng.randint(15, 30)):
        day = start + timedelta(days=rng.randint(0, (bank_end - start).days))
        amount = rng.randint(1000, 500000) * rng.choice([1, -1, -1])
        if amount in amounts:
            continue
        desc = rng.choice(
            ["CARD PURCHASE CLOUD HOSTING", "SALARY RUN", "WIRE FROM CUSTOMER ACME LTD", "OFFICE RENT", "TAX PAYMENT", "INTEREST"]
        )
        lines.append({"date": day, "amount": amount, "description": desc})
    lines.sort(key=lambda x: (x["date"], x["description"]))
    for i, ln in enumerate(lines):
        ln["line"] = i + 2  # after the header
        if ln.pop("truth", None):
            w.truth.append({"class": "bank_deposit_unexplained", "key": f"bank:{ln['line']}"})
    w.bank = lines

    # ---------------------------------------------------------------- what the exports cover
    w.report = [t for t in w.txns if t.payout_id in in_report and not t.hidden]
    w.report_payouts = [w.payouts[pid] for pid in sorted(in_report, key=lambda pid: w.payouts[pid]["day"])]
    lo, hi = t0 - timedelta(days=5), t_end + timedelta(days=2)
    w.ledger_export = sorted((e for e in w.ledger if lo <= e["created"] < hi), key=lambda e: e["created"])
    # truth only for what the exports show
    report_ids = {t.txn_id for t in w.report}
    w.truth = [x for x in w.truth if x["class"] not in ("fee_above_schedule", "row_arithmetic") or x["key"] in report_ids]
    return w


def _major(minor: int, exp: int = 2) -> str:
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 10**exp)
    return f"{sign}{whole}.{frac:0{exp}d}"


def write(w: World, out: str | Path) -> dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "report": out / f"{w.provider}-report.csv",
        "ledger": out / "ledger.csv",
        "bank": out / "bank.csv",
        "truth": out / "truth.json",
    }
    with paths["report"].open("w", newline="") as f:
        (_write_stripe if w.provider == "stripe" else _write_adyen)(w, csv.writer(f))
    with paths["ledger"].open("w", newline="") as f:
        c = csv.writer(f)
        c.writerow(["id", "kind", "amount", "currency", "created_at", "status", "payment_ref", "order_ref"])
        for e in w.ledger_export:
            c.writerow(
                [
                    e["id"],
                    e["kind"],
                    _major(e["amount"]),
                    e["currency"],
                    e["created"].astimezone(LEDGER_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    e["status"],
                    e["payment_ref"],
                    e["order_ref"],
                ]
            )
    with paths["bank"].open("w", newline="") as f:
        c = csv.writer(f)
        c.writerow(["date", "amount", "currency", "description"])
        for ln in w.bank:
            c.writerow([ln["date"].isoformat(), _major(ln["amount"]), "USD", ln["description"]])
    paths["truth"].write_text(json.dumps(sorted(w.truth, key=lambda x: (x["class"], x["key"])), indent=1) + "\n")
    return paths


def _write_stripe(w: World, c):
    c.writerow(
        [
            "balance_transaction_id",
            "created_utc",
            "available_on",
            "currency",
            "gross",
            "fee",
            "net",
            "reporting_category",
            "description",
            "automatic_payout_id",
            "automatic_payout_effective_at",
            "payment_intent_id",
            "charge_id",
            "customer_facing_amount",
            "customer_facing_currency",
            "payment_metadata[order_id]",
        ]
    )
    category = {
        "payment": "charge",
        "refund": "refund",
        "dispute": "dispute",
        "partial_capture_reversal": "partial_capture_reversal",
        "payment_reversal": "charge_failure",
        "fee": "fee",
    }
    for t in sorted(w.report, key=lambda t: (t.payout_day, t.created)):
        fee = t.fee if t.fee_written is None else t.fee_written
        c.writerow(
            [
                t.txn_id,
                t.created.strftime("%Y-%m-%d %H:%M:%S"),
                t.payout_day.isoformat(),
                "usd",
                _major(t.gross),
                _major(fee),
                _major(t.gross - t.fee),
                category[t.kind],
                "",
                t.payout_id,
                t.payout_day.isoformat() + " 00:00:00",
                t.payment_ref,
                t.charge_ref,
                _major(abs(t.amount_tx)) if t.amount_tx is not None else "",
                "eur" if t.amount_tx is not None else "",
                t.order_ref,
            ]
        )


ADYEN_HEADER = [
    "Company Account",
    "Merchant Account",
    "Psp Reference",
    "Merchant Reference",
    "Payment Method",
    "Creation Date",
    "TimeZone",
    "Type",
    "Modification Reference",
    "Gross Currency",
    "Gross Debit (GC)",
    "Gross Credit (GC)",
    "Exchange Rate",
    "Net Currency",
    "Net Debit (NC)",
    "Net Credit (NC)",
    "Commission (NC)",
    "Markup (NC)",
    "Scheme Fees (NC)",
    "Interchange (NC)",
    "Payment Method Variant",
    "Modification Merchant Reference",
    "Batch Number",
] + [f"Reserved{i}" for i in range(4, 11)]


def _write_adyen(w: World, c):
    c.writerow(ADYEN_HEADER)
    adyen_type = {"payment": "Settled", "refund": "Refunded", "dispute": "Chargeback", "fee": "Fee", "payment_reversal": "SettledReversed"}
    batch_no = {p["id"]: p["id"].split()[-1] for p in w.report_payouts}
    for p in w.report_payouts:
        for t in sorted((t for t in p["members"] if not t.hidden), key=lambda t: t.created):
            when = t.created.astimezone(ADYEN_TZ).strftime("%Y-%m-%d %H:%M:%S")
            net = t.gross - t.fee
            if t.kind == "fee":
                c.writerow(
                    [
                        "Shop",
                        "ShopUSD",
                        "",
                        "",
                        "",
                        when,
                        "CEST",
                        "Fee",
                        t.txn_id,
                        "USD",
                        "",
                        "",
                        "",
                        "USD",
                        _major(-net),
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        batch_no[p["id"]],
                    ]
                    + [""] * 7
                )
                continue
            gc_cur = "EUR" if t.amount_tx is not None else "USD"
            gc = (t.amount_tx if t.amount_tx is not None else t.gross) + (t.gross_gc_bump if t.gross > 0 else -t.gross_gc_bump)
            rate = str(t.rate) if t.amount_tx is not None else "1"
            gross_debit, gross_credit = (_major(-gc), "") if gc < 0 else ("", _major(gc))
            net_debit, net_credit = (_major(-net), "") if net < 0 else ("", _major(net))
            c.writerow(
                [
                    "Shop",
                    "ShopUSD",
                    t.payment_ref,
                    t.order_ref,
                    "visa",
                    when,
                    "CEST",
                    adyen_type[t.kind],
                    t.txn_id,
                    gc_cur,
                    gross_debit,
                    gross_credit,
                    rate,
                    "USD",
                    net_debit,
                    net_credit,
                    _major(t.fee) if t.fee else "",
                    "",
                    "",
                    "",
                    "visaclassic",
                    "",
                    batch_no[p["id"]],
                ]
                + [""] * 7
            )
        c.writerow(
            [
                "Shop",
                "ShopUSD",
                "",
                "",
                "",
                datetime.combine(p["day"], datetime.min.time()).replace(hour=4).strftime("%Y-%m-%d %H:%M:%S"),
                "CEST",
                "MerchantPayout",
                f"{p['token']} batch {batch_no[p['id']]}, ShopUSD batch {batch_no[p['id']]}",
                "USD",
                "",
                "",
                "",
                "USD",
                _major(p["amount"]),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                batch_no[p["id"]],
            ]
            + [""] * 7
        )


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m payout_bridge.synth", description="Write a made-up month with known discrepancies.")
    p.add_argument("--provider", choices=("stripe", "adyen"), default="stripe")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    w = generate(a.provider, a.seed)
    paths = write(w, a.out)
    print(
        f"{len(w.report)} report rows, {len(w.ledger_export)} ledger entries, {len(w.bank)} bank lines, {len(w.truth)} known discrepancies"
    )
    for k, v in paths.items():
        print(f"{k}: {v}")


if __name__ == "__main__":  # pragma: no cover
    main()
