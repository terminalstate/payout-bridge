import csv
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from payout_bridge.model import BankLine, LedgerEntry, ProviderRow

UTC = timezone.utc
_line = iter(range(2, 10**9))


def at(day: int, hour: int = 12, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def prow(
    kind="payment",
    gross=1000,
    fee=None,
    *,
    ref=None,
    order=None,
    day=10,
    hour=12,
    payout=None,
    payout_day=None,
    currency="USD",
    txn=None,
    net=None,
    amount_tx=None,
    currency_tx=None,
    report_tz=UTC,
):
    fee = (30 if kind == "payment" else 0) if fee is None else fee
    line = next(_line)
    return ProviderRow(
        line=line,
        txn_id=txn or f"txn_{line}",
        kind=kind,
        gross=gross,
        fee=fee,
        net=gross - fee if net is None else net,
        currency=currency,
        created=at(day, hour),
        payment_ref=ref,
        order_ref=order,
        payout_id=payout,
        payout_date=date(2026, 9, payout_day) if payout_day else None,
        amount_tx=amount_tx,
        currency_tx=currency_tx,
        report_tz=report_tz,
    )


def entry(kind="payment", amount=1000, *, ref=None, order=None, day=10, hour=12, status="paid", currency="USD", eid=None, paid=None):
    line = next(_line)
    return LedgerEntry(
        line=line,
        entry_id=eid or f"L{line}",
        kind=kind,
        amount=amount,
        currency=currency,
        created=at(day, hour),
        status=status,
        payment_ref=ref,
        order_ref=order,
        paid=(status in ("paid", "")) if paid is None else paid,
    )


def bline(day, amount, description="", currency="USD"):
    return BankLine(line=next(_line), day=date(2026, 9, day), amount=amount, currency=currency, description=description)


class TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def csv(self, name, header, rows):
        path = self.dir / name
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return path


def check_walk(lines):
    """Every total equals the previous total plus the lines between them. Returns the first broken label or None."""
    running = None
    last_total = max(i for i, ln in enumerate(lines) if ln.total)
    for ln in lines[: last_total + 1]:
        if ln.total:
            if running is not None and running != ln.amount:
                return f"{ln.label}: expected {running}, got {ln.amount}"
            running = ln.amount
        else:
            running += ln.amount
    return None
