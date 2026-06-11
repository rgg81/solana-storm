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
STATE_DIR = Path(__file__).resolve().parent / "state"


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
    # 4-specialist keys + legacy 'solana_expert' fallback when split keys absent.
    for spec_name in (
        "market_analyst_optimist",
        "market_analyst_pessimist",
        "solana_expert_optimist",
        "solana_expert_pessimist",
        "solana_expert",
    ):
        if spec_name == "solana_expert" and (
            sb.get("solana_expert_optimist") or sb.get("solana_expert_pessimist")
        ):
            continue
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

    # Reflections (Phase 6 — what-if + Reflector LLM output)
    refl_block = reflections_summary()
    if refl_block:
        lines.append("")
        lines.append(refl_block)

    return "\n".join(lines)


# ============================================================
# Phase 6 — Reflections
# ============================================================

REFL_PATH = STATE_DIR / "lessons_reflections.jsonl"
EQUITY_PATH = STATE_DIR / "equity.jsonl"
AUDIT_LOG_PATH = STATE_DIR / "closed_trades_audit.jsonl"


def refresh_frontmatter_counters() -> dict:
    """Recompute rollup counters from jsonl ground truth and write to frontmatter.

    Fixes the drift between:
      - lessons_reflections.jsonl (Reflector writes here) → validated/candidate/rejected counts
      - equity.jsonl (Phase 0 writes here)              → total_ticks_recorded
      - closed_trades_audit.jsonl (audit.audit_close)   → total_closed_trades_audited

    Idempotent; safe to call from Phase 6 every tick.
    """
    updates: dict = {}

    agg = _aggregate_reflections()
    updates["validated_rules_count"] = len(agg.get("validated", []))
    updates["candidate_rules_count"] = len(agg.get("candidates", []))
    updates["disconfirmed_rules_count"] = len(agg.get("rejected", []))

    if EQUITY_PATH.exists():
        n_ticks = sum(1 for line in EQUITY_PATH.read_text().splitlines() if line.strip())
        updates["total_ticks_recorded"] = n_ticks

    if AUDIT_LOG_PATH.exists():
        n_audited = sum(1 for line in AUDIT_LOG_PATH.read_text().splitlines() if line.strip())
        updates["total_closed_trades_audited"] = n_audited

    return update_frontmatter(updates)


_BODY_MARKER_VALIDATED = "## Validated lessons"
_BODY_MARKER_CANDIDATE = "## Candidate lessons"
_BODY_MARKER_DISCONFIRMED = "## Disconfirmed lessons"
_BODY_AUTOGEN_BEGIN = "<!-- LESSONS_AUTOGEN_BEGIN — managed by lessons_io.refresh_body -->"
_BODY_AUTOGEN_END = "<!-- LESSONS_AUTOGEN_END -->"


def _render_rules_section(label: str, rules: list[dict]) -> list[str]:
    if not rules:
        return [f"### {label}", "", "_(none)_", ""]
    out = [f"### {label}", ""]
    for r in sorted(rules, key=lambda x: -x.get("supporting_count", 0)):
        kind = r.get("kind", "?")
        pattern = (r.get("pattern") or r.get("candidate_lesson") or "")[:280]
        n = r.get("supporting_count", 0)
        d = r.get("disconfirming_count", 0)
        out.append(f"- **[{kind}]** {pattern}  _(supports={n}, disconfirms={d})_")
    out.append("")
    return out


def refresh_body() -> dict:
    """Render aggregated reflection rules into the lessons.md body.

    Wires _aggregate_reflections() output into Validated/Candidate/Disconfirmed
    sections wrapped in an autogen block so the rest of the body (header,
    notes, manual sections) is preserved across runs.

    Historically the body remained at '_(none yet — cold start)_' even after
    the frontmatter showed 16+ validated rules (multi-agent review 2026-06-06).
    """
    if not LESSONS_PATH.exists():
        return {"updated": False, "reason": "lessons.md missing"}
    agg = _aggregate_reflections()
    if agg.get("total_reflection_rows", 0) == 0:
        return {"updated": False, "reason": "no reflections yet"}

    rendered: list[str] = [_BODY_AUTOGEN_BEGIN, ""]
    rendered += _render_rules_section("Validated (promoted)", agg.get("validated", []))
    rendered += _render_rules_section("Candidate (awaiting promotion)", agg.get("candidates", []))
    rendered += _render_rules_section("Disconfirmed (rejected anti-patterns)", agg.get("rejected", []))
    rendered.append(_BODY_AUTOGEN_END)
    rendered_block = "\n".join(rendered)

    body = load_body()
    # Replace an existing autogen block in-place, else inject after the body
    # header (before the cold-start placeholder sections).
    if _BODY_AUTOGEN_BEGIN in body and _BODY_AUTOGEN_END in body:
        before, _, rest = body.partition(_BODY_AUTOGEN_BEGIN)
        _, _, after = rest.partition(_BODY_AUTOGEN_END)
        new_body = before + rendered_block + after
    else:
        # Strip the historical cold-start placeholders so the autogen block is
        # the single source of truth for the rule list.
        cleaned = re.sub(
            r"## Validated lessons.*?(?=\n##|\Z)|## Candidate lessons.*?(?=\n##|\Z)|## Disconfirmed lessons.*?(?=\n##|\Z)",
            "",
            body,
            flags=re.DOTALL,
        )
        sep = "\n\n" if not cleaned.endswith("\n\n") else ""
        new_body = cleaned.rstrip() + sep + "\n" + rendered_block + "\n"

    write(load_frontmatter(), new_body)
    return {
        "updated": True,
        "n_validated": len(agg.get("validated", [])),
        "n_candidates": len(agg.get("candidates", [])),
        "n_rejected": len(agg.get("rejected", [])),
    }


def _load_reflections() -> list[dict]:
    if not REFL_PATH.exists(): return []
    rows = []
    for line in REFL_PATH.read_text().splitlines():
        if not line.strip(): continue
        try: rows.append(json.loads(line))
        except Exception: pass
    return rows


def append_reflection(row: dict) -> None:
    """Append a single Reflector output row (one candidate, one confirmation, etc.)."""
    existing = REFL_PATH.read_text() if REFL_PATH.exists() else ""
    tmp = REFL_PATH.with_suffix(REFL_PATH.suffix + ".tmp")
    tmp.write_text(existing + json.dumps(row, default=str) + "\n")
    tmp.rename(REFL_PATH)


def _aggregate_reflections() -> dict:
    """Walk lessons_reflections.jsonl, fold confirmations into candidates,
    return current state: {candidates: [...], validated: [...], rejected: [...]}.

    Promotion rule: candidate with ≥3 confirming observations → validated.
    Demotion rule: candidate with ≥3 disconfirming observations → rejected.
    """
    rows = _load_reflections()
    by_id: dict[str, dict] = {}

    for r in rows:
        if r.get("kind_row") == "new_candidate":
            cid = r["candidate_id"]
            by_id.setdefault(cid, {
                "candidate_id": cid,
                "kind": r.get("kind"),
                "pattern": r.get("pattern"),
                "candidate_lesson": r.get("candidate_lesson"),
                "affects": r.get("affects", []),
                "supporting_count": r.get("supporting_count", 0),
                "disconfirming_count": 0,
                "status": "candidate",
                # Latest EXPLICIT status the Reflector suggested (None = never
                # suggested). A deliberate "candidate" hold-back vetoes count-based
                # auto-promotion — the Reflector is the judgment agent.
                "explicit_status": None,
                # Did the rule earn supporting evidence in a FORWARD tick (a
                # confirmation row), or is its supporting_count purely the
                # Reflector's backward-looking genesis seed? Count-based promotion
                # requires forward confirmation — a candidate must keep holding in
                # live ticks, not just survive its initial retrospective count.
                "has_forward_confirming": False,
                "first_seen_tick": r.get("tick_id"),
                "last_updated_tick": r.get("tick_id"),
            })
        elif r.get("kind_row") == "confirmation":
            cid = r.get("prior_candidate_id")
            if not cid or cid not in by_id: continue
            c = by_id[cid]
            if r.get("kind") == "confirming":
                c["supporting_count"] = max(c["supporting_count"], r.get("new_supporting_count", c["supporting_count"] + 1))
                c["has_forward_confirming"] = True
            elif r.get("kind") == "disconfirming":
                c["disconfirming_count"] += 1
            c["last_updated_tick"] = r.get("tick_id")
            sugg = r.get("new_status_suggestion")
            if sugg is not None:
                c["status"] = sugg
                c["explicit_status"] = sugg  # remember the latest explicit call

    # Apply promotion/demotion rules. Precedence:
    #   1. disconfirming >= 3 → rejected (safety demotion, always wins)
    #   2. Reflector explicitly called "rejected" → rejected (judgment retirement;
    #      a single decisive falsification test can retire a hypothesis without
    #      waiting for 3 mechanical disconfirming rows — the Reflector is the judge.
    #      e.g. a watchlist hypothesis falsified-on-arrival by the exact test case it
    #      targeted). Honored symmetrically with the explicit-"candidate" hold below.
    #   3. Reflector explicitly held it at "candidate" → stay candidate (veto the
    #      count rule; the judge deliberately wants more evidence of a specific kind)
    #   4. Reflector explicitly called "validated" → validated (judgment promotion;
    #      symmetric with the explicit "rejected"/"candidate" calls above)
    #   5. supporting >= 3 AND earned via FORWARD confirmation → validated. A high
    #      supporting_count on the genesis new_candidate row alone is NOT enough —
    #      that count is the Reflector's backward-looking evidence; the
    #      candidate→validated lifecycle requires the rule to keep holding in a
    #      later live tick (a confirmation row), not just survive its first appearance.
    #   6. otherwise → candidate
    candidates, validated, rejected = [], [], []
    for c in by_id.values():
        if c["disconfirming_count"] >= 3:
            c["status"] = "rejected"
            rejected.append(c)
        elif c.get("explicit_status") == "rejected":
            c["status"] = "rejected"
            rejected.append(c)
        elif c.get("explicit_status") == "candidate":
            c["status"] = "candidate"
            candidates.append(c)
        elif c.get("explicit_status") == "validated":
            c["status"] = "validated"
            validated.append(c)
        elif c["supporting_count"] >= 3 and c.get("has_forward_confirming"):
            c["status"] = "validated"
            validated.append(c)
        else:
            c["status"] = "candidate"
            candidates.append(c)
    return {"candidates": candidates, "validated": validated, "rejected": rejected,
            "total_reflection_rows": len(rows)}


def reflections_summary(max_chars: int = 1200) -> str:
    """Compact block for agent prompts — appended to lessons summary.

    Surfaces validated reflection-rules + top candidates (most-supported)."""
    agg = _aggregate_reflections()
    if agg["total_reflection_rows"] == 0:
        return ""
    lines = ["REFLECTIONS (post-tick learning, predictions/fund/state/lessons_reflections.jsonl):"]
    v = agg["validated"]
    if v:
        lines.append(f"  Validated reflection-rules: {len(v)}")
        for c in v[:5]:
            lines.append(f"    ✓ [{c['kind']}] {c['candidate_lesson'][:180]} (n={c['supporting_count']})")
    c_list = sorted(agg["candidates"], key=lambda x: -x.get("supporting_count", 0))[:3]
    if c_list:
        lines.append(f"  Top candidates (need more confirms):")
        for c in c_list:
            lines.append(f"    · [{c['kind']}] {c['candidate_lesson'][:180]} "
                          f"(supports={c['supporting_count']} discon={c['disconfirming_count']})")
    out = "\n".join(lines)
    return out[:max_chars]


if __name__ == "__main__":
    print("=== Frontmatter ===")
    print(json.dumps(load_frontmatter(), indent=2, default=str)[:1500])
    print("\n=== Agent-prompt summary ===")
    print(summary_for_agent_prompt())
