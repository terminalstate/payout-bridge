"""Readers for provider reports, ledger exports and bank statements.

Provider formats follow the providers' public documentation:
- Stripe: the itemized payout reconciliation report (payout_reconciliation.itemized.*) and the other
  itemized balance reports with the same columns. Amounts are in major units; net = gross − fee.
- Adyen: the Settlement details report. Debit and credit columns instead of signs; fees split into
  commission or markup + scheme fees + interchange; one MerchantPayout row per batch.
- generic: one row per balance movement, columns named below, for any other provider.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .csvread import InputError, pick, read_rows
from .model import DEFAULT_PAID_STATUSES, KIND_SIGN, KINDS, LEDGER_KIND_ALIASES, BankLine, LedgerEntry, Payout, ProviderRow
from .money import AmountError, exponent, parse_decimal, parse_minor, round_minor
from .timeparse import UTC, TimeError, parse_date, parse_datetime, zone


class _Ctx:
    """Collects errors with line numbers instead of stopping at the first one."""

    def __init__(self, path):
        self.path = str(path)
        self.errors: list[str] = []

    def fail(self, line: int, msg: str):
        self.errors.append(f"{Path(self.path).name}:{line}: {msg}")

    def raise_if_any(self):
        if self.errors:
            head = "\n".join(self.errors[:20])
            more = f"\n… and {len(self.errors) - 20} more" if len(self.errors) > 20 else ""
            raise InputError(head + more)


def _require(headers, path, **needed):
    missing = [name for name, col in needed.items() if col is None]
    if missing:
        raise InputError(f"{path}: missing column(s): {', '.join(missing)}; found: {', '.join(h for h in headers if h)}")


# ---------------------------------------------------------------- Stripe

_STRIPE_KIND = {
    "charge": "payment",
    "refund": "refund",
    "refund_failure": "refund_failure",
    "partial_capture_reversal": "partial_capture_reversal",
    "charge_failure": "payment_reversal",
    "dispute": "dispute",
    "dispute_reversal": "dispute_reversal",
    "fee": "fee",
    "tax": "fee",
    "payout": "payout",
    "payout_reversal": "payout_reversal",
    "other_adjustment": "adjustment",
    "risk_reserved_funds": "reserve",
    "connect_reserved_funds": "reserve",
    "transfer": "transfer",
    "transfer_reversal": "transfer",
}


def load_stripe(path, *, order_key: str | None = None, report_tz: str | None = None, exponents=None) -> list[ProviderRow]:
    headers, rows = read_rows(path)
    c_id = pick(headers, "balance_transaction_id", "id")
    c_cat = pick(headers, "reporting_category")
    c_gross, c_fee, c_net = pick(headers, "gross"), pick(headers, "fee"), pick(headers, "net")
    c_cur = pick(headers, "currency")
    c_created_utc, c_created = pick(headers, "created_utc"), pick(headers, "created")
    _require(
        headers,
        path,
        balance_transaction_id=c_id,
        reporting_category=c_cat,
        gross=c_gross,
        fee=c_fee,
        net=c_net,
        currency=c_cur,
        created=c_created_utc or c_created,
    )
    c_pi, c_charge, c_source = pick(headers, "payment_intent_id"), pick(headers, "charge_id"), pick(headers, "source_id")
    meta_cols = [h for h in headers if h.startswith("payment_metadata[")]
    if order_key:
        c_order = pick(headers, f"payment_metadata[{order_key}]", order_key)
        if c_order is None:
            raise InputError(f"{path}: no column payment_metadata[{order_key}]; metadata columns found: {', '.join(meta_cols) or 'none'}")
    else:
        c_order = meta_cols[0] if len(meta_cols) == 1 else pick(headers, "order_id")
        if c_order is None and len(meta_cols) > 1:
            raise InputError(
                f"{path}: several metadata columns ({', '.join(meta_cols)}): say which one holds your order id with --order-key"
            )
    if not (c_pi or c_charge or c_order):
        raise InputError(
            f"{path}: nothing links these rows to your orders. Stripe's default columns have no such link: "
            "download the report again with payment_intent_id (or charge_id) and payment_metadata[<your order key>] added"
        )
    c_payout, c_payout_at = pick(headers, "automatic_payout_id"), pick(headers, "automatic_payout_effective_at")
    c_payout_at_utc = pick(headers, "automatic_payout_effective_at_utc")
    c_cfa, c_cfc = pick(headers, "customer_facing_amount"), pick(headers, "customer_facing_currency")
    token_cols = [c for c in (pick(headers, "trace_id"), pick(headers, "payout_reference_token")) if c]
    tz = zone(report_tz) if report_tz else UTC
    ctx = _Ctx(path)
    out: list[ProviderRow] = []
    for line, r in rows:
        try:
            cur = r[c_cur].upper()
            if not cur:
                raise AmountError("empty currency")
            gross = parse_minor(r[c_gross], cur, overrides=exponents) or 0
            fee = parse_minor(r[c_fee], cur, overrides=exponents) or 0
            net = parse_minor(r[c_net], cur, overrides=exponents)
            if net is None:
                net = gross - fee
            created = (
                parse_datetime(r[c_created_utc], UTC) if c_created_utc and r.get(c_created_utc) else parse_datetime(r.get(c_created), tz)
            )
            payout_date = None
            if c_payout_at_utc and r.get(c_payout_at_utc):
                payout_date = parse_date(r[c_payout_at_utc], UTC)
            elif c_payout_at and r.get(c_payout_at):
                payout_date = parse_date(r[c_payout_at], tz)
            amount_tx = currency_tx = None
            if c_cfa and c_cfc and r.get(c_cfc) and r[c_cfc].upper() != cur:
                currency_tx = r[c_cfc].upper()
                raw = parse_minor(r[c_cfa], currency_tx, overrides=exponents)
                if raw is not None:
                    amount_tx = abs(raw) * (1 if gross >= 0 else -1)
        except (AmountError, TimeError, KeyError) as e:
            ctx.fail(line, str(e))
            continue
        category = r[c_cat].lower()
        kind = _STRIPE_KIND.get(category, "other")
        ids = [r.get(c, "") for c in (c_pi, c_charge) if c]
        if c_source:
            ids.append(r.get(c_source, ""))  # the row's own object: the charge, the refund (re_…), the dispute (dp_…)
        ids = list(dict.fromkeys(i for i in ids if i))
        payment_ref = ids[0] if ids else ""
        tokens = tuple(t for t in [r.get(c_payout, "") if c_payout else ""] + [r.get(c, "") for c in token_cols] if t)
        out.append(
            ProviderRow(
                line=line,
                txn_id=r[c_id],
                kind=kind,
                gross=gross,
                fee=fee,
                net=net,
                currency=cur,
                created=created,
                payment_ref=payment_ref or None,
                order_ref=(r.get(c_order) or None) if c_order else None,
                payout_id=(r.get(c_payout) or None) if c_payout else None,
                payout_date=payout_date,
                amount_tx=amount_tx,
                currency_tx=currency_tx,
                raw_type=category,
                payout_tokens=tokens,
                alt_refs=tuple(ids[1:]),
                report_tz=tz,
            )
        )
    ctx.raise_if_any()
    return out


# ---------------------------------------------------------------- Adyen

_ADYEN_KIND = {
    "settled": "payment",
    "settledinstallment": "payment",
    "settledbulk": "payment",
    "refunded": "refund",
    "refundedinstallment": "refund",
    "refundedbulk": "refund",
    "chargeback": "dispute",
    "secondchargeback": "dispute",
    "chargebackreversedcorrection": "dispute",
    "chargebackreversed": "dispute_reversal",
    "refundedreversed": "refund_failure",
    "refundfailed": "refund_failure",
    "fee": "fee",
    "invoicededuction": "fee",
    "misccosts": "fee",
    "paymentcost": "fee",
    "merchantpayout": "payout",
    "bankinstructionreturned": "payout_reversal",
    "depositcorrection": "reserve",
    "reserveadjustment": "reserve",
    "balancetransfer": "adjustment",
    "manualcorrected": "adjustment",
    "matchedstatement": "adjustment",
    "settledreversed": "payment_reversal",
    "capturefailed": "payment_reversal",
    "merchantpayin": "adjustment",
    "merchantpayinreversed": "adjustment",
    "paidout": "transfer",
    "paidoutreversed": "transfer",
}
_TX_TOKEN = re.compile(r"\bTX\w+XT\b")


def load_adyen(path, *, report_tz: str | None = None, exponents=None) -> list[ProviderRow]:
    headers, rows = read_rows(path)
    col = {
        k: pick(headers, k)
        for k in (
            "Merchant Account",
            "Psp Reference",
            "Merchant Reference",
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
            "Batch Number",
        )
    }
    _require(
        headers,
        path,
        **{
            k: v
            for k, v in col.items()
            if k
            in (
                "Psp Reference",
                "Merchant Reference",
                "Creation Date",
                "Type",
                "Net Currency",
                "Net Debit (NC)",
                "Net Credit (NC)",
                "Batch Number",
            )
        },
    )
    fixed_tz = zone(report_tz) if report_tz else None
    accounts = {r.get(col["Merchant Account"], "") for _, r in rows} if col["Merchant Account"] else {""}
    ctx = _Ctx(path)
    out: list[ProviderRow] = []

    def get(r, key):
        c = col[key]
        return r.get(c, "") if c else ""

    for line, r in rows:
        try:
            tz = fixed_tz or zone(get(r, "TimeZone") or "UTC")
            ncur = get(r, "Net Currency").upper()
            gcur = (get(r, "Gross Currency") or ncur).upper()
            net = (parse_minor(get(r, "Net Credit (NC)"), ncur, overrides=exponents) or 0) - (
                parse_minor(get(r, "Net Debit (NC)"), ncur, overrides=exponents) or 0
            )
            commission = parse_minor(get(r, "Commission (NC)"), ncur, overrides=exponents)
            parts = [parse_minor(get(r, k), ncur, overrides=exponents) for k in ("Markup (NC)", "Scheme Fees (NC)", "Interchange (NC)")]
            fee = commission if commission is not None else sum(p or 0 for p in parts)
            gross_gc = (parse_minor(get(r, "Gross Credit (GC)"), gcur, overrides=exponents) or 0) - (
                parse_minor(get(r, "Gross Debit (GC)"), gcur, overrides=exponents) or 0
            )
            rate = parse_decimal(get(r, "Exchange Rate")) or Decimal(1)
            created = parse_datetime(get(r, "Creation Date"), tz)
        except (AmountError, TimeError) as e:
            ctx.fail(line, str(e))
            continue
        raw_type = get(r, "Type")
        kind = _ADYEN_KIND.get(raw_type.replace(" ", "").lower(), "other")
        account = get(r, "Merchant Account")
        batch = get(r, "Batch Number")
        payout_id = (f"{account} batch {batch}" if len(accounts) > 1 else f"batch {batch}") if batch else None
        mod_ref = get(r, "Modification Reference")
        if kind in ("payment", "refund", "refund_failure", "dispute", "dispute_reversal"):
            gross = net + fee
        else:
            gross, fee = net, 0
        row = ProviderRow(
            line=line,
            txn_id=mod_ref or f"{get(r, 'Psp Reference')}:{raw_type}:{line}",
            kind=kind,
            gross=gross,
            fee=fee,
            net=net,
            currency=ncur,
            created=created,
            payment_ref=get(r, "Psp Reference") or None,
            order_ref=get(r, "Merchant Reference") or None,
            payout_id=payout_id,
            raw_type=raw_type,
            report_tz=tz,
        )
        if kind in ("payment", "refund", "refund_failure", "dispute", "dispute_reversal") and gross_gc:
            if gcur != ncur:
                row.amount_tx, row.currency_tx = gross_gc, gcur
            # what the gross columns say the gross is, in settlement currency: checked against net + fee
            scaled = Decimal(gross_gc).scaleb(-_exp(gcur, exponents)) * rate
            row.gross_reported = round_minor(scaled.scaleb(_exp(ncur, exponents)))
            row.fx = gcur != ncur or rate != 1
        if kind == "payout":
            row.payout_tokens = tuple(_TX_TOKEN.findall(mod_ref)) + ((payout_id,) if payout_id else ())
            row.payout_date = created.astimezone(tz).date() if created else None
        out.append(row)
    ctx.raise_if_any()
    # every row of a batch is paid out on the batch's payout date
    # a batch without a MerchantPayout row has not been paid out (yet, or because its balance is negative)
    dates = {r.payout_id: r.payout_date for r in out if r.kind == "payout"}
    for r in out:
        if r.kind == "payout":
            continue
        if r.payout_id in dates:
            r.payout_date = dates[r.payout_id]
        else:
            r.payout_id = None
    return out


def _exp(currency, exponents):
    return exponent(currency, exponents)


# ---------------------------------------------------------------- generic provider CSV

GENERIC_PROVIDER_COLUMNS = (
    "txn_id",
    "kind",
    "gross",
    "fee",
    "net",
    "currency",
    "created_at",
    "payment_ref",
    "order_ref",
    "payout_id",
    "payout_date",
    "amount_tx",
    "currency_tx",
)


def load_generic(path, *, report_tz: str | None = None, exponents=None, decimal_comma=False) -> list[ProviderRow]:
    headers, rows = read_rows(path)
    c = {k: pick(headers, k) for k in GENERIC_PROVIDER_COLUMNS}
    c["txn_id"] = c["txn_id"] or pick(headers, "id")
    c["created_at"] = c["created_at"] or pick(headers, "created")
    c["gross"] = c["gross"] or pick(headers, "amount")
    _require(headers, path, txn_id=c["txn_id"], kind=c["kind"], gross=c["gross"], currency=c["currency"])
    tz = zone(report_tz) if report_tz else UTC
    ctx = _Ctx(path)
    out = []
    for line, r in rows:
        try:
            cur = r[c["currency"]].upper()
            kind = r[c["kind"]].strip().lower()
            if kind not in KINDS:
                raise AmountError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
            gross = parse_minor(r[c["gross"]], cur, decimal_comma=decimal_comma, overrides=exponents) or 0
            fee = (parse_minor(r[c["fee"]], cur, decimal_comma=decimal_comma, overrides=exponents) if c["fee"] else None) or 0
            net = parse_minor(r[c["net"]], cur, decimal_comma=decimal_comma, overrides=exponents) if c["net"] else None
            created = parse_datetime(r.get(c["created_at"]), tz) if c["created_at"] else None
            payout_date = parse_date(r.get(c["payout_date"]), tz) if c["payout_date"] else None
            amount_tx = currency_tx = None
            if c["currency_tx"] and r.get(c["currency_tx"]) and r[c["currency_tx"]].upper() != cur and c["amount_tx"]:
                currency_tx = r[c["currency_tx"]].upper()
                amount_tx = parse_minor(r[c["amount_tx"]], currency_tx, decimal_comma=decimal_comma, overrides=exponents)
        except (AmountError, TimeError) as e:
            ctx.fail(line, str(e))
            continue
        payout_id = (r.get(c["payout_id"]) or None) if c["payout_id"] else None
        row = ProviderRow(
            line=line,
            txn_id=r[c["txn_id"]],
            kind=kind,
            gross=gross,
            fee=fee,
            net=gross - fee if net is None else net,
            currency=cur,
            created=created,
            payment_ref=(r.get(c["payment_ref"]) or None) if c["payment_ref"] else None,
            order_ref=(r.get(c["order_ref"]) or None) if c["order_ref"] else None,
            payout_id=payout_id,
            payout_date=payout_date,
            amount_tx=amount_tx,
            currency_tx=currency_tx,
            raw_type=kind,
            report_tz=tz,
        )
        if kind == "payout":
            row.payout_tokens = tuple(t for t in (payout_id, row.txn_id) if t)
        out.append(row)
    ctx.raise_if_any()
    return out


def load_provider(fmt: str, path, **kw) -> list[ProviderRow]:
    if fmt == "stripe":
        kw.pop("decimal_comma", None)
        return load_stripe(path, **kw)
    kw.pop("order_key", None)
    if fmt == "adyen":
        kw.pop("decimal_comma", None)
        return load_adyen(path, **kw)
    if fmt == "generic":
        return load_generic(path, **kw)
    raise InputError(f"unknown provider format {fmt!r}: stripe, adyen or generic")


def payouts_from(rows: list[ProviderRow]) -> dict[str, Payout]:
    """Payouts by id. A payout row in the report declares its own amount; otherwise the amount is the sum of the rows.
    A payout id with rows in two currencies is two payouts ('<id> EUR', '<id> USD'): a bank account gets one currency."""
    groups: dict[tuple[str, str], list[ProviderRow]] = defaultdict(list)
    declared: dict[tuple[str, str], ProviderRow] = {}
    for r in rows:
        if not r.payout_id:
            continue
        if r.kind == "payout":
            declared[(r.payout_id, r.currency)] = r
        else:
            groups[(r.payout_id, r.currency)].append(r)
    keys = sorted(set(groups) | set(declared))
    currencies_of: dict[str, set] = defaultdict(set)
    for pid, cur in keys:
        currencies_of[pid].add(cur)
    out = {}
    for pid, cur in keys:
        members = groups.get((pid, cur), [])
        d = declared.get((pid, cur))
        name = pid if len(currencies_of[pid]) == 1 else f"{pid} {cur}"
        day = (d.payout_date if d else None) or next((m.payout_date for m in members if m.payout_date), None)
        tokens = tuple(dict.fromkeys(t for m in ([d] if d else []) + members for t in m.payout_tokens))
        amount = -d.net if d else sum(m.net for m in members)
        out[name] = Payout(
            payout_id=name,
            day=day,
            amount=amount,
            currency=cur,
            rows=members,
            tokens=tokens or (pid,),
            declared=d is not None,
            line=d.line if d else None,
        )
    return out


# ---------------------------------------------------------------- ledger

LEDGER_SYNONYMS = {
    "id": ("id", "entry_id", "transaction_id", "payment_id"),
    "kind": ("kind", "type", "entry_type", "transaction_type"),
    "amount": ("amount", "total", "gross", "value"),
    "currency": ("currency", "currency_code"),
    "created_at": ("created_at", "created", "date", "timestamp", "paid_at", "booked_at"),
    "status": ("status", "state", "payment_status"),
    "payment_ref": (
        "payment_ref",
        "provider_ref",
        "psp_reference",
        "payment_intent_id",
        "payment_intent",
        "charge_id",
        "provider_payment_id",
    ),
    "order_ref": ("order_ref", "order_id", "order", "merchant_reference", "reference", "order_number"),
}


def _columns(headers, synonyms, mapping, path):
    cols = {}
    for key, names in synonyms.items():
        if mapping and key in mapping:
            c = pick(headers, mapping[key])
            if c is None:
                raise InputError(f"{path}: mapped column {mapping[key]!r} for {key} not found")
            cols[key] = c
        else:
            cols[key] = pick(headers, *names)
    return cols


def load_ledger(
    path,
    *,
    tz: str | None = None,
    mapping: dict | None = None,
    paid_statuses=None,
    currency: str | None = None,
    exponents=None,
    decimal_comma=False,
) -> list[LedgerEntry]:
    headers, rows = read_rows(path)
    c = _columns(headers, LEDGER_SYNONYMS, mapping, path)
    _require(headers, path, id=c["id"], amount=c["amount"])
    if not (c["currency"] or currency):
        raise InputError(f"{path}: no currency column; pass --ledger-currency")
    if not (c["payment_ref"] or c["order_ref"]):
        raise InputError(f"{path}: needs a payment reference or an order reference column to match on (see --map-ledger)")
    # an empty status counts as paid: refunds and disputes are often booked without one
    paid = (frozenset(s.strip().lower() for s in paid_statuses) | {""}) if paid_statuses else DEFAULT_PAID_STATUSES
    zone_ = zone(tz) if tz else UTC
    ctx = _Ctx(path)
    out = []
    for line, r in rows:
        try:
            cur = (r.get(c["currency"]) if c["currency"] else "") or currency or ""
            cur = cur.upper()
            if not cur:
                raise AmountError("empty currency (pass --ledger-currency for a default)")
            amount = parse_minor(r[c["amount"]], cur, decimal_comma=decimal_comma, overrides=exponents)
            if amount is None:
                raise AmountError("empty amount")
            raw_kind = (r.get(c["kind"]) if c["kind"] else "").strip().lower()
            if raw_kind:
                kind = LEDGER_KIND_ALIASES.get(raw_kind)
                if kind is None:
                    raise AmountError(f"unknown kind {raw_kind!r}; map it to payment, refund, dispute, dispute_reversal or refund_failure")
                if kind == "payment" and amount < 0:
                    kind = "refund"
            else:
                kind = "payment" if amount >= 0 else "refund"
            signed = abs(amount) * KIND_SIGN[kind]
            created = parse_datetime(r.get(c["created_at"]), zone_) if c["created_at"] else None
        except (AmountError, TimeError) as e:
            ctx.fail(line, str(e))
            continue
        status = (r.get(c["status"]) if c["status"] else "").strip()
        out.append(
            LedgerEntry(
                line=line,
                entry_id=r[c["id"]],
                kind=kind,
                amount=signed,
                currency=cur,
                created=created,
                status=status,
                payment_ref=(r.get(c["payment_ref"]) or None) if c["payment_ref"] else None,
                order_ref=(r.get(c["order_ref"]) or None) if c["order_ref"] else None,
                paid=status.lower() in paid,
            )
        )
    ctx.raise_if_any()
    return out


# ---------------------------------------------------------------- bank

BANK_SYNONYMS = {
    "date": ("date", "booking_date", "booking date", "value_date", "value date", "transaction_date", "transaction date", "posted"),
    "amount": ("amount", "value"),
    "credit": ("credit", "money in", "paid in", "deposit"),
    "debit": ("debit", "money out", "paid out", "withdrawal"),
    "currency": ("currency", "ccy"),
    "description": ("description", "details", "narrative", "reference", "memo", "payee", "counterparty", "text"),
}


def load_bank(path, *, mapping: dict | None = None, currency: str | None = None, exponents=None, decimal_comma=False) -> list[BankLine]:
    headers, rows = read_rows(path)
    c = _columns(headers, BANK_SYNONYMS, mapping, path)
    _require(headers, path, date=c["date"])
    if not (c["amount"] or c["credit"]):
        raise InputError(f"{path}: needs an amount column, or credit and debit columns")
    if not (c["currency"] or currency):
        raise InputError(f"{path}: no currency column; pass --bank-currency")
    if mapping and "description" in mapping:
        desc_cols = [c["description"]]
    else:
        desc_cols = [h for h in headers if h in set(BANK_SYNONYMS["description"])] or ([c["description"]] if c["description"] else [])
    ctx = _Ctx(path)
    out = []
    for line, r in rows:
        try:
            cur = ((r.get(c["currency"]) if c["currency"] else "") or currency or "").upper()
            if not cur:
                raise AmountError("empty currency (pass --bank-currency for a default)")
            if c["amount"]:
                amount = parse_minor(r[c["amount"]], cur, decimal_comma=decimal_comma, overrides=exponents)
            else:
                cr = parse_minor(r.get(c["credit"], ""), cur, decimal_comma=decimal_comma, overrides=exponents) or 0
                dr = parse_minor(r.get(c["debit"], ""), cur, decimal_comma=decimal_comma, overrides=exponents) if c["debit"] else 0
                amount = abs(cr) - abs(dr or 0)
            if amount is None:
                raise AmountError("empty amount")
            day = parse_date(r[c["date"]])
            if day is None:
                raise TimeError("empty date")
        except (AmountError, TimeError) as e:
            ctx.fail(line, str(e))
            continue
        description = " | ".join(r.get(d, "") for d in desc_cols if r.get(d))
        out.append(BankLine(line=line, day=day, amount=amount, currency=cur, description=description))
    ctx.raise_if_any()
    return out


def parse_mapping(text: str | None) -> dict:
    """'amount=total_amount,order_ref=order_no' → {'amount': 'total_amount', 'order_ref': 'order_no'}."""
    if not text:
        return {}
    out = {}
    for part in text.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise InputError(f"mapping {part!r} must look like ours=yours")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


__all__ = ["load_stripe", "load_adyen", "load_generic", "load_provider", "payouts_from", "load_ledger", "load_bank", "parse_mapping"]
