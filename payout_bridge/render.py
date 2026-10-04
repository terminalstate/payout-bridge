"""The result as a Markdown report for people and JSON for whatever runs the check on a schedule."""

from __future__ import annotations

import json

from .model import CLASSES, SEVERITY
from .money import fmt
from .reconcile import Result


def _amount(minor, currency, exponents=None, sign=False):
    return fmt(minor, currency, exponents, sign=sign) if currency else str(minor)


def render_markdown(res: Result, *, examples: int = 10, exponents=None, title: str = "Reconciliation") -> str:
    out: list[str] = [f"# {title}", ""]
    i = res.inputs
    out.append(
        f"{i['provider_rows']} provider rows, {i['ledger_entries']} ledger entries, {i['bank_lines']} bank lines, {i['payouts']} payouts."
    )
    pw, lw = i["provider_window"], i["ledger_window"]
    if pw[0]:
        out.append(
            f"Report covers {pw[0][:16]} – {pw[1][:16]} UTC; ledger export {lw[0][:16] if lw[0] else '?'} – {lw[1][:16] if lw[1] else '?'} UTC."
        )
    out.append("")
    by_class = res.by_class()
    counts = {c: len(v) for c, v in by_class.items()}
    problems = [c for c in CLASSES if c in counts and CLASSES[c][0] != "info"]
    if problems:
        out.append("**Findings:** " + ", ".join(f"{counts[c]} {c}" for c in problems) + ".")
    else:
        out.append("**No findings** beyond the informational ones.")
    out.append("")
    for note in res.notes:
        out += [f"> {note}", ""]

    for cur, lines in res.walks.items():
        out += [f"## From your ledger to the bank, {cur}", "", "| | amount | rows | class |", "|---|---:|---:|---|"]
        for ln in lines:
            label = f"**{ln.label}**" if ln.total else f"&nbsp;&nbsp;{ln.label}"
            amount = _amount(ln.amount, cur, exponents, sign=not ln.total)
            amount = f"**{amount}**" if ln.total else amount
            out.append(f"| {label} | {amount} | {'' if ln.count is None else ln.count} | {ln.cls or ''} |")
        out.append("")
    for cur, lines in res.ledger_only.items():
        out += [f"## In your ledger only, {cur}", "", "| | amount | entries | class |", "|---|---:|---:|---|"]
        for ln in lines:
            out.append(f"| {ln.label} | {_amount(ln.amount, cur, exponents, sign=True)} | {ln.count} | {ln.cls} |")
        out.append("")

    out += ["## Findings", ""]
    for cls in sorted(by_class, key=lambda c: (-SEVERITY[CLASSES[c][0]], list(CLASSES).index(c))):
        fs = by_class[cls]
        sev, desc = CLASSES[cls]
        currencies = sorted({f.currency for f in fs if f.currency})
        totals = ", ".join(_amount(sum(f.amount for f in fs if f.currency == c), c, exponents, sign=True) + f" {c}" for c in currencies)
        out.append(f"### {cls} — {desc}")
        out.append("")
        if sev == "info":
            out.append(f"{len(fs)} ({sev}).")
        else:
            out.append(f"{len(fs)} ({sev}); total {totals or '—'}.")
        out.append("")
        if sev == "info" and examples < 50:
            shown = fs[: min(examples, 3)]
        else:
            shown = fs[:examples]
        out += ["| order | payment | ledger | provider | payout / bank | effect | note |", "|---|---|---|---|---|---:|---|"]
        for f in shown:
            ledger = "<br>".join(
                f"{e.entry_id} (line {e.line}) {_amount(e.amount, e.currency, exponents)} {e.currency} {e.status}".strip() for e in f.ledger
            )
            provider = "<br>".join(
                f"{r.txn_id} (line {r.line}) {r.kind} {_amount(r.gross, r.currency, exponents)} {r.currency}" for r in f.provider
            )
            pb = ""
            if f.payout:
                pb = f"{f.payout.payout_id} {f.payout.day} {_amount(f.payout.amount, f.payout.currency, exponents)}"
            if f.bank:
                pb += (
                    "<br>" if pb else ""
                ) + f"bank line {f.bank.line} {f.bank.day} {_amount(f.bank.amount, f.bank.currency, exponents)} “{f.bank.description}”"
            effect = _amount(f.amount, f.currency, exponents, sign=True) if f.currency and f.cls != "day_shift" else ""
            out.append(f"| {f.order_ref or ''} | {f.payment_ref or ''} | {ledger} | {provider} | {pb} | {effect} | {f.message} |")
        if len(fs) > len(shown):
            out.append(f"| … {len(fs) - len(shown)} more | | | | | | |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_json(res: Result) -> str:
    def walk(lines):
        return [{"label": ln.label, "amount_minor": ln.amount, "count": ln.count, "class": ln.cls, "total": ln.total} for ln in lines]

    by_class = res.by_class()
    doc = {
        "inputs": res.inputs,
        "worst": res.worst(),
        "counts": {c: len(v) for c, v in by_class.items()},
        "notes": res.notes,
        "walks": {cur: walk(lines) for cur, lines in res.walks.items()},
        "ledger_only": {cur: walk(lines) for cur, lines in res.ledger_only.items()},
        "findings": [f.to_dict() for f in res.findings],
        "bank_matches": {pid: {"line": b.line, "date": str(b.day), "amount_minor": b.amount} for pid, b in res.bank_matches.items()},
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)
