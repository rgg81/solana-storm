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

from predictions import config  # noqa: E402


def _dry_run_payload() -> dict:
    """Load the committed dry-run fixture."""
    fixture = config.DRY_RUN_DIR / "recent_graduations.dry_run.json"
    return json.loads(fixture.read_text())


def _recent_graduations_sql(cutoff_str: str) -> str:
    """Build the SQL for the last UNIVERSE_HOURS_BACK hours of graduations.

    Joins three sources:
    - pump_call_migrate          : graduation event (mint, pool, bonding_curve,
                                   graduation_time, graduation_slot)
    - pump_amm_evt_createpoolevent: deployer wallet + prior-launch count +
                                   deployer age; also carries initial pool
                                   reserves at the createpool event
    - pump_evt_tradeevent        : last bonding-curve trade before migration
                                   -> curve_real_sol_reserves

    A missing createpoolevent or tradeevent row falls back to NULLs (LEFT JOIN).
    curve_completion_time_secs is approximated as seconds from the bonding-curve
    first trade to the migration (NULL when the bonding-curve slot is missing).
    """
    return f"""
WITH grads AS (
    SELECT
        account_mint          AS mint,
        account_pool          AS pool_address,
        account_bonding_curve AS bonding_curve_address,
        account_user          AS migrator_wallet,
        call_block_time       AS graduation_time,
        call_block_slot       AS graduation_slot
    FROM pumpdotfun_solana.pump_call_migrate
    WHERE call_block_time >= TIMESTAMP '{cutoff_str}'
),

-- Deployer wallet, prior launches, deployer age (from createpoolevent)
deployer_raw AS (
    SELECT
        base_mint         AS mint,
        coin_creator      AS deployer_wallet,
        evt_block_time    AS pool_create_time,
        pool_quote_token_reserves AS init_quote_reserve,
        pool_base_token_reserves  AS init_base_reserve
    FROM pumpdotfun_solana.pump_amm_evt_createpoolevent
    WHERE base_mint IN (SELECT mint FROM grads)
),

history AS (
    SELECT coin_creator, evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_createpoolevent
),

deployer_signals AS (
    SELECT
        d.mint,
        d.deployer_wallet,
        d.pool_create_time,
        d.init_quote_reserve,
        d.init_base_reserve,
        COUNT(*) FILTER (WHERE h.evt_block_time < (
            SELECT graduation_time FROM grads g WHERE g.mint = d.mint
        ))                                                 AS deployer_prior_launches,
        CAST(date_diff('second',
            MIN(h.evt_block_time),
            (SELECT graduation_time FROM grads g WHERE g.mint = d.mint)
        ) AS BIGINT)                                       AS deployer_age_secs
    FROM deployer_raw d
    JOIN history h ON h.coin_creator = d.deployer_wallet
    GROUP BY d.mint, d.deployer_wallet, d.pool_create_time,
             d.init_quote_reserve, d.init_base_reserve
),

-- Final bonding-curve state before migration (last trade before grad_slot)
curve_final AS (
    SELECT
        t.mint,
        e.real_sol_reserves                AS curve_real_sol_reserves,
        e.evt_block_slot                   AS curve_last_slot,
        e.evt_block_time                   AS curve_last_time
    FROM pumpdotfun_solana.pump_evt_tradeevent e
    JOIN (
        SELECT grads.mint, grads.graduation_slot,
               e2.evt_block_slot AS last_trade_slot
        FROM grads
        JOIN pumpdotfun_solana.pump_evt_tradeevent e2
            ON e2.mint = grads.mint
           AND e2.evt_block_slot < grads.graduation_slot
           AND e2.real_sol_reserves IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY grads.mint ORDER BY e2.evt_block_slot DESC
        ) = 1
    ) t ON e.mint = t.mint AND e.evt_block_slot = t.last_trade_slot
)

SELECT
    g.mint,
    g.pool_address,
    CAST(to_unixtime(g.graduation_time) AS BIGINT) AS graduation_time_unix,
    COALESCE(d.deployer_wallet, g.migrator_wallet) AS deployer_wallet,
    COALESCE(d.deployer_prior_launches, 0)         AS deployer_prior_launches,
    COALESCE(d.deployer_age_secs, 0)               AS deployer_age_secs,
    COALESCE(d.init_quote_reserve, 0)              AS liq_quote_reserve_lamports,
    COALESCE(d.init_base_reserve, 0)               AS liq_base_reserve_lamports,
    COALESCE(c.curve_real_sol_reserves, 0)         AS curve_real_sol_reserves_lamports,
    COALESCE(
        CAST(date_diff('second', c.curve_last_time, g.graduation_time) AS BIGINT),
        0
    )                                              AS curve_completion_time_secs
FROM grads g
LEFT JOIN deployer_signals d ON d.mint = g.mint
LEFT JOIN curve_final c ON c.mint = g.mint
ORDER BY g.graduation_time DESC
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

    out = []
    for r in rows:
        out.append({
            "mint": str(r.get("mint") or ""),
            "pool_address": str(r.get("pool_address") or ""),
            "graduation_time_unix": _to_int(r.get("graduation_time_unix")),
            "deployer_wallet": str(r.get("deployer_wallet") or ""),
            "deployer_prior_launches": _to_int(r.get("deployer_prior_launches")),
            "deployer_age_secs": _to_int(r.get("deployer_age_secs")),
            "liq_quote_reserve_lamports": _to_int(r.get("liq_quote_reserve_lamports")),
            "liq_base_reserve_lamports": _to_int(r.get("liq_base_reserve_lamports")),
            "curve_real_sol_reserves_lamports": _to_int(r.get("curve_real_sol_reserves_lamports")),
            "curve_completion_time_secs": _to_int(r.get("curve_completion_time_secs")),
        })

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
