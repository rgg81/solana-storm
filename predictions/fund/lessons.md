---
version: 1
last_updated: '2026-06-07T13:32:31Z'
total_ticks_recorded: 111
total_closed_trades_audited: 2
scoreboard:
  market_analyst_optimist:
    closed_trades_scored: 1
    correct_directional_calls: 0
    avg_score_on_winners: null
    avg_score_on_losers: 0.75
    over_confidence_flag: false
    _n_losers: 1
  market_analyst_pessimist:
    closed_trades_scored: 1
    correct_directional_calls: 0
    avg_score_on_winners: null
    avg_score_on_losers: 0.1
    over_caution_flag: false
    _n_losers: 1
  solana_expert:
    closed_trades_scored: 1
    correct_directional_calls: 0
    avg_score_on_winners: null
    avg_score_on_losers: 0.1
    _n_losers: 1
  risk_manager:
    forced_closes_executed: 0
    forced_closes_validated: 0
    size_adjustments_correlation: null
  portfolio_manager:
    trades_executed: 0
    closes_executed: 1
disagreement_outcome:
  spread_0_to_15:
    n: 0
    avg_return_pct: null
    win_rate: null
  spread_15_to_40:
    n: 0
    avg_return_pct: null
    win_rate: null
  spread_40_to_70:
    n: 1
    avg_return_pct: -6.26
    win_rate: 0.0
  spread_70_plus:
    n: 0
    avg_return_pct: null
    win_rate: null
validated_rules_count: 18
candidate_rules_count: 3
disconfirmed_rules_count: 2
---

# SMAF — Rolling Lessons & Specialist Memory

This file is the **persistent memory** of the Solana Multi-Agent Fund. Every tick all 5 agents see a summary of this file. The frontmatter is updated by `audit.audit_close()` when positions close; the body is updated when lessons are proposed/promoted/disconfirmed.

**Cold-start state**: no closed trades audited yet. Performance metrics are tick-snapshot-only (Sharpe, DD). Specialist scoreboards are empty; lessons section is empty. Agents fall back to their role-defined heuristics.

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


<!-- LESSONS_AUTOGEN_BEGIN — managed by lessons_io.refresh_body -->

### Validated (promoted)

- **[calibration_observation]** Across 4 ticks and all 38 rejection what-ifs (11 universe symbols x 6h/12h/18h/24h windows), zero positive price deltas occurred. The strong_bear regime BUY-floor of +0.35 has produced a 0/38 missed-winner rate at the >=5% materiality bar.  _(supports=94, disconfirms=2)_
- **[good_rejection]** High-conviction MA-Pessimist (<=-0.50) on rejected symbols has been directionally aligned 8/8 with the subsequent move; 2/8 (VIRTUAL t3, TRUMP t4) crossed the >=5% materiality bar.  _(supports=90, disconfirms=1)_
- **[good_rejection]** In confirmed SOL strong_bear regime with the +0.05 regime BUY-floor adder active, REJECT decisions where MA-Optimist was >=+0.40 still produced >=5% down moves within 6-24h.  _(supports=42, disconfirms=2)_
- **[calibration_observation]** When 3+ universe names print uncontested 5-7% 6h upside on a single tick (max_consensus_in_window all <+0.35) AND the regime axis (SOL) does NOT participate, this is a periphery-rotation tape-beta event NOT a per-name calibration failure - the team had no plausible entry on any o  _(supports=20, disconfirms=1)_
- **[good_rejection]** The Risk Manager's practice of pre-committing a specific quantitative override tripwire BEFORE a catalyst rally peak (tick-27/28 set 'SE-Pes >=+0.10 AND consensus >=+0.20 for RENDER') held discipline through the tick-13/14 peak across all 3 candidate names (RENDER, JUP, PYTH) and  _(supports=19, disconfirms=0)_
- **[good_rejection]** The MA-Pes <=-0.50 HARD VETO anchor SET at the CURRENT tick (not just the prior-tick source) is empirically vindicated within the same 6h window as a forward-looking real-time defensive signal — JUP's current-tick MA-Pes -0.55 HARD VETO at t32 occurred in the same 6h window in wh  _(supports=18, disconfirms=0)_
- **[good_rejection]** When market_disagreement is HIGH (>=0.55) but on-chain disagreement is LOW (<=0.30) AND the two on-chain specialists AGREE on a negative direction, REJECT decisions in strong_bear regime have produced >=5% downside in 4/4 material observations within 18-24h windows.  _(supports=17, disconfirms=0)_
- **[calibration_observation]** Across the 9 reflector dispatches since the audit fix (tick-19 through tick-27), the maximum consensus printed on any universe symbol on any tick has been +0.20 (JUP at the tick-35 source in user numbering). The realized consensus distribution in this calm-bear tape is structural  _(supports=16, disconfirms=1)_
- **[calibration_observation]** Solana-infrastructure / AI-compute names (PYTH, GRASS, RENDER) rejected on the strong_bear BUY floor with MA-Opt >=+0.45 AND a positive MA-Pes flip have produced material 24h upside in 3/3 observed catalyst-rally instances, all of which round-tripped within 12-24h of the print.  _(supports=15, disconfirms=2)_
- **[calibration_observation]** Four consecutive Reflector dispatches (tick-31, tick-32, tick-33, tick-34) have ALL been bear-side trigger dispatches with ZERO probes fired, ZERO contested-signal tests of the +0.40 BUY floor, ZERO crosses of the +0.35 contest gate, and ZERO >=5% upside excursions across the las  _(supports=12, disconfirms=1)_
- **[calibration_observation]** When the cand_24_dc388268 periphery-rotation cycle (lift t23 -> persist -> unwind t28 -> bounce t29) enters a SECOND material leg-down (t31->t32 in this run) AND SOL PARTICIPATES in that leg-down for the first time (vs. staying flat on the prior 4 legs), the regime-axis has trans  _(supports=9, disconfirms=1)_
- **[calibration_observation]** When 4+ uncontested_rejection_down triggers fire SIMULTANEOUSLY in a single dispatch with ALL max_consensus_in_window values strictly below the +0.35 contest gate AND the same dispatch produces 6+ simultaneous MA-Pes <=-0.50 HARD VETOs all printing direction-aligned downside, the  _(supports=8, disconfirms=1)_
- **[calibration_observation]** [regime=strong_bear, vol=normal] UNIVERSE-WIDE SIMULTANEOUS UP-MOVE — 8 of 9 names lifted +3-12%/6h (RENDER +6.29%, PUMP +5.49%, PENGU +8.05%, JTO +12.08%, SOL +4.49%, JUP +3.08%, PYTH +2.20%, VIRTUAL +4.18%, TRUMP +3.19%). First such universe-wide upside print in the 42-tick pos  _(supports=6, disconfirms=0)_
- **[good_rejection]** When a probe-path setup meets MA-Opt >=+0.45 catalyst trigger but consensus falls SHORT of the +0.20 second-leg gate by <=0.10 (knife-edge or wide-tight near-miss), the consensus axis tends to COLLAPSE within the next 6h (>=0.20 absolute drop) AND price tends to fall direction-al  _(supports=4, disconfirms=1)_
- **[calibration_observation]** When a name has been through the full periphery-rotation cycle (lift -> persist -> unwind) and then prints a t29-class uncontested-bounce missed_winner at +5-8%/6h, the consensus axis on that name can SIMULTANEOUSLY lift through the +0.20 probe second-leg gate at the bounce tick   _(supports=4, disconfirms=1)_
- **[calibration_observation]** Within the 5-dispatch post-audit silence stretch (tick-31 through tick-35), THREE INDEPENDENT axes of bear-stress de-escalation have emerged in this single dispatch for the first time: (a) trigger count receding 4 -> 3 -> 2 across the last three dispatches; (b) HARD VETO count re  _(supports=4, disconfirms=1)_
- **[calibration_observation]** The Risk Manager's Pass 2.5 probe path requires regime = strong_bear AND vol_bucket = calm_vol, but the vol bucket has been 'normal' (SOL 30d daily ~2.91%) throughout the 42-tick post-audit streak. This means the probe path may have been STRUCTURALLY INELIGIBLE for the entire str  _(supports=4, disconfirms=0)_
- **[calibration_observation]** [regime=strong_bear, vol=normal] The 2026-06-06 audit fix (calm_vol prereq dropped from Pass 2.5 probe gate) ENABLED FRAMEWORK ACTIVATION. The structurally-closed gate that cand_41_e806e2e6 identified opened, and the first organic score-cleared probe in 100+ ticks fired on JTO th  _(supports=3, disconfirms=0)_

### Candidate (awaiting promotion)

- **[calibration_observation]** Solana-infra MA-Opt approach to +0.45 within strong_bear has now produced n=2 distinct-name FADE outcomes (JTO t38 MA-Opt +0.40 -> +0.30 by t40 with consensus 0.105 -> 0.095 plateau then fade; PYTH t40 MA-Opt +0.42 -> +0.05 at t41 with consensus +0.143 -> -0.063 reversal). Both n  _(supports=2, disconfirms=1)_
- **[good_entry]** [regime=strong_bear, vol=normal] Pass 2.5 probe entries (audit-fix-enabled path: MA-Opt >= +0.45 catalyst trigger, consensus >= +0.20 second-leg gate, combined_uncertainty <= 0.55, +0.40 strong_bear BUY floor cleared via probe path) pay off within 1-3 ticks when SOL is also print  _(supports=2, disconfirms=0)_
- **[good_exit]** [regime=strong_bear, vol=normal] Distribution-into-strength signature on a vertical-print probe entry (h1 print materially smaller than h6 print AND buy-side flow skew below 55% on the same vertical day) correctly identified the moment to TIGHTEN_STOP rather than hold open the or  _(supports=2, disconfirms=0)_

### Disconfirmed (rejected anti-patterns)

- **[calibration_observation]** Bounce-from-capitulation prints (single-window upside excursion >=5% from prior_consensus near or below zero, MA-Opt <=+0.20, max_consensus_in_window UNCONTESTED <+0.20, occurring during SOL's largest concurrent down-leg of the regime) appear simultaneously across 3 distinct name  _(supports=5, disconfirms=3)_
- **[calibration_observation]** The cand_17_7467b064 candidate's 'FADE-the-print' framing should be RESTATED to separate CONSENSUS-fade (validated across both catalyst and tape-beta variants) from PRICE-fade (validated only in the high-MA-Opt catalyst variant - in tape-beta variants the price tends to persist o  _(supports=3, disconfirms=9)_

<!-- LESSONS_AUTOGEN_END -->
