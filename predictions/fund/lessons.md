---
version: 1
last_updated: 2026-05-24T10:30:00Z
total_ticks_recorded: 0
total_closed_trades_audited: 0

# Specialist scoreboard — track who's been right per closed trade
# Updated by audit.audit_close() when a position is closed (stop, TP, or manual)
scoreboard:
  market_analyst_optimist:
    closed_trades_scored: 0
    correct_directional_calls: 0   # entry-time score sign matched final realized return sign
    avg_score_on_winners: null     # avg entry-time score across closed-winning positions
    avg_score_on_losers: null      # avg entry-time score across closed-losing positions
    over_confidence_flag: false    # set true if avg_score_on_losers > +0.30 (cried wolf positive)
  market_analyst_pessimist:
    closed_trades_scored: 0
    correct_directional_calls: 0
    avg_score_on_winners: null
    avg_score_on_losers: null
    over_caution_flag: false       # set true if avg_score_on_winners < -0.10 (rejected winners)
  solana_expert:
    closed_trades_scored: 0
    correct_directional_calls: 0
    avg_score_on_winners: null
    avg_score_on_losers: null
  risk_manager:
    forced_closes_executed: 0
    forced_closes_validated: 0     # if forced close was followed by further decline (validated)
    size_adjustments_correlation: null  # disagreement-driven size cuts → did they avoid losses?
  portfolio_manager:
    trades_executed: 0
    closes_executed: 0

# Disagreement → outcome correlation
# When Optimist + Pessimist disagreed by N, what's the realized return?
disagreement_outcome:
  spread_0_to_15:    {n: 0, avg_return_pct: null, win_rate: null}
  spread_15_to_40:   {n: 0, avg_return_pct: null, win_rate: null}
  spread_40_to_70:   {n: 0, avg_return_pct: null, win_rate: null}
  spread_70_plus:    {n: 0, avg_return_pct: null, win_rate: null}

# Validated lessons (≥3 confirms, drive hard rules on agents)
validated_rules_count: 0

# Candidate lessons (1-2 confirms, awaiting more data)
candidate_rules_count: 0

# Disconfirmed lessons (negated by evidence)
disconfirmed_rules_count: 0
---

# SMAF — Rolling Lessons & Specialist Memory

This file is the **persistent memory** of the Solana Multi-Agent Fund. Every tick all 5 agents see a summary of this file. The frontmatter is updated by `audit.audit_close()` when positions close; the body is updated when lessons are proposed/promoted/disconfirmed.

**Cold-start state**: no closed trades audited yet. Performance metrics are tick-snapshot-only (Sharpe, DD). Specialist scoreboards are empty; lessons section is empty. Agents fall back to their role-defined heuristics.

---

## Validated lessons (≥3 audit confirms — HARD VETO inputs for all specialists)

_(none yet — cold start)_

## Candidate lessons (1-2 confirms — soft signals, awaiting promotion)

_(none yet)_

## Disconfirmed lessons (status: DISCONFIRMED — anti-patterns to NOT use)

_(none yet)_

---

## Per-specialist track record (auto-updated)

After ≥3 closed trades, each specialist will have a measurable hit rate.

| Specialist | Closed trades scored | Correct directional calls | Avg score on winners | Avg score on losers |
|---|---|---|---|---|
| Optimist | 0 | 0 | n/a | n/a |
| Pessimist | 0 | 0 | n/a | n/a |
| Solana Expert | 0 | 0 | n/a | n/a |

**Interpretation guide:**
- "Correct directional calls" = entry-time score sign matched the eventual realized P&L sign (positive score → winner, negative score → loser)
- "Avg score on winners": if this drifts toward 0 or below, the analyst is consistently underrating winners (Pessimist over-caution)
- "Avg score on losers": if this stays positive, the analyst is consistently overrating losers (Optimist over-confidence)

## Disagreement → outcome correlation

| Optimist-Pessimist spread | N | Avg realized return | Win rate |
|---|---|---|---|
| 0.00 - 0.15 (consensus) | 0 | n/a | n/a |
| 0.15 - 0.40 (mild) | 0 | n/a | n/a |
| 0.40 - 0.70 (moderate) | 0 | n/a | n/a |
| 0.70+ (split) | 0 | n/a | n/a |

**Interpretation**: this tells us whether the disagreement-based size-cut rules in Risk Manager are well-calibrated. If 0.70+ spread trades come back with similar return to consensus trades, the rejection rule is too aggressive.

## How agents should use this file

1. **Validated lessons** are hard vetoes. If a lesson says "AVOID if pattern X", every specialist must respect it.
2. **Specialist scoreboard**: if the agent's own track record shows over-confidence/over-caution, the agent should self-calibrate (be MORE conservative when over-confident, more aggressive when over-cautious).
3. **Disagreement correlation**: if 0.70+ split trades have historically returned positive on average, the Risk Mgr's "REJECT on >0.70 split" rule should be revisited.

## Notes

- This file IS committed to git — rolling memory persists across sessions
- Per-tick state files in `predictions/fund/state/` are gitignored
- Audit updates happen via `audit.audit_close()` when a position closes
