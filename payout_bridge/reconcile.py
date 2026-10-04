"""Match the three sides and name every difference.

Ledger ↔ provider: by the provider's payment reference, then by the merchant's order reference. Never by
amount and date: with recurring prices, the same amount on the same day is the normal case, and a match
by amount pairs a payment with somebody else's.

Provider ↔ bank: by a reference the bank statement carries (payout id, Adyen's TX…XT), then by exact
amount within a few days of the payout date, then (for deposits that name the provider) by the nearest
amount.

Every provider row ends up in exactly one place, so the walk from the ledger to the bank adds up to the
cent by construction; what does not match is a finding, not a remainder.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal

from .model import CLASSES, PROVIDER_ONLY_KINDS, SEVERITY, BankLine, Finding, LedgerEntry, Payout, ProviderRow
from .money import FeeSchedule, fmt, round_minor
from .sources import payouts_from
from .timeparse import UTC

LEDGER_TO_PROVIDER_KIND = {
    "payment": ("payment",),
    "refund": ("refund", "partial_capture_reversal"),
    "refund_failure": ("refund_failure",),
    "dispute": ("dispute",),
    "dispute_reversal": ("dispute_reversal",),
}


@dataclass
class Options:
    tolerance: int = 1  # minor units: a difference this small is rounding
    edge_days: int = 2  # unmatched entries this close to the edge of the other side's dates are not judged
    window_from: datetime | None = None  # the report's dates, when known; otherwise taken from its rows
    window_to: datetime | None = None
    bank_before_days: int = 1  # a deposit may be dated this many days before the payout date
    bank_grace_days: int = 5  # … or this many after it
    bank_hint: str | None = None  # text in a bank line's description that marks a deposit from the provider
    near_ratio: Decimal = Decimal("0.1")  # a provider deposit within 10% of a payout is that payout, with a difference
    fee_schedule: FeeSchedule | None = None
    ledger_tz: tzinfo = UTC  # the zone the ledger's days are counted in
    exponents: dict | None = None


@dataclass
class Unit:
    """One provider transaction as the ledger would see it: a payment with its partial-capture reversal attached."""

    kind: str
    rows: list[ProviderRow]
    payment_ref: str | None
    order_ref: str | None
    created: datetime | None
    currency: str
    amount: int  # settlement currency, after the attached reversal
    full: int  # settlement currency, before it
    currency_tx: str | None = None
    amount_tx: int | None = None
    full_tx: int | None = None
    taken: bool = False
    refs: tuple[str, ...] = ()  # every id the report gives this payment

    @property
    def main(self) -> ProviderRow:
        return self.rows[0]

    def amounts_in(self, currency: str) -> tuple[int, int] | None:
        if currency == self.currency:
            return self.amount, self.full
        if currency == self.currency_tx and self.amount_tx is not None:
            return self.amount_tx, self.full_tx if self.full_tx is not None else self.amount_tx
        return None

    @property
    def has_reversal(self) -> bool:
        return any(r.kind == "partial_capture_reversal" for r in self.rows)


@dataclass
class Pair:
    entry: LedgerEntry
    unit: Unit
    ledger_settle: int  # what the ledger says, in the unit's settlement currency (0 if the entry is not paid)
    cls: str | None = None  # None if the amounts agree
    comparable: bool = True  # the report has an amount in the ledger entry's currency


@dataclass
class WalkLine:
    label: str
    amount: int
    count: int | None = None
    cls: str | None = None
    total: bool = False


@dataclass
class Result:
    findings: list[Finding]
    pairs: list[Pair]
    walks: dict[str, list[WalkLine]]
    ledger_only: dict[str, list[WalkLine]]
    payouts: dict[str, Payout]
    bank_matches: dict[str, BankLine]
    inputs: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # what the reader needs to know to read the walk

    def by_class(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            out[f.cls].append(f)
        return dict(out)

    def worst(self) -> str | None:
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=SEVERITY.get)


def _refs(r: ProviderRow) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x for x in (r.payment_ref, *r.alt_refs) if x))


def _unit(r: ProviderRow) -> Unit:
    return Unit(
        r.kind,
        [r],
        r.payment_ref,
        r.order_ref,
        r.created,
        r.currency,
        r.gross,
        r.gross,
        r.currency_tx,
        r.amount_tx,
        r.amount_tx,
        refs=_refs(r),
    )


def _units(rows: list[ProviderRow], ledger: list[LedgerEntry]) -> list[Unit]:
    """Provider transactions as the ledger sees them.

    Several captures of one payment (rows with the same payment id: Adyen's partial captures, Stripe's
    multicapture) are one unit when the ledger books that payment once, or when one of its entries has the
    total; a ledger that books every capture gets them one by one. A partial-capture reversal is attached
    to its payment."""
    entries = [e for e in ledger if e.kind == "payment"]
    groups: dict[tuple, list[ProviderRow]] = defaultdict(list)
    order: list[tuple] = []
    for r in rows:
        if r.kind != "payment":
            continue
        key = (r.payment_ref, r.currency, r.currency_tx) if r.payment_ref else (id(r),)
        if key not in groups:
            order.append(key)
        groups[key].append(r)
    units: list[Unit] = []
    payments: dict[str, Unit] = {}
    for key in order:
        members = groups[key]
        merged = len(members) > 1 and _booked_once(members, entries)
        for part in [members] if merged else [[m] for m in members]:
            u = _unit(part[0])
            for r in part[1:]:
                u.rows.append(r)
                u.amount += r.gross
                u.full += r.gross
                if u.amount_tx is not None and r.amount_tx is not None:
                    u.amount_tx += r.amount_tx
                    u.full_tx += r.amount_tx
                u.refs = tuple(dict.fromkeys(u.refs + _refs(r)))
            units.append(u)
            if u.payment_ref:
                payments.setdefault(u.payment_ref, u)
    for r in rows:
        if r.kind == "payment" or r.kind in PROVIDER_ONLY_KINDS:
            continue
        host = payments.get(r.payment_ref) if r.kind == "partial_capture_reversal" and r.payment_ref else None
        if host is not None and host.currency == r.currency:
            host.rows.append(r)
            host.amount += r.gross
            if host.amount_tx is not None and r.amount_tx is not None:
                host.amount_tx += r.amount_tx
            continue
        units.append(_unit(r))
    return units


def _booked_once(captures: list[ProviderRow], entries: list[LedgerEntry]) -> bool:
    """Does the ledger book these captures of one payment as one entry?"""
    refs = {x for r in captures for x in _refs(r)}
    orders = {r.order_ref for r in captures if r.order_ref}
    booked = [e for e in entries if (e.payment_ref in refs) or (not e.payment_ref and e.order_ref in orders)]
    if len(booked) <= 1:
        return True
    total = sum(r.gross for r in captures)
    total_tx = sum(r.amount_tx for r in captures) if all(r.amount_tx is not None for r in captures) else None
    cur, cur_tx = captures[0].currency, captures[0].currency_tx
    return any((e.currency == cur and e.amount == total) or (e.currency == cur_tx and e.amount == total_tx) for e in booked)


def _distance(a: datetime | None, b: datetime | None) -> float:
    if a is None or b is None:
        return float("inf")
    return abs((a - b).total_seconds())


class _Index:
    def __init__(self, units: list[Unit]):
        self.by_ref: dict[tuple[str, str], list[Unit]] = defaultdict(list)
        self.by_order: dict[tuple[str, str], list[Unit]] = defaultdict(list)
        for u in units:
            for ref in u.refs:
                self.by_ref[(u.kind, ref)].append(u)
            if u.order_ref:
                self.by_order[(u.kind, u.order_ref)].append(u)

    def candidates(self, e: LedgerEntry, by: str) -> list[Unit]:
        """by='ref': units with the entry's payment reference.

        by='order': units with the entry's order reference, for a paid entry whose payment reference the report
        does not know (or that has none). A taken unit is no reason to look further: the entry is then a duplicate,
        not a match for some other payment of the same order. An unpaid entry (a failed attempt, a pending order)
        is matched by its own reference only; a capture for its order is found from the other side."""
        kinds = LEDGER_TO_PROVIDER_KIND.get(e.kind, (e.kind,))
        if by == "ref":
            if not e.payment_ref:
                return []
            return [u for k in kinds for u in self.by_ref.get((k, e.payment_ref), ()) if not u.taken]
        if not e.paid or not e.order_ref:
            return []
        if e.payment_ref and any(self.by_ref.get((k, e.payment_ref)) for k in kinds):
            return []
        return [u for k in kinds for u in self.by_order.get((k, e.order_ref), ()) if not u.taken]


def _pick(e: LedgerEntry, cands: list[Unit], exact: bool) -> Unit | None:
    best, best_key = None, None
    for u in cands:
        amounts = u.amounts_in(e.currency)
        if exact:
            if amounts is None or amounts[0] != e.amount:
                continue
            key = (0, _distance(e.created, u.created))
        else:
            gap = min(abs(a - e.amount) for a in amounts) if amounts else float("inf")
            key = (gap, _distance(e.created, u.created))
        if best_key is None or key < best_key:
            best, best_key = u, key
    return best


def _in(moment: datetime | None, lo: datetime | None, hi: datetime | None, edge: timedelta) -> bool:
    if moment is None or lo is None or hi is None:
        return True
    return lo + edge <= moment <= hi - edge


def reconcile(
    rows: list[ProviderRow], ledger: list[LedgerEntry], bank: list[BankLine] | None = None, opts: Options | None = None
) -> Result:
    opts = opts or Options()
    ex = opts.exponents
    findings: list[Finding] = []
    units = _units(rows, ledger)
    index = _Index(units)

    # ---------------------------------------------------------------- ledger ↔ provider
    pairs: list[Pair] = []
    paid = sorted((e for e in ledger if e.paid), key=lambda e: (e.created or datetime.min.replace(tzinfo=UTC), e.line))
    unpaid = sorted((e for e in ledger if not e.paid), key=lambda e: (e.created or datetime.min.replace(tzinfo=UTC), e.line))
    matched_entries: set[int] = set()

    def take(e: LedgerEntry, u: Unit):
        u.taken = True
        matched_entries.add(id(e))
        pairs.append(Pair(e, u, 0))

    for group, passes in (
        # a payment id named by an entry is taken by that entry before any entry falls back to its order
        (paid, ((True, "ref"), (False, "ref"), (True, "order"), (False, "order"))),
        (unpaid, ((True, "ref"), (False, "ref"))),
    ):
        for exact, by in passes:
            for e in group:
                if id(e) in matched_entries:
                    continue
                u = _pick(e, index.candidates(e, by), exact)
                if u is not None:
                    take(e, u)

    # order → the payment ids of its matched, paid payments. A second capture of the same payment id is not a
    # second charge; a capture under another payment id is.
    paid_orders_matched: dict[tuple[str, str], set] = defaultdict(set)
    for p in pairs:
        if p.entry.paid and p.unit.kind == "payment":
            for o in (p.entry.order_ref, p.unit.order_ref):
                if o:
                    paid_orders_matched[("payment", o)].update(p.unit.refs)

    def charged_twice(u: Unit, order: str | None) -> bool:
        refs = paid_orders_matched.get(("payment", order)) if order else None
        return u.kind == "payment" and refs is not None and not (refs & set(u.refs))

    for p in pairs:
        e, u = p.entry, p.unit
        if not e.paid and charged_twice(u, e.order_ref or u.order_ref):
            # a timeout, a retry, and both went through: the attempt the ledger gave up on was captured too
            p.cls = "double_charge"
            findings.append(
                Finding(
                    "double_charge",
                    f"captured although the ledger has this attempt as {e.status or 'empty'}; another payment of the order is matched",
                    u.currency,
                    u.amount,
                    u.kind,
                    [e],
                    list(u.rows),
                )
            )
            continue
        if not e.paid:
            p.cls = "pending_in_ledger"
            findings.append(
                Finding(
                    "pending_in_ledger",
                    f"{u.kind} captured at the provider; ledger status is {e.status or 'empty'}",
                    u.currency,
                    u.amount,
                    u.kind,
                    [e],
                    list(u.rows),
                )
            )
            continue
        amounts = u.amounts_in(e.currency)
        if amounts is None:
            p.cls, p.comparable = "amount_mismatch", False
            findings.append(
                Finding(
                    "amount_mismatch",
                    f"ledger in {e.currency}, provider in {u.currency}" + (f"/{u.currency_tx}" if u.currency_tx else ""),
                    u.currency,
                    u.amount,
                    u.kind,
                    [e],
                    list(u.rows),
                    detail={"ledger_currency": e.currency},
                )
            )
            continue
        amount, full = amounts
        diff = amount - e.amount
        settle_diff = diff if e.currency == u.currency else (round_minor(Decimal(diff) * u.amount / amount) if amount else diff)
        p.ledger_settle = u.amount - settle_diff
        if diff == 0:
            pass
        elif u.has_reversal and full == e.amount:
            p.cls = "partial_capture"
        elif abs(diff) <= opts.tolerance:
            p.cls = "rounding"
        else:
            p.cls = "amount_mismatch"
        if p.cls:
            findings.append(
                Finding(
                    p.cls,
                    f"ledger {fmt(e.amount, e.currency, ex)}, provider {fmt(amount, e.currency, ex)} {e.currency}",
                    u.currency,
                    settle_diff,
                    u.kind,
                    [e],
                    list(u.rows),
                    detail={"ledger_minor": e.amount, "provider_minor": amount, "diff_minor": diff, "currency": e.currency},
                )
            )
        if e.created and u.created:
            ld = e.created.astimezone(opts.ledger_tz).date()
            pd = u.created.astimezone(u.main.report_tz or UTC).date()
            if ld != pd:
                findings.append(
                    Finding(
                        "day_shift",
                        f"ledger day {ld}, provider day {pd}",
                        u.currency,
                        u.amount,
                        u.kind,
                        [e],
                        list(u.rows),
                        detail={"ledger_day": str(ld), "provider_day": str(pd)},
                    )
                )

    # windows: an unmatched entry is only judged where the other side's export covers its date
    times = [u.created for u in units if u.created]
    p_lo = opts.window_from or (min(times) if times else None)
    p_hi = opts.window_to or (max(times) if times else None)
    p_edge = timedelta(0) if (opts.window_from and opts.window_to) else timedelta(days=opts.edge_days)
    ltimes = [e.created for e in ledger if e.created]
    l_lo, l_hi = (min(ltimes), max(ltimes)) if ltimes else (None, None)
    l_edge = timedelta(days=opts.edge_days)

    unpaid_by_order = defaultdict(list)
    for e in unpaid:
        if e.order_ref and id(e) not in matched_entries:
            unpaid_by_order[(e.kind, e.order_ref)].append(e)
    paid_orders = {(e.kind, e.order_ref) for e in paid if e.order_ref}
    for u in units:
        if u.taken:
            continue
        waiting = unpaid_by_order.get((u.kind, u.order_ref)) if u.order_ref and (u.kind, u.order_ref) not in paid_orders else None
        if not _in(u.created, l_lo, l_hi, l_edge):
            findings.append(
                Finding(
                    "outside_window",
                    "provider row outside the ledger export's dates",
                    u.currency,
                    u.amount,
                    u.kind,
                    [],
                    list(u.rows),
                    detail={"side": "provider"},
                )
            )
        elif charged_twice(u, u.order_ref):
            findings.append(
                Finding(
                    "double_charge",
                    f"order {u.order_ref} has another captured payment that the ledger matches",
                    u.currency,
                    u.amount,
                    u.kind,
                    [],
                    list(u.rows),
                )
            )
        elif waiting:
            statuses = ", ".join(sorted({e.status or "empty" for e in waiting}))
            findings.append(
                Finding(
                    "pending_in_ledger",
                    f"{u.kind} captured at the provider; the ledger has this order as {statuses}",
                    u.currency,
                    u.amount,
                    u.kind,
                    list(waiting),
                    list(u.rows),
                )
            )
        else:
            findings.append(
                Finding("not_in_ledger", f"{u.kind} at the provider with no ledger entry", u.currency, u.amount, u.kind, [], list(u.rows))
            )

    by_ref, by_order = defaultdict(list), defaultdict(list)
    for p in pairs:
        if p.entry.paid:
            if p.entry.payment_ref:
                by_ref[(p.entry.kind, p.entry.payment_ref)].append(p.entry)
            if p.entry.order_ref:
                by_order[(p.entry.kind, p.entry.order_ref)].append(p.entry)
    for e in paid:
        if id(e) in matched_entries or e.amount == 0:  # a free order has nothing to collect
            continue
        # a twin has the same payment id (whatever its amount: two entries cannot both be that payment); an entry
        # without a payment id is compared by order, and then only an entry of the same amount is its twin
        if e.payment_ref:
            twins = by_ref.get((e.kind, e.payment_ref), [])
        else:
            twins = [t for t in by_order.get((e.kind, e.order_ref), []) if t.amount == e.amount] if e.order_ref else []
        if twins:
            findings.append(
                Finding(
                    "duplicate_in_ledger",
                    f"same {e.kind} as ledger entry {twins[0].entry_id}"
                    + ("" if twins[0].amount == e.amount else ", with a different amount"),
                    e.currency,
                    -e.amount,
                    e.kind,
                    [e, twins[0]],
                )
            )
        elif not _in(e.created, p_lo, p_hi, p_edge):
            findings.append(
                Finding(
                    "outside_window",
                    "ledger entry outside the report's dates",
                    e.currency,
                    -e.amount,
                    e.kind,
                    [e],
                    detail={"side": "ledger"},
                )
            )
        else:
            findings.append(Finding("not_at_provider", f"{e.kind} in the ledger with no provider row", e.currency, -e.amount, e.kind, [e]))

    # ---------------------------------------------------------------- the report itself
    for r in rows:
        if r.kind == "payout":
            continue
        if r.net != r.gross - r.fee:
            findings.append(
                Finding(
                    "row_arithmetic",
                    f"net {fmt(r.net, r.currency, ex)} ≠ gross {fmt(r.gross, r.currency, ex)} − fee {fmt(r.fee, r.currency, ex)}",
                    r.currency,
                    r.net - (r.gross - r.fee),
                    r.kind,
                    [],
                    [r],
                )
            )
        elif r.gross_reported is not None and abs(r.gross_reported - r.gross) > (opts.tolerance if r.fx else 0):
            findings.append(
                Finding(
                    "row_arithmetic",
                    f"gross columns give {fmt(r.gross_reported, r.currency, ex)}, net + fee give {fmt(r.gross, r.currency, ex)}",
                    r.currency,
                    0,
                    r.kind,
                    [],
                    [r],
                    detail={"gross_reported": r.gross_reported, "gross": r.gross},
                )
            )
        if opts.fee_schedule and r.kind == "payment" and r.gross > 0:
            expected = opts.fee_schedule.expected(r.gross, r.currency, opts.exponents)
            if r.fee - expected > opts.tolerance:
                findings.append(
                    Finding(
                        "fee_above_schedule",
                        f"fee {fmt(r.fee, r.currency, ex)}, schedule {opts.fee_schedule.text} gives {fmt(expected, r.currency, ex)}",
                        r.currency,
                        -(r.fee - expected),
                        r.kind,
                        [],
                        [r],
                        detail={"fee": r.fee, "expected": expected},
                    )
                )

    payouts = payouts_from(rows)
    payout_sum_diff: dict[str, int] = defaultdict(int)
    for po in payouts.values():
        if po.declared:
            total = sum(m.net for m in po.rows)
            if total != po.amount:
                payout_sum_diff[po.currency] += po.amount - total
                findings.append(
                    Finding(
                        "payout_sum",
                        f"payout {fmt(po.amount, po.currency, ex)}, its rows add up to {fmt(total, po.currency, ex)}",
                        po.currency,
                        po.amount - total,
                        "payout",
                        payout=po,
                        detail={"rows_total": total},
                    )
                )

    # ---------------------------------------------------------------- provider ↔ bank
    bank = bank or []
    bank_matches: dict[str, BankLine] = {}
    if bank:
        bank_matches, bank_findings = _match_bank(payouts, bank, opts)
        findings.extend(bank_findings)

    walks, ledger_only = _walks(rows, units, pairs, findings, payouts, bank_matches, payout_sum_diff, bool(bank))
    findings.sort(key=lambda f: (-SEVERITY[f.severity], list(CLASSES).index(f.cls)))
    inputs = {
        "provider_rows": len(rows),
        "ledger_entries": len(ledger),
        "bank_lines": len(bank),
        "provider_window": [str(p_lo) if p_lo else None, str(p_hi) if p_hi else None],
        "ledger_window": [str(l_lo) if l_lo else None, str(l_hi) if l_hi else None],
        "payouts": len(payouts),
    }
    return Result(findings, pairs, walks, ledger_only, payouts, bank_matches, inputs, _notes(rows, ledger, findings, opts))


def _notes(rows, ledger, findings, opts) -> list[str]:
    notes = []
    outside = [f for f in findings if f.cls == "outside_window" and f.detail.get("side") == "ledger"]
    if outside:
        by_cur: dict[str, int] = defaultdict(int)
        for f in outside:
            by_cur[f.currency] -= f.amount
        sums = ", ".join(f"{fmt(v, c, opts.exponents)} {c}" for c, v in sorted(by_cur.items()))
        notes.append(f"{len(outside)} paid ledger entries ({sums}) are outside the report's dates and not in the walk (outside_window).")
    known = {r.currency for r in rows} | {r.currency_tx for r in rows if r.currency_tx}
    foreign = sorted({e.currency for e in ledger if e.paid and e.currency not in known})
    if foreign and rows:
        notes.append(
            f"The ledger has entries in {', '.join(foreign)}, and the report has no amounts in that currency, so they cannot be compared: "
            "for Stripe, download the report with customer_facing_amount and customer_facing_currency."
        )
    return notes


def _token_in(token: str, text: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", text, re.IGNORECASE))


def _match_bank(payouts: dict[str, Payout], bank: list[BankLine], opts: Options):
    findings: list[Finding] = []
    used: set[int] = set()
    matches: dict[str, BankLine] = {}
    hint = re.compile(opts.bank_hint, re.IGNORECASE) if opts.bank_hint else None
    b_lo, b_hi = min(b.day for b in bank), max(b.day for b in bank)
    ordered = sorted(payouts.values(), key=lambda p: (p.day or date.min, p.payout_id))

    def window(p: Payout, b: BankLine) -> bool:
        return p.day is not None and p.day - timedelta(days=opts.bank_before_days) <= b.day <= p.day + timedelta(days=opts.bank_grace_days)

    def record(p: Payout, b: BankLine):
        used.add(b.line)
        matches[p.payout_id] = b
        if b.amount != p.amount:
            findings.append(
                Finding(
                    "bank_amount_differs",
                    f"payout {fmt(p.amount, p.currency, opts.exponents)}, bank {fmt(b.amount, b.currency, opts.exponents)}",
                    p.currency,
                    b.amount - p.amount,
                    "payout",
                    payout=p,
                    bank=b,
                    detail={"payout_minor": p.amount, "bank_minor": b.amount},
                )
            )

    def strong(p: Payout) -> list[str]:
        return [t for t in p.tokens if len(t) >= 8 and not re.search(r"\s", t)]

    # 1. a reference the statement carries: on a line of the payout's sign, the exact amount first, then the nearest
    for p in ordered:
        tokens = strong(p)
        if not tokens:
            continue
        cands = [
            b
            for b in bank
            if b.line not in used
            and b.currency == p.currency
            and (b.amount > 0) == (p.amount > 0)
            and any(_token_in(t, b.description) for t in tokens)
        ]
        if cands:
            record(p, min(cands, key=lambda b: (b.amount != p.amount, abs(b.amount - p.amount), b.line)))
    refs_on_statement = bool(matches)
    # 2. exact amount near the payout date
    for p in ordered:
        if p.payout_id in matches:
            continue
        cands = [b for b in bank if b.line not in used and b.currency == p.currency and b.amount == p.amount and window(p, b)]
        if cands:
            cands.sort(key=lambda b: (0 if hint and hint.search(b.description) else 1, abs((b.day - p.day).days), b.line))
            record(p, cands[0])
    # 3. a deposit that names the provider and is a little smaller than a payout: something was deducted on the
    #    way (a bank's incoming-wire fee, an intermediary's charge). Nearest date first, then the smallest
    #    deduction, over all payouts at once. A deposit larger than the payout is not explained this way. And
    #    when the statement shows payout references (some payout was matched by one), a deposit without this
    #    payout's reference is not this payout.
    if hint:
        near = []
        for p in ordered:
            if p.payout_id in matches or p.amount == 0 or (refs_on_statement and strong(p)):
                continue
            limit = abs(Decimal(p.amount)) * opts.near_ratio
            for b in bank:
                if (
                    b.line not in used
                    and b.currency == p.currency
                    and window(p, b)
                    and hint.search(b.description)
                    and (b.amount > 0) == (p.amount > 0)
                    and 0 < p.amount - b.amount <= limit
                ):
                    near.append((abs((b.day - p.day).days), p.amount - b.amount, b.line, p.payout_id, p, b))
        for *_, p, b in sorted(near, key=lambda x: x[:4]):
            if p.payout_id not in matches and b.line not in used:
                record(p, b)
    on_statement = {b.currency for b in bank}
    for p in ordered:
        if p.payout_id in matches:
            continue
        if p.currency not in on_statement:
            findings.append(
                Finding(
                    "outside_window",
                    f"the statement has no {p.currency} lines",
                    p.currency,
                    -p.amount,
                    "payout",
                    payout=p,
                    detail={"side": "bank"},
                )
            )
        elif (
            p.day is not None
            and p.day - timedelta(days=opts.bank_before_days) >= b_lo
            and p.day + timedelta(days=opts.bank_grace_days) <= b_hi
        ):
            findings.append(
                Finding("payout_not_in_bank", f"payout of {p.day} not on the statement", p.currency, -p.amount, "payout", payout=p)
            )
        else:
            findings.append(
                Finding(
                    "outside_window",
                    "payout outside the statement's dates",
                    p.currency,
                    -p.amount,
                    "payout",
                    payout=p,
                    detail={"side": "bank"},
                )
            )
    if hint:
        days = [p.day for p in payouts.values() if p.day]
        # judged between the first and the last payout date: a deposit after the last one is most likely
        # the next report's payout, one before the first the previous report's
        lo = min(days) - timedelta(days=opts.bank_before_days) if days else None
        hi = max(days) if days else None
        for b in bank:
            if b.line in used or b.amount <= 0 or not hint.search(b.description):
                continue
            if lo is not None and lo <= b.day <= hi:
                findings.append(
                    Finding(
                        "bank_deposit_unexplained",
                        "names the provider; no payout in the report explains it",
                        b.currency,
                        b.amount,
                        None,
                        bank=b,
                    )
                )
            else:
                findings.append(
                    Finding(
                        "outside_window",
                        "provider deposit outside the report's payout dates",
                        b.currency,
                        b.amount,
                        None,
                        bank=b,
                        detail={"side": "bank_line"},
                    )
                )
    return matches, findings


def _walks(rows, units, pairs, findings, payouts, bank_matches, payout_sum_diff, has_bank):
    walks: dict[str, list[WalkLine]] = {}
    ledger_only: dict[str, list[WalkLine]] = {}
    currencies = sorted({r.currency for r in rows})

    def sum_cls(cls, cur, *, kind=None, side=None):
        fs = [
            f
            for f in findings
            if f.cls == cls and f.currency == cur and (kind is None or f.kind == kind) and (side is None or f.detail.get("side") == side)
        ]
        return sum(f.amount for f in fs), len(fs)

    for cur in currencies:
        lines: list[WalkLine] = []
        cur_pairs = [p for p in pairs if p.unit.currency == cur and p.entry.paid]
        matched = sum(p.ledger_settle for p in cur_pairs)
        nap, nap_n = sum_cls("not_at_provider", cur)
        dup, dup_n = sum_cls("duplicate_in_ledger", cur)
        fx = sum(1 for p in cur_pairs if p.entry.currency != cur and p.comparable)
        label = "Your ledger, paid entries in the report's dates" + (f" ({fx} in other currencies, at the provider's rate)" if fx else "")
        lines.append(WalkLine(label, matched - nap - dup, total=True))
        for kind in ("payment", "refund", "refund_failure", "dispute", "dispute_reversal", "payment_reversal"):
            a, n = sum_cls("not_at_provider", cur, kind=kind)
            if n:
                lines.append(WalkLine(f"{kind.replace('_', ' ')}s in your ledger, not at the provider", a, n, "not_at_provider"))
        if dup_n:
            lines.append(WalkLine("booked twice in your ledger", dup, dup_n, "duplicate_in_ledger"))
        lines.append(WalkLine("Your ledger, matched to the provider", matched, len(cur_pairs), total=True))
        for cls in ("rounding", "partial_capture", "amount_mismatch", "pending_in_ledger", "double_charge"):
            a, n = sum_cls(cls, cur)
            if n:
                lines.append(WalkLine(CLASSES[cls][1], a, n, cls))
        for kind in ("payment", "refund", "partial_capture_reversal", "refund_failure", "dispute", "dispute_reversal", "payment_reversal"):
            a, n = sum_cls("not_in_ledger", cur, kind=kind)
            if n:
                lines.append(WalkLine(f"{kind.replace('_', ' ')}s at the provider, not in your ledger", a, n, "not_in_ledger"))
        a, n = sum_cls("outside_window", cur, side="provider")
        if n:
            lines.append(WalkLine("provider rows outside your ledger export's dates", a, n, "outside_window"))
        gross_txn = sum(u.amount for u in units if u.currency == cur)
        lines.append(
            WalkLine("Provider: payments, refunds, disputes (gross)", gross_txn, sum(1 for u in units if u.currency == cur), total=True)
        )
        cur_rows = [r for r in rows if r.currency == cur and r.kind != "payout"]
        fees = -sum(r.fee for r in cur_rows)
        lines.append(WalkLine("provider fees on those rows", fees, sum(1 for r in cur_rows if r.fee)))
        for kind in PROVIDER_ONLY_KINDS:
            if kind == "payout":
                continue
            ks = [r for r in cur_rows if r.kind == kind]
            if ks:
                lines.append(WalkLine(f"{kind.replace('_', ' ')} rows", sum(r.gross for r in ks), len(ks)))
        arith = sum(r.net - (r.gross - r.fee) for r in cur_rows)
        if arith:
            lines.append(WalkLine("rows where net ≠ gross − fee", arith, None, "row_arithmetic"))
        net = sum(r.net for r in cur_rows)
        lines.append(WalkLine("Provider: net", net, total=True))
        in_payouts = {id(m) for po in payouts.values() for m in po.rows}
        unpaid_rows = [r for r in cur_rows if id(r) not in in_payouts]
        if unpaid_rows:
            lines.append(WalkLine("not in a payout yet", -sum(r.net for r in unpaid_rows), len(unpaid_rows)))
        if payout_sum_diff.get(cur):
            lines.append(WalkLine("payouts that differ from the sum of their rows", payout_sum_diff[cur], None, "payout_sum"))
        cur_payouts = [p for p in payouts.values() if p.currency == cur]
        total_payouts = sum(p.amount for p in cur_payouts)
        lines.append(WalkLine("Payouts", total_payouts, len(cur_payouts), total=True))
        if has_bank:
            a, n = sum_cls("payout_not_in_bank", cur)
            if n:
                lines.append(WalkLine("payouts not on the bank statement", a, n, "payout_not_in_bank"))
            a, n = sum_cls("outside_window", cur, side="bank")
            if n:
                lines.append(WalkLine("payouts outside the statement's dates", a, n, "outside_window"))
            a, n = sum_cls("bank_amount_differs", cur)
            if n:
                lines.append(WalkLine("the bank received a different amount", a, n, "bank_amount_differs"))
            got = [b for pid, b in bank_matches.items() if payouts[pid].currency == cur]
            lines.append(WalkLine("Bank: deposits matched to payouts", sum(b.amount for b in got), len(got), total=True))
            a, n = sum_cls("bank_deposit_unexplained", cur)
            if n:
                lines.append(WalkLine("provider deposits no payout explains (not in the total)", a, n, "bank_deposit_unexplained"))
        walks[cur] = lines

    # ledger entries in currencies the report does not settle in
    other = sorted({f.currency for f in findings if f.cls in ("not_at_provider", "duplicate_in_ledger") and f.currency not in currencies})
    for cur in other:
        out = []
        for cls in ("not_at_provider", "duplicate_in_ledger"):
            a, n = sum_cls(cls, cur)
            if n:
                out.append(WalkLine(CLASSES[cls][1], a, n, cls))
        ledger_only[cur] = out
    return walks, ledger_only
