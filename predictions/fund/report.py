"""Human-readable tick report generator for SMAF.

Produces predictions/fund/reports/YYYY-MM-DD-HHMM-tick-N.md after every full tick.

Reads the per-phase JSON artifacts left by the orchestrator:
- /tmp/smaf_universe.json          (Universe Scout)
- /tmp/smaf_market_analyst_optimist.json
- /tmp/smaf_market_analyst_pessimist.json
- /tmp/smaf_solana_expert.json
- /tmp/smaf_risk.json
- /tmp/smaf_pm.json
- predictions/fund/state/tick_phase2_input.json (raw per-symbol data)
- predictions/fund/state/equity.jsonl (P&L tracking)
- predictions/fund/state/bugs.jsonl   (operational issues)

Designed to be the ONE document the human reads per tick — every reasoning
chain compressed to its essentials, every number traceable.

Usage:
    python3 predictions/fund/report.py
    python3 predictions/fund/report.py --tick 3 --quiet   # just print path
"""
from __future__ import annotations
import argparse, json, sys, time, datetime as dt
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from predictions.fund import account, performance, bugs

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = Path(__file__).resolve().parent / "state"


def _safe_load(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def _fmt_pct(x, decimals=2):
    if x is None: return "n/a"
    return f"{x*100:+.{decimals}f}%"


def _fmt_usd(x, decimals=2):
    if x is None: return "n/a"
    return f"${x:,.{decimals}f}"


def build_report() -> str:
    now = dt.datetime.utcnow()
    
    # Load all artifacts
    scout = _safe_load("/tmp/smaf_universe.json") or {}
    optimist = _safe_load("/tmp/smaf_market_analyst_optimist.json") or _safe_load("/tmp/smaf_market_analyst.json") or {}
    pessimist = _safe_load("/tmp/smaf_market_analyst_pessimist.json") or {}
    sexpert = _safe_load("/tmp/smaf_solana_expert.json") or {}
    risk = _safe_load("/tmp/smaf_risk.json") or {}
    pm = _safe_load("/tmp/smaf_pm.json") or {}
    phase2_input = _safe_load(STATE_DIR / "tick_phase2_input.json") or {}
    
    # Account snapshot
    state = account.load()
    eq_log = []
    if (STATE_DIR / "equity.jsonl").exists():
        eq_log = [json.loads(l) for l in (STATE_DIR / "equity.jsonl").read_text().splitlines() if l.strip()]
    tick_n = len(eq_log)
    current_equity = eq_log[-1]["equity_usd"] if eq_log else state["deposit_usd"]
    prev_equity = eq_log[-2]["equity_usd"] if len(eq_log) >= 2 else state["deposit_usd"]
    tick_pnl_usd = current_equity - prev_equity
    tick_pnl_pct = tick_pnl_usd / prev_equity if prev_equity > 0 else 0
    
    perf = performance.compute()
    recent_bugs = bugs.recent(hours=24, min_severity="MEDIUM")
    
    # ===== Build report =====
    lines = []
    lines.append(f"# SMAF Tick {tick_n} — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("> _The tools are the data. The team is responsible for the decisions. Risk management is non-negotiable._")
    lines.append("")
    
    # --- Section 1: Account snapshot
    lines.append("## 1. Account snapshot")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Equity | {_fmt_usd(current_equity)} (deposit {_fmt_usd(state['deposit_usd'])}) |")
    lines.append(f"| This-tick P&L | {_fmt_usd(tick_pnl_usd)} ({_fmt_pct(tick_pnl_pct)}) |")
    lines.append(f"| Total return | {_fmt_pct(perf.get('total_return_pct', 0)/100 if perf.get('total_return_pct') else 0)} |")
    lines.append(f"| Annualized | {perf.get('annualized_return_pct', 0):+.1f}% (after {perf.get('days_running', 0):.1f} days) |")
    lines.append(f"| Sharpe (ann) | {perf.get('sharpe_ratio_annualized', 0):.2f} |")
    lines.append(f"| Max DD | {perf.get('max_drawdown_pct', 0):.2f}% |")
    lines.append(f"| Current DD | {perf.get('drawdown_now_pct', 0):.2f}% |")
    lines.append(f"| Open positions | {perf.get('open_positions_count', 0)} |")
    lines.append(f"| Cash | {_fmt_usd(state['cash_usd'])} |")
    lines.append(f"| Fees paid (total) | {_fmt_usd(state.get('total_fees_paid_usd', 0))} ({perf.get('fee_drag_pct', 0):.2f}% drag) |")
    lines.append(f"| Closed trades | {perf.get('closed_trades', 0)} (hit rate {perf.get('hit_rate_pct') or 'n/a'}%) |")
    lines.append("")
    
    # Open positions table
    holdings = {t: h for t, h in state.get("holdings", {}).items() if h.get("units", 0) > 0}
    if holdings:
        lines.append("### Open positions")
        lines.append("")
        lines.append(f"| Ticker | Units | Avg entry | Cost basis | Stop | TP |")
        lines.append(f"|---|---|---|---|---|---|")
        for t, h in holdings.items():
            lines.append(f"| {t} | {h['units']:.4f} | "
                          f"{_fmt_usd(h.get('avg_entry_price_usd', 0), 6)} | "
                          f"{_fmt_usd(h.get('cost_basis_usd', 0))} | "
                          f"{_fmt_usd(h.get('stop_loss_price_usd'), 6)} | "
                          f"{_fmt_usd(h.get('take_profit_price_usd'), 6)} |")
        lines.append("")
    
    # --- Section 1.5: Goal status (NEW)
    try:
        from predictions.fund import goals
        lines.append("## 1.5 Fund goal status")
        lines.append("")
        gs = goals.compute_status()
        lines.append(f"**Target:** +{gs['monthly_target_pct']:.1f}% monthly  •  "
                      f"**Floor:** +{gs['monthly_floor_pct']:.1f}%  •  "
                      f"**Stretch:** +{gs['monthly_stretch_pct']:.1f}%")
        lines.append("")
        lines.append(f"| Metric | Current | Target | Status |")
        lines.append(f"|---|---|---|---|")
        rr = gs.get('monthly_runrate_pct')
        rr_str = f"{rr:+.2f}%/mo" if rr is not None else "n/a"
        target_str = f"+{gs['monthly_target_pct']:.1f}%/mo"
        lines.append(f"| Monthly run-rate | {rr_str} | {target_str} | **{gs['status']}** |")
        lines.append(f"| Sharpe (ann) | {gs['current_sharpe']:.2f} | ≥{gs['target_sharpe']:.1f} | {'✓' if gs['current_sharpe'] >= gs['target_sharpe'] else '—'} |")
        lines.append(f"| Hit rate | {gs.get('hit_rate_pct') or 'n/a'}% | ≥{gs['target_hit_rate_pct']:.0f}% | — |")
        lines.append(f"| Current DD | {gs['current_dd_pct']:.2f}% | > {gs['max_drawdown_threshold_pct']:.0f}% | {'⚠' if gs['current_dd_pct'] < -10 else '✓'} |")
        lines.append("")
        lines.append(f"**Posture recommendation:** {gs['posture_recommendation']}")
        if gs['maturity_bucket'] == 'cold_start':
            lines.append("")
            lines.append("_Sample size too small to judge; targets are direction, not verdict._")
        lines.append("")
    except Exception as e:
        lines.append(f"_(goal status unavailable: {e})_")
        lines.append("")
    
    # --- Section 1.6: Regime status (NEW)
    try:
        from predictions.fund import regime
        r = regime.combined_regime()
        sol = r["sol"]; cor = r["correlation"]
        lines.append("## 1.6 Market regime")
        lines.append("")
        if sol.get("data_status") == "ok":
            lines.append(f"**SOL trend:** {sol['sol_trend']} (price ${sol['sol_current']} vs SMA200 ${sol['sol_sma200']}, SMA50 ${sol['sol_sma50']})")
            lines.append(f"**SOL 30d vol:** {sol['sol_30d_vol_pct']}% daily → **{sol['sol_vol_regime']}**")
        if cor.get("data_status") == "ok":
            lines.append(f"**Universe correlation:** {cor['mean_pairwise_corr']} across {cor['n_pairs']} pairs → **{cor['corr_regime']}**")
        if r["notes"]:
            lines.append("")
            lines.append("**Active risk adjustments:**")
            for n in r["notes"]:
                lines.append(f"- {n}")
        ra = r["risk_adjustments"]
        if ra["max_position_pct_multiplier"] != 1.0 or ra["buy_floor_adjustment"] != 0.0 or ra["max_deployed_multiplier"] != 1.0:
            lines.append("")
            lines.append(f"_Risk Mgr sees: position size × {ra['max_position_pct_multiplier']}, BUY floor +{ra['buy_floor_adjustment']:.2f}, deployed cap × {ra['max_deployed_multiplier']}_")
        lines.append("")
    except Exception as e:
        lines.append(f"_(regime unavailable: {e})_")
        lines.append("")
    
    # --- Section 1.7: Risk calibration (NEW — auto-tuned parameters)
    try:
        from predictions.fund import risk_calibration
        calib = risk_calibration._load()
        if calib:
            lines.append("## 1.7 Risk calibration (auto-tuned)")
            lines.append("")
            mult = calib.get("stop_vol_multiplier", risk_calibration.DEFAULT_STOP_VOL_MULT)
            coef = calib.get("slippage_coefficient", risk_calibration.DEFAULT_SLIPPAGE_COEF)
            lines.append(f"- **stop_vol_multiplier**: {mult} (default {risk_calibration.DEFAULT_STOP_VOL_MULT})")
            lines.append(f"- **slippage_coefficient**: {coef} (default {risk_calibration.DEFAULT_SLIPPAGE_COEF})")
            pens = calib.get("disagreement_penalty_by_bucket")
            if pens:
                lines.append(f"- **disagreement penalties**: {pens}")
            so = calib.get("stop_outcomes") or {}
            if so.get("n_total", 0) > 0:
                lines.append(f"- stop outcomes: {so['n_total']} total = {so.get('n_triggered_savings',0)} savings + {so.get('n_triggered_winners',0)} stopped-winners + {so.get('n_never_triggered',0)} never-triggered")
            last = calib.get("last_adjustment_reason")
            if last:
                lines.append(f"- last adjustment: {last}")
            lines.append("")
    except Exception:
        pass
    
    # --- Section 2: Universe Scout
    lines.append("## 2. Universe selection")
    lines.append("")
    selected = scout.get("selected_symbols") or []
    if selected:
        tickers = [s.get("ticker") for s in selected]
        lines.append(f"**Scout selected {len(selected)} symbols**: {', '.join(tickers)}")
        lines.append("")
        lines.append(f"**Reasoning:** {scout.get('reasoning', '<none>')}")
        if scout.get("notable_catalysts"):
            lines.append("")
            lines.append("**Notable catalysts:**")
            for c in scout["notable_catalysts"][:5]:
                lines.append(f"- **{c.get('ticker')}**: {c.get('signal') or c.get('reason')}")
    else:
        lines.append("_(no universe selection this tick)_")
    lines.append("")
    
    # --- Section 3: Per-symbol consensus
    lines.append("## 3. Specialist consensus (per symbol)")
    lines.append("")
    # Normalize: a specialist may emit score=null for honest no-data. Treat as 0.0
    # so downstream consensus arithmetic doesn't trip on None.
    def _score(s):
        v = s.get("score")
        return float(v) if v is not None else 0.0
    opt_scores = {s["ticker"]: _score(s) for s in optimist.get("scores", [])}
    pes_scores = {s["ticker"]: _score(s) for s in pessimist.get("scores", [])}
    se_scores = {s["ticker"]: _score(s) for s in sexpert.get("scores", [])}
    opt_reasons = {s["ticker"]: s for s in optimist.get("scores", [])}
    pes_reasons = {s["ticker"]: s for s in pessimist.get("scores", [])}

    # Solana Expert split-aware: prefer split if present, fall back to unified
    se_opt_data = _safe_load("/tmp/smaf_solana_expert_optimist.json")
    se_pes_data = _safe_load("/tmp/smaf_solana_expert_pessimist.json")
    if se_opt_data and se_pes_data:
        se_opt_scores = {s["ticker"]: _score(s) for s in se_opt_data.get("scores", [])}
        se_pes_scores = {s["ticker"]: _score(s) for s in se_pes_data.get("scores", [])}
        has_se_split = True
    else:
        se_opt_scores = se_scores
        se_pes_scores = se_scores
        has_se_split = False

    if opt_scores or pes_scores or se_scores:
        # If both opt and pes are present, show 4-col table
        has_both = bool(opt_scores) and bool(pes_scores)
        if has_both and has_se_split:
            lines.append("| Ticker | MA-Opt | MA-Pes | SE-Opt | SE-Pes | Consensus | Mkt-Disag | OnChain-Disag |")
            lines.append("|---|---|---|---|---|---|---|---|")
        elif has_both:
            lines.append("| Ticker | Optimist | Pessimist | Solana Exp | Consensus | Disagreement |")
            lines.append("|---|---|---|---|---|---|")
        else:
            lines.append("| Ticker | Market Analyst | Solana Exp | Consensus |")
            lines.append("|---|---|---|---|")
        universe = phase2_input.get("universe") or list(set(opt_scores) | set(pes_scores) | set(se_scores))
        for t in universe:
            opt = opt_scores.get(t, 0.0)
            pes = pes_scores.get(t, opt)
            seo = se_opt_scores.get(t, 0.0)
            sep = se_pes_scores.get(t, seo)
            if has_both and has_se_split:
                consensus = (opt + pes + seo + sep) / 4
                mkt_disag = abs(opt - pes)
                oc_disag = abs(seo - sep)
                combined = max(mkt_disag, oc_disag)
                mark = ""
                if combined > 0.7: mark = " ⚠split"
                elif combined > 0.4: mark = " ⚡mod"
                lines.append(f"| {t} | {opt:+.2f} | {pes:+.2f} | {seo:+.2f} | {sep:+.2f} | **{consensus:+.2f}** | {mkt_disag:.2f} | {oc_disag:.2f}{mark} |")
            elif has_both:
                consensus = (opt + pes + seo) / 3
                disagree = abs(opt - pes)
                mark = " ⚠split" if disagree > 0.7 else " ⚡mod" if disagree > 0.4 else ""
                lines.append(f"| {t} | {opt:+.2f} | {pes:+.2f} | {seo:+.2f} | **{consensus:+.2f}** | {disagree:.2f}{mark} |")
            else:
                consensus = (opt + seo) / 2
                lines.append(f"| {t} | {opt:+.2f} | {seo:+.2f} | **{consensus:+.2f}** |")
        lines.append("")
        
        # ===== Sentiment breakdown subsection =====
        if has_both:
            opt_sents = {}
            pes_sents = {}
            opt_headlines = {}
            opt_weighting = {}
            pes_weighting = {}
            for s in optimist.get("scores", []):
                ns = s.get("news_sentiment") or {}
                opt_sents[s["ticker"]] = ns.get("score") if ns else None
                opt_headlines[s["ticker"]] = ns.get("headlines_used") or []
                opt_weighting[s["ticker"]] = s.get("weighting_rationale", "")
            for s in pessimist.get("scores", []):
                ns = s.get("news_sentiment") or {}
                pes_sents[s["ticker"]] = ns.get("score") if ns else None
                pes_weighting[s["ticker"]] = s.get("weighting_rationale", "")
            # Sentiment table — only if at least one MA produced sentiment data
            any_sent = any(v is not None for v in list(opt_sents.values()) + list(pes_sents.values()))
            if any_sent:
                lines.append("### News-sentiment scoring (Optimist vs Pessimist)")
                lines.append("")
                lines.append("Both analysts read the same headlines but **interpret + weight them independently per symbol**. The weighting (tech vs sentiment) is the agent's call per-symbol with explicit rationale.")
                lines.append("")
                lines.append("| Ticker | Opt sent | Opt weighting | Pes sent | Pes weighting | Headline |")
                lines.append("|---|---|---|---|---|---|")
                for t in universe:
                    os = opt_sents.get(t)
                    ps = pes_sents.get(t)
                    headlines = opt_headlines.get(t) or []
                    n_h = len(headlines)
                    sample = ""
                    if headlines:
                        h0 = headlines[0]
                        sample = f'"{h0.get("title","")[:55]}"'
                    os_s = f"{os:+.2f}" if os is not None else "n/a"
                    ps_s = f"{ps:+.2f}" if ps is not None else "n/a"
                    ow = (opt_weighting.get(t, "")[:30])
                    pw = (pes_weighting.get(t, "")[:30])
                    lines.append(f"| {t} | {os_s} | {ow} | {ps_s} | {pw} | {sample if sample else '_no_data_'} |")
                lines.append("")
        
        # ===== Anchor vs override delta (NEW) =====
        anchors = phase2_input.get("sentiment_anchors") or {}
        anomalies = phase2_input.get("sentiment_anomalies") or {}
        if anchors:
            lines.append("### NLP sentiment signals (informational)")
            lines.append("")
            lines.append("VADER+FinBERT scores per symbol — supplementary data inputs the agents see. Agents may use, ignore, or contradict. Final `news_sentiment.score` is the LLM's call.")
            lines.append("")
            lines.append("| Ticker | NLP signal | MA-Opt sent | MA-Pes sent | Anomaly |")
            lines.append("|---|---|---|---|---|")
            for t in universe:
                a = anchors.get(t, {})
                anchor = a.get("anchor_score", 0.0) if a.get("n_headlines", 0) > 0 else None
                opt_sent = opt_sents.get(t)
                pes_sent = pes_sents.get(t)
                anc_str = f"{anchor:+.2f}" if anchor is not None else "n/a"
                anom = anomalies.get(t)
                anom_str = f"{anom['type']} z={anom['z_score']:+.1f}" if anom else "—"
                opt_s_str = f"{opt_sent:+.2f}" if opt_sent is not None else "n/a"
                pes_s_str = f"{pes_sent:+.2f}" if pes_sent is not None else "n/a"
                lines.append(f"| {t} | {anc_str} | {opt_s_str} | {pes_s_str} | {anom_str} |")
            lines.append("")
        
        # Top BUY candidates + top AVOIDs with reasoning.
        # 4-way consensus when SE is split; legacy 3-way otherwise. Historically
        # this block computed 3-way even with split SE — inconsistent with the
        # per-symbol table above and with what the Risk Manager actually sees
        # (multi-agent review 2026-06-06).
        def _row_consensus(t: str) -> float:
            opt = opt_scores.get(t, 0)
            pes = pes_scores.get(t, opt)
            if has_se_split:
                seo = se_opt_scores.get(t, 0)
                sep = se_pes_scores.get(t, seo)
                return (opt + pes + seo + sep) / 4
            return (opt + pes + se_scores.get(t, 0)) / 3

        consensus_sorted = sorted(universe, key=lambda t: -_row_consensus(t))
        lines.append("### Top BUY candidates (consensus ≥ +0.2)")
        lines.append("")
        any_buy = False
        for t in consensus_sorted[:6]:
            c = _row_consensus(t)
            if c < 0.2: continue
            any_buy = True
            opt_r = opt_reasons.get(t, {})
            pes_r = pes_reasons.get(t, {})
            lines.append(f"- **{t}** (consensus {c:+.2f})")
            if opt_r.get("bullish_thesis"):
                lines.append(f"  - 🟢 Optimist: {opt_r['bullish_thesis']}")
            if pes_r.get("bearish_thesis"):
                lines.append(f"  - 🔴 Pessimist: {pes_r['bearish_thesis']}")
        if not any_buy:
            lines.append("_(none — no symbol cleared the +0.2 consensus floor)_")
        lines.append("")
    
    # --- Section 4: Risk Manager decisions
    lines.append("## 4. Risk Manager decisions")
    lines.append("")
    gate = risk.get("account_gate") or {}
    lines.append(f"**Account gate:** drawdown {gate.get('drawdown_pct', 0):.2f}%, "
                  f"halted={gate.get('halt_buys', False)}, "
                  f"remaining_budget={_fmt_usd(gate.get('remaining_budget_for_new_positions_usd', 0))}")
    lines.append("")
    
    # Stop triggers verified
    trigs = risk.get("stop_trigger_verifications") or []
    if trigs:
        lines.append("### Stop/TP triggers verified (Pass 0)")
        lines.append("")
        for t in trigs:
            lines.append(f"- **{t.get('ticker')}** {t.get('trigger_type')}: "
                          f"{t.get('decision')} — {t.get('reasoning', '')}")
        lines.append("")
    
    # Existing positions reviewed
    existing = risk.get("existing_positions") or []
    if existing:
        lines.append("### Existing position actions (Pass 1)")
        lines.append("")
        for e in existing:
            lines.append(f"- **{e.get('ticker')}** → {e.get('action')}: {e.get('reason', '')}")
        lines.append("")
    
    # New entry recommendations
    new_entries = risk.get("new_entry_recommendations") or []
    if new_entries:
        lines.append("### New entry recommendations (Pass 2)")
        lines.append("")
        for e in new_entries:
            ticker = e.get("ticker")
            cons = e.get("consensus") or e.get("specialist_consensus", 0)
            sl = e.get("stop_loss_pct", 0)
            tp = e.get("take_profit_pct", 0)
            sz = e.get("max_size_pct", 0)
            size_usd = e.get("max_size_usd", 0)
            lines.append(f"- **{ticker}** consensus {cons:+.2f} → "
                          f"stop {sl*100:+.0f}%, TP {tp*100:+.0f}%, size {sz}% (≤{_fmt_usd(size_usd)})")
            if e.get("reason"):
                lines.append(f"  - {e['reason']}")
        lines.append("")
    
    rejections = risk.get("rejections") or []
    if rejections:
        lines.append(f"**Rejected {len(rejections)}:** " + 
                      ", ".join(f"{r.get('ticker')} ({r.get('reason', '?')[:40]})" for r in rejections[:8]))
        lines.append("")
    
    if risk.get("summary"):
        lines.append(f"**Risk Mgr summary:** {risk['summary']}")
        lines.append("")
    
    # --- Section 5: Portfolio Manager execution
    lines.append("## 5. Portfolio Manager — final execution")
    lines.append("")
    trades = pm.get("trades") or []
    if trades:
        lines.append(f"| Ticker | Side | USD | Price | Fee | Slip | Stop | TP |")
        lines.append(f"|---|---|---|---|---|---|---|---|")
        for t in trades:
            lines.append(f"| {t.get('ticker')} | {t.get('side', '').upper()} | "
                          f"{_fmt_usd(t.get('usd_amount'))} | "
                          f"{_fmt_usd(t.get('price_usd'), 6)} | "
                          f"{_fmt_usd(t.get('fee_usd'), 3)} | "
                          f"{_fmt_usd(t.get('slippage_usd'), 3)} | "
                          f"{_fmt_usd(t.get('stop_loss_price_usd') or t.get('stop_loss_usd'), 6)} | "
                          f"{_fmt_usd(t.get('take_profit_price_usd') or t.get('take_profit_usd'), 6)} |")
        lines.append("")
    else:
        lines.append("_(no trades executed this tick)_")
        lines.append("")
    
    stop_updates = pm.get("stop_updates") or []
    if stop_updates:
        lines.append("**Stop updates:**")
        for u in stop_updates:
            lines.append(f"- {u.get('ticker')}: stop → {_fmt_usd(u.get('new_stop_usd') or u.get('new_stop_loss_price_usd'), 6)} ({u.get('reason', '')})")
        lines.append("")
    
    if pm.get("summary"):
        lines.append(f"**PM summary:** {pm['summary']}")
        lines.append("")
    
    # --- Section 6: Lessons state (rolling memory)
    lines.append("## 6. Lessons state (rolling memory)")
    lines.append("")
    try:
        from predictions.fund import lessons_io
        fm = lessons_io.load_frontmatter()
        closed = fm.get("total_closed_trades_audited", 0)
        lines.append(f"**Audited closed trades:** {closed}")
        lines.append("")
        sb = fm.get("scoreboard") or {}
        if closed > 0:
            lines.append("### Specialist scoreboard")
            lines.append("")
            lines.append("| Specialist | Closed trades | Correct calls | Avg score on winners | Avg score on losers | Flag |")
            lines.append("|---|---|---|---|---|---|")
            # 4-specialist keys (audit.py writes solana_expert_optimist /
            # solana_expert_pessimist). 'solana_expert' is a legacy unified
            # key kept for back-compat when the split keys are missing.
            for spec_name in (
                "market_analyst_optimist",
                "market_analyst_pessimist",
                "solana_expert_optimist",
                "solana_expert_pessimist",
                "solana_expert",  # legacy fallback
            ):
                s = sb.get(spec_name, {})
                # Skip legacy key if either split key is present (avoid double-count).
                if spec_name == "solana_expert" and (
                    sb.get("solana_expert_optimist") or sb.get("solana_expert_pessimist")
                ):
                    continue
                ct = s.get("closed_trades_scored", 0)
                cc = s.get("correct_directional_calls", 0)
                rate = f"{cc}/{ct} ({cc/ct*100:.0f}%)" if ct > 0 else "n/a"
                aow = s.get("avg_score_on_winners")
                aol = s.get("avg_score_on_losers")
                flag = ""
                if s.get("over_confidence_flag"): flag = "⚠ over-confident"
                elif s.get("over_caution_flag"): flag = "⚠ over-cautious"
                lines.append(f"| {spec_name.replace('market_analyst_','MA-')} | {ct} | {rate} | {aow if aow is not None else 'n/a'} | {aol if aol is not None else 'n/a'} | {flag} |")
            lines.append("")
            
            do = fm.get("disagreement_outcome") or {}
            nonzero = [(k,v) for k,v in do.items() if isinstance(v, dict) and v.get("n", 0) > 0]
            if nonzero:
                lines.append("### Disagreement → outcome correlation")
                lines.append("")
                lines.append("| Spread bucket | N | Avg return | Win rate |")
                lines.append("|---|---|---|---|")
                for k, v in nonzero:
                    lines.append(f"| {k} | {v['n']} | {v.get('avg_return_pct', 'n/a')}% | {v.get('win_rate', 'n/a')}% |")
                lines.append("")
        else:
            lines.append("_No closed trades audited yet — cold start. Specialist scoreboards will populate after first close._")
            lines.append("")
        vr = fm.get("validated_rules_count", 0)
        cr = fm.get("candidate_rules_count", 0)
        lines.append(f"**Validated rules:** {vr}  •  **Candidate rules:** {cr}")
        lines.append("")
    except Exception as e:
        lines.append(f"_(lessons file unavailable: {e})_")
        lines.append("")
    
    # --- Section 7: Operational health
    lines.append("## 7. Operational health")
    lines.append("")
    if not recent_bugs:
        lines.append("✅ No MEDIUM+ issues in last 24h")
    else:
        by_sev = {}
        for b in recent_bugs:
            by_sev.setdefault(b["severity"], []).append(b)
        for sev in ("CRITICAL", "HIGH", "MEDIUM"):
            if sev in by_sev:
                lines.append(f"**{sev} ({len(by_sev[sev])}):**")
                for b in by_sev[sev][:5]:
                    lines.append(f"- [{b.get('component')}] {b.get('message')}")
    lines.append("")

    # =====================================================
    # Section 8 — Post-tick reflection (Phase 6)
    # Informational — surfaces decisions that paid off AND decisions worth
    # revisiting, symmetrically. No verdicts on individual agents.
    # =====================================================
    try:
        from predictions.fund import lessons_io
        from pathlib import Path as _Path
        agg = lessons_io._aggregate_reflections()
        refl_inputs_path = _Path(__file__).resolve().parent / "state" / "reflection_inputs.jsonl"
        latest_phase6 = None
        if refl_inputs_path.exists():
            for ln in reversed(refl_inputs_path.read_text().splitlines()):
                if ln.strip():
                    try:
                        latest_phase6 = json.loads(ln); break
                    except Exception: pass

        # Pull the latest Reflector LLM run (full output: summary, watchlist, decisions)
        from predictions.fund import phase6_orchestrator
        latest_refl = phase6_orchestrator.latest_reflector_run()

        if agg["total_reflection_rows"] > 0 or latest_phase6 or latest_refl:
            lines.append("## 8. Post-tick reflection (Phase 6)")
            lines.append("")
            lines.append("_Informational. Surfaces what we now know about prior decisions — both what worked and what's worth revisiting. No verdicts on individual agents; the team decides jointly._")
            lines.append("")
            if latest_phase6:
                lines.append(f"**Phase 6 last run:** tick {latest_phase6.get('tick_id')} — "
                              f"{latest_phase6.get('n_whatifs', 0)} what-ifs, "
                              f"{latest_phase6.get('n_triggers', 0)} triggers, "
                              f"LLM dispatched: {latest_phase6.get('dispatched_llm', False)}")
                tk = latest_phase6.get("trigger_kinds") or []
                if tk:
                    good_tk = [k for k in tk if k.startswith(("good_", "premature_")) is False or k.startswith("good_")]
                    revisit_tk = [k for k in tk if "missed" in k or "premature" in k or "underwater" in k]
                    if good_tk: lines.append(f"  · **Decisions that paid off (this run):** {', '.join(good_tk)}")
                    if revisit_tk: lines.append(f"  · **Worth revisiting (this run):** {', '.join(revisit_tk)}")
                lines.append("")

            # Latest Reflector LLM output — full summary, watchlist, decision outcomes
            if latest_refl:
                lines.append(f"### Reflector summary (tick {latest_refl.get('tick_id')})")
                lines.append("")
                lines.append(f"> {latest_refl.get('summary', '') or '_(no summary)_'}")
                lines.append("")

                # Decision outcomes scoreboard
                do = latest_refl.get("decision_outcomes_summary") or {}
                if do:
                    lines.append("**Decision outcomes (cumulative, ±5% threshold):**")
                    lines.append("")
                    lines.append("| Category | Count |")
                    lines.append("|---|---|")
                    label_map = {
                        "rejections_followed_by_up_move_5pct": "Rejections followed by ≥+5% (revisit-worthy)",
                        "rejections_followed_by_down_move_5pct": "Rejections followed by ≥-5% (paid off)",
                        "rejections_neutral": "Rejections within ±5% (neutral)",
                        "entries_followed_by_up_move_5pct": "Entries followed by ≥+5% (paid off)",
                        "entries_followed_by_down_move_5pct": "Entries followed by ≥-5% (revisit-worthy)",
                        "exits_followed_by_continued_up_5pct": "Exits followed by continued ≥+5% (revisit-worthy)",
                        "exits_followed_by_down_move_5pct": "Exits followed by ≥-5% drop (paid off)",
                        "high_conviction_optimist_vindicated": "High-conviction Optimist (≥+0.5) vindicated",
                        "high_conviction_optimist_contradicted": "High-conviction Optimist contradicted",
                        "high_conviction_pessimist_vindicated": "High-conviction Pessimist (≤-0.5) vindicated",
                        "high_conviction_pessimist_contradicted": "High-conviction Pessimist contradicted",
                    }
                    for k, label in label_map.items():
                        v = do.get(k)
                        if v is not None:
                            lines.append(f"| {label} | {v} |")
                    if do.get("window_ticks"):
                        lines.append(f"| _window_ticks_ | {do['window_ticks']} |")
                    lines.append("")

                # Watch-list notes (not yet candidates)
                wl = latest_refl.get("notes_for_watchlist") or []
                if wl:
                    lines.append(f"**Watch list ({len(wl)} — single-observation notes; need ≥2 to become candidates):**")
                    lines.append("")
                    for n in wl[:10]:
                        sym = n.get("symbol", "?")
                        obs = (n.get("observation") or "")[:200]
                        why = (n.get("why_not_yet_a_candidate") or "")[:80]
                        lines.append(f"- **{sym}**: {obs}  _({why})_")
                    lines.append("")

            # Split validated + candidates into "decisions that paid off" vs "worth revisiting"
            def _categorize(c):
                k = (c.get("kind") or "")
                if k in ("good_rejection", "good_exit", "good_entry"): return "paid_off"
                if k in ("missed_winner", "premature_exit", "entry_underwater", "over_rejection"): return "revisit"
                return "neutral"

            paid_off_v = [c for c in agg["validated"] if _categorize(c) == "paid_off"]
            revisit_v = [c for c in agg["validated"] if _categorize(c) == "revisit"]
            neutral_v = [c for c in agg["validated"] if _categorize(c) == "neutral"]

            if agg["validated"]:
                lines.append(f"### Validated patterns ({len(agg['validated'])})")
                lines.append("")
                if paid_off_v:
                    lines.append(f"**Decisions that paid off ({len(paid_off_v)})**")
                    lines.append("")
                    lines.append("| Pattern | Lesson | n |")
                    lines.append("|---|---|---|")
                    for c in paid_off_v[:5]:
                        lines.append(f"| {c.get('pattern','')[:120]} | {(c.get('candidate_lesson') or '')[:120]} | {c.get('supporting_count')} |")
                    lines.append("")
                if revisit_v:
                    lines.append(f"**Worth revisiting ({len(revisit_v)})**")
                    lines.append("")
                    lines.append("| Pattern | Lesson | n |")
                    lines.append("|---|---|---|")
                    for c in revisit_v[:5]:
                        lines.append(f"| {c.get('pattern','')[:120]} | {(c.get('candidate_lesson') or '')[:120]} | {c.get('supporting_count')} |")
                    lines.append("")
                if neutral_v:
                    lines.append(f"**Calibration observations ({len(neutral_v)})**")
                    lines.append("")
                    for c in neutral_v[:5]:
                        lines.append(f"- {c.get('candidate_lesson', '')[:160]} (n={c.get('supporting_count')})")
                    lines.append("")

            if agg["candidates"]:
                lines.append(f"### Candidate patterns ({len(agg['candidates'])} — need more confirms)")
                lines.append("")
                lines.append("| Pattern | Supports | Disconfirms |")
                lines.append("|---|---|---|")
                for c in sorted(agg["candidates"], key=lambda x: -x.get("supporting_count", 0))[:10]:
                    lines.append(f"| {c.get('pattern','')[:140]} | {c.get('supporting_count')} | {c.get('disconfirming_count')} |")
                lines.append("")
            if agg["rejected"]:
                lines.append(f"_Patterns that did not hold up (≥3 disconfirms): {len(agg['rejected'])}_")
                lines.append("")
    except Exception as e:
        lines.append(f"## 8. Post-tick reflection (Phase 6)")
        lines.append(f"")
        lines.append(f"_Reflection section unavailable: {e}_")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"_Generated {now.strftime('%Y-%m-%d %H:%M:%S UTC')} — `predictions/fund/report.py`_")

    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quiet", action="store_true", help="Just print the output path")
    args = p.parse_args()
    
    text = build_report()
    now = dt.datetime.utcnow()
    
    # Determine tick number from equity log length
    eq_path = STATE_DIR / "equity.jsonl"
    tick_n = len(eq_path.read_text().splitlines()) if eq_path.exists() else 0
    out_path = REPORTS_DIR / f"{now.strftime('%Y-%m-%d-%H%M')}-tick-{tick_n}.md"
    
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.rename(out_path)
    
    if args.quiet:
        print(out_path)
    else:
        print(text)
        print()
        print(f"--- Saved to {out_path} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
