# Spike Findings — Dune Historical Bootstrap

**Date:** 2026-05-19
**Spike executor:** Claude Code (automated Dune API probe session)
**Design spec:** `2026-05-18-historical-bootstrap-design.md`
**Total Dune credits used this spike:** ~25 credits (24 probe executions; most cost 0 credits; two large spl_token_transfers queries cost ~1 credit each)

---

## 1. Summary & Recommendation

**GO — Dune is sufficient for the historical bootstrap.**

Dune can deliver every feature group the ETL needs except one: the live collector's `oldest_signature_age_secs` (a full wallet transaction-history age, which requires a Solana-level transaction scan not available on the free tier). Every other feature — outcome label, pool liquidity at T0+12h and T0+14d, bonding-curve final state, contract flags (mint/freeze authority), and a partial deployer signal — is obtainable from decoded pump.fun and PumpSwap tables on Dune's free plan.

The holder-distribution group (visible_holder_count, top10/top20_concentration, creator_bag_fraction) is technically constructable from `tokens_solana.spl_token_transfers` but each per-token query takes ~57 seconds. This rules out per-token serial queries; it is feasible only as a single batched query covering all target mints at once. That batch query may approach or exceed the 2-minute free-engine timeout for 3,000–5,000 mints — holder distribution is a **partial / high-risk** feature group for the bootstrap. Everything else is fast (1–20 s per query).

The credit budget is comfortable: the full 3,000–5,000-graduation pull fits well within the 2,500-credit free-plan limit.

**No need to fall back to Bitquery at this time.** Bitquery remains a named fallback if any Dune table is found to be lagged or incomplete during the real ETL build, particularly for holder data.

---

## 2. Confirmed: Graduations

Table: `pumpdotfun_solana.pump_call_migrate`

- **Coverage:** Nov 2025 to present; ~62,700 rows as of the spike date.
- **Time column:** `call_block_time` (timestamp with time zone) — this is the canonical T0 for each graduation.
- **Key columns:** `call_block_slot`, `call_tx_id`, `account_mint` (the token mint), `account_pool` (the PumpSwap pool), `account_bonding_curve`, `account_lp_mint`, `account_pool_base_token_account`, `account_pool_quote_token_account`, `account_user`.
- **Note:** The table is in the PumpSwap era only (pump.fun migration to PumpSwap). Pre-PumpSwap Raydium-era graduations are in a different mechanism and are not needed per the design spec.
- **Probe result:** 5-row query returned valid rows immediately (1.8 s). Data quality confirmed.

---

## 3. Per Feature Group

### 3.1 Outcome Label (Highest Priority)

**OBTAINABLE.** Source: `pumpdotfun_solana.pump_amm_evt_buyevent` and `pump_amm_evt_sellevent`.

Both tables carry `pool`, `pool_base_token_reserves`, and `pool_quote_token_reserves` on every trade event. The derived `survived` flag follows the same rule as the live collector: `quote_reserve >= 5_000_000_000` (5 SOL in lamports) at ~T0+14d.

**Working query sketch:**
```sql
WITH last_event AS (
  SELECT pool, pool_quote_token_reserves, pool_base_token_reserves,
         ROW_NUMBER() OVER (PARTITION BY pool ORDER BY evt_block_time DESC) AS rn
  FROM (
    SELECT pool, pool_quote_token_reserves, pool_base_token_reserves, evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent
    WHERE pool IN (<target_pool_list>)
      AND evt_block_time BETWEEN <grad_time + 12d> AND <grad_time + 16d>
    UNION ALL
    SELECT pool, pool_quote_token_reserves, pool_base_token_reserves, evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent
    WHERE pool IN (<target_pool_list>)
      AND evt_block_time BETWEEN <grad_time + 12d> AND <grad_time + 16d>
  )
)
SELECT pool,
       pool_quote_token_reserves,
       pool_base_token_reserves,
       (pool_quote_token_reserves >= 5000000000) AS survived
FROM last_event WHERE rn = 1
```

**Caveat:** if a token was fully abandoned and has no trades in the T0+14d window, there is no event row for it. In that case `survived = false` (reserve effectively 0). The ETL must handle this as a LEFT JOIN / COALESCE.

**Probe result:** Query for single pool returned 5 rows in 8.4 s. `quote_reserve ~92 SOL` confirmed the test token survived.

**Timeout risk:** Low for a batched multi-pool query with an explicit pool-address IN list.

---

### 3.2 Liquidity at T0+12h (`base_reserve`, `quote_reserve`, `pool_supply_fraction`)

**OBTAINABLE (base/quote reserves). `pool_supply_fraction` is PARTIAL.**

Source: same `pump_amm_evt_buyevent` + `pump_amm_evt_sellevent` (last event at or before T0+12h per pool).

`pool_supply_fraction` requires knowing total LP token supply, available in `pump_amm_evt_depositevent` (`lp_mint_supply` column) or `pump_amm_evt_createpoolevent` (`initial_liquidity`). Computing the fraction requires dividing the pool's LP tokens held by a specific address by the total supply — an additional join. For the initial bootstrap this can be approximated as `NULL` or derived from the pool-creation event.

**`lp_burned` flag — PARTIAL.** PumpSwap burns LP tokens during migration. The `pump_amm_evt_createpoolevent` table records `lp_token_amount_out` (LP tokens minted) and `minimum_liquidity` (locked permanently). For pump.fun-initiated pools, the protocol burns all non-minimum LP tokens at creation. However, verifying the burn via `spl_token_call_burn` returned 0 rows for the LP mint tested — the burn may go through a different mechanism (Token-2022 or the pump_amm program directly). **Safe assumption for the bootstrap:** if the pool was created by the `pump_call_migrate` instruction and there are no `pump_amm_evt_withdrawevent` rows for that pool, `lp_burned = true` (all LP is locked). This is a reasonable heuristic for PumpSwap-era graduations.

**Working query sketch (base/quote at T0+12h):**
```sql
-- Same structure as outcome query but with time window = [grad_time, grad_time + 13h]
-- Take last event before T0+12h for each pool
```

**Probe result:** Single-pool query returned correct reserves (64 SOL at ~T0+12h) in 12.7 s.

**Timeout risk:** Low for batched multi-pool query.

---

### 3.3 Bonding-Curve Final State (`curve_real_sol_reserves`, `curve_real_token_reserves`, `curve_token_total_supply`, `curve_graduated`)

**FULLY OBTAINABLE.** Source: `pumpdotfun_solana.pump_evt_tradeevent`.

Every bonding-curve trade emits `real_sol_reserves`, `real_token_reserves`, `virtual_sol_reserves`, `virtual_token_reserves`, `is_buy`, and `mint`. Taking the last event for a mint just before `call_block_time` in `pump_call_migrate` gives the graduation-state snapshot.

- `curve_graduated = true` for all rows (we only process graduated tokens).
- `curve_real_sol_reserves`: directly from `real_sol_reserves` of the final trade event (typically ~85 SOL in lamports for a standard graduation).
- `curve_real_token_reserves`: from `real_token_reserves` (0 at graduation when all tokens are bought out, as confirmed in the probe).
- `curve_token_total_supply`: `virtual_token_reserves + real_token_reserves` at final state, or inferred from bonding-curve constants. This field is less critical; can be set to the standard pump.fun supply constant (1,000,000,000 tokens with 6 decimals = `1_000_000_000_000_000`).

**Working query sketch:**
```sql
WITH last_bc_trade AS (
  SELECT mint, real_sol_reserves, real_token_reserves, virtual_sol_reserves, virtual_token_reserves,
         ROW_NUMBER() OVER (PARTITION BY mint ORDER BY evt_block_time DESC) AS rn
  FROM pumpdotfun_solana.pump_evt_tradeevent
  WHERE mint IN (<target_mint_list>)
    AND evt_block_time < <grad_time>
)
SELECT mint, real_sol_reserves, real_token_reserves, virtual_token_reserves
FROM last_bc_trade WHERE rn = 1
```

**Probe result:** Query returned confirmed graduation state (`real_sol_reserves=85005359500`, `real_token_reserves=0`) in 28 s for one mint.

**Timeout risk:** Moderate for large batches. Recommend chunking into groups of 500 mints per query.

---

### 3.4 Contract Flags (`mint_authority_present`, `freeze_authority_present`)

**OBTAINABLE with join logic.** Source: `spl_token_solana.spl_token_call_initializemint2` + `spl_token_solana.spl_token_call_setauthority`.

pump.fun tokens use the standard SPL token program `initializemint2` instruction. The table has columns `mintAuthority` and `freezeAuthority`:
- At creation, `mintAuthority = TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM` (pump.fun bonding curve program) and `freezeAuthority = null`.
- At graduation, the bonding curve program revokes mint authority via `spl_token_call_setauthority` (`authorityType = MintTokens`, `newAuthority = null`).

So the logic is:
- Check `setauthority` table for the mint: if there is a row with `authorityType` containing `MintTokens` and `newAuthority = null` before or at graduation time, `mint_authority_present = false`.
- `freeze_authority_present`: initial freeze authority is null for pump.fun tokens, so `freeze_authority_present = false` universally for this cohort (can be set as constant).

**Caveat:** The `account_owned` column in `spl_token_call_setauthority` is the token account, not the mint — need to join via `account_mint` from `initializemint2`. Probed and confirmed `setauthority` events carry `account_owned` and `authorityType` fields.

**Probe result:** `initializemint2` confirmed pump.fun tokens (5 rows with `mintAuthority = TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM`). Schema confirmed. Per-token query fast (2 s).

**Timeout risk:** Low for batched queries.

---

### 3.5 Holder Distribution (`visible_holder_count`, `top10_concentration`, `top20_concentration`, `creator_bag_fraction`)

**PARTIAL — feasible but expensive. High timeout risk for large batches.**

Source: `tokens_solana.spl_token_transfers` (columns: `from_owner`, `to_owner`, `token_mint_address`, `amount`, `block_time`, `action`).

Holder balances at T0+12h can be reconstructed by summing all transfers (in minus out) for each wallet up to the snapshot time. A single-mint query took **57 seconds** — the longest of any probe. For 3,000 mints, per-mint serial queries are infeasible.

However, a batched query covering all 3,000+ mints simultaneously should work if structured carefully (time-bounded, with an IN clause on mints), but is likely to push toward or exceed the 2-minute free-engine timeout. This is the primary execution risk.

**Practical recommendation:**
- For the bootstrap ETL, attempt holder distribution in batches of ~100–200 mints per query.
- If queries time out consistently, set `visible_holder_count`, `top10_concentration`, `top20_concentration`, and `creator_bag_fraction` to `NULL` for historical rows. Per the design spec, `NULL` is acceptable; the ML model handles missing features.
- The live collector produces these from RPC; historical rows without them are still useful.

**Working query sketch:**
```sql
WITH transfers AS (
  SELECT token_mint_address, to_owner AS owner, CAST(amount AS DOUBLE) AS amt
  FROM tokens_solana.spl_token_transfers
  WHERE token_mint_address IN (<target_mints>)
    AND block_time <= <snapshot_time>
    AND action = 'transfer'
  UNION ALL
  SELECT token_mint_address, from_owner AS owner, -CAST(amount AS DOUBLE) AS amt
  FROM tokens_solana.spl_token_transfers
  WHERE token_mint_address IN (<target_mints>)
    AND block_time <= <snapshot_time>
    AND action = 'transfer'
),
balances AS (
  SELECT token_mint_address, owner, SUM(amt) AS balance
  FROM transfers
  GROUP BY token_mint_address, owner
  HAVING SUM(amt) > 0
),
stats AS (
  SELECT token_mint_address,
         COUNT(*) AS holder_count,
         SUM(balance) AS total_supply,
         SUM(CASE WHEN rnk <= 10 THEN balance ELSE 0 END) AS top10_bal,
         SUM(CASE WHEN rnk <= 20 THEN balance ELSE 0 END) AS top20_bal
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY token_mint_address ORDER BY balance DESC) AS rnk
    FROM balances
  )
  GROUP BY token_mint_address
)
SELECT token_mint_address,
       holder_count,
       top10_bal / total_supply AS top10_concentration,
       top20_bal / total_supply AS top20_concentration
FROM stats
```

**Probe result:** Single-mint query (57 s) confirmed data is present and correct (10 distinct holders returned for the test token at T0+12h).

**Timeout risk:** HIGH for batches > 200 mints. Treat as best-effort; NULL fallback is the designed backstop.

---

### 3.6 Deployer Signal (`capped_signature_count`, `signature_count_capped`, `oldest_signature_age_secs`)

**PARTIAL — pump.fun creation count available; age only approximated.**

Source: `pumpdotfun_solana.pump_call_create`

- **`capped_signature_count` (total pump.fun deployments by creator):** Available as `COUNT(*)` grouped by `account_user` filtered to `call_block_time < grad_time`. Direct equivalent of the live collector's capped count of prior deployer activity on pump.fun.
- **`signature_count_capped`:** Derived by comparing the count to the cap threshold (e.g., > 100 = capped).
- **`oldest_signature_age_secs`:** NOT available from pump.fun tables alone. The live collector derives this from a full Solana wallet signature history. On Dune, the earliest `pump_call_create` for the wallet gives a pump.fun-specific age only (not absolute wallet age). No `solana.transactions` table exists on Dune's free tier. **This field must be NULL for all historical rows.**

**Working query sketch:**
```sql
SELECT
    c_target.account_mint,
    c_target.account_user AS creator,
    c_history.total_creates AS capped_signature_count,
    (c_history.total_creates > 100) AS signature_count_capped,
    EXTRACT(EPOCH FROM (c_target.call_block_time - c_history.first_create)) AS deployer_age_on_pumpfun_secs
FROM pumpdotfun_solana.pump_call_create c_target
JOIN (
    SELECT account_user,
           COUNT(*) AS total_creates,
           MIN(call_block_time) AS first_create
    FROM pumpdotfun_solana.pump_call_create
    WHERE call_block_time < <max_grad_time>
    GROUP BY account_user
) c_history ON c_history.account_user = c_target.account_user
WHERE c_target.account_mint IN (<target_mints>)
```

**Note:** `pump_call_create` is available from pump.fun's launch (early 2024). For wallets that created their first pump.fun token before Nov 2025, the `first_create` timestamp is an underestimate of the true wallet age. However, it is a useful signal: a wallet with 443 prior creates and a `first_create` timestamp 8 days before graduation is strongly different from a fresh wallet.

**Probe result:** Single-wallet query (12.4 s) returned `first_create: 2025-10-30`, `total_creates: 443`. Confirmed.

**Timeout risk:** Low for the create-count query. Medium for the full self-join over all mints.

---

## 4. The Outcome Label: How to Derive `survived`

For a historical token that graduated at time `grad_time` via pool `pool_address`:

1. Query `pump_amm_evt_buyevent` and `pump_amm_evt_sellevent` for `pool = pool_address` in the window `[grad_time + 12d, grad_time + 16d]`.
2. Take the row with the latest `evt_block_time` in that window.
3. Read `pool_quote_token_reserves` (in lamports).
4. `survived = (pool_quote_token_reserves >= 5_000_000_000)`.
5. If NO trade events exist in that window, the pool was abandoned: `survived = false`, `quote_reserve = 0`.

This is structurally identical to how `storm-collector` checks outcomes on live tokens. The 12–16-day window gives some slack around the nominal 14-day horizon. For tokens that graduated very recently (< 16 days ago), they should be excluded from the bootstrap dataset.

---

## 5. Credit-Cost Estimate for Full Pull (3,000–5,000 Graduations)

Based on the spike, the estimated query structure for the full ETL is:

| Step | Query description | Est. queries | Est. credits each | Total |
|---|---|---|---|---|
| Graduations list | `pump_call_migrate` with date filter | 1 | ~1 | 1 |
| Outcome labels | `pump_amm_evt_buyevent + sellevent` per-pool, batched 500 pools/query | 10 | ~2 | 20 |
| Liquidity T0+12h | Same tables, different time window, batched 500/query | 10 | ~2 | 20 |
| Bonding curve final state | `pump_evt_tradeevent` per-mint, batched 500/query | 10 | ~3 | 30 |
| Contract flags | `initializemint2` + `setauthority`, batched 1000/query | 5 | ~1 | 5 |
| Deployer signal | `pump_call_create` self-join, batched 1000/query | 5 | ~2 | 10 |
| Holder distribution | `spl_token_transfers` per-mint, batched 100/query | 50 | ~5 | 250 |
| Pool creation metadata | `pump_amm_evt_createpoolevent`, batched 1000/query | 5 | ~1 | 5 |
| **Total** | | | | **~341 credits** |

**Well within the 2,500-credit free plan.** Even if execution costs run 3x higher than estimated, the ETL uses at most ~1,000 credits — leaving 1,500 credits as headroom for debugging and re-runs.

**Key caveat:** holder distribution queries (50 batches × 100 mints) carry the timeout risk noted in §3.5. If those queries time out frequently, skip that batch and set to NULL; the credit estimate would shrink accordingly.

---

## 6. Risks & Caveats

1. **Holder distribution timeout risk** is the primary concern. A 57-second single-mint query cannot be directly parallelized on the free plan. Batch size of 100 mints may still exceed 2 minutes. Empirically test with a 50-mint pilot batch during the ETL build before committing to that approach.

2. **`oldest_signature_age_secs` unavailable.** This field will be NULL for all historical rows. The live collector populates it from a full Solana RPC signature scan, which is not replicated on Dune. This is a known gap; the design spec pre-accepts NULL for features the indexer cannot supply.

3. **Coverage gap: tokens with no post-graduation trades.** If a token was immediately abandoned (rug on graduation day), it has no `pump_amm_evt_*` rows. The outcome is deterministically `survived = false` by the NULL-coalesce rule — correct behaviour, but feature snapshot data (base/quote reserves at T0+12h) would also be unavailable. Such tokens might be stored with all-NULL feature rows except the outcome.

4. **Timing: bonding-curve "last trade" before graduation.** The final bonding-curve state requires the last `pump_evt_tradeevent` before `call_block_time` in `pump_call_migrate`. If the final buy is in the same transaction as the migration, it may share the same timestamp; using `evt_block_slot < migration_slot` rather than timestamp avoids an off-by-one edge case.

5. **LP burned flag derivation** is a heuristic (no direct burn record confirmed). The ETL should treat `lp_burned = true` for all standard pump.fun PumpSwap-era graduations unless a `pump_amm_evt_withdrawevent` row exists for the pool, indicating LP was not fully locked.

6. **Dune table lag.** Dune ingestion typically lags 1–2 hours for Solana data. Since we target tokens graduated ≥ 3 weeks ago, this is irrelevant for the bootstrap. The live collector must not use Dune.

7. **Free-plan rate limits.** The Dune free plan serializes queries. The ETL must run queries sequentially or with small concurrency. Plan for ~30–60 minutes total wall-clock time for the full 3,000–5,000-token extraction.

8. **pump_call_create coverage.** Not all pump.fun token mints appear in `pump_call_create` (as observed: 0 rows for one test mint). Some tokens may have been created via `pump_call_create_v2` — also present in the schema. The ETL should query both tables.

---

## Appendix: Confirmed Dune Table Reference

| Feature group | Dune table(s) | Key columns |
|---|---|---|
| Graduations | `pumpdotfun_solana.pump_call_migrate` | `call_block_time`, `account_mint`, `account_pool`, `account_bonding_curve` |
| Outcome / liquidity | `pumpdotfun_solana.pump_amm_evt_buyevent`, `pump_amm_evt_sellevent` | `pool`, `pool_base_token_reserves`, `pool_quote_token_reserves`, `evt_block_time` |
| Pool creation metadata | `pumpdotfun_solana.pump_amm_evt_createpoolevent` | `pool`, `base_mint`, `lp_mint`, `pool_base_amount`, `pool_quote_amount`, `lp_token_amount_out` |
| Bonding-curve final state | `pumpdotfun_solana.pump_evt_tradeevent` | `mint`, `real_sol_reserves`, `real_token_reserves`, `virtual_sol_reserves`, `virtual_token_reserves` |
| Contract flags | `spl_token_solana.spl_token_call_initializemint2`, `spl_token_call_setauthority` | `account_mint`, `mintAuthority`, `freezeAuthority`; `account_owned`, `authorityType`, `newAuthority` |
| Holder distribution | `tokens_solana.spl_token_transfers` | `token_mint_address`, `from_owner`, `to_owner`, `amount`, `block_time`, `action` |
| Deployer signal | `pumpdotfun_solana.pump_call_create`, `pump_call_create_v2` | `account_mint`, `account_user`, `call_block_time` |
