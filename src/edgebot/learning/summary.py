"""Human-readable view of what the bot has learned, with the evidence."""

from __future__ import annotations

from datetime import datetime

from edgebot.learning.minutes_bias import learn_bias, projection_errors
from edgebot.learning.postmortem import verdict_counts
from edgebot.learning.segments import SegmentStat, learn_segments, settled_bets

VERDICT_ORDER = [
    "good bet, won",
    "good bet, lost (variance)",
    "lucky win",
    "bad bet, lost",
    "beaten by the market",
    "availability miss",
]


def collect(store, now: datetime, mode: str = "paper") -> dict:
    stats = learn_segments(settled_bets(store, now, mode))
    bias = learn_bias(projection_errors(store, now))
    versions = store.query("SELECT * FROM model_versions ORDER BY id DESC LIMIT 3")
    return {
        "verdicts": verdict_counts(store, mode),
        "rules": [s for s in stats if s.action in ("avoid", "raise_edge", "favor")],
        "watching": sorted(
            (s for s in stats if s.action in ("neutral", "insufficient")),
            key=lambda s: s.estimate,
        ),
        "bias": sorted(bias.items(), key=lambda kv: -abs(kv[1]["bias"]))[:8],
        "versions": [dict(v) for v in versions],
    }


def _rule_line(s: SegmentStat) -> str:
    effect = {
        "avoid": "skip these bets",
        "raise_edge": f"require {s.multiplier:.2g}x the usual edge",
        "favor": f"require {s.multiplier:.2g}x the usual edge (floored)",
    }[s.action]
    return (
        f"{s.dimension}={s.value}: CLV {s.estimate:+.3f} "
        f"[{s.lower:+.3f}, {s.upper:+.3f}] over {s.n} bets -> {effect}"
    )


def to_text(store, now: datetime, mode: str = "paper") -> str:
    c = collect(store, now, mode)
    lines = ["what edgebot has learned"]
    total = sum(c["verdicts"].values())
    if total:
        parts = [f"{v}: {c['verdicts'][v]}" for v in VERDICT_ORDER if c["verdicts"].get(v)]
        lines.append(f"post-mortems ({total} bets): " + " | ".join(parts))
    else:
        lines.append("post-mortems: no settled bets yet")
    if c["rules"]:
        lines.append("active rules:")
        lines += [f"  {_rule_line(s)}" for s in c["rules"]]
    else:
        lines.append("active rules: none yet (each pattern needs 20+ bets and a clear CLV signal)")
    if c["bias"]:
        lines.append("minutes corrections learned from projection errors:")
        lines += [
            f"  {name}: {v['bias']:+.1f} min (avg error {v['mean_error']:+.1f} over {v['n']} games)"
            for name, v in c["bias"]
        ]
    for v in c["versions"][:1]:
        status = "promoted" if v["promoted"] else "kept champion"
        lines.append(f"last retraining check ({v['known_at'][:10]}): {status}: {v['reason']}")
    return "\n".join(lines)


def to_markdown(store, now: datetime, mode: str = "paper") -> str:
    c = collect(store, now, mode)
    out = ["", "## What it has learned", ""]
    total = sum(c["verdicts"].values())
    if total:
        out += ["| Post-mortem verdict | Bets |", "| - | - |"]
        out += [f"| {v} | {c['verdicts'][v]} |" for v in VERDICT_ORDER if c["verdicts"].get(v)]
    else:
        out.append("No settled bets reviewed yet.")
    out.append("")
    if c["rules"]:
        out += ["Active rules (learned from closing line value, shrunk toward no effect):", ""]
        out += [f"- {_rule_line(s)}" for s in c["rules"]]
    else:
        out.append("No segment rules yet: each needs 20+ bets and a clear CLV signal.")
    return "\n".join(out) + "\n"
