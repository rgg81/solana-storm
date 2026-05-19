"""Per-stage Dune SQL builders.

Each function returns the SQL string for one ETL stage. The SQL bodies come
directly from the spike findings (2026-05-18-historical-bootstrap-spike-
findings.md). Batched stages embed a mint/pool list as a quoted SQL IN(...)
list. Solana base58 addresses contain no quote characters; _sql_in_list
rejects any value that does, as defence against a malformed address breaking
the SQL string.
"""

from __future__ import annotations

from typing import Iterable


def _sql_in_list(values: Iterable[str]) -> str:
    """Render values as a quoted, comma-joined SQL IN-list body.

    Raises ValueError if any value contains a single quote.
    """
    out = []
    for value in values:
        text = str(value)
        if "'" in text:
            raise ValueError(f"value contains a quote, refusing: {text!r}")
        out.append(f"'{text}'")
    return ", ".join(out)


def graduations_sql(window_start: str, settle_cutoff: str) -> str:
    """The graduations list: PumpSwap-era migrations whose outcome is settled.

    window_start  -- ISO date, the PumpSwap-era start (e.g. 2025-11-01).
    settle_cutoff -- ISO date, now - outcome_settle_days; tokens that
                     graduated on/after this are excluded (outcome not settled).
    """
    return f"""
SELECT
    account_mint              AS mint,
    account_pool              AS pool_address,
    account_bonding_curve     AS bonding_curve_address,
    account_lp_mint           AS lp_mint,
    account_user              AS migrator_wallet,
    call_block_time           AS graduation_time,
    call_block_slot           AS graduation_slot
FROM pumpdotfun_solana.pump_call_migrate
WHERE call_block_time >= TIMESTAMP '{window_start}'
  AND call_block_time <  TIMESTAMP '{settle_cutoff}'
ORDER BY call_block_time
""".strip()


def outcome_sql(pools: Iterable[str], window_start: str = "2025-11-01") -> str:
    """Outcome label: the last pool reserves observed for each pool.

    Unions buy/sell events for the pool batch and keeps the latest event per
    pool. The graduations-list query already excludes tokens younger than the
    settle window, so the latest event is effectively the post-horizon
    (settled) state -- see the plan's self-review for the timing rationale.

    window_start limits the scan to PumpSwap-era events only, which greatly
    reduces the data scanned and avoids free-engine timeouts.
    """
    pool_list = _sql_in_list(pools)
    return f"""
WITH events AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent
    WHERE pool IN ({pool_list})
      AND evt_block_time >= TIMESTAMP '{window_start}'
    UNION ALL
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent
    WHERE pool IN ({pool_list})
      AND evt_block_time >= TIMESTAMP '{window_start}'
),
ranked AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time,
           ROW_NUMBER() OVER (
               PARTITION BY pool ORDER BY evt_block_time DESC
           ) AS rn
    FROM events
)
SELECT pool                       AS pool_address,
       pool_base_token_reserves   AS outcome_base_reserve,
       pool_quote_token_reserves  AS outcome_quote_reserve,
       evt_block_time             AS outcome_event_time
FROM ranked
WHERE rn = 1
""".strip()


def liquidity_sql(pools: Iterable[str], window_start: str = "2025-11-01") -> str:
    """Liquidity at ~T0+12h: the last pool reserves for each pool in the batch.

    Same buy/sell-event tables as the outcome query. snapshot timing is
    approximate (spec 5); the merge step keeps the latest event per pool.

    window_start limits the scan to PumpSwap-era events only, which greatly
    reduces the data scanned and avoids free-engine timeouts.
    """
    pool_list = _sql_in_list(pools)
    return f"""
WITH events AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent
    WHERE pool IN ({pool_list})
      AND evt_block_time >= TIMESTAMP '{window_start}'
    UNION ALL
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent
    WHERE pool IN ({pool_list})
      AND evt_block_time >= TIMESTAMP '{window_start}'
),
ranked AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time,
           ROW_NUMBER() OVER (
               PARTITION BY pool ORDER BY evt_block_time DESC
           ) AS rn
    FROM events
)
SELECT pool                       AS pool_address,
       pool_base_token_reserves   AS liq_base_reserve,
       pool_quote_token_reserves  AS liq_quote_reserve,
       evt_block_time             AS liq_event_time
FROM ranked
WHERE rn = 1
""".strip()


def bonding_curve_sql(mints: Iterable[str]) -> str:
    """Bonding-curve final state: all trade events for the mint batch.

    The merge step keeps, per mint, the last row whose evt_block_slot precedes
    the migration slot (findings caveat 4 -- slot, not timestamp, avoids the
    same-tx off-by-one).
    """
    mint_list = _sql_in_list(mints)
    return f"""
SELECT mint,
       real_sol_reserves,
       real_token_reserves,
       virtual_token_reserves,
       evt_block_slot
FROM pumpdotfun_solana.pump_evt_tradeevent
WHERE mint IN ({mint_list})
""".strip()


def contract_flags_sql(mints: Iterable[str]) -> str:
    """Contract flags: whether mint authority was revoked by graduation.

    A setauthority row with authorityType 'MintTokens' and a null newAuthority
    means the mint authority was revoked. freeze_authority_present is a
    constant 0 for the pump.fun cohort (findings 3.4) and is set by transform.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH minted AS (
    SELECT account_mint AS mint
    FROM spl_token_solana.spl_token_call_initializemint2
    WHERE account_mint IN ({mint_list})
),
revokes AS (
    SELECT DISTINCT account_owned AS mint
    FROM spl_token_solana.spl_token_call_setauthority
    WHERE account_owned IN ({mint_list})
      AND authorityType LIKE '%MintTokens%'
      AND newAuthority IS NULL
)
SELECT minted.mint AS mint,
       CASE WHEN revokes.mint IS NULL THEN 1 ELSE 0 END
           AS mint_authority_present
FROM minted
LEFT JOIN revokes ON revokes.mint = minted.mint
""".strip()


def deployer_sql(mints: Iterable[str], max_grad_time: str) -> str:
    """Deployer signal (first-class): prior pump.fun launches and wallet age.

    Unions pump_call_create and pump_call_create_v2 (findings caveat 8), then
    self-joins each target token's creator to that creator's full history --
    count of prior creates and earliest create time.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH creates AS (
    SELECT account_mint, account_user, call_block_time
    FROM pumpdotfun_solana.pump_call_create
    UNION ALL
    SELECT account_mint, account_user, call_block_time
    FROM pumpdotfun_solana.pump_call_create_v2
),
target AS (
    SELECT account_mint, account_user, call_block_time
    FROM creates
    WHERE account_mint IN ({mint_list})
),
history AS (
    SELECT account_user,
           COUNT(*)              AS total_creates,
           MIN(call_block_time)  AS first_create
    FROM creates
    WHERE call_block_time < TIMESTAMP '{max_grad_time}'
    GROUP BY account_user
)
SELECT target.account_mint AS mint,
       target.account_user AS deployer_wallet,
       history.total_creates AS deployer_prior_launches,
       CAST(
           date_diff('second', history.first_create,
                     target.call_block_time) AS BIGINT
       ) AS deployer_age_secs
FROM target
JOIN history ON history.account_user = target.account_user
""".strip()


def holders_sql(mints: Iterable[str], snapshot_time: str) -> str:
    """Holder distribution (best-effort): holder count + top-10/20 share.

    Reconstructs balances from spl_token_transfers up to a single snapshot
    time. This is the high-timeout-risk stage; run.py batches it small and
    NULLs the columns on a DuneTimeout.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH transfers AS (
    SELECT token_mint_address, to_owner AS owner,
           CAST(amount AS DOUBLE) AS amt
    FROM tokens_solana.spl_token_transfers
    WHERE token_mint_address IN ({mint_list})
      AND block_time <= TIMESTAMP '{snapshot_time}'
      AND action = 'transfer'
    UNION ALL
    SELECT token_mint_address, from_owner AS owner,
           -CAST(amount AS DOUBLE) AS amt
    FROM tokens_solana.spl_token_transfers
    WHERE token_mint_address IN ({mint_list})
      AND block_time <= TIMESTAMP '{snapshot_time}'
      AND action = 'transfer'
),
balances AS (
    SELECT token_mint_address, owner, SUM(amt) AS balance
    FROM transfers
    GROUP BY token_mint_address, owner
    HAVING SUM(amt) > 0
),
ranked AS (
    SELECT token_mint_address, balance,
           ROW_NUMBER() OVER (
               PARTITION BY token_mint_address ORDER BY balance DESC
           ) AS rnk
    FROM balances
),
stats AS (
    SELECT token_mint_address,
           COUNT(*)                                        AS holder_count,
           SUM(balance)                                    AS total_supply,
           SUM(CASE WHEN rnk <= 10 THEN balance ELSE 0 END) AS top10_bal,
           SUM(CASE WHEN rnk <= 20 THEN balance ELSE 0 END) AS top20_bal
    FROM ranked
    GROUP BY token_mint_address
)
SELECT token_mint_address                AS mint,
       holder_count                      AS visible_holder_count,
       top10_bal / total_supply          AS top10_concentration,
       top20_bal / total_supply          AS top20_concentration
FROM stats
""".strip()
