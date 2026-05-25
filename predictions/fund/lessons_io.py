"""Read/write SMAF lessons.md with atomic writes + YAML frontmatter.

The file lives at predictions/fund/lessons.md (committed). Format:
    ---
    <YAML frontmatter>
    ---
    
    <markdown body>

Functions:
- load_frontmatter() / load_body()
- update_frontmatter(updates) — merge dict into frontmatter, atomic write
- summary_for_agent_prompt() — short text block to inject into agent inputs
"""
from __future__ import annotations
import re, json, time
from pathlib import Path

LESSONS_PATH = Path(__file__).resolve().parent / "lessons.md"


def _try_import_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        return None


def load_frontmatter() -> dict:
    """Parse the YAML frontmatter from lessons.md."""
    if not LESSONS_PATH.exists(): return {}
    text = LESSONS_PATH.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m: return {}
    yaml = _try_import_yaml()
    if yaml:
        try:
            return yaml.safe_load(m.group(1)) or {}
        except Exception:
            pass
    # Fallback: very crude line-by-line parser for flat keys
    out = {}
    for line in m.group(1).splitlines():
        if line.startswith("#") or ":" not in line: continue
        k, _, v = line.partition(":")
        v = v.strip()
        if not v or v.startswith("#"): continue
        try:
            out[k.strip()] = json.loads(v) if v[0] in '"[{0123456789tfn-' else v
        except Exception:
            out[k.strip()] = v
    return out


def load_body() -> str:
    if not LESSONS_PATH.exists(): return ""
    text = LESSONS_PATH.read_text()
    m = re.match(r"^---\n.*?\n---\n(.*)$", text, re.DOTALL)
    return m.group(1).lstrip("\n") if m else text


def write(frontmatter: dict, body: str) -> None:
    """Atomic write: frontmatter as YAML, then body."""
    yaml = _try_import_yaml()
    if yaml:
        fm_text = yaml.safe_dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip()
    else:
        # Crude fallback
        fm_text = "\n".join(f"{k}: {json.dumps(v)}" for k, v in frontmatter.items())
    new_text = f"---\n{fm_text}\n---\n\n{body}"
    tmp = LESSONS_PATH.with_suffix(".tmp")
    tmp.write_text(new_text)
    tmp.rename(LESSONS_PATH)


def update_frontmatter(updates: dict) -> dict:
    """Deep-merge updates into existing frontmatter. Returns the new frontmatter."""
    fm = load_frontmatter()
    
    def deep_merge(base, new):
        for k, v in new.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                deep_merge(base[k], v)
            else:
                base[k] = v
        return base
    
    deep_merge(fm, updates)
    fm["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write(fm, load_body())
    return fm


def summary_for_agent_prompt() -> str:
    """Compact block (~20 lines) to inject into every agent's prompt.
    
    Includes: validated rules count, scoreboard summary, disagreement correlation.
    """
    fm = load_frontmatter()
    if not fm:
        return "LESSONS: cold start — no closed trades audited yet, no validated rules"
    
    lines = ["LESSONS state (rolling memory, predictions/fund/lessons.md):"]
    closed = fm.get("total_closed_trades_audited", 0)
    lines.append(f"  Audited closed trades: {closed}")
    
    sb = fm.get("scoreboard") or {}
    for spec_name in ("market_analyst_optimist", "market_analyst_pessimist", "solana_expert"):
        s = sb.get(spec_name, {})
        ct = s.get("closed_trades_scored", 0)
        cc = s.get("correct_directional_calls", 0)
        rate = (cc / ct * 100) if ct > 0 else None
        flag = ""
        if s.get("over_confidence_flag"): flag = " ⚠ over-confident"
        elif s.get("over_caution_flag"): flag = " ⚠ over-cautious"
        if ct > 0:
            lines.append(f"  {spec_name}: {cc}/{ct} correct calls ({rate:.0f}%){flag}")
        else:
            lines.append(f"  {spec_name}: no closed-trade data yet")
    
    do = fm.get("disagreement_outcome") or {}
    nonzero = [(k, v) for k, v in do.items() if isinstance(v, dict) and v.get("n", 0) > 0]
    if nonzero:
        lines.append("  Disagreement→outcome:")
        for k, v in nonzero:
            lines.append(f"    {k}: n={v['n']}, avg_return={v.get('avg_return_pct')}%, win_rate={v.get('win_rate')}%")
    
    vr = fm.get("validated_rules_count", 0)
    cr = fm.get("candidate_rules_count", 0)
    if vr > 0: lines.append(f"  Validated rules (HARD VETO): {vr} — see lessons.md body")
    if cr > 0: lines.append(f"  Candidate rules (soft signal): {cr} — see lessons.md body")
    if vr == 0 and cr == 0:
        lines.append("  No validated/candidate rules yet (cold start)")
    
    # Per-symbol observations (item H)
    psa = fm.get("per_symbol_specialist_accuracy") or {}
    if psa:
        traded = [t for t, d in psa.items() if d.get("closed_trades", 0) > 0]
        bl = [t for t, d in psa.items() if d.get("blacklist_hint")]
        if traded:
            lines.append(f"  Per-symbol closed trades: " + ", ".join(
                f"{t}={d['avg_realized_pct']:+.1f}%×{d['closed_trades']}" 
                for t, d in psa.items() if d.get("closed_trades", 0) > 0))
        if bl:
            lines.append(f"  ⚠ Blacklist hints (≥2 closes avg <-5%): {', '.join(bl)}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print("=== Frontmatter ===")
    print(json.dumps(load_frontmatter(), indent=2, default=str)[:1500])
    print("\n=== Agent-prompt summary ===")
    print(summary_for_agent_prompt())
