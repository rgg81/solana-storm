# pump-prediction — Claude skill for pump.fun graduation signals

A Claude Code skill that picks 3–5 pump.fun graduations every invocation,
audits 24h-old prior picks for realized return, and maintains a self-improving
diary (rolling lessons + auto-built smart-wallet registry).

**Spec:** `docs/superpowers/specs/2026-05-21-pump-prediction-skill-design.md`

## Run

```bash
# normal mode — calls live APIs, writes to predictions/diary/
claude --skill pump-prediction

# rehearsal mode — canned data, output to stdout
PUMP_PREDICTION_REHEARSAL=1 claude --skill pump-prediction
```

## Required env (in repo `.env`)

- `HELIUS_API_KEY` — for Helius RPC
- `DUNE_API_KEY` — for Dune SQL

## Interpret output

Each invocation writes:
- `predictions/diary/decisions/<YYYY-MM-DD-HH-MM>.md` — picks + reasoning
- `predictions/diary/outcomes/<original-decision-id>-outcome.md` — audits of 24h-old picks
- May update `predictions/diary/lessons.md` (rolling synthesis, COMMITTED)

Read `lessons.md` to see what the skill has learned. The frontmatter shows
`buy_hit_rate_last_7d` vs `buy_hit_rate_first_7d` — if not improving after 30+
audits, the approach isn't working and the project should be retired.

## Run the helper tests

```bash
python3 -m pytest predictions/helpers/tests/ -v
```

## First-run validation notes (Task 9)

Validation completed: 2026-05-21 UTC.

Unit tests: **19/19 passed** (all helpers, dry-run fixtures).

Helper status against live APIs (mint `A2Lobq7x2xy2DHSWyJtD9Lez63CYNVwMHHdPyd7npump`,
pool `CRLcGtzWidvekVZvV6JhkKS8FRhuRb7u1JAPgcG4fWNe` — a graduation from minutes before
the test run):

- `recent_graduations.py`: **works** — returned 217 unique mints from the last 24h with
  deployer_wallet, deployer_prior_launches, deployer_age_secs, and
  curve_real_sol_reserves populated. Three SQL bugs fixed during validation (see commit
  `f153090`): QUALIFY clause replaced with GROUP BY MAX(), non-existent reserve columns
  removed from `createpoolevent` CTE, scalar subquery fan-out fixed with explicit GROUP
  BY. Note: `liq_quote_reserve_lamports` and `liq_base_reserve_lamports` are hardcoded
  to 0 — `pump_amm_evt_createpoolevent` does not carry reserve columns; these signals
  are not available from Dune at graduation time.

- `helius_trade_flow.py`: **works** — found 1 buy / 0 sells / 1 unique buyer on the
  test mint within the 60-minute window. Config fix (`f153090`) added SOLANA_RPC_URL
  fallback so HELIUS_API_KEY placeholder in .env doesn't block calls.

- `pumpfun_scrape.py`: **API unavailable during test** — `frontend-api.pump.fun`
  returned HTTP 530 (Cloudflare origin unreachable) for all endpoints on 2026-05-21.
  The helper code is correct (graceful `endpoints_failed` list returned). Re-test on
  first manual invocation; if pump.fun continues blocking WSL/server IPs, consider
  routing through a residential proxy or scraping the main `pump.fun` page instead.

- `telegram_chatter.py`: **works** — all 5 channels accessible (0 channels dropped).
  Mention counts are 0 for "STORM" (expected — not a current token).

- `audit_outcome.py`: **RESOLVED** -- now uses `getTokenAccountsByOwner` to fetch
  base + quote SPL-token vaults directly (sidesteps PumpSwap layout parsing).
  Verified against live pools.

### env setup note

`HELIUS_API_KEY` in `.env` is a placeholder. The actual key is embedded in
`SOLANA_RPC_URL`. After Task 9's config fix, helpers fall back to `SOLANA_RPC_URL`
automatically. Alternatively, set `HELIUS_API_KEY` to the real key value
(`f2055ada-...`) in `.env` to make the fallback unnecessary.

## How to do the first manual invocation

The skill is ready. In a fresh Claude Code session in this repo directory, invoke:

```
/pump-prediction
```

(The skill lives at `.claude/skills/pump-prediction.md` and auto-loads via Claude Code's
skill discovery.)

Expected behavior on first invocation:

- **Phase 1** finds 0 pending audits (no prior decisions exist yet) and skips to Phase 2.
- **Phase 2** queries Dune for the last 24h graduations (~200-300 tokens), applies
  pre-filters, enriches a 30-50 token shortlist (Helius trade flow + Telegram chatter
  per token), then writes a decision file at
  `predictions/diary/decisions/<YYYY-MM-DD-HH-MM>.md` with 3-5 picks.
- **Total time:** ~5-15 minutes depending on shortlist size and helper response times.
- **Total cost:** ~1-2 Dune credits + a few hundred Helius RPC credits (free-tier) +
  HTTP scraping (free). No paid LLM API calls beyond the Claude Code session itself.

After 4-6 hours, invoke again. Phase 1 will audit the first invocation's picks.
Repeat 4-6x/day.

**Audit accuracy note:** `audit_outcome.py` now uses `getTokenAccountsByOwner` to
read base + quote SPL-token vault balances directly, returning real reserve data for
active pools. `pool_closed: true` (and `realized_return: -100%`) is only returned for
tokens whose vaults were genuinely emptied (rug-and-close).

After ~10-30 invocations, check `predictions/diary/lessons.md` — the smart-wallet
registry and validated lessons should be populating. If after 30+ audits the rolling
hit-rate stats show `trend: flat` or `declining`, the skill isn't developing edge;
retire per the spec.

### Prefilter field switched (post-validation fix)

The skill's Phase 2 prefilter originally used `liq_quote_reserve_lamports < 5 SOL` to drop low-liquidity tokens. During validation we discovered this field is `0` for all tokens (the source Dune view doesn't expose initial pool reserves). With every token at 0, the original prefilter would have dropped 100% of the universe → empty shortlists.

The skill (and `config.PREFILTER_MIN_CURVE_SOL_LAMPORTS = 50_000_000_000`) now filters on `curve_real_sol_reserves_lamports < 50 SOL` instead. This field IS populated and gives the same intent: "tokens that graduated with meaningful capital." The 50 SOL threshold matches the spirit of the original 5 SOL liquidity floor (pump.fun graduation requires ~85 SOL on the curve, so 50 SOL is a reasonable floor).

The original `PREFILTER_MIN_LIQ_QUOTE_LAMPORTS` constant is kept in config.py with value `0` (effectively unused) until the source data gap is closed.
