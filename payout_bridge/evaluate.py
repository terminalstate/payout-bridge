"""Run the checker over many generated months and compare what it reports with what was put in.

    python -m payout_bridge.evaluate --seeds 200

Every month goes through the CSV files, so the readers are tested too. Informational classes
(day_shift, outside_window) are not scored: they follow from the dates, not from an injected case.
"""

from __future__ import annotations

import argparse
import tempfile
from collections import Counter
from pathlib import Path

from .model import CLASSES
from .money import FeeSchedule
from .reconcile import Options, reconcile
from .sources import load_bank, load_ledger, load_provider
from .synth import LEDGER_TZ, SCHEDULE, generate, write
from .timeparse import zone

SCORED = [c for c, (sev, _) in CLASSES.items() if sev != "info"]


def key_of(f) -> tuple[str, str] | None:
    if f.cls in ("row_arithmetic", "fee_above_schedule"):
        return f.cls, f.provider[0].txn_id
    if f.cls in ("payout_not_in_bank", "bank_amount_differs", "payout_sum"):
        return f.cls, f.payout.payout_id
    if f.cls == "bank_deposit_unexplained":
        return f.cls, f"bank:{f.bank.line}"
    return f.cls, f.order_ref or ""


BANK = ("payout_not_in_bank", "bank_amount_differs", "bank_deposit_unexplained")


def walk_ok(lines) -> bool:
    """Every total equals the previous total plus the lines between them."""
    running = None
    last = max(i for i, ln in enumerate(lines) if ln.total)
    for ln in lines[: last + 1]:
        if ln.total:
            if running is not None and running != ln.amount:
                return False
            running = ln.amount
        else:
            running += ln.amount
    return True


def run_one(provider: str, seed: int, workdir: Path):
    w = generate(provider, seed)
    paths = write(w, workdir / f"{provider}-{seed}")
    rows = load_provider(provider, paths["report"])
    ledger = load_ledger(paths["ledger"], tz="-04:00")
    bank = load_bank(paths["bank"])
    opts = Options(fee_schedule=FeeSchedule(SCHEDULE), ledger_tz=zone("-04:00"), bank_hint=provider)
    res = reconcile(rows, ledger, bank, opts)
    found = Counter(key_of(f) for f in res.findings if f.cls in SCORED)
    truth = Counter((t["class"], t["key"]) for t in w.truth)
    return res, found, truth, w


def evaluate(seeds: int = 100, providers=("stripe", "adyen")):
    table = {(p, c): Counter() for p in providers for c in SCORED}
    by_statement = {(p, refs, c): Counter() for p in providers for refs in (True, False) for c in BANK}
    misses, broken_walks = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for provider in providers:
            for seed in range(1, seeds + 1):
                res, found, truth, w = run_one(provider, seed, Path(tmp))
                refs = w.flags["bank_has_payout_refs"]
                broken_walks += [(provider, seed, cur) for cur, lines in res.walks.items() if not walk_ok(lines)]
                for cls, key in found | truth:
                    tp = min(found[(cls, key)], truth[(cls, key)])
                    for t in (table[(provider, cls)],) + ((by_statement[(provider, refs, cls)],) if cls in BANK else ()):
                        t["tp"] += tp
                        t["fp"] += found[(cls, key)] - tp
                        t["fn"] += truth[(cls, key)] - tp
                    if found[(cls, key)] != truth[(cls, key)]:
                        misses.append((provider, seed, cls, key, found[(cls, key)], truth[(cls, key)]))
    return table, misses, by_statement, broken_walks


def _cell(c: Counter) -> str:
    return f"{c['tp'] + c['fn']} / {c['tp']} / {c['fp']}"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m payout_bridge.evaluate")
    p.add_argument("--seeds", type=int, default=100)
    p.add_argument("--provider", choices=("stripe", "adyen", "both"), default="both")
    a = p.parse_args(argv)
    providers = ("stripe", "adyen") if a.provider == "both" else (a.provider,)
    table, misses, by_statement, broken = evaluate(a.seeds, providers)
    print(f"{a.seeds} generated months per format; ledger in {LEDGER_TZ}, fee schedule {SCHEDULE}\n")
    print("| class | " + " | ".join(f"{p}: put in / found / wrong" for p in providers) + " |")
    print("|---|" + "---:|" * len(providers))
    for cls in SCORED:
        print(f"| {cls} | " + " | ".join(_cell(table[(prov, cls)]) for prov in providers) + " |")
    print("\nBank side, by whether the statement shows payout references:\n")
    print("| class | " + " | ".join(f"{p}, {'with' if r else 'without'}" for p in providers for r in (True, False)) + " |")
    print("|---|" + "---:|" * (2 * len(providers)))
    for cls in BANK:
        print(f"| {cls} | " + " | ".join(_cell(by_statement[(p, r, cls)]) for p in providers for r in (True, False)) + " |")
    print(f"\nWalks that do not add up: {len(broken)}")
    if misses:
        print(f"\n{len(misses)} disagreements, first 20:")
        for m in misses[:20]:
            provider, seed, cls, key, f, t = m
            print(f"  provider={provider} seed={seed} class={cls} key={key} found={f} truth={t}")
    return 1 if misses or broken else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
