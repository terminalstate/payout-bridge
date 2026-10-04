"""payout-bridge REPORT --provider stripe --ledger ledger.csv [--bank bank.csv] ..."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import time, timedelta
from pathlib import Path

from .csvread import InputError
from .model import SEVERITY
from .money import FeeSchedule
from .reconcile import Options, reconcile
from .render import render_json, render_markdown
from .sources import load_bank, load_ledger, load_provider, parse_mapping
from .timeparse import TimeError, parse_datetime, zone


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="payout-bridge",
        description="Walk from your ledger to the provider's report to the bank, and name every difference. Reads CSV exports; writes nothing but the report.",
    )
    p.add_argument("report", help="the provider's report (CSV)")
    p.add_argument("--provider", required=True, choices=("stripe", "adyen", "generic"), help="format of the report")
    p.add_argument(
        "--ledger", required=True, help="your ledger export (CSV): id, kind, amount, currency, created_at, status, payment_ref, order_ref"
    )
    p.add_argument("--bank", help="bank statement (CSV): date, amount (or credit/debit), currency, description")
    g = p.add_argument_group("columns and formats")
    g.add_argument("--order-key", help="Stripe: the payment_metadata key that holds your order id")
    g.add_argument("--map-ledger", help="ledger column names, ours=yours: 'amount=total,order_ref=order_no'")
    g.add_argument("--map-bank", help="bank column names, ours=yours: 'date=Booking date,description=Details'")
    g.add_argument("--ledger-currency", help="currency of the ledger if it has no currency column")
    g.add_argument("--bank-currency", help="currency of the statement if it has no currency column")
    g.add_argument(
        "--paid-statuses", help="ledger statuses that mean paid (default: paid, succeeded, captured, completed, settled, refunded, …)"
    )
    g.add_argument("--decimal-comma", action="store_true", help="ledger and bank amounts are written 1.234,56")
    g.add_argument("--exponent", action="append", default=[], metavar="CUR=N", help="minor units for a currency, if not ISO 4217's")
    t = p.add_argument_group("time")
    t.add_argument("--report-tz", help="zone of the report's dates without an offset (Stripe: the report's time zone; default UTC)")
    t.add_argument(
        "--ledger-tz",
        help="zone of the ledger's timestamps without an offset, and of its days: an IANA name, or an offset written --ledger-tz=-04:00 (default UTC)",
    )
    t.add_argument("--from", dest="date_from", help="first day the report covers (report zone); with --to, edges are judged too")
    t.add_argument("--to", dest="date_to", help="last day the report covers (report zone)")
    t.add_argument(
        "--edge-days",
        type=int,
        default=2,
        help="unmatched entries this close to the other side's first or last date are not judged (default 2)",
    )
    c = p.add_argument_group("checks")
    c.add_argument("--tolerance", type=int, default=1, help="minor units of difference that count as rounding (default 1)")
    c.add_argument("--fee", help="fee schedule for payments, e.g. '2.9%%+0.30' (fixed part in settlement currency)")
    c.add_argument(
        "--bank-hint", help="regex that marks a bank line as a deposit from the provider (default: 'stripe' or 'adyen'; none for generic)"
    )
    c.add_argument(
        "--bank-days", default="1,5", help="a deposit may be dated BEFORE days before and AFTER days after the payout (default 1,5)"
    )
    o = p.add_argument_group("output")
    o.add_argument("--md", help="write the Markdown report here (default: stdout)")
    o.add_argument("--json", help="write the JSON report here")
    o.add_argument("--examples", type=int, default=10, help="rows shown per finding class (default 10)")
    o.add_argument(
        "--fail-on",
        default="error",
        choices=("error", "warning", "info", "never"),
        help="exit 1 if a finding this severe exists (default error)",
    )
    return p


def run(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        exponents = {}
        for item in args.exponent:
            cur, _, n = item.partition("=")
            if not n.isdigit():
                raise InputError(f"--exponent {item!r} must look like JPY=0")
            exponents[cur.strip().upper()] = int(n)
        exponents = exponents or None
        rows = load_provider(
            args.provider,
            args.report,
            order_key=args.order_key,
            report_tz=args.report_tz,
            exponents=exponents,
            decimal_comma=args.decimal_comma,
        )
        ledger = load_ledger(
            args.ledger,
            tz=args.ledger_tz,
            mapping=parse_mapping(args.map_ledger),
            paid_statuses=args.paid_statuses.split(",") if args.paid_statuses else None,
            currency=args.ledger_currency,
            exponents=exponents,
            decimal_comma=args.decimal_comma,
        )
        bank = (
            load_bank(
                args.bank,
                mapping=parse_mapping(args.map_bank),
                currency=args.bank_currency,
                exponents=exponents,
                decimal_comma=args.decimal_comma,
            )
            if args.bank
            else None
        )
        before, _, after = args.bank_days.partition(",")
        report_zone = zone(args.report_tz) if args.report_tz else None
        w_from = w_to = None
        if args.date_from or args.date_to:
            if not (args.date_from and args.date_to):
                raise InputError("--from and --to go together")
            w_from = parse_datetime(args.date_from, report_zone or zone("UTC"))
            last = parse_datetime(args.date_to, report_zone or zone("UTC"))
            w_to = (
                last + timedelta(days=1) - timedelta(microseconds=1)
                if last.astimezone(report_zone or zone("UTC")).time() == time(0)
                else last
            )
        hint = args.bank_hint if args.bank_hint is not None else (args.provider if args.provider in ("stripe", "adyen") else None)
        if hint:
            try:
                re.compile(hint)
            except re.error as e:
                raise InputError(f"--bank-hint {hint!r} is not a regular expression: {e}") from e
        opts = Options(
            tolerance=args.tolerance,
            edge_days=args.edge_days,
            window_from=w_from,
            window_to=w_to,
            bank_before_days=int(before or 1),
            bank_grace_days=int(after or 5),
            bank_hint=hint or None,
            fee_schedule=FeeSchedule(args.fee) if args.fee else None,
            ledger_tz=zone(args.ledger_tz) if args.ledger_tz else zone("UTC"),
            exponents=exponents,
        )
    except (InputError, TimeError, ValueError, OSError) as e:
        print(f"payout-bridge: {e}", file=sys.stderr)
        return 2
    res = reconcile(rows, ledger, bank, opts)
    md = render_markdown(
        res, examples=args.examples, exponents=exponents, title=f"{Path(args.report).name} against {Path(args.ledger).name}"
    )
    try:
        if args.json:
            Path(args.json).write_text(render_json(res), encoding="utf-8")
        if args.md:
            Path(args.md).write_text(md, encoding="utf-8")
        else:
            sys.stdout.write(md)
    except OSError as e:
        print(f"payout-bridge: {e}", file=sys.stderr)
        return 2
    worst = res.worst()
    if args.fail_on == "never" or worst is None:
        return 0
    return 1 if SEVERITY[worst] >= SEVERITY[args.fail_on] else 0


def main():  # pragma: no cover
    sys.exit(run())
