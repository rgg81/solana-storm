# Design Spec — Intra-Period Stop-Loss Strategy

**Date:** 2026-05-20
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`solana-storm`'s price-prediction pivot (merged 2026-05-20 as `ea83aa7`)
showed the calibrated `positive_return` classifier could not enter any
positions across all 5 folds because the post-filter ~10% positive-class
base rate combined with isotonic calibration capped predicted probabilities
below 0.5. That follows Phase 3's survival classifier (`6b6baac`) which DID
enter positions but lost −98.68% because surviving ≠ retaining price. **Two
FAILs through different mechanisms is strong evidence that the dataset's
features at T0+12h don't separate winners from losers at the T0+14d
horizon.**

Rather than tweaking the target/threshold for a third ML attempt, this spec
attacks the dominant loss source structurally: the ~50% of graduated tokens
that go to −100% by abandoning their pools over the 1–14 days following
graduation. The thesis: most −100% outcomes are not instantaneous; they
leak liquidity gradually, so a **fixed-rule stop-loss exit** at the first
sign of liquidity loss can attenuate the structural drag — without any ML,
without any scoring, with no new features.

## 2. Goal & success criteria

A self-contained extension of the `model/` backtest engine that:

1. Reads a new `intraperiod_snapshots` table — 14 daily liquidity snapshots
   per token from T0+1d through T0+14d — backfilled from Helius archival
   RPC.
2. Implements a fixed-rule stop-loss exit in `run_backtest`: exit at the
   first daily snapshot where pool quote-reserve drops below 50% of entry
   quote-reserve.
3. Runs a new strategy — the filtered `buy_everything` universe with the
   stop-loss exit — alongside the 3 existing baselines (which keep
   hold-to-horizon exits) in the same walk-forward + decision-gate
   framework.
4. Emits a fresh report showing each strategy's total return, max drawdown,
   per-regime breakdown, and the decision gate's PASS / FAIL verdict.

**The decision gate (unchanged from Phase 3 / pivot, pre-committed):** the
new stop-loss strategy basket beats all three (no-stop-loss) baselines,
out-of-sample, after costs, across ≥ 2 distinct market regimes, with max
drawdown ≤ 40%. The 40% ceiling stays fixed.

**Success** is the honest report. A third "no edge — do not deploy" verdict
remains an acceptable, planned outcome.

## 3. Scope

**In scope:**

- A new `bootstrap/fetch_intraperiod.py` script + `intraperiod_snapshots`
  SQLite table populated from Helius archival RPC.
- An optional `stop_loss_threshold` parameter on `run_backtest` that, when
  set, walks the 14 daily snapshots and exits early on threshold breach.
- One new "strategy" — the filtered `buy_everything` membership with the
  stop-loss exit — reported alongside the 3 unchanged baselines.
- One new `Config` field: the stop-loss threshold default (0.5).
- `LEAKAGE_FORBIDDEN` extension so the 28 snapshot columns never become
  features.

**Out of scope:**

- Take-profit exits (YAGNI; if stop-loss can't attenuate the −99%, locking
  in small gains won't either).
- Sub-day snapshot resolution.
- Trailing stop-loss / volatility-adaptive thresholds.
- ML scoring on top of the stop-loss strategy.
- Changes to `model/filter.py`, `model/baselines.py`, `model/survival.py`,
  `model/regime.py`, `model/costs.py`.
- Changes to the decision gate.

## 4. Data extension — `intraperiod_snapshots`

### 4.1 Schema

A new SQLite table in `./storm.db`:

```sql
CREATE TABLE intraperiod_snapshots (
    mint TEXT NOT NULL,
    snapshot_index INTEGER NOT NULL,    -- 1..14, days after graduation
    snapshot_time INTEGER NOT NULL,     -- unix seconds; graduation_time + snapshot_index * 86400
    snapshot_slot INTEGER NOT NULL,     -- Solana slot closest to snapshot_time
    base_reserve TEXT,                  -- u64 lamports; NULL if unavailable
    quote_reserve TEXT,                 -- u64 lamports; NULL if unavailable
    PRIMARY KEY (mint, snapshot_index)
);
```

`NULL` reserves mean the snapshot couldn't be fetched (pool account closed,
RPC failure after retries, etc.). The backtest treats NULL as "no data,
skip and continue" — neither a stop-loss trigger nor a veto.

### 4.2 Bootstrap — `bootstrap/fetch_intraperiod.py`

For each token in `historical_graduations`:

1. Compute 14 snapshot timestamps: `graduation_time + i * 86_400` for
   `i ∈ 1..14`.
2. For each timestamp, find the Solana slot at-or-before that time.
   Helius's `getBlockTime` / `getSlot` endpoints, or an interpolated
   slot-time index built from sparse polling, both work.
3. For each `(mint, slot)`, call `getAccountInfo(pool_address, slot)` and
   parse the pool reserves. `bootstrap/load.py` already decodes pump.fun
   AMM pool accounts; reuse that decoder.
4. Insert the row. On any error (account closed, RPC timeout after
   retries), insert with NULL reserves so the row exists and idempotent
   re-runs don't re-attempt indefinitely.

**Total RPC budget:** 4,755 tokens × 14 snapshots ≈ 66,570 archival
`getAccountInfo` calls. Helius free tier is ~100k credits/day. One day's
headroom even at 1.5 credits/call.

**Idempotency:** The script reads existing `intraperiod_snapshots` rows on
startup and skips already-recorded `(mint, snapshot_index)` pairs. Failures
re-attempted by deleting the NULL rows and re-running. The script's progress
is logged at module level (e.g., every 500 tokens) so a long run can be
monitored.

### 4.3 `model/data.py` extension

When `intraperiod_snapshots` exists, the loaded DataFrame gains 28 paired
columns: `snap_{i}_base_reserve`, `snap_{i}_quote_reserve` for `i ∈ 1..14`.
NULL reserves load as NaN, consistent with how `historical_graduations`
loads its u64 reserve columns. If the table is missing or empty, the
DataFrame loads as today (Phase 3 / pivot behavior) — backward-compatible.

The 28 new columns are added to `LEAKAGE_FORBIDDEN` in `model/features.py`:
they are future data relative to T0+12h entry and must NEVER appear in `X`.
They are used ONLY by `run_backtest` for the stop-loss exit logic.

## 5. Backtest — the stop-loss exit

### 5.1 New parameter

`run_backtest(test_df, basket, slot_count, initial_bankroll, dex_fee_rate,
stop_loss_threshold=None)`:

- `stop_loss_threshold=None` — existing behavior unchanged. Every basket
  position holds until T0+14d outcome reserves and exits via `exit_fill`.
- `stop_loss_threshold=float ∈ (0, 1)` — per position, walk the 14
  intra-period snapshots in chronological order. At the first snapshot `i`
  where `snap_i_quote_reserve` is non-NaN AND `< threshold *
  entry_quote_reserve`, exit at that snapshot's price via
  `exit_fill(snap_i_base_reserve, snap_i_quote_reserve, position_size,
  dex_fee_rate)`. If no snapshot triggers, exit at T0+14d as before.

### 5.2 Ordering

Snapshots are evaluated in chronological order (`i = 1, 2, …, 14`). The
position exits at the FIRST triggering snapshot — not the worst, not the
last. Once a position exits, capital returns to the bankroll for the next
fill (the existing slot-recycling logic is unchanged).

### 5.3 Cost model

`exit_fill` from `model/costs.py` is unchanged. The slippage paid at the
stop-loss exit is the constant-product cost on the snapshot's pool reserves
at that moment, with the existing 0.25% DEX fee. No new cost dimension.

### 5.4 No-stop-loss baselines

The 3 existing baselines (`buy_everything`, `random_basket`,
`heuristic_basket`) keep `stop_loss_threshold=None`. The comparison is
apples-to-apples: same selectors, same filtered universe, same costs; the
ONLY difference between the new strategy and `buy_everything` is the exit
rule.

## 6. Strategy

A single new strategy is added alongside the 3 unchanged baselines AND the
unchanged pivot model basket:

- **`stop_loss_buy_everything`** — membership identical to `buy_everything`
  (the entire filtered universe at fold test time), exit via the stop-loss
  rule with `threshold = 0.5`.

No new "selector" function in `model/baselines.py`. `model/walkforward.py`'s
`_run_one_fold` is extended with a single additional `run_backtest` call:

```python
baseline_results['stop_loss_buy_everything'] = run_backtest(
    test_df, buy_everything(test_df),
    slot_count=config.slot_count,
    initial_bankroll=config.initial_bankroll,
    dex_fee_rate=config.dex_fee_rate,
    stop_loss_threshold=config.stop_loss_threshold,
)
```

**The pivot's model-basket logic remains in place untouched** — it runs as
before, producing whatever it produced in the pivot (the empty-basket / 0%
result is the most likely outcome since features are unchanged). Keeping
the model path alive serves as a sanity-check comparator: the report will
show the model basket alongside the new stop-loss strategy and the 3
baselines, so a reader can verify the pivot's verdict still holds. The
implementation surface is minimal — one `run_backtest` call added; no
restructuring of `_run_one_fold`.

The decision gate evaluates `stop_loss_buy_everything` as the candidate
against the 3 hold-to-horizon baselines. The model basket is reported as a
comparison point but is NOT in the gate's baseline set (it was the
previous iteration's candidate).

## 7. Walk-forward + regime validation

No change to the harness's structure. Same expanding-window folds; same
`TRUE_MONTHLY_GRADUATIONS` regime labeling. The new strategy appears in
each fold's results alongside the 3 unchanged baselines. The decision-
gate computation in `model/report.py` adds the new strategy as the
candidate (in place of the model basket) and evaluates it against the 3
baselines.

## 8. Decision gate

Identical to Phase 3 / pivot, pre-committed:

- The new strategy basket beats every other strategy in the comparison
  (the 3 hold-to-horizon baselines) on total return,
- out-of-sample,
- after costs,
- across ≥ 2 distinct market regimes,
- with max drawdown ≤ 40%.

No goalpost-moving after the run. If the gate is FAIL, the result is FAIL.

## 9. Architecture — touched files

| Path | Change |
|---|---|
| `bootstrap/fetch_intraperiod.py` | **NEW** — Helius archival RPC scraper for daily intra-period snapshots |
| `bootstrap/tests/test_fetch_intraperiod.py` | **NEW** — unit tests for timestamp→slot mapping, idempotency, NULL-on-failure |
| `model/data.py` | **Modified** — load `intraperiod_snapshots` table if present; expose 28 new columns |
| `model/tests/test_data.py` | **Modified** — fixture + assertions for the new joined columns |
| `model/backtest.py` | **Modified** — `run_backtest` gains optional `stop_loss_threshold`; new exit logic |
| `model/tests/test_backtest.py` | **Modified** — new tests for the stop-loss exit path (trigger / no-trigger / NaN-skip) |
| `model/walkforward.py` | **Modified** — `_run_one_fold` adds the 4th strategy call |
| `model/tests/test_walkforward.py` | **Modified** — fixture extended with snapshot columns; the new strategy is asserted in results |
| `model/run.py` | **Modified** — log the stop-loss threshold; pass it through to the harness |
| `model/config.py` | **Modified** — one new field `stop_loss_threshold: float = 0.5` |
| `model/tests/test_config.py` | **Modified** — defaults assertion updated |
| `model/features.py` | **Modified** — `LEAKAGE_FORBIDDEN` gains the 28 snapshot columns |
| `model/tests/test_features.py` | **Modified** — leakage assertion now includes the snapshot columns |
| `model/report.py` | **Modified** — new strategy line in the comparison table; gate evaluates new strategy |
| `model/README.md` | **Modified** — run-log section for the stop-loss strategy result |
| All other `model/*.py` | **Unchanged** — `filter.py`, `baselines.py`, `survival.py`, `regime.py`, `costs.py` |

## 10. Pre-committed parameters

- **Snapshot density:** 14 daily snapshots, T0+1d through T0+14d.
- **Stop-loss threshold:** `0.5` (50% of entry quote-reserve).
- **Strategy basket:** filtered `buy_everything` (entire post-filter universe).
- **Slot count:** 20 (`Config.slot_count`, unchanged).
- **DEX fee rate:** 0.0025 (unchanged).
- **Random seed:** 20260519 (unchanged).
- **Decision gate:** max_dd ≤ 40%, beats baselines, ≥ 2 regimes (unchanged).

## 11. Error handling & testing

TDD discipline carries over: every change is written test-first, run red,
implement, run green, commit. The no-leakage tests get a new line item —
the 28 snapshot columns must be in `LEAKAGE_FORBIDDEN` and absent from
`FEATURE_COLUMNS` (which is also irrelevant for this strategy since no model
is trained, but the assertion guards future iterations that might add ML
back).

`test_backtest.py` adds the new behaviors:

- With `stop_loss_threshold=None`: every existing test still passes
  (back-compat).
- With a threshold set: a token whose `snap_3_quote_reserve < 0.5 * entry`
  exits AT snap 3 — not earlier (no triggering snapshot before), not later.
- NaN snapshots are SKIPPED — not treated as triggers and not as vetoes.
- A token whose reserves never drop below threshold exits at T0+14d via
  the existing `exit_fill` on outcome reserves.

`bootstrap/fetch_intraperiod.py` is exercised by the actual one-time run.
Its unit tests cover:

- Timestamp→slot mapping (the slot-at-or-before-timestamp function).
- Idempotency (re-running with existing rows skips them).
- NULL-on-failure handling (RPC errors leave NULL reserves, not crash).

## 12. Risks & honest caveats

Phase 3's / the pivot's caveats carry over verbatim into the new report:
hold-to-horizon for the no-stop-loss baselines, cost-basis equity curve (so
max drawdown is a lower bound), thin feature universe, approximate
snapshot timing, limited statistical power. Three new ones specific to
this iteration:

- **Snapshot resolution is 1 day.** Hard rugs completing inside a single
  day are caught at the next-day check — by which point price has often
  already crashed. The stop-loss exits at the crashed price, paying
  slippage. Sub-day rugs degrade the result; they don't break the strategy.
- **Helius archival pricing uncertainty.** Free-tier archival reads at
  historical slots may cost more than 1 credit each per Helius's docs.
  Verify before the full 67k-call pull; downscale to a sparser schedule if
  per-call cost is materially higher than expected.
- **Closed pool accounts.** Some rug-and-close tokens have NULL snapshots
  after the close. The backtest treats NULL as skip-and-continue, so these
  tokens reach T0+14d with outcome quote-reserve = 0 — i.e., still −100%.
  The stop-loss cannot help them because no snapshot shows the intermediate
  drawdown. The break-even rate for the strategy depends on what fraction
  of −100% tokens leak gradually (catchable) vs slam shut (uncatchable).
- **The strategy is unconditional on the dataset side.** Holds every
  filtered token. To beat the 3 baselines (each at ~−99% on filtered
  hold-to-horizon) AND the 40% drawdown gate, the stop-loss must do
  meaningful work on the rug-bound tokens. The expected effect hinges on
  a number we don't yet know: what fraction of the −100% tokens leak
  gradually (catchable at a daily snapshot) vs slam shut between
  snapshots (uncatchable). The portfolio backtest's churn through 20
  slots compounds per-position outcomes nonlinearly, so naive token-level
  arithmetic ("if half are caught at −60% the average is …") does NOT
  directly project to the basket equity curve. **The verdict is whatever
  the backtest produces.** A third FAIL is fully on the table —
  especially if rugs are dominated by fast (sub-day) liquidity pulls that
  the daily-snapshot resolution can't catch in time.

## 13. Open decisions (resolved during implementation)

- The exact slot↔timestamp mapping method — Helius `getBlockTime` per
  snapshot vs a sparse-polled slot-time index. Implementer's choice; both
  fit the cost budget.
- Whether the new strategy's per-fold result lives in
  `FoldResult.baseline_results` with key `"stop_loss_buy_everything"` or
  as a new field on `FoldResult`. Both work for the report.
- Whether `model/config.py`'s new `stop_loss_threshold` field is exposed
  as a CLI flag in `run.py`. The entry threshold already is; adding this
  one is trivial but only needed for sweeps (which violate pre-commit).
