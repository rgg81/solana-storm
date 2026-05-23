"""pump.fun v3 /coins/<mint> helper: rich per-token enrichment for B-phase specialists.

Usage:
    python3 pumpfun_coin_detail.py <mint> [--dry-run]

Output JSON to stdout:
    {"data": {...}, "error": null}

This helper is complementary to `pumpfun_scrape.py` (which hits the same endpoint
but extracts a different / smaller subset under legacy field names retained for
backward compatibility). This helper exposes the raw curve-reserve fields and
computed curve-state derivatives needed by the late_curve and early_curve
specialists during Phase 3 prompt building.

Fields returned in `data`:
    mint, symbol, name, creator_wallet
    bonding_curve_pct                 -- (real_sol_reserves / 1e9) / 85.0 * 100, [0, 100]
    complete                          -- bool, true after migration
    virtual_sol_reserves_lamports     -- raw API
    virtual_token_reserves            -- raw API
    real_sol_reserves_lamports        -- raw API
    real_token_reserves               -- raw API
    total_supply                      -- raw API
    curve_price_sol_per_token         -- virtual_sol_reserves / virtual_token_reserves / 1e9
    market_cap_sol                    -- from API, may be 0
    market_cap_usd                    -- from API
    ath_market_cap_usd                -- from API
    ath_mc_ratio                      -- ath_market_cap_usd / max(market_cap_usd, 1); C1 input
    reply_count                       -- from API
    last_trade_timestamp_unix         -- last_trade_timestamp (ms) // 1000
    last_trade_age_sec                -- now - last_trade_timestamp_unix
    socials                           -- {telegram, twitter, website} strings, "" if absent
    nsfw, is_banned                   -- bool
    fetched_at_unix
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402

from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15

# Pump.fun bonding curve graduates at 85 SOL of real reserves.
_CURVE_GRADUATION_SOL = 85.0
_LAMPORTS_PER_SOL = 1_000_000_000


def _dry_run_payload(mint: str) -> dict:
    fixture = config.DRY_RUN_DIR / "pumpfun_coin_detail.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint
    return payload


def _get_json(url: str) -> dict | list | None:
    """GET with 3-retry exponential backoff. Returns parsed JSON or None on failure."""
    for delay in [0, 1, 3, 9]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "5"))
                time.sleep(retry_after)
                continue
            if r.status_code >= 400:
                return None
            return r.json()
        except Exception:
            continue
    return None


def _as_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_str(v) -> str:
    return str(v or "")


def _live_query(mint: str) -> dict:
    base = config.PUMPFUN_API_BASE.rstrip("/")
    coin = _get_json(f"{base}/coins/{mint}")
    if not isinstance(coin, dict):
        return {"data": None, "error": "pumpfun /coins/ endpoint failed"}

    virtual_sol_lamports = _as_int(coin.get("virtual_sol_reserves"))
    virtual_token = _as_int(coin.get("virtual_token_reserves"))
    real_sol_lamports = _as_int(coin.get("real_sol_reserves"))
    real_token = _as_int(coin.get("real_token_reserves"))
    total_supply = _as_int(coin.get("total_supply"))

    # Bonding curve progress: real_sol_reserves / 85 SOL.
    real_sol = real_sol_lamports / _LAMPORTS_PER_SOL
    bonding_curve_pct = (real_sol / _CURVE_GRADUATION_SOL) * 100.0
    if bonding_curve_pct < 0:
        bonding_curve_pct = 0.0
    if bonding_curve_pct > 100:
        bonding_curve_pct = 100.0

    # Curve price: unit-agnostic ratio of lamports-per-base-unit divided by 1e9.
    # This collapses to a comparable "price" between updates on the same mint.
    if virtual_token > 0:
        curve_price = virtual_sol_lamports / virtual_token / _LAMPORTS_PER_SOL
    else:
        curve_price = 0.0

    market_cap_sol = _as_float(coin.get("market_cap_sol"))
    market_cap_usd = _as_float(coin.get("market_cap"))
    ath_market_cap_usd = _as_float(coin.get("ath_market_cap"))
    ath_mc_ratio = ath_market_cap_usd / max(market_cap_usd, 1.0)

    last_trade_ms = _as_int(coin.get("last_trade_timestamp"))
    last_trade_unix = last_trade_ms // 1000 if last_trade_ms else 0
    now_unix = int(time.time())
    last_trade_age_sec = (now_unix - last_trade_unix) if last_trade_unix else 0

    socials = {
        "telegram": _as_str(coin.get("telegram")),
        "twitter": _as_str(coin.get("twitter")),
        "website": _as_str(coin.get("website")),
    }

    return {
        "data": {
            "mint": mint,
            "symbol": _as_str(coin.get("symbol")),
            "name": _as_str(coin.get("name")),
            "creator_wallet": _as_str(coin.get("creator")),
            "bonding_curve_pct": bonding_curve_pct,
            "complete": bool(coin.get("complete")),
            "virtual_sol_reserves_lamports": virtual_sol_lamports,
            "virtual_token_reserves": virtual_token,
            "real_sol_reserves_lamports": real_sol_lamports,
            "real_token_reserves": real_token,
            "total_supply": total_supply,
            "curve_price_sol_per_token": curve_price,
            "market_cap_sol": market_cap_sol,
            "market_cap_usd": market_cap_usd,
            "ath_market_cap_usd": ath_market_cap_usd,
            "ath_mc_ratio": ath_mc_ratio,
            "reply_count": _as_int(coin.get("reply_count")),
            "last_trade_timestamp_unix": last_trade_unix,
            "last_trade_age_sec": last_trade_age_sec,
            "socials": socials,
            "nsfw": bool(coin.get("nsfw")),
            "is_banned": bool(coin.get("is_banned")),
            "fetched_at_unix": now_unix,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint)
    else:
        payload = _live_query(args.mint)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
