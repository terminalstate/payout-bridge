"""payout-bridge: walk from your ledger to the provider's report to the bank, and name every difference."""

from .model import CLASSES, BankLine, Finding, LedgerEntry, Payout, ProviderRow
from .money import FeeSchedule
from .reconcile import Options, Result, reconcile
from .render import render_json, render_markdown
from .sources import load_adyen, load_bank, load_generic, load_ledger, load_provider, load_stripe, payouts_from

__version__ = "0.1.0"

__all__ = [
    "CLASSES",
    "BankLine",
    "Finding",
    "LedgerEntry",
    "Payout",
    "ProviderRow",
    "FeeSchedule",
    "Options",
    "Result",
    "reconcile",
    "render_json",
    "render_markdown",
    "load_adyen",
    "load_bank",
    "load_generic",
    "load_ledger",
    "load_provider",
    "load_stripe",
    "payouts_from",
    "__version__",
]
