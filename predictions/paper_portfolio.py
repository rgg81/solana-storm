"""Paper-trading P&L aggregator over v1 + v2 audit outcomes.

Usage:
    python3 predictions/paper_portfolio.py

Outputs a markdown report covering:
- Per-pick paper P&L (BUY HIGH=1.0 SOL, BUY MEDIUM=0.5 SOL, WATCH=0.2 SOL paper sizing)
- Cumulative P&L (realized + open) per specialist + total
- Hit rate per conviction tier
- Shadow-watch counterfactuals (would-be BUYs vetoed by VALIDATED lessons)
- Verdict on "are we profitable" given current data

Treats v1 (post-graduation picker) and v2 (multi-agent fund) as one continuous
audit ledger — lessons.md carries forward; paper P&L compounds.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from predictions import config  # noqa: E402

PAPER_SIZE_SOL = {
    "BUY HIGH": 1.0,
    "BUY MEDIUM": 0.5,
    "WATCH": 0.2,
}

# v1 audits — outcomes per-pick are documented in body markdown, not frontmatter.
# Hardcoded here because v1 outcome files don't expose realized_return as a structured field.
V1_AUDITS = [
    # (decision_id, ticker, conviction_at_entry, realized_return)
    ("2026-05-21-09-15", "HANE6NAj", "WATCH", -0.868),
    ("2026-05-21-09-15", "ApGBE2Qk", "WATCH", -0.979),
    ("2026-05-21-13-25", "MEMEWC",   "WATCH", -0.995),
    ("2026-05-21-22-00", "NATRO",    "WATCH", -0.251),
    ("2026-05-22-02-07", "CR7",      "WATCH", -0.997),
    ("2026-05-22-17-49", "NOAR",     "WATCH", -0.973),
]


def _parse_frontmatter(path: Path) -> dict:
    try:
        raw = path.read_text()
    except Exception:
        return {}
    m = re.match(r"^---\n(.*?)\n---", raw, re.DOTALL)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        try:
            out[k.strip()] = json.loads(v) if v else v
        except Exception:
            out[k.strip()] = v
    return out


def _walk_v2_outcomes() -> list[dict]:
    """Read v2 outcome files (one pick per file, realized_return in frontmatter)."""
    out_dir = config._REPO_ROOT / "predictions" / "diary" / "outcomes"
    if not out_dir.exists():
        return []
    out = []
    v1_decision_ids = {a[0] for a in V1_AUDITS}
    for p in sorted(out_dir.glob("*-outcome.md")):
        fm = _parse_frontmatter(p)
        # Skip v1-format outcome files (decision_id matches v1 set + no realized_return at top-level)
        decision_id = fm.get("decision_id") or ""
        if decision_id in v1_decision_ids and "realized_return" not in fm:
            continue
        if "realized_return" not in fm:
            continue
        out.append({
            "path": str(p),
            "pick_id": fm.get("pick_id") or p.stem,
            "specialist": fm.get("specialist") or "unknown",
            "mint": fm.get("mint") or "",
            "realized_return": float(fm.get("realized_return") or 0.0),
            "pool_closed": bool(fm.get("pool_closed")),
            "won": bool(fm.get("won")),
            "kind": fm.get("kind") or "pick",  # "pick" or "shadow_watch"
            "audited_at_unix": int(fm.get("audited_at_unix") or 0),
        })
    return out


def compute_portfolio() -> dict:
    """Aggregate v1 + v2 audits into a paper-trading P&L view."""
    rows: list[dict] = []

    # v1: hardcoded
    for decision_id, ticker, conviction, ret in V1_AUDITS:
        size = PAPER_SIZE_SOL.get(conviction, 0.0)
        rows.append({
            "source": "v1",
            "decision_id": decision_id,
            "ticker": ticker,
            "specialist": "v1_legacy",
            "conviction": conviction,
            "realized_return": ret,
            "size_sol": size,
            "pnl_sol": size * ret,
            "kind": "pick",
        })

    # v2: outcome files
    for o in _walk_v2_outcomes():
        # v2 outcomes don't currently record conviction at entry — assume WATCH for paper sizing
        # (v2 hasn't fired BUY yet; will update sizing when realized data exists)
        conviction = "WATCH"
        size = PAPER_SIZE_SOL.get(conviction, 0.0)
        rows.append({
            "source": "v2",
            "decision_id": o["pick_id"],
            "ticker": o.get("mint", "")[:8],
            "specialist": o["specialist"],
            "conviction": conviction,
            "realized_return": o["realized_return"],
            "size_sol": size if o["kind"] == "pick" else 0.0,  # shadow-watches don't consume capital
            "pnl_sol": (size if o["kind"] == "pick" else 0.0) * o["realized_return"],
            "kind": o["kind"],
        })

    # Aggregate stats
    audits = [r for r in rows if r["kind"] == "pick"]
    shadows = [r for r in rows if r["kind"] == "shadow_watch"]
    total_capital_deployed = sum(r["size_sol"] for r in audits)
    cumulative_pnl = sum(r["pnl_sol"] for r in audits)
    return_on_deployed = (cumulative_pnl / total_capital_deployed) if total_capital_deployed else 0.0
    winners = [r for r in audits if r["realized_return"] >= 0.5]
    losers = [r for r in audits if r["realized_return"] < 0.5]

    # Per-specialist breakdown
    by_spec: dict[str, dict] = {}
    for r in audits:
        s = r["specialist"]
        st = by_spec.setdefault(s, {"picks": 0, "capital": 0.0, "pnl": 0.0, "winners": 0})
        st["picks"] += 1
        st["capital"] += r["size_sol"]
        st["pnl"] += r["pnl_sol"]
        if r["realized_return"] >= 0.5:
            st["winners"] += 1

    # Per-conviction breakdown
    by_tier: dict[str, dict] = {}
    for r in audits:
        t = r["conviction"]
        st = by_tier.setdefault(t, {"picks": 0, "winners": 0, "avg_return": 0.0, "_sum_ret": 0.0})
        st["picks"] += 1
        st["_sum_ret"] += r["realized_return"]
        if r["realized_return"] >= 0.5:
            st["winners"] += 1
    for t, st in by_tier.items():
        st["avg_return"] = st["_sum_ret"] / st["picks"] if st["picks"] else 0.0
        del st["_sum_ret"]
        st["hit_rate"] = st["winners"] / st["picks"] if st["picks"] else 0.0

    return {
        "total_audits": len(audits),
        "total_winners": len(winners),
        "total_losers": len(losers),
        "hit_rate": len(winners) / len(audits) if audits else 0.0,
        "total_capital_deployed_sol": total_capital_deployed,
        "cumulative_pnl_sol": cumulative_pnl,
        "return_on_deployed": return_on_deployed,
        "by_specialist": by_spec,
        "by_conviction_tier": by_tier,
        "shadow_watches_audited": len(shadows),
        "audits": audits,
        "shadows": shadows,
    }


def format_report(stats: dict) -> str:
    lines = ["# Paper-portfolio status\n"]
    lines.append(f"**Audits to date:** {stats['total_audits']} picks, {stats['total_winners']} winners ≥+50%, {stats['total_losers']} losers")
    lines.append(f"**Hit rate:** {stats['hit_rate']*100:.1f}%")
    lines.append(f"**Capital deployed (paper):** {stats['total_capital_deployed_sol']:.2f} SOL")
    lines.append(f"**Cumulative P&L:** {stats['cumulative_pnl_sol']:+.3f} SOL")
    lines.append(f"**Return on deployed:** {stats['return_on_deployed']*100:+.1f}%")
    lines.append("")

    lines.append("## Per-specialist breakdown")
    lines.append("| Specialist | Picks | Capital (SOL) | P&L (SOL) | Winners | Hit Rate |")
    lines.append("|---|---|---|---|---|---|")
    for s, st in sorted(stats["by_specialist"].items()):
        hr = (st["winners"] / st["picks"] * 100) if st["picks"] else 0
        lines.append(f"| {s} | {st['picks']} | {st['capital']:.2f} | {st['pnl']:+.3f} | {st['winners']} | {hr:.1f}% |")
    lines.append("")

    lines.append("## Per-conviction tier")
    lines.append("| Conviction | Picks | Avg Return | Hit Rate |")
    lines.append("|---|---|---|---|")
    for t, st in sorted(stats["by_conviction_tier"].items()):
        lines.append(f"| {t} | {st['picks']} | {st['avg_return']*100:+.1f}% | {st['hit_rate']*100:.1f}% |")
    lines.append("")

    if stats["shadows"]:
        lines.append(f"## Shadow-watches audited: {stats['shadow_watches_audited']}")
        lines.append("(would-be BUYs vetoed by VALIDATED lessons — used to detect when veto over-fires)")
        lines.append("")

    # Verdict
    lines.append("## Verdict")
    audits = stats["total_audits"]
    if audits == 0:
        lines.append("**Insufficient data** — no audits yet.")
    elif audits < 20:
        ret = stats["return_on_deployed"] * 100
        if ret < -50:
            lines.append(f"**Trending NOT profitable.** Cumulative paper return is {ret:+.1f}% on {audits} picks. The cohort is in a confirmed loss regime; insufficient data to call this final but the trajectory is bad.")
        elif ret > 0:
            lines.append(f"**Trending profitable** at {ret:+.1f}% on {audits} picks — but the sample is too small for confidence. Need ≥20 audits for a real call.")
        else:
            lines.append(f"**Flat-to-down** at {ret:+.1f}% on {audits} picks. Below the 20-audit confidence threshold.")
    else:
        ret = stats["return_on_deployed"] * 100
        if ret > 10:
            lines.append(f"**Profitable** at {ret:+.1f}% return on deployed across {audits} audits.")
        elif ret > 0:
            lines.append(f"**Marginally profitable** at {ret:+.1f}% — barely above break-even after slippage/fees would be subtracted.")
        else:
            lines.append(f"**NOT profitable.** Cumulative paper return is {ret:+.1f}% on {audits} picks. The skill's predictive edge is negative on this cohort.")

    return "\n".join(lines)


def main():
    stats = compute_portfolio()
    print(format_report(stats))
    # Persist a snapshot for the user to refer to
    snap_path = config._REPO_ROOT / "predictions" / "diary" / "paper_portfolio_snapshot.md"
    snap_path.write_text(format_report(stats))
    print(f"\nSnapshot written to: {snap_path}")


if __name__ == "__main__":
    main()
