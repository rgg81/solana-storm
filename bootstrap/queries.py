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


def _sql_values_pairs(pairs: Iterable[tuple]) -> str:
    """Render (pool, grad_time) pairs as a SQL VALUES body:
    `(CAST('pool' AS VARCHAR), TIMESTAMP 'grad_time'), ...`.

    `grad_time` is a 'YYYY-MM-DD HH:MM:SS' string. Raises ValueError if any
    value contains a single quote (defence against a malformed value).
    """
    out = []
    for pool, grad_time in pairs:
        pool_text, time_text = str(pool), str(grad_time)
        if "'" in pool_text or "'" in time_text:
            raise ValueError(
                f"value contains a quote, refusing: {pool_text!r} {time_text!r}"
            )
        out.append(
            f"(CAST('{pool_text}' AS VARCHAR), TIMESTAMP '{time_text}')"
        )
    return ",\n    ".join(out)


def _sql_values_int_pairs(pairs: Iterable[tuple]) -> str:
    """Render (text, integer) pairs as a SQL VALUES body:
    `(CAST('text' AS VARCHAR), <int>), ...`.

    Raises ValueError if the text contains a single quote.
    """
    out = []
    for text_value, int_value in pairs:
        text = str(text_value)
        if "'" in text:
            raise ValueError(f"value contains a quote, refusing: {text!r}")
        out.append(f"(CAST('{text}' AS VARCHAR), {int(int_value)})")
    return ",\n    ".join(out)


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


def outcome_sql(pairs: Iterable[tuple], window_start: str = "2025-11-01") -> str:
    """Outcome label: the pool's reserves at the latest trade inside each
    token's settled T0+12d..T0+16d window.

    `pairs` is a list of (pool_address, graduation_time) where graduation_time
    is a 'YYYY-MM-DD HH:MM:SS' string. The static `window_start` floor lets the
    engine prune the (very large) event tables; the per-token BETWEEN refines
    to the correct fixed horizon. A pool with no trade in its window had no
    activity at ~2 weeks -- the transform treats that as rugged.
    """
    values = _sql_values_pairs(pairs)
    return f"""
WITH targets(pool, grad_time) AS (
    VALUES
    {values}
),
events AS (
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
      AND e.evt_block_time BETWEEN t.grad_time + INTERVAL '12' DAY
                               AND t.grad_time + INTERVAL '16' DAY
    UNION ALL
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
      AND e.evt_block_time BETWEEN t.grad_time + INTERVAL '12' DAY
                               AND t.grad_time + INTERVAL '16' DAY
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


def liquidity_sql(pairs: Iterable[tuple], window_start: str = "2025-11-01") -> str:
    """Liquidity near T0+12h: the pool's reserves at the latest trade inside
    each token's [T0, T0+12h] window.

    `pairs` is a list of (pool_address, graduation_time) strings, as in
    `outcome_sql`. A pool with no trade in the window leaves liq reserves NULL.
    """
    values = _sql_values_pairs(pairs)
    return f"""
WITH targets(pool, grad_time) AS (
    VALUES
    {values}
),
events AS (
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
      AND e.evt_block_time BETWEEN t.grad_time
                               AND t.grad_time + INTERVAL '12' HOUR
    UNION ALL
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
      AND e.evt_block_time BETWEEN t.grad_time
                               AND t.grad_time + INTERVAL '12' HOUR
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


def intraperiod_snapshot_sql(
    pairs: Iterable[tuple],
    snapshot_day_offset: int,
    window_start: str = "2025-11-01",
    static_lower: str = "",
    static_upper: str = "",
) -> str:
    """Pool reserves at the latest trade inside [T0+Nd, T0+(N+1)d].

    The caller MUST ensure `len(pairs) >= 1`; an empty list produces
    invalid SQL (a bare `VALUES` with no rows).

    Same shape as `outcome_sql` and `liquidity_sql`: takes (pool, grad_time)
    pairs, returns one row per pool with the LATEST swap event's resulting
    reserves inside the per-token day-N window. A pool with no trade in
    that window does not appear in the result -- the orchestrator inserts
    NULL reserves so the row still exists.

    Args:
        pairs: list of (pool_address, graduation_time_string) pairs.
        snapshot_day_offset: integer N >= 1; the snapshot is day N after
            graduation, in [T0+Nd, T0+(N+1)d].
        window_start: ISO date floor for partition pruning on the (very
            large) event tables.
        static_lower: optional static lower-bound timestamp string
            'YYYY-MM-DD HH:MM:SS' — when set, adds a tight static
            `e.evt_block_time >= TIMESTAMP '<static_lower>'` that lets
            the query engine skip partitions before the batch's earliest
            window. Derived from the batch's min grad_time + N days.
        static_upper: optional static upper-bound timestamp string
            'YYYY-MM-DD HH:MM:SS' — when set, adds `e.evt_block_time <=
            TIMESTAMP '<static_upper>'` to prune partitions after the
            batch's latest window. Derived from max grad_time + (N+1) days.
    """
    pairs_list = list(pairs)
    values = _sql_values_pairs(pairs_list)
    next_day = snapshot_day_offset + 1

    lower_clause = (
        f"      AND e.evt_block_time >= TIMESTAMP '{static_lower}'\n"
        if static_lower
        else ""
    )
    upper_clause = (
        f"      AND e.evt_block_time <= TIMESTAMP '{static_upper}'\n"
        if static_upper
        else ""
    )

    return f"""
WITH targets(pool, grad_time) AS (
    VALUES
    {values}
),
events AS (
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time, e.evt_block_slot
    FROM pumpdotfun_solana.pump_amm_evt_buyevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
{lower_clause}{upper_clause}      AND e.evt_block_time BETWEEN t.grad_time + INTERVAL '{snapshot_day_offset}' DAY
                               AND t.grad_time + INTERVAL '{next_day}' DAY
    UNION ALL
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time, e.evt_block_slot
    FROM pumpdotfun_solana.pump_amm_evt_sellevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
{lower_clause}{upper_clause}      AND e.evt_block_time BETWEEN t.grad_time + INTERVAL '{snapshot_day_offset}' DAY
                               AND t.grad_time + INTERVAL '{next_day}' DAY
),
ranked AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time, evt_block_slot,
           ROW_NUMBER() OVER (
               PARTITION BY pool ORDER BY evt_block_time DESC
           ) AS rn
    FROM events
)
SELECT pool                       AS pool_address,
       pool_base_token_reserves   AS base_reserve,
       pool_quote_token_reserves  AS quote_reserve,
       evt_block_time             AS event_time,
       evt_block_slot             AS event_slot
FROM ranked
WHERE rn = 1
""".strip()


def bonding_curve_sql(pairs: Iterable[tuple]) -> str:
    """Bonding-curve final state: per token, the last trade event strictly
    before its migration slot.

    `pairs` is a list of (mint, graduation_slot) with an integer slot. The
    "last trade before migration" filter (findings caveat 4 -- by slot, not
    timestamp) and the one-row-per-mint reduction are done in SQL via a
    ROW_NUMBER window. A token has hundreds of bonding-curve trades, so
    returning every trade is a ~hundredfold datapoint blow-up; only the final
    pre-migration state is needed. Rows with a NULL reserve are excluded so the
    surviving last row is the last fully decoded trade.
    """
    values = _sql_values_int_pairs(pairs)
    return f"""
WITH targets(mint, grad_slot) AS (
    VALUES
    {values}
),
trades AS (
    SELECT t.mint,
           e.real_sol_reserves,
           e.real_token_reserves,
           e.virtual_token_reserves,
           e.evt_block_slot,
           ROW_NUMBER() OVER (
               PARTITION BY t.mint ORDER BY e.evt_block_slot DESC
           ) AS rn
    FROM pumpdotfun_solana.pump_evt_tradeevent e
    JOIN targets t ON e.mint = t.mint
    WHERE e.evt_block_slot < t.grad_slot
      AND e.real_sol_reserves IS NOT NULL
      AND e.real_token_reserves IS NOT NULL
      AND e.virtual_token_reserves IS NOT NULL
)
SELECT mint, real_sol_reserves, real_token_reserves,
       virtual_token_reserves, evt_block_slot
FROM trades
WHERE rn = 1
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


def deployer_sql(mints: Iterable[str]) -> str:
    """Deployer signal (first-class), from `pump_amm_evt_createpoolevent` --
    one row per graduation, full coverage. For each target token: its creator
    (`coin_creator`), the creator's count of prior graduations, and the span in
    seconds from the creator's first graduation to this one.

    `deployer_prior_launches` is therefore "prior graduations by this creator"
    -- a fully-covered, history-native deployer track-record signal.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH targets AS (
    SELECT base_mint AS mint, coin_creator, evt_block_time AS grad_time
    FROM pumpdotfun_solana.pump_amm_evt_createpoolevent
    WHERE base_mint IN ({mint_list})
),
history AS (
    SELECT coin_creator, evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_createpoolevent
)
SELECT t.mint            AS mint,
       t.coin_creator    AS deployer_wallet,
       COUNT(*) FILTER (WHERE h.evt_block_time < t.grad_time)
                         AS deployer_prior_launches,
       CAST(date_diff('second', MIN(h.evt_block_time), t.grad_time) AS BIGINT)
                         AS deployer_age_secs
FROM targets t
JOIN history h ON h.coin_creator = t.coin_creator
GROUP BY t.mint, t.coin_creator, t.grad_time
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
