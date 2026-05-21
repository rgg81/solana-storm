"""Dune query helper: last N hours of pump.fun graduations.

Reads UNIVERSE_HOURS_BACK from predictions.config. Returns JSON to stdout:
    {"data": [...], "error": null}  on success
    {"data": null, "error": "<msg>"}  on failure

The helper does NOT raise on errors -- the skill consumes the JSON and
decides whether to proceed based on the error field.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the repo root importable for `bootstrap.*` and `predictions.config`.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env so DUNE_API_KEY is available when bootstrap.config.load_config() runs.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on environment having the key

from predictions import config  # noqa: E402


def _dry_run_payload() -> dict:
    """Load the committed dry-run fixture."""
    fixture = config.DRY_RUN_DIR / "recent_graduations.dry_run.json"
    return json.loads(fixture.read_text())


def _recent_graduations_sql(cutoff_str: str) -> str:
    """Build the SQL for the last UNIVERSE_HOURS_BACK hours of graduations.

    MINIMAL VERSION (2026-05-21, post-double-timeout): every join we tried
    (pump_evt_tradeevent for curve reserves, pump_amm_evt_createpoolevent
    for deployer history, pump_amm_evt_buy/sellevent for entry reserves)
    timed out on Dune's 2-minute free-engine limit even with IN-clause
    filters. The buy/sellevent tables are too large for any kind of scan
    even when narrowed to ~200 pools.

    This version queries ONE small table only -- `pump_call_migrate` --
    and returns just the graduation facts: mint, pool, graduation_time,
    migrator_wallet. ~200 rows for a 24h window, completes in seconds.

    Reserves + deployer signals are deliberately omitted (set to 0). The
    skill's Phase 2 deep-enrich step picks them up directly via Helius RPC
    (`helius_trade_flow.py` + `audit_outcome.py` both fetch live pool
    state via `getTokenAccountsByOwner`). Phase 2 also skips the
    reserve-based prefilter when this helper returns 0 reserves -- it
    instead caps the shortlist at SHORTLIST_MAX most-recent graduations
    and lets the deep-enrich Helius calls discover real liquidity per
    token. That's slightly more Helius credits per run but well within
    the free tier (50 tokens x 4 calls = 200 credits vs the 100k/day cap).
    """
    return f"""
SELECT
    account_mint                                    AS mint,
    account_pool                                    AS pool_address,
    CAST(to_unixtime(call_block_time) AS BIGINT)    AS graduation_time_unix,
    account_user                                    AS deployer_wallet,
    CAST(0 AS BIGINT)                               AS deployer_prior_launches,
    CAST(0 AS BIGINT)                               AS deployer_age_secs,
    CAST(0 AS BIGINT)                               AS liq_quote_reserve_lamports,
    CAST(0 AS BIGINT)                               AS liq_base_reserve_lamports,
    CAST(0 AS BIGINT)                               AS curve_real_sol_reserves_lamports,
    CAST(0 AS BIGINT)                               AS curve_completion_time_secs
FROM pumpdotfun_solana.pump_call_migrate
WHERE call_block_time >= TIMESTAMP '{cutoff_str}'
ORDER BY call_block_time DESC
LIMIT 1000
""".strip()


def _live_query() -> dict:
    """Run the actual Dune query for the recent graduations cohort.

    Reuses `bootstrap.dune_client.DuneClient` and the same table sources as
    `bootstrap.queries` (pump_call_migrate, pump_amm_evt_createpoolevent,
    pump_evt_tradeevent). Filters to the last UNIVERSE_HOURS_BACK hours.
    """
    try:
        from bootstrap.dune_client import DuneClient
        from bootstrap.config import load_config as load_bootstrap_config
    except Exception as e:
        return {"data": None, "error": f"bootstrap import failed: {e}"}

    try:
        bcfg = load_bootstrap_config()
    except Exception as e:
        return {"data": None, "error": f"bootstrap config failed: {e}"}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.UNIVERSE_HOURS_BACK)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    sql = _recent_graduations_sql(cutoff_str)

    try:
        client = DuneClient(bcfg)
        rows, _credits = client.run_sql(sql)
    except Exception as e:
        return {"data": None, "error": f"dune query failed: {e}"}

    # `pump_call_migrate` can return multiple rows per migration (e.g.,
    # one per migrator-wallet participant). Dedupe by mint, keeping the
    # most recent row per mint.
    by_mint: dict[str, dict] = {}
    for r in rows:
        mint = str(r.get("mint") or "")
        if not mint:
            continue
        row = {
            "mint": mint,
            "pool_address": str(r.get("pool_address") or ""),
            "graduation_time_unix": _to_int(r.get("graduation_time_unix")),
            "deployer_wallet": str(r.get("deployer_wallet") or ""),
            "deployer_prior_launches": _to_int(r.get("deployer_prior_launches")),
            "deployer_age_secs": _to_int(r.get("deployer_age_secs")),
            "liq_quote_reserve_lamports": _to_int(r.get("liq_quote_reserve_lamports")),
            "liq_base_reserve_lamports": _to_int(r.get("liq_base_reserve_lamports")),
            "curve_real_sol_reserves_lamports": _to_int(r.get("curve_real_sol_reserves_lamports")),
            "curve_completion_time_secs": _to_int(r.get("curve_completion_time_secs")),
        }
        existing = by_mint.get(mint)
        if existing is None or row["graduation_time_unix"] > existing["graduation_time_unix"]:
            by_mint[mint] = row

    # Return in chronological order (most recent first).
    out = sorted(by_mint.values(), key=lambda r: -r["graduation_time_unix"])
    return {"data": out, "error": None}


def _to_int(v) -> int:
    """Coerce a value to int, returning 0 on None/failure."""
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query Dune for pump.fun graduations in the last 24h."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Return canned fixture, don't hit Dune.")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload()
    else:
        payload = _live_query()

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
