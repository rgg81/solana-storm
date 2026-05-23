"""Pure-Python technical indicators for the Technical Analyst agent.

All functions take a list of closing prices (most recent last) and return
either a single value (latest) or a list aligned with input length.
None is used for warm-up period where there's not enough data.
"""
from __future__ import annotations
import math
import statistics


def sma(prices: list[float], window: int) -> list[float | None]:
    """Simple moving average."""
    out: list[float | None] = [None] * (window - 1)
    if len(prices) < window: return [None] * len(prices)
    for i in range(window - 1, len(prices)):
        out.append(sum(prices[i - window + 1:i + 1]) / window)
    return out


def ema(prices: list[float], window: int) -> list[float | None]:
    """Exponential moving average."""
    if len(prices) < window: return [None] * len(prices)
    k = 2 / (window + 1)
    out: list[float | None] = [None] * (window - 1)
    # Seed with SMA of first `window` prices
    seed = sum(prices[:window]) / window
    out.append(seed)
    for i in range(window, len(prices)):
        out.append(prices[i] * k + out[-1] * (1 - k))
    return out


def rsi(prices: list[float], window: int = 14) -> list[float | None]:
    """Wilder's RSI(14)."""
    if len(prices) <= window: return [None] * len(prices)
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    rsis: list[float | None] = [None] * window
    avg_gain = sum(max(d, 0) for d in deltas[:window]) / window
    avg_loss = sum(-min(d, 0) for d in deltas[:window]) / window
    for i in range(window, len(deltas) + 1):
        if i > window:
            gain = max(deltas[i-1], 0); loss = -min(deltas[i-1], 0)
            avg_gain = (avg_gain * (window - 1) + gain) / window
            avg_loss = (avg_loss * (window - 1) + loss) / window
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100.0 - 100.0 / (1 + rs))
    return rsis


def macd(prices: list[float], fast: int = 12, slow: int = 26,
          signal: int = 9) -> dict:
    """MACD line, signal line, histogram. Returns latest values."""
    ef = ema(prices, fast)
    es = ema(prices, slow)
    macd_line: list[float | None] = []
    for f, s in zip(ef, es):
        macd_line.append(f - s if (f is not None and s is not None) else None)
    # Signal = EMA of MACD line (only over non-None values)
    valid_macd = [v for v in macd_line if v is not None]
    sig = ema(valid_macd, signal) if len(valid_macd) >= signal else []
    sig_latest = sig[-1] if sig else None
    macd_latest = macd_line[-1] if macd_line else None
    hist = (macd_latest - sig_latest) if (macd_latest is not None and sig_latest is not None) else None
    return {"macd": macd_latest, "signal": sig_latest, "histogram": hist}


def bollinger(prices: list[float], window: int = 20, n_std: float = 2.0) -> dict:
    """Bollinger bands (lower, mid, upper) plus %B (where in band)."""
    if len(prices) < window: return {"lower": None, "mid": None, "upper": None, "pct_b": None}
    recent = prices[-window:]
    mid = sum(recent) / window
    sd = statistics.stdev(recent) if window > 1 else 0
    upper = mid + n_std * sd
    lower = mid - n_std * sd
    pct_b = (prices[-1] - lower) / (upper - lower) if upper > lower else 0.5
    return {"lower": lower, "mid": mid, "upper": upper, "pct_b": pct_b}


def atr(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float | None:
    """Average True Range (volatility)."""
    if len(closes) <= window: return None
    trs = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-window:]) / window if len(trs) >= window else None


def daily_returns(prices: list[float]) -> list[float]:
    return [prices[i+1] / prices[i] - 1.0 for i in range(len(prices)-1)]


def volatility(prices: list[float], window: int = 30) -> float | None:
    """Standard deviation of daily returns over trailing window."""
    if len(prices) < window + 1: return None
    rets = daily_returns(prices[-(window+1):])
    return statistics.stdev(rets) if len(rets) > 1 else None


def summarize(prices: list[float]) -> dict:
    """All-in-one snapshot for an agent prompt: every indicator at current bar."""
    if len(prices) < 30:
        return {"insufficient_data": True, "n_bars": len(prices)}
    cur = prices[-1]
    sma20 = sma(prices, 20)[-1]
    sma50 = sma(prices, 50)[-1] if len(prices) >= 50 else None
    ema12 = ema(prices, 12)[-1]
    ema26 = ema(prices, 26)[-1]
    rsi14 = rsi(prices, 14)[-1]
    macd_d = macd(prices, 12, 26, 9)
    bb = bollinger(prices, 20, 2.0)
    vol30 = volatility(prices, 30)
    # Simple trend label
    trend = None
    if sma20 and sma50:
        if cur > sma20 > sma50: trend = "strong_up"
        elif cur > sma20: trend = "up"
        elif cur < sma20 < sma50: trend = "strong_down"
        elif cur < sma20: trend = "down"
        else: trend = "flat"
    # Recent move
    ret_1d = prices[-1] / prices[-2] - 1.0 if len(prices) >= 2 else None
    ret_7d = prices[-1] / prices[-8] - 1.0 if len(prices) >= 8 else None
    ret_30d = prices[-1] / prices[-31] - 1.0 if len(prices) >= 31 else None
    return {
        "current_price": cur,
        "trend": trend,
        "ret_1d_pct": (ret_1d * 100) if ret_1d is not None else None,
        "ret_7d_pct": (ret_7d * 100) if ret_7d is not None else None,
        "ret_30d_pct": (ret_30d * 100) if ret_30d is not None else None,
        "sma20": sma20, "sma50": sma50,
        "ema12": ema12, "ema26": ema26,
        "rsi14": rsi14,
        "macd": macd_d,
        "bollinger": bb,
        "volatility_30d_daily": vol30,
        "price_vs_sma20_pct": ((cur / sma20 - 1) * 100) if sma20 else None,
    }


if __name__ == "__main__":
    import json
    # Self-test on a synthetic uptrend
    prices = [100 + i * 0.5 + (i % 5) for i in range(60)]
    print(json.dumps(summarize(prices), indent=2, default=str))
