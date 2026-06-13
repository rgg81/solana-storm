"""Paper-trading account for the Solana Multi-Agent Fund.

Tracks: cash USD, holdings (units per symbol), every trade with fees+slippage,
equity history, drawdown, per-position cost basis.

State files (gitignored):
- predictions/fund/state/account.json    — current state (atomic write)
- predictions/fund/state/trades.jsonl    — every executed paper trade
- predictions/fund/state/equity.jsonl    — daily equity snapshots
"""
from __future__ import annotations
import json, time, hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(__file__).resolve().parent / "state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNT_PATH = _STATE_DIR / "account.json"
TRADES_PATH = _STATE_DIR / "trades.jsonl"
EQUITY_PATH = _STATE_DIR / "equity.jsonl"
INITIAL_DEPOSIT_USD = 10_000.0
# A position whose market value falls below this is treated as flat (a "full
# close" leftover from float rounding). Below this, a residual is swept on sell
# and ignored by stop-trigger / position-count logic so it can't fire phantom
# stops or register as an open position. See tests/test_dust_sweep.py.
DUST_USD = 0.01


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    tmp.rename(path)


def _append_jsonl(path: Path, row: dict) -> None:
    existing = path.read_text() if path.exists() else ""
    _atomic_write(path, existing + json.dumps(row) + "\n")


def initialize(deposit_usd: float = INITIAL_DEPOSIT_USD) -> dict:
    """Initialize a fresh account. No-op if account.json already exists."""
    if ACCOUNT_PATH.exists():
        return load()
    state = {
        "deposit_usd": deposit_usd,
        "cash_usd": deposit_usd,
        "holdings": {},  # {ticker: {"units": float, "cost_basis_usd": float, "first_buy_unix": int}}
        "created_at": int(time.time()),
        "trade_count": 0,
        "total_fees_paid_usd": 0.0,
        "total_slippage_usd": 0.0,
        "peak_equity_usd": deposit_usd,
        "halted": False,
        "halt_reason": None,
    }
    _atomic_write(ACCOUNT_PATH, json.dumps(state, indent=2))
    return state


def load() -> dict:
    if not ACCOUNT_PATH.exists():
        return initialize()
    return json.loads(ACCOUNT_PATH.read_text())


def save(state: dict) -> None:
    _atomic_write(ACCOUNT_PATH, json.dumps(state, indent=2))


def mark_to_market(state: dict, prices: dict[str, float]) -> dict:
    """Compute equity = cash + sum(holdings.units × current_price).

    When a price is missing for an open position, mark at AVG ENTRY PRICE as
    a conservative fallback (rather than zeroing the position). The mark is
    flagged stale=True so callers know.
    """
    holdings_value = 0.0
    pos = {}
    for ticker, h in state["holdings"].items():
        price = prices.get(ticker)
        units = h["units"]
        if units == 0:
            pos[ticker] = {"units": 0, "current_price": price, "market_value_usd": 0.0,
                            "cost_basis_usd": h.get("cost_basis_usd", 0.0),
                            "unrealized_pnl_usd": 0.0, "unrealized_pnl_pct": 0.0, "stale": False}
            continue
        stale = False
        if price is None:
            # Fall back to avg entry price (mark at cost) so equity doesn't artificially crater
            price = h.get("avg_entry_price_usd") or (h.get("cost_basis_usd", 0) / units if units else 0)
            stale = True
        mv = units * price
        pnl = mv - h.get("cost_basis_usd", 0.0)
        pnl_pct = (pnl / h["cost_basis_usd"]) if h.get("cost_basis_usd") else 0.0
        pos[ticker] = {"units": units, "current_price": price, "market_value_usd": mv,
                        "cost_basis_usd": h["cost_basis_usd"],
                        "unrealized_pnl_usd": pnl, "unrealized_pnl_pct": pnl_pct,
                        "stale": stale}
        holdings_value += mv
    equity = state["cash_usd"] + holdings_value
    drawdown = (equity / state["peak_equity_usd"] - 1.0) if state["peak_equity_usd"] > 0 else 0.0
    if equity > state["peak_equity_usd"]:
        state["peak_equity_usd"] = equity
    return {
        "cash_usd": state["cash_usd"],
        "holdings_value_usd": holdings_value,
        "equity_usd": equity,
        "deposit_usd": state["deposit_usd"],
        "total_return_pct": equity / state["deposit_usd"] - 1.0,
        "drawdown_from_peak_pct": drawdown,
        "peak_equity_usd": state["peak_equity_usd"],
        "positions": pos,
        "deployed_pct": holdings_value / equity if equity > 0 else 0.0,
        # Count only positions worth more than DUST_USD — a float sliver from a
        # full close is not an open position (see tests/test_dust_sweep.py).
        "n_positions": sum(1 for p in pos.values() if p["market_value_usd"] >= DUST_USD),
    }


def execute_trade(state: dict, ticker: str, side: str, usd_amount: float,
                   price_usd: float, fee_pct: float, slippage_pct: float,
                   reason: str = "") -> dict:
    """Apply a paper trade with fees and slippage. Mutates state.
    
    side: 'buy' or 'sell'
    usd_amount: gross USD intended to spend (buy) or receive (sell, before costs)
    price_usd: current mid-price for the symbol
    fee_pct: e.g. 0.003 = 0.3%
    slippage_pct: e.g. 0.005 = 0.5%
    """
    assert side in ("buy", "sell")
    if state.get("halted"):
        return {"executed": False, "reason": "ACCOUNT_HALTED"}
    
    fee_usd = usd_amount * fee_pct
    slippage_usd = usd_amount * slippage_pct
    cost_usd = fee_usd + slippage_usd

    holdings = state["holdings"].setdefault(ticker, {
        "units": 0.0, "cost_basis_usd": 0.0, "avg_entry_price_usd": 0.0,
        "first_buy_unix": 0, "last_buy_unix": 0,
        "stop_loss_price_usd": None, "take_profit_price_usd": None,
        "stop_set_by": None, "stop_set_at_unix": 0,
        "peak_price_since_entry": 0.0,
    })
    
    if side == "buy":
        if state["cash_usd"] < usd_amount:
            return {"executed": False, "reason": "INSUFFICIENT_CASH",
                    "have": state["cash_usd"], "need": usd_amount}
        # A FRESH entry opens from flat (pre-buy units worth < DUST_USD). The holdings
        # dict persists after a full close (dust-sweep zeros units but doesn't delete
        # it), so first_buy_unix / peak_price_since_entry from a PRIOR position would
        # carry over and mislead trailing-stop logic (tick-133: a stale $2.36 peak on
        # the RENDER probe re-opened at $1.80). Reset those on a fresh open; an ADD to
        # an already-open position keeps the original history + the higher running peak.
        fresh_entry = holdings["units"] * price_usd < DUST_USD
        # Effective amount actually getting into position (after fees & slippage)
        effective_usd = usd_amount - cost_usd
        units_bought = effective_usd / price_usd
        state["cash_usd"] -= usd_amount
        holdings["units"] += units_bought
        holdings["cost_basis_usd"] += usd_amount  # full gross — that's what we paid
        # Average entry price (cost-basis-weighted)
        holdings["avg_entry_price_usd"] = (holdings["cost_basis_usd"] / holdings["units"]
                                            if holdings["units"] > 0 else 0)
        now = int(time.time())
        if fresh_entry:
            holdings["first_buy_unix"] = now
            holdings["peak_price_since_entry"] = price_usd
        elif holdings["first_buy_unix"] == 0:
            holdings["first_buy_unix"] = now
        holdings["last_buy_unix"] = now
        units_change = units_bought
    else:  # sell
        # usd_amount = how many USD we WANT to receive (gross). We size the units accordingly.
        # Or alternatively: usd_amount = "sell N USD worth at current price"
        units_to_sell = usd_amount / price_usd
        if holdings["units"] < units_to_sell:
            units_to_sell = holdings["units"]  # sell what we have
            usd_amount = units_to_sell * price_usd
            fee_usd = usd_amount * fee_pct
            slippage_usd = usd_amount * slippage_pct
            cost_usd = fee_usd + slippage_usd
        if units_to_sell == 0:
            return {"executed": False, "reason": "NO_UNITS_TO_SELL"}
        gross_proceeds = usd_amount
        net_proceeds = gross_proceeds - cost_usd
        # Cost basis released proportional to fraction sold
        frac_sold = units_to_sell / holdings["units"] if holdings["units"] > 0 else 1.0
        basis_released = holdings["cost_basis_usd"] * frac_sold
        realized_pnl = net_proceeds - basis_released
        holdings["units"] -= units_to_sell
        holdings["cost_basis_usd"] -= basis_released
        state["cash_usd"] += net_proceeds
        units_change = -units_to_sell
        # Sweep float-rounding dust: if the residual is worth less than DUST_USD,
        # the sell was effectively a full close. Zero the position and clear its
        # stop/TP so the sliver can't re-fire a phantom trigger next tick.
        if 0 < holdings["units"] * price_usd < DUST_USD:
            holdings["units"] = 0.0
            holdings["cost_basis_usd"] = 0.0
            holdings["stop_loss_price_usd"] = None
            holdings["take_profit_price_usd"] = None

    state["trade_count"] += 1
    state["total_fees_paid_usd"] += fee_usd
    state["total_slippage_usd"] += slippage_usd
    trade_id = hashlib.md5(f"{int(time.time())}{ticker}{side}{usd_amount}".encode()).hexdigest()[:12]
    
    trade = {
        "trade_id": trade_id,
        "timestamp": int(time.time()),
        "ticker": ticker, "side": side,
        "usd_amount": usd_amount,
        "price_usd": price_usd,
        "fee_usd": fee_usd,
        "slippage_usd": slippage_usd,
        "total_cost_usd": cost_usd,
        "units_change": units_change,
        "cash_after": state["cash_usd"],
        "reason": reason,
    }
    if side == "sell":
        trade["realized_pnl_usd"] = realized_pnl
    _append_jsonl(TRADES_PATH, trade)
    return {"executed": True, **trade}


def snapshot_equity(state: dict, mtm: dict) -> None:
    """Append a daily equity snapshot (call once per tick)."""
    _append_jsonl(EQUITY_PATH, {
        "timestamp": int(time.time()),
        "equity_usd": mtm["equity_usd"],
        "cash_usd": state["cash_usd"],
        "holdings_value_usd": mtm["holdings_value_usd"],
        "deployed_pct": mtm["deployed_pct"],
        "n_positions": mtm["n_positions"],
        "drawdown_pct": mtm["drawdown_from_peak_pct"],
        "total_fees_to_date": state["total_fees_paid_usd"],
        "total_slippage_to_date": state["total_slippage_usd"],
    })



def set_risk_levels(state: dict, ticker: str, stop_loss_price: float | None = None,
                     take_profit_price: float | None = None, set_by: str = "risk_manager") -> bool:
    """Risk Manager sets stop-loss / take-profit per open position."""
    h = state["holdings"].get(ticker)
    if not h or h.get("units", 0) <= 0:
        return False
    if stop_loss_price is not None:
        h["stop_loss_price_usd"] = float(stop_loss_price)
    if take_profit_price is not None:
        h["take_profit_price_usd"] = float(take_profit_price)
    h["stop_set_by"] = set_by
    h["stop_set_at_unix"] = int(time.time())
    return True


TRIGGERS_LOG_PATH = _STATE_DIR / "stop_triggers.jsonl"


def check_stop_triggers(state: dict, prices: dict[str, float]) -> list[dict]:
    """Return positions whose stop_loss or take_profit was breached at current price.
    
    Note: with our 4h cadence, we only see PRICE AT TICK TIME, not the path.
    A stop that was breached intraday between ticks will trigger at the NEXT tick.
    Realistic vs production-grade stop-loss; acceptable for paper-trading.
    """
    triggered = []
    for ticker, h in state["holdings"].items():
        units = h.get("units", 0)
        if units <= 0: continue
        cur = prices.get(ticker)
        if cur is None: continue
        # Skip sub-dust residuals — a float sliver carrying a stale stop must not
        # fire a phantom trigger (see tests/test_dust_sweep.py).
        if units * cur < DUST_USD: continue
        # Update peak (for trailing stops)
        if cur > h.get("peak_price_since_entry", 0):
            h["peak_price_since_entry"] = cur
        # Stop-loss check
        sl = h.get("stop_loss_price_usd")
        if sl is not None and cur <= sl:
            triggered.append({"ticker": ticker, "trigger": "stop_loss",
                              "current_price": cur, "level": sl, "units": units,
                              "loss_pct": (cur / h.get("avg_entry_price_usd", cur) - 1)})
            continue
        # Take-profit check
        tp = h.get("take_profit_price_usd")
        if tp is not None and cur >= tp:
            triggered.append({"ticker": ticker, "trigger": "take_profit",
                              "current_price": cur, "level": tp, "units": units,
                              "gain_pct": (cur / h.get("avg_entry_price_usd", cur) - 1)})
    # Persist every detection — Risk Mgr verifies, PM executes
    if triggered:
        for ev in triggered:
            ev_with_ts = {"timestamp": int(time.time()), "detected_at_tick": True, **ev}
            _append_jsonl(TRIGGERS_LOG_PATH, ev_with_ts)
    return triggered
