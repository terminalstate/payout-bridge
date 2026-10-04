"""The three sides of a reconciliation, normalized, and what can be wrong between them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# What a provider row is. Ledgers book the first group; the rest only exist at the provider.
TXN_KINDS = ("payment", "refund", "refund_failure", "dispute", "dispute_reversal", "partial_capture_reversal", "payment_reversal")
PROVIDER_ONLY_KINDS = ("fee", "adjustment", "reserve", "transfer", "payout", "payout_reversal", "other")
KINDS = TXN_KINDS + PROVIDER_ONLY_KINDS

# The sign a ledger amount gets from its kind: money towards the merchant is positive.
KIND_SIGN = {
    "payment": 1,
    "refund": -1,
    "refund_failure": 1,
    "dispute": -1,
    "dispute_reversal": 1,
    "partial_capture_reversal": -1,
    "payment_reversal": -1,  # a settled payment taken back: a failed bank debit, funds never received
}

LEDGER_KIND_ALIASES = {
    "payment": "payment",
    "charge": "payment",
    "sale": "payment",
    "capture": "payment",
    "captured": "payment",
    "order": "payment",
    "settled": "payment",
    "refund": "refund",
    "refunded": "refund",
    "return": "refund",
    "refund_failure": "refund_failure",
    "refund_failed": "refund_failure",
    "refund_reversal": "refund_failure",
    "dispute": "dispute",
    "chargeback": "dispute",
    "dispute_reversal": "dispute_reversal",
    "chargeback_reversal": "dispute_reversal",
    "dispute_won": "dispute_reversal",
    "payment_reversal": "payment_reversal",
    "payment_failure": "payment_reversal",
    "charge_failure": "payment_reversal",
    "reversal": "payment_reversal",
}

DEFAULT_PAID_STATUSES = frozenset(
    {
        "paid",
        "succeeded",
        "success",
        "successful",
        "captured",
        "completed",
        "complete",
        "settled",
        "authorised_and_captured",
        "refunded",
        "partially_refunded",
        "partially-refunded",
        "processing",
        "fulfilled",
        "done",
        "ok",
        "booked",
        "posted",
        "",
    }
)


@dataclass
class ProviderRow:
    line: int  # line in the source file, for evidence
    txn_id: str
    kind: str
    gross: int  # settlement currency, minor units, + = towards the merchant
    fee: int  # settlement currency, + = charged to the merchant
    net: int  # settlement currency, what the row did to the balance
    currency: str  # settlement currency
    created: datetime | None
    payment_ref: str | None = None  # the provider's id of the payment (payment intent, charge, PSP reference)
    order_ref: str | None = None  # the merchant's own reference (order id), if the export carries it
    payout_id: str | None = None
    payout_date: date | None = None
    amount_tx: int | None = None  # in the currency the customer paid, if different
    currency_tx: str | None = None
    raw_type: str = ""
    payout_tokens: tuple[str, ...] = ()  # strings that can appear on a bank statement for this payout
    alt_refs: tuple[str, ...] = ()  # other ids of the same payment (Stripe: the charge id next to the payment intent)
    report_tz: object = None  # tzinfo the report's dates are written in
    gross_reported: int | None = None  # gross per the report's gross columns, in settlement currency (Adyen)
    fx: bool = False  # converted between currencies: a rounding unit of difference is allowed

    def amount_in(self, currency: str) -> int | None:
        if currency.upper() == self.currency:
            return self.gross
        if self.currency_tx and currency.upper() == self.currency_tx and self.amount_tx is not None:
            return self.amount_tx
        return None


@dataclass
class LedgerEntry:
    line: int
    entry_id: str
    kind: str
    amount: int  # signed by kind, ledger currency
    currency: str
    created: datetime | None
    status: str = ""
    payment_ref: str | None = None
    order_ref: str | None = None
    paid: bool = True


@dataclass
class BankLine:
    line: int
    day: date
    amount: int  # + = money in
    currency: str
    description: str = ""


@dataclass
class Payout:
    payout_id: str
    day: date | None
    amount: int  # + = sent to the bank
    currency: str
    rows: list[ProviderRow] = field(default_factory=list)
    tokens: tuple[str, ...] = ()
    declared: bool = False  # the report has a payout row with its own amount (not just a sum of rows)
    line: int | None = None


SEVERITY = {"error": 3, "warning": 2, "info": 1}

# class → (severity, one line for the report)
CLASSES = {
    "pending_in_ledger": ("error", "captured at the provider, not paid in your ledger"),
    "not_in_ledger": ("error", "at the provider, not in your ledger"),
    "double_charge": ("error", "a second capture for an order your ledger has once"),
    "not_at_provider": ("error", "in your ledger, not at the provider"),
    "duplicate_in_ledger": ("error", "booked twice in your ledger, once at the provider"),
    "amount_mismatch": ("error", "same payment, different amount"),
    "partial_capture": ("error", "your ledger booked the authorization, the provider captured less"),
    "rounding": ("warning", "same payment, amounts differ by a rounding unit"),
    "row_arithmetic": ("error", "the report's own row does not add up (net ≠ gross − fee)"),
    "payout_sum": ("error", "a payout is not the sum of its rows"),
    "fee_above_schedule": ("warning", "fee above the schedule you gave"),
    "payout_not_in_bank": ("error", "a payout the bank statement does not show"),
    "bank_amount_differs": ("warning", "the bank received a different amount than the payout"),
    "bank_deposit_unexplained": ("warning", "a deposit from the provider that no payout explains"),
    "day_shift": ("info", "same payment, booked on a different day by each side"),
    "outside_window": ("info", "outside the other side's dates; not judged"),
}


@dataclass
class Finding:
    cls: str
    message: str
    currency: str | None = None
    amount: int = 0  # effect on the walk, settlement currency where known
    kind: str | None = None
    ledger: list[LedgerEntry] = field(default_factory=list)
    provider: list[ProviderRow] = field(default_factory=list)
    payout: Payout | None = None
    bank: BankLine | None = None
    detail: dict = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return CLASSES[self.cls][0]

    @property
    def order_ref(self) -> str | None:
        for e in self.ledger:
            if e.order_ref:
                return e.order_ref
        for r in self.provider:
            if r.order_ref:
                return r.order_ref
        return None

    @property
    def payment_ref(self) -> str | None:
        for r in self.provider:
            if r.payment_ref:
                return r.payment_ref
        for e in self.ledger:
            if e.payment_ref:
                return e.payment_ref
        return None

    def to_dict(self) -> dict:
        return {
            "class": self.cls,
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "currency": self.currency,
            "amount_minor": self.amount,
            "order_ref": self.order_ref,
            "payment_ref": self.payment_ref,
            "ledger": [
                {"id": e.entry_id, "line": e.line, "amount_minor": e.amount, "currency": e.currency, "status": e.status}
                for e in self.ledger
            ],
            "provider": [
                {"id": r.txn_id, "line": r.line, "kind": r.kind, "gross_minor": r.gross, "currency": r.currency} for r in self.provider
            ],
            "payout": None
            if self.payout is None
            else {"id": self.payout.payout_id, "date": str(self.payout.day), "amount_minor": self.payout.amount},
            "bank": None
            if self.bank is None
            else {
                "line": self.bank.line,
                "date": str(self.bank.day),
                "amount_minor": self.bank.amount,
                "description": self.bank.description,
            },
            "detail": self.detail,
        }
