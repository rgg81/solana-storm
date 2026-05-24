"""Performance statistics for the Solana Multi-Agent Fund.

Computes per-tick metrics over the full lifetime + recent windows:
- Total return, annualized return
- Sharpe (annualized, daily returns from equity.jsonl)
- Max drawdown
- Hit rate on closed positions
- Avg win % / avg loss %
- Fee drag (total fees as % of deposit)
- Per-symbol P&L attribution (which symbols made/lost money)
- Specialist scoreboard (which agent's recommendations track)

Returns a dict that's serialized into every agent prompt as `performance_state`.
"""
from __future__ import annotations
import json, math, statistics
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(__file__).resolve().parent / "state"
EQUITY_PATH = _STATE_DIR / "equity.jsonl"
TRADES_PATH = _STATE_DIR / "trades.jsonl"
ACCOUNT_PATH = _STATE_DIR / "account.json"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists(): return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _annualize_sharpe(daily_rets: list[float]) -> float:
    """Annualized Sharpe from a series of daily (or per-tick) returns."""
    if len(daily_rets) < 2: return 0.0
    m = sum(daily_rets) / len(daily_rets)
    sd = statistics.stdev(daily_rets)
    if sd == 0: return 0.0
    return (m / sd) * math.sqrt(365)


def compute(prices: dict[str, float] | None = None) -> dict:
    """Compute full performance stats. Pass current prices for unrealized stats."""
    if not ACCOUNT_PATH.exists():
        return {"insufficient_data": True, "reason": "account not initialized"}
    state = json.loads(ACCOUNT_PATH.read_text())
    equity_log = _read_jsonl(EQUITY_PATH)
    trades = _read_jsonl(TRADES_PATH)

    deposit = state["deposit_usd"]
    current_equity = (equity_log[-1]["equity_usd"] if equity_log else deposit)

    # --- Returns / Sharpe / drawdown ---
    sharpe = max_dd_pct = total_return = annual_return = 0.0
    days_running = 0
    if equity_log:
        equities = [e["equity_usd"] for e in equity_log]
        ts0, tsN = equity_log[0]["timestamp"], equity_log[-1]["timestamp"]
        days_running = (tsN - ts0) / 86400 if tsN > ts0 else 0
        # Per-tick returns
        rets = [equities[i] / equities[i-1] - 1.0 for i in range(1, len(equities)) if equities[i-1] > 0]
        sharpe = _annualize_sharpe(rets)
        total_return = current_equity / deposit - 1.0
        if days_running > 0:
            annual_return = (1 + total_return) ** (365 / days_running) - 1
        peak = equities[0]
        dd_running = 0.0
        for e in equities:
            peak = max(peak, e)
            dd = (e / peak - 1.0) if peak > 0 else 0
            dd_running = min(dd_running, dd)
        max_dd_pct = dd_running

    # --- Trade-level stats (closed legs only — i.e., sells with realized_pnl) ---
    closes = [t for t in trades if t.get("side") == "sell" and "realized_pnl_usd" in t]
    n_closed = len(closes)
    winners = [t for t in closes if t["realized_pnl_usd"] > 0]
    losers = [t for t in closes if t["realized_pnl_usd"] < 0]
    hit_rate = len(winners) / n_closed if n_closed else None
    avg_win = sum(t["realized_pnl_usd"] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t["realized_pnl_usd"] for t in losers) / len(losers) if losers else 0
    total_realized_pnl = sum(t["realized_pnl_usd"] for t in closes)
    profit_factor = (sum(t["realized_pnl_usd"] for t in winners) / abs(sum(t["realized_pnl_usd"] for t in losers))
                      if losers else None)

    # --- Cost stats ---
    total_fees = state["total_fees_paid_usd"]
    total_slippage = state["total_slippage_usd"]
    fee_drag_pct = (total_fees + total_slippage) / deposit if deposit > 0 else 0

    # --- Per-symbol P&L attribution ---
    per_symbol = {}
    for t in closes:
        sym = t["ticker"]
        per_symbol.setdefault(sym, {"n_trades": 0, "realized_pnl_usd": 0, "fees_usd": 0})
        per_symbol[sym]["n_trades"] += 1
        per_symbol[sym]["realized_pnl_usd"] += t["realized_pnl_usd"]
        per_symbol[sym]["fees_usd"] += t.get("fee_usd", 0) + t.get("slippage_usd", 0)

    # --- Open positions snapshot (uses current prices if passed) ---
    n_open = sum(1 for h in state["holdings"].values() if h.get("units", 0) > 0)
    open_unrealized = 0
    if prices:
        for tk, h in state["holdings"].items():
            if h.get("units", 0) <= 0: continue
            p = prices.get(tk)
            if p is None: continue
            mv = h["units"] * p
            open_unrealized += mv - h.get("cost_basis_usd", 0)

    return {
        "snapshot_unix": int(equity_log[-1]["timestamp"]) if equity_log else 0,
        "deposit_usd": deposit,
        "current_equity_usd": current_equity,
        "total_return_pct": round(total_return * 100, 2),
        "annualized_return_pct": round(annual_return * 100, 1),
        "days_running": round(days_running, 1),
        "sharpe_ratio_annualized": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd_pct * 100, 2),
        "drawdown_now_pct": round((current_equity / state.get("peak_equity_usd", deposit) - 1) * 100, 2),

        "closed_trades": n_closed,
        "winners": len(winners),
        "losers": len(losers),
        "hit_rate_pct": round(hit_rate * 100, 1) if hit_rate is not None else None,
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "total_realized_pnl_usd": round(total_realized_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor else None,

        "total_fees_usd": round(total_fees, 2),
        "total_slippage_usd": round(total_slippage, 2),
        "fee_drag_pct": round(fee_drag_pct * 100, 2),

        "open_positions_count": n_open,
        "open_unrealized_pnl_usd": round(open_unrealized, 2) if prices else None,

        "per_symbol_pnl": {k: {"n_trades": v["n_trades"],
                                "realized_pnl_usd": round(v["realized_pnl_usd"], 2)}
                            for k, v in per_symbol.items()},
        "tick_count": len(equity_log),
    }


def format_for_agent_prompt(stats: dict) -> str:
    """Render a tight ~10-line block for embedding in agent prompts."""
    if stats.get("insufficient_data"):
        return "FUND_PERFORMANCE: cold start — no trades yet, $10k initial deposit"
    return "\n".join([
        f"FUND_PERFORMANCE (as of tick {stats.get('tick_count', 0)}, {stats.get('days_running', 0)} days running):",
        f"  Equity: ${stats['current_equity_usd']:,.2f} (deposit ${stats['deposit_usd']:,.2f})",
        f"  Total return: {stats['total_return_pct']:+.2f}%  Annualized: {stats['annualized_return_pct']:+.1f}%",
        f"  Sharpe (ann): {stats['sharpe_ratio_annualized']}  Max DD: {stats['max_drawdown_pct']:.2f}%  Current DD: {stats['drawdown_now_pct']:.2f}%",
        f"  Closed trades: {stats['closed_trades']}  Hit rate: {stats['hit_rate_pct']}%  Profit factor: {stats['profit_factor']}",
        f"  Avg win: ${stats['avg_win_usd']}  Avg loss: ${stats['avg_loss_usd']}",
        f"  Total fees+slip: ${stats['total_fees_usd'] + stats['total_slippage_usd']:,.2f}  Drag: {stats['fee_drag_pct']:.2f}%",
        f"  Open positions: {stats['open_positions_count']}  Open unrealized: ${stats['open_unrealized_pnl_usd']}",
        f"  Per-symbol PnL: {stats['per_symbol_pnl']}",
    ])


if __name__ == "__main__":
    s = compute()
    print(json.dumps(s, indent=2, default=str))
    print()
    print(format_for_agent_prompt(s))
