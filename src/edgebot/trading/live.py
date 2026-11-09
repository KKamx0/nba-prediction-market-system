"""Live trading entry point.

Not built yet: the LiveExecutor and the paper -> live gate come last (Phase 10).
Until then this refuses unconditionally, so ``--live`` can never send an order.
"""

from __future__ import annotations


def run_live(args, settings, store) -> int:
    print("live trading is not available yet: paper mode only (see README, Paper -> live gate)")
    return 2
