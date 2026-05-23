"""Pure functions for FM allocation math. No I/O — fully unit-testable."""
from __future__ import annotations

COLD_START_PICKS_AUDITED_THRESHOLD = 30
COLD_START_FM_TOTAL_THRESHOLD = 20
CONVICTION_MULT = {"BUY HIGH": 1.0, "BUY MEDIUM": 0.6, "WATCH": 0.2, "SKIP": 0.0}

MAX_POSITION_PCT_MATURE = 0.20
MAX_POSITION_PCT_COLD = 0.10
MAX_BOOK_DEPLOYED_MATURE = 0.80
MAX_BOOK_DEPLOYED_COLD = 0.50
MIN_POSITION_PCT = 0.02


def specialist_weights(stats: dict) -> dict[str, float]:
    out = {}
    for spec, s in stats.items():
        audited = int(s.get("picks_audited", 0))
        if audited < COLD_START_PICKS_AUDITED_THRESHOLD:
            out[spec] = 1.0
        else:
            hr = float(s.get("hit_rate_last_30d") or 0.0)
            out[spec] = max(0.1, hr)
    return out


def score_pick(specialist: str, conviction: str, specialist_weight: float,
               validated_lesson_fires: bool, candidate_lesson_fires: int,
               convergence_count: int) -> float:
    if validated_lesson_fires:
        return 0.0
    base = specialist_weight * CONVICTION_MULT.get(conviction, 0.0)
    penalty = min(1.0, 0.3 * candidate_lesson_fires)
    bonus = 0.1 if convergence_count >= 2 else 0.0
    return max(0.0, base * (1 - penalty) + bonus)


def compute_sizes(scored_picks: list[tuple[str, float]], cold_start: bool) -> dict[str, float]:
    """Inputs: [(pick_id, score)]. Returns {pick_id: size_pct} respecting caps."""
    if not scored_picks:
        return {}
    max_pos = MAX_POSITION_PCT_COLD if cold_start else MAX_POSITION_PCT_MATURE
    max_book = MAX_BOOK_DEPLOYED_COLD if cold_start else MAX_BOOK_DEPLOYED_MATURE

    scores = [s for _, s in scored_picks if s > 0]
    if not scores:
        return {}
    max_score = max(scores)
    raw = {pid: min(s / max_score * max_pos, max_pos) for pid, s in scored_picks if s > 0}
    raw = {pid: s for pid, s in raw.items() if s >= MIN_POSITION_PCT}

    total = sum(raw.values())
    if total > max_book:
        scale = max_book / total
        raw = {pid: s * scale for pid, s in raw.items()}
    return raw


def is_cold_start_total(total_picks_audited: int) -> bool:
    return total_picks_audited < COLD_START_FM_TOTAL_THRESHOLD
