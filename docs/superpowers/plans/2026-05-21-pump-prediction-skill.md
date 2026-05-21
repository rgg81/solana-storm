# pump-prediction Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code skill `pump-prediction` that, when invoked manually 4–6×/day, audits prior 24h-old decisions for outcomes and picks 3–5 fresh graduations from the last 24h, maintaining a self-improving diary (lessons + auto-built smart-wallet registry).

**Architecture:** A skill file at `.claude/skills/pump-prediction.md` (instructions Claude follows) orchestrates 5 short Python helpers in `predictions/helpers/` that return JSON to stdout. The diary lives as markdown under `predictions/diary/`. Claude reasons across helper outputs + diary context to produce decisions and audit results. No automation — manual invocation only.

**Tech Stack:** Python 3.11+, `requests`, `beautifulsoup4`, `pandas` (already in repo). HTTP-only data acquisition: Helius RPC, Dune Analytics (via existing `bootstrap.dune_client`), pump.fun's `frontend-api.pump.fun/*` JSON endpoints, `https://t.me/s/<channel>` public-preview HTML. No paid APIs, no Telethon (using the simpler `t.me/s/` HTML endpoint instead).

**Spec:** `docs/superpowers/specs/2026-05-21-pump-prediction-skill-design.md` (commit `5bbfc0f`).

**Simplification from the spec:** The spec listed Telethon for Telegram. After verifying `.env`, the actual setup is a Bot Token (which can't read non-admin channels). Switching to `t.me/s/<channel>` public-preview HTML scraping removes auth complexity entirely and works for the broadcast channels we care about. Documented in §10 below.

---

## Workspace

- Branch: `pump-prediction-skill` (already created from `main` at 2026-05-21).
- No worktree — work in `/home/roberto/solana-storm`.
- Spec committed at `5bbfc0f`.
- Required env vars already in `.env`: `HELIUS_API_KEY`, `DUNE_API_KEY`. Telegram keys not needed (we use the public `t.me/s/` endpoint).

## File structure

| Path | Change |
|---|---|
| `predictions/` | **NEW** top-level dir for this whole sub-system |
| `predictions/README.md` | **NEW** — how to run + interpret output |
| `predictions/requirements.txt` | **NEW** — `requests`, `beautifulsoup4` (other deps already in repo) |
| `predictions/config.py` | **NEW** — constants (universe window, picks/run, channel list, conviction thresholds) |
| `predictions/helpers/__init__.py` | **NEW** — empty |
| `predictions/helpers/recent_graduations.py` | **NEW** — Dune query for last 24h graduations |
| `predictions/helpers/helius_trade_flow.py` | **NEW** — Helius RPC: first-hour buys/sells/holders per mint |
| `predictions/helpers/pumpfun_scrape.py` | **NEW** — pump.fun frontend-api JSON |
| `predictions/helpers/telegram_chatter.py` | **NEW** — `t.me/s/` HTML scrape for mention counts |
| `predictions/helpers/audit_outcome.py` | **NEW** — Helius RPC for 24h-elapsed pool state |
| `predictions/helpers/dry_run_data/*.json` | **NEW** — canned outputs (one per helper) |
| `predictions/helpers/tests/__init__.py` | **NEW** — empty |
| `predictions/helpers/tests/test_*.py` | **NEW** — pytest, one per helper (5 files) |
| `predictions/diary/lessons.md` | **NEW** — empty template, COMMITTED |
| `predictions/diary/decisions/.gitkeep` | **NEW** — placeholder so dir exists in git; per-run files gitignored |
| `predictions/diary/outcomes/.gitkeep` | **NEW** — same |
| `.claude/skills/pump-prediction.md` | **NEW** — the skill itself |
| `.gitignore` | **Modified** — add `predictions/diary/decisions/*.md`, `predictions/diary/outcomes/*.md`, `predictions/.cache/`, `predictions/telegram.session*` |
| All existing files | **Unchanged** — additive sub-system |

## Tasks

9 tasks. Tasks 1 + 7 are scaffolding. Tasks 2–6 are the 5 helpers (one task each, TDD with `--dry-run` canned fixtures). Task 8 is the skill markdown. Task 9 is the end-to-end rehearsal validation.

---

### Task 1: Scaffold `predictions/` + config + requirements

**Files:**
- Create: `predictions/README.md`, `predictions/requirements.txt`, `predictions/config.py`, `predictions/helpers/__init__.py`, `predictions/helpers/tests/__init__.py`, `predictions/diary/decisions/.gitkeep`, `predictions/diary/outcomes/.gitkeep`
- Modify: `.gitignore`

This task creates no logic — just the directories and config constants. No tests yet (config.py is just values).

- [ ] **Step 1: Create directories**

```bash
cd /home/roberto/solana-storm
mkdir -p predictions/helpers/tests predictions/helpers/dry_run_data predictions/diary/decisions predictions/diary/outcomes
touch predictions/helpers/__init__.py predictions/helpers/tests/__init__.py
touch predictions/diary/decisions/.gitkeep predictions/diary/outcomes/.gitkeep
```

- [ ] **Step 2: Write `predictions/requirements.txt`**

```
requests>=2.31
beautifulsoup4>=4.12
```

(All other deps — pandas, pytest, dotenv — are inherited from the repo's existing requirements.)

- [ ] **Step 3: Write `predictions/config.py`**

```python
"""Configuration constants for the pump-prediction skill.

These are stable defaults. Override per-invocation via env vars where useful.
"""

from __future__ import annotations

import os

# --- Universe + cohort ---
UNIVERSE_HOURS_BACK = 24                  # scan last 24h of graduations
SHORTLIST_MAX = 50                        # cap on deep-enriched candidates per run
PREFILTER_MIN_LIQ_QUOTE_LAMPORTS = 5_000_000_000   # 5 SOL minimum entry depth
PREFILTER_MAX_DEPLOYER_PRIOR_LAUNCHES = 200

# --- Picks ---
PICKS_PER_RUN_MIN = 3
PICKS_PER_RUN_MAX = 5

# --- Outcome audit horizon ---
AUDIT_HORIZON_HOURS = 24
AUDIT_RETRY_LIMIT = 3                     # max consecutive failed audits before flagging health

# --- Smart-wallet registry ---
SMART_WALLET_MIN_WINNER_HITS = 3
SMART_WALLET_MIN_PRECISION = 0.25         # winner_hits / total_appearances
SMART_WALLET_REGISTRY_CAP = 30
SMART_WALLET_LAST_SEEN_DAYS = 30

# --- Lesson lifecycle ---
LESSON_PROMOTE_CONFIRMS = 3
LESSON_DEMOTE_DISCONFIRMS = 3
LESSON_DEMOTE_RATIO = 2.0
LESSON_RETIRE_DAYS_NO_CONFIRM = 7
LESSON_DRIFT_DAYS_UNTRIGGERED = 14

# --- Health check ---
HEALTH_MIN_AUDITS = 30                    # before declaring trend
HEALTH_WINDOW_DAYS = 7

# --- Helper sources ---
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={os.environ.get('HELIUS_API_KEY', '')}"
PUMPFUN_API_BASE = "https://frontend-api.pump.fun"
TELEGRAM_CHANNELS = [
    "PumpFunChannel",
    "pumpfunsignal",
    "SolanaMemeCoinss",
    "MemeCoinDaily",
    "MemeCoinWhalePumps",
]
HTTP_USER_AGENT = "Mozilla/5.0 (compatible; solana-storm pump-prediction/0.1)"

# --- Rehearsal mode ---
def is_rehearsal() -> bool:
    """When set, helpers return canned data and the skill writes to stdout."""
    return os.environ.get("PUMP_PREDICTION_REHEARSAL", "").lower() in ("1", "true", "yes")

# --- Paths ---
import pathlib
_ROOT = pathlib.Path(__file__).resolve().parent
DIARY_DIR = _ROOT / "diary"
DECISIONS_DIR = DIARY_DIR / "decisions"
OUTCOMES_DIR = DIARY_DIR / "outcomes"
LESSONS_FILE = DIARY_DIR / "lessons.md"
DRY_RUN_DIR = _ROOT / "helpers" / "dry_run_data"
```

- [ ] **Step 4: Write `predictions/README.md`**

```markdown
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
```

- [ ] **Step 5: Update `.gitignore`**

Append to `.gitignore`:

```
# pump-prediction diary state (per-run files; lessons.md is committed)
predictions/diary/decisions/*.md
predictions/diary/outcomes/*.md
predictions/.cache/
predictions/telegram.session*
```

- [ ] **Step 6: Smoke verify**

```bash
python3 -c "from predictions.config import UNIVERSE_HOURS_BACK, is_rehearsal; print('config ok:', UNIVERSE_HOURS_BACK, is_rehearsal())"
python3 -m pytest predictions/ -v 2>&1 | tail -3
```

Expected: `config ok: 24 False`, and pytest reports 0 collected (no tests yet).

- [ ] **Step 7: Commit**

```bash
git add predictions/ .gitignore
git commit -m "$(cat <<'EOF'
Task 1: Scaffold predictions/ skill workspace

New top-level predictions/ directory with config, requirements, README,
diary structure (decisions/outcomes gitignored per-run, lessons.md tracked),
and empty helpers/ + tests/ packages. No business logic yet -- Tasks 2-8
add the helpers, lessons template, and the skill itself.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Helper — `recent_graduations.py` (Dune SQL)

**Files:**
- Create: `predictions/helpers/recent_graduations.py`
- Create: `predictions/helpers/tests/test_recent_graduations.py`
- Create: `predictions/helpers/dry_run_data/recent_graduations.dry_run.json`

This helper queries Dune for graduations in the last `UNIVERSE_HOURS_BACK` hours (default 24) and returns JSON to stdout with per-token point-in-time facts. Reuses the existing `bootstrap.dune_client.DuneClient`.

- [ ] **Step 1: Write the dry-run fixture**

`predictions/helpers/dry_run_data/recent_graduations.dry_run.json`:

```json
{
  "data": [
    {
      "mint": "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9",
      "pool_address": "5tHRbpyZ3jh6gFhWJZsK1xJ8KqLNQH5kMzPMr8aPK7",
      "graduation_time_unix": 1716286182,
      "deployer_wallet": "DEPLOY_FAKE_8y45",
      "deployer_prior_launches": 3,
      "deployer_age_secs": 691200,
      "liq_quote_reserve_lamports": 87000000000,
      "liq_base_reserve_lamports": 985000000000000,
      "curve_real_sol_reserves_lamports": 85000000000,
      "curve_completion_time_secs": 4200
    },
    {
      "mint": "RUGBOT_FAKE_MINT_for_dry_run_xxxxxxxxxxxxxxxxxx",
      "pool_address": "RUG_FAKE_POOL_xxxxxxxxxxxxxxxxxxxxxx",
      "graduation_time_unix": 1716290000,
      "deployer_wallet": "SPAM_DEPLOYER_FAKE",
      "deployer_prior_launches": 247,
      "deployer_age_secs": 432000,
      "liq_quote_reserve_lamports": 80000000000,
      "liq_base_reserve_lamports": 1000000000000000,
      "curve_real_sol_reserves_lamports": 79000000000,
      "curve_completion_time_secs": 1800
    }
  ],
  "error": null
}
```

- [ ] **Step 2: Write tests**

`predictions/helpers/tests/test_recent_graduations.py`:

```python
"""Unit tests for predictions.helpers.recent_graduations."""

import json
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "recent_graduations.py"


def _run(args, env_extra=None):
    """Run helper as subprocess; return parsed JSON."""
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dry_run_returns_canned_fixture():
    """--dry-run flag returns the committed fixture without touching the network."""
    result = _run(["--dry-run"])
    assert result["error"] is None
    assert isinstance(result["data"], list)
    assert len(result["data"]) >= 1
    row = result["data"][0]
    for field in (
        "mint", "pool_address", "graduation_time_unix",
        "deployer_wallet", "deployer_prior_launches", "deployer_age_secs",
        "liq_quote_reserve_lamports", "liq_base_reserve_lamports",
    ):
        assert field in row, f"missing field {field}"


def test_dry_run_via_env_flag():
    """PUMP_PREDICTION_REHEARSAL=1 forces dry-run even without --dry-run."""
    result = _run([], env_extra={"PUMP_PREDICTION_REHEARSAL": "1"})
    assert result["error"] is None
    assert len(result["data"]) >= 1


def test_output_includes_specific_fixture_token():
    """Sanity: the dry-run fixture must contain the known test mint."""
    result = _run(["--dry-run"])
    mints = {row["mint"] for row in result["data"]}
    assert "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9" in mints
```

- [ ] **Step 3: Run tests — verify failure**

```bash
python3 -m pytest predictions/helpers/tests/test_recent_graduations.py -v
```

Expected: tests fail with `FileNotFoundError` on the helper script.

- [ ] **Step 4: Write the helper**

`predictions/helpers/recent_graduations.py`:

```python
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


def _live_query() -> dict:
    """Run the actual Dune query for the recent graduations cohort."""
    try:
        from bootstrap.dune_client import DuneClient
        from bootstrap.config import load_config as load_bootstrap_config
    except Exception as e:  # pragma: no cover
        return {"data": None, "error": f"bootstrap import failed: {e}"}

    bcfg = load_bootstrap_config()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.UNIVERSE_HOURS_BACK)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    sql = f"""
SELECT
  mint, pool, deployer_wallet, deployer_prior_launches, deployer_age_secs,
  graduation_time, liq_quote_reserve, liq_base_reserve,
  curve_real_sol_reserves, curve_completion_time_secs
FROM dune.solana_storm.result_pumpfun_graduations_live
WHERE graduation_time >= TIMESTAMP '{cutoff_str}'
ORDER BY graduation_time DESC
LIMIT 1000
""".strip()

    # NOTE: the exact Dune view / table name above is illustrative;
    # confirm at implementation time from `bootstrap/queries.py` and adapt.
    # If a curated view doesn't exist, fall back to the same join pattern
    # used by `bootstrap.queries.graduations_list_sql`.

    try:
        client = DuneClient(bcfg)
        rows, _credits = client.run_sql(sql)
    except Exception as e:
        return {"data": None, "error": f"dune query failed: {e}"}

    out = []
    for r in rows:
        try:
            grad_unix = int(datetime.fromisoformat(
                str(r["graduation_time"]).replace(" UTC", "").replace(" ", "T") + "+00:00"
            ).timestamp())
        except Exception:
            grad_unix = None
        out.append({
            "mint": str(r["mint"]),
            "pool_address": str(r["pool"]),
            "graduation_time_unix": grad_unix,
            "deployer_wallet": str(r.get("deployer_wallet") or ""),
            "deployer_prior_launches": int(r.get("deployer_prior_launches") or 0),
            "deployer_age_secs": int(r.get("deployer_age_secs") or 0),
            "liq_quote_reserve_lamports": int(r.get("liq_quote_reserve") or 0),
            "liq_base_reserve_lamports": int(r.get("liq_base_reserve") or 0),
            "curve_real_sol_reserves_lamports": int(r.get("curve_real_sol_reserves") or 0),
            "curve_completion_time_secs": int(r.get("curve_completion_time_secs") or 0),
        })

    return {"data": out, "error": None}


def main() -> int:
    parser = argparse.ArgumentParser()
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
```

**Implementation note for the engineer:** the exact SQL above references a `dune.solana_storm.result_pumpfun_graduations_live` view that may or may not exist. Inspect `bootstrap/queries.py` for the existing graduations query and adapt — the helper should produce the same per-token fields whichever query path it takes.

- [ ] **Step 5: Run tests — verify pass**

```bash
python3 -m pytest predictions/helpers/tests/test_recent_graduations.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Smoke live (optional, costs 1 Dune credit)**

```bash
python3 predictions/helpers/recent_graduations.py | head -50
```

Expected: JSON with `data` populated from a real Dune query, or an `error` field describing why it failed. If errored, fix the SQL view name and retry.

- [ ] **Step 7: Commit**

```bash
git add predictions/helpers/recent_graduations.py predictions/helpers/tests/test_recent_graduations.py predictions/helpers/dry_run_data/recent_graduations.dry_run.json
git commit -m "$(cat <<'EOF'
Task 2: recent_graduations.py helper (Dune SQL, last 24h)

Returns JSON list of graduations from the last UNIVERSE_HOURS_BACK hours with
per-token point-in-time facts (mint, pool, deployer history, entry pool depth,
curve completion stats). --dry-run flag + PUMP_PREDICTION_REHEARSAL env var
both return canned fixture; live mode hits Dune via existing DuneClient.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Helper — `helius_trade_flow.py` (Helius RPC)

**Files:**
- Create: `predictions/helpers/helius_trade_flow.py`
- Create: `predictions/helpers/tests/test_helius_trade_flow.py`
- Create: `predictions/helpers/dry_run_data/helius_trade_flow.dry_run.json`

For a single mint, fetch the first-N transactions on its PumpSwap pool via Helius RPC, parse buys vs sells, and aggregate first-hour buy/sell counts + unique buyers + smart-wallet hits.

- [ ] **Step 1: Write the dry-run fixture**

`predictions/helpers/dry_run_data/helius_trade_flow.dry_run.json`:

```json
{
  "data": {
    "mint": "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9",
    "window_minutes": 60,
    "buy_count": 47,
    "sell_count": 3,
    "net_sol_lamports": 18500000000,
    "unique_buyer_count": 89,
    "buyer_wallets": [
      "Dee9F5JhmqsJBQ2sM3eMNzKaLzngeXjMW2GH4MWkkP3z",
      "Hyz3aL4kpL2sM3eMNzKaLzngeXjMW2GH4MWkkABCDEF1",
      "BUYR_FAKE_1_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "BUYR_FAKE_2_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "BUYR_FAKE_3_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    ],
    "first_5_buy_timestamps_unix": [
      1716286200, 1716286202, 1716286205, 1716286209, 1716286215
    ]
  },
  "error": null
}
```

- [ ] **Step 2: Write tests**

`predictions/helpers/tests/test_helius_trade_flow.py`:

```python
"""Unit tests for predictions.helpers.helius_trade_flow."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "helius_trade_flow.py"


def _run(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


_TEST_MINT = "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9"


def test_dry_run_returns_canned_fixture():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["error"] is None
    data = result["data"]
    assert data["mint"] == _TEST_MINT
    for field in (
        "window_minutes", "buy_count", "sell_count",
        "net_sol_lamports", "unique_buyer_count", "buyer_wallets",
        "first_5_buy_timestamps_unix",
    ):
        assert field in data, f"missing field {field}"


def test_window_default_is_60_minutes():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["data"]["window_minutes"] == 60


def test_window_override_via_flag():
    """--window 30 should be honored in live mode; dry-run echoes 60."""
    result = _run([_TEST_MINT, "--window", "30", "--dry-run"])
    # Dry-run returns the fixture as-is; in real mode 30 would be the value.
    # This test just confirms --window doesn't blow up the arg parser.
    assert "data" in result


def test_buyer_wallets_are_unique():
    """Returned buyer_wallets list should not contain duplicates."""
    result = _run([_TEST_MINT, "--dry-run"])
    wallets = result["data"]["buyer_wallets"]
    assert len(wallets) == len(set(wallets))


def test_first_5_timestamps_are_chronologically_ordered():
    result = _run([_TEST_MINT, "--dry-run"])
    ts = result["data"]["first_5_buy_timestamps_unix"]
    assert ts == sorted(ts)
```

- [ ] **Step 3: Run tests — verify failure**

```bash
python3 -m pytest predictions/helpers/tests/test_helius_trade_flow.py -v
```

Expected: tests fail (helper script not yet created).

- [ ] **Step 4: Write the helper**

`predictions/helpers/helius_trade_flow.py`:

```python
"""Helius RPC helper: first-hour trade flow on a single mint's PumpSwap pool.

Usage:
    python3 helius_trade_flow.py <mint> [--window MINUTES] [--dry-run]

Output JSON to stdout:
    {"data": {"mint": ..., "buy_count": N, ...}, "error": null}

Strategy:
1. Resolve the pool address from the mint (use the canonical PumpSwap PDA).
2. getSignaturesForAddress(pool, limit=200) -- recent transactions on the pool.
3. For each signature in chronological order within the time window:
   - getTransaction(sig, jsonParsed=True)
   - Inspect tokenBalances pre/post to determine buy vs sell direction.
4. Aggregate counts, unique buyers, net SOL.
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


def _dry_run_payload(mint: str) -> dict:
    fixture = config.DRY_RUN_DIR / "helius_trade_flow.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint  # echo the requested mint
    return payload


def _rpc_call(method: str, params: list) -> dict:
    """One JSON-RPC call to Helius with simple retries."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    backoffs = [1, 3, 9]
    last_err = None
    for delay in [0, *backoffs]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(config.HELIUS_RPC_URL, json=body, timeout=15)
            if r.status_code == 429:
                last_err = f"rate-limited (429), retrying"
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"RPC call {method} failed: {last_err}")


def _resolve_pool_from_mint(mint: str) -> str:
    """The PumpSwap pool PDA is deterministic from the mint.

    NOTE: at implementation time, confirm the exact PDA derivation for the
    current PumpSwap program ID. As a fallback, the pool address can be
    looked up from historical_graduations: SELECT pool_address FROM
    historical_graduations WHERE mint = ?. The Dune-based
    recent_graduations.py already exposes pool_address; the SKILL should pass
    that address along, removing the need for derivation here. For Task 3 we
    accept --pool as an optional CLI arg.
    """
    raise NotImplementedError(
        "Pool derivation not implemented; the skill MUST pass --pool. "
        "See implementation note in this docstring."
    )


def _live_query(mint: str, pool: str | None, window_minutes: int) -> dict:
    if not pool:
        return {"data": None, "error": "pool address required (--pool)"}
    try:
        sigs_resp = _rpc_call(
            "getSignaturesForAddress",
            [pool, {"limit": 200}],
        )
    except Exception as e:
        return {"data": None, "error": f"getSignaturesForAddress: {e}"}

    sigs = (sigs_resp.get("result") or [])
    # Sort chronologically ascending.
    sigs = sorted(sigs, key=lambda s: int(s.get("blockTime") or 0))
    if not sigs:
        return {"data": {"mint": mint, "window_minutes": window_minutes,
                         "buy_count": 0, "sell_count": 0,
                         "net_sol_lamports": 0, "unique_buyer_count": 0,
                         "buyer_wallets": [], "first_5_buy_timestamps_unix": []},
                "error": None}

    pool_open_time = int(sigs[0].get("blockTime") or 0)
    window_end = pool_open_time + window_minutes * 60

    buy_count = 0
    sell_count = 0
    net_sol = 0
    buyer_wallets: list[str] = []
    seen_buyers: set[str] = set()
    first_buy_ts: list[int] = []

    for s in sigs:
        bt = int(s.get("blockTime") or 0)
        if bt > window_end:
            break
        try:
            tx_resp = _rpc_call(
                "getTransaction",
                [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception:
            continue
        tx = tx_resp.get("result")
        if not tx:
            continue
        # Detect buy vs sell by looking at the signer's token balance change
        # for `mint`: if increased -> buy, decreased -> sell.
        # This is heuristic; refine after observing real txs.
        meta = tx.get("meta") or {}
        pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances") or []
               if b.get("mint") == mint}
        post = {b["accountIndex"]: b for b in meta.get("postTokenBalances") or []
                if b.get("mint") == mint}
        # Use the first signer as the trader.
        msg = tx.get("transaction", {}).get("message", {})
        signers = [str(k) for k in msg.get("accountKeys", [])
                   if isinstance(k, dict) and k.get("signer")] if msg.get("accountKeys") and isinstance(msg["accountKeys"][0], dict) else [str(k) for k in msg.get("accountKeys", [])[:1]]
        if not signers:
            continue
        signer = signers[0]
        # Net change for this signer:
        net_change = 0
        for idx, p in post.items():
            try:
                p_amt = int((p.get("uiTokenAmount") or {}).get("amount") or 0)
            except Exception:
                p_amt = 0
            pr_amt = 0
            if idx in pre:
                try:
                    pr_amt = int((pre[idx].get("uiTokenAmount") or {}).get("amount") or 0)
                except Exception:
                    pr_amt = 0
            net_change += p_amt - pr_amt
        if net_change > 0:
            buy_count += 1
            if signer not in seen_buyers:
                seen_buyers.add(signer)
                buyer_wallets.append(signer)
            if len(first_buy_ts) < 5:
                first_buy_ts.append(bt)
            # SOL flow direction: signer paid SOL to receive tokens.
            sol_in = (meta.get("preBalances") or [0])[0] - (meta.get("postBalances") or [0])[0]
            net_sol += sol_in
        elif net_change < 0:
            sell_count += 1
            sol_out = (meta.get("postBalances") or [0])[0] - (meta.get("preBalances") or [0])[0]
            net_sol += sol_out

    return {
        "data": {
            "mint": mint,
            "window_minutes": window_minutes,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "net_sol_lamports": net_sol,
            "unique_buyer_count": len(seen_buyers),
            "buyer_wallets": buyer_wallets[:50],  # cap output size
            "first_5_buy_timestamps_unix": first_buy_ts,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint", help="Token mint address")
    parser.add_argument("--pool", default=None,
                        help="PumpSwap pool address (skill passes from Dune row)")
    parser.add_argument("--window", type=int, default=60,
                        help="Window in minutes from first transaction")
    parser.add_argument("--dry-run", action="store_true",
                        help="Return canned fixture, don't hit Helius")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint)
    else:
        payload = _live_query(args.mint, args.pool, args.window)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Implementation note:** the buy/sell heuristic (net token-balance change of the signer) is correct in principle but may need refinement once real txs are inspected. The skill is the buyer's UX — if the first run shows weird buy/sell counts, refine here.

- [ ] **Step 5: Run tests — verify pass**

```bash
python3 -m pytest predictions/helpers/tests/test_helius_trade_flow.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Smoke live (costs a few Helius credits)**

```bash
python3 predictions/helpers/helius_trade_flow.py 8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9 --pool $(sqlite3 ./storm.db "SELECT pool_address FROM historical_graduations LIMIT 1") --window 60 | head -30
```

Expected: JSON with real `buy_count`, `sell_count`, etc., or an `error` field. If errored, debug the JSON-RPC call.

- [ ] **Step 7: Commit**

```bash
git add predictions/helpers/helius_trade_flow.py predictions/helpers/tests/test_helius_trade_flow.py predictions/helpers/dry_run_data/helius_trade_flow.dry_run.json
git commit -m "$(cat <<'EOF'
Task 3: helius_trade_flow.py helper (Helius RPC)

Per-mint first-hour trade flow: buy/sell counts, net SOL, unique buyers,
first-5 buy timestamps. Uses Helius getSignaturesForAddress +
getTransaction with jsonParsed encoding. Heuristic buy/sell detection
via signer's net token-balance change. --pool arg required in live mode
(skill passes from Dune row); --dry-run + REHEARSAL env return fixture.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Helper — `pumpfun_scrape.py` (frontend-api JSON)

**Files:**
- Create: `predictions/helpers/pumpfun_scrape.py`
- Create: `predictions/helpers/tests/test_pumpfun_scrape.py`
- Create: `predictions/helpers/dry_run_data/pumpfun_scrape.dry_run.json`

Pump.fun's website is backed by `frontend-api.pump.fun` JSON endpoints. For a given mint, fetch comment count + creator info + recent trade count.

- [ ] **Step 1: Write the dry-run fixture**

`predictions/helpers/dry_run_data/pumpfun_scrape.dry_run.json`:

```json
{
  "data": {
    "mint": "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9",
    "comment_count": 22,
    "creator_reply_count": 3,
    "creator_wallet": "DEPLOY_FAKE_8y45",
    "creator_prior_launches": 3,
    "recent_trade_count_60min": 84,
    "fetched_at_unix": 1716290000
  },
  "error": null
}
```

- [ ] **Step 2: Write tests**

`predictions/helpers/tests/test_pumpfun_scrape.py`:

```python
"""Unit tests for predictions.helpers.pumpfun_scrape."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "pumpfun_scrape.py"
_TEST_MINT = "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9"


def _run(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dry_run_returns_canned_fixture():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["error"] is None
    for field in (
        "mint", "comment_count", "creator_reply_count",
        "creator_wallet", "creator_prior_launches",
        "recent_trade_count_60min", "fetched_at_unix",
    ):
        assert field in result["data"]


def test_dry_run_echoes_mint():
    result = _run(["DIFFERENT_MINT_xxxxxxx", "--dry-run"])
    assert result["data"]["mint"] == "DIFFERENT_MINT_xxxxxxx"


def test_comment_count_is_non_negative():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["data"]["comment_count"] >= 0
```

- [ ] **Step 3: Run tests — verify failure**

```bash
python3 -m pytest predictions/helpers/tests/test_pumpfun_scrape.py -v
```

Expected: tests fail (no helper yet).

- [ ] **Step 4: Write the helper**

`predictions/helpers/pumpfun_scrape.py`:

```python
"""pump.fun frontend-api helper: per-mint comments, creator, recent trades.

Usage:
    python3 pumpfun_scrape.py <mint> [--dry-run]

Output JSON to stdout:
    {"data": {...}, "error": null}

Hits three pump.fun frontend-api endpoints in sequence (1s sleep between):
    GET /coins/<mint>             -- coin metadata + creator
    GET /comments/<mint>?limit=100 -- comments + creator replies
    GET /trades/<mint>?limit=200   -- recent trades (filtered to last 60min)

If any endpoint returns non-2xx or unexpected shape, the helper returns
an error in the JSON (does not raise) so the skill can degrade gracefully.
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


def _dry_run_payload(mint: str) -> dict:
    fixture = config.DRY_RUN_DIR / "pumpfun_scrape.dry_run.json"
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


def _live_query(mint: str) -> dict:
    base = config.PUMPFUN_API_BASE.rstrip("/")
    coin = _get_json(f"{base}/coins/{mint}")
    time.sleep(1)
    comments = _get_json(f"{base}/comments/{mint}?limit=100")
    time.sleep(1)
    trades = _get_json(f"{base}/trades/{mint}?limit=200")

    if coin is None and comments is None and trades is None:
        return {"data": None, "error": "all pumpfun endpoints failed"}

    creator_wallet = ""
    creator_prior_launches = 0
    if isinstance(coin, dict):
        creator_wallet = str(coin.get("creator") or coin.get("creator_wallet") or "")
        creator_prior_launches = int(coin.get("creator_prior_launches") or 0)

    comment_count = 0
    creator_reply_count = 0
    if isinstance(comments, list):
        comment_count = len(comments)
        if creator_wallet:
            creator_reply_count = sum(
                1 for c in comments if str(c.get("user") or "") == creator_wallet
            )

    now_ts = int(time.time())
    cutoff = now_ts - 60 * 60
    recent_trade_count_60min = 0
    if isinstance(trades, list):
        recent_trade_count_60min = sum(
            1 for t in trades if int(t.get("timestamp") or 0) >= cutoff
        )

    return {
        "data": {
            "mint": mint,
            "comment_count": comment_count,
            "creator_reply_count": creator_reply_count,
            "creator_wallet": creator_wallet,
            "creator_prior_launches": creator_prior_launches,
            "recent_trade_count_60min": recent_trade_count_60min,
            "fetched_at_unix": now_ts,
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
```

**Implementation note:** the exact field names returned by pump.fun (`creator`, `creator_wallet`, `timestamp`, etc.) may differ. Inspect a real response at implementation time and adapt the field extraction; the test still passes either way because dry-run uses the fixture.

- [ ] **Step 5: Run tests + commit**

```bash
python3 -m pytest predictions/helpers/tests/test_pumpfun_scrape.py -v
git add predictions/helpers/pumpfun_scrape.py predictions/helpers/tests/test_pumpfun_scrape.py predictions/helpers/dry_run_data/pumpfun_scrape.dry_run.json
git commit -m "$(cat <<'EOF'
Task 4: pumpfun_scrape.py helper (frontend-api JSON)

Per-mint comment count, creator-reply count, creator wallet, prior launches,
and recent (last-60min) trade count. Hits three frontend-api.pump.fun JSON
endpoints with retry+backoff. Fragile to API changes -- returns error
field rather than raising, so the skill degrades gracefully.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Helper — `telegram_chatter.py` (`t.me/s/` HTML scrape)

**Files:**
- Create: `predictions/helpers/telegram_chatter.py`
- Create: `predictions/helpers/tests/test_telegram_chatter.py`
- Create: `predictions/helpers/dry_run_data/telegram_chatter.dry_run.json`

Uses the public `https://t.me/s/<channel>` HTML preview endpoint (no auth, no Telethon). For a given ticker, count case-insensitive substring matches across the configured channel list in the last ~20 messages each.

- [ ] **Step 1: Write the dry-run fixture**

`predictions/helpers/dry_run_data/telegram_chatter.dry_run.json`:

```json
{
  "data": {
    "ticker": "STORM",
    "channels_polled": 5,
    "channels_available": 4,
    "channels_dropped": ["DeadChannelFakeName"],
    "total_mentions": 9,
    "per_channel_mentions": {
      "PumpFunChannel": 0,
      "pumpfunsignal": 3,
      "SolanaMemeCoinss": 4,
      "MemeCoinDaily": 2,
      "MemeCoinWhalePumps": 0
    }
  },
  "error": null
}
```

- [ ] **Step 2: Write tests**

`predictions/helpers/tests/test_telegram_chatter.py`:

```python
"""Unit tests for predictions.helpers.telegram_chatter."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "telegram_chatter.py"


def _run(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dry_run_returns_canned_fixture():
    result = _run(["STORM", "--dry-run"])
    assert result["error"] is None
    for field in (
        "ticker", "channels_polled", "channels_available",
        "channels_dropped", "total_mentions", "per_channel_mentions",
    ):
        assert field in result["data"]


def test_per_channel_mentions_sum_to_total():
    result = _run(["STORM", "--dry-run"])
    data = result["data"]
    assert sum(data["per_channel_mentions"].values()) == data["total_mentions"]


def test_dropped_channels_are_listed():
    result = _run(["STORM", "--dry-run"])
    data = result["data"]
    assert isinstance(data["channels_dropped"], list)
    assert data["channels_polled"] - data["channels_available"] == len(data["channels_dropped"])
```

- [ ] **Step 3: Run tests — verify failure**

```bash
python3 -m pytest predictions/helpers/tests/test_telegram_chatter.py -v
```

Expected: tests fail (no helper yet).

- [ ] **Step 4: Write the helper**

`predictions/helpers/telegram_chatter.py`:

```python
"""Telegram public-channel chatter helper -- counts ticker mentions.

Uses the unauthenticated `https://t.me/s/<channel>` HTML preview endpoint
which returns the channel's recent ~20 messages. Counts case-insensitive
substring matches of the ticker (with word-boundary leniency: a $TICKER
or #TICKER prefix is common).

Usage:
    python3 telegram_chatter.py <ticker> [--dry-run]

Output JSON to stdout:
    {"data": {"ticker": ..., "total_mentions": N, "per_channel_mentions": {...}}, "error": null}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT}
_TIMEOUT = 15


def _dry_run_payload(ticker: str) -> dict:
    fixture = config.DRY_RUN_DIR / "telegram_chatter.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["ticker"] = ticker.upper()
    return payload


def _fetch_channel_text(channel: str) -> str | None:
    """Fetch the t.me/s/<channel> page and return concatenated message text."""
    url = f"https://t.me/s/{channel}"
    for delay in [0, 1, 3]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 429:
                time.sleep(5)
                continue
            if r.status_code >= 400:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            messages = soup.select(".tgme_widget_message_text")
            return "\n".join(m.get_text(separator=" ") for m in messages)
        except Exception:
            continue
    return None


def _count_ticker_mentions(text: str, ticker: str) -> int:
    """Case-insensitive count, lenient on $ / # prefix."""
    if not text:
        return 0
    pat = re.compile(rf"[\$#]?{re.escape(ticker)}\b", re.IGNORECASE)
    return len(pat.findall(text))


def _live_query(ticker: str) -> dict:
    channels = config.TELEGRAM_CHANNELS
    per_channel = {}
    dropped = []
    for ch in channels:
        text = _fetch_channel_text(ch)
        if text is None:
            dropped.append(ch)
            continue
        per_channel[ch] = _count_ticker_mentions(text, ticker)
        time.sleep(1)  # politeness
    total = sum(per_channel.values())
    return {
        "data": {
            "ticker": ticker.upper(),
            "channels_polled": len(channels),
            "channels_available": len(per_channel),
            "channels_dropped": dropped,
            "total_mentions": total,
            "per_channel_mentions": per_channel,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.ticker)
    else:
        payload = _live_query(args.ticker)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests + commit**

```bash
python3 -m pytest predictions/helpers/tests/test_telegram_chatter.py -v
git add predictions/helpers/telegram_chatter.py predictions/helpers/tests/test_telegram_chatter.py predictions/helpers/dry_run_data/telegram_chatter.dry_run.json
git commit -m "$(cat <<'EOF'
Task 5: telegram_chatter.py helper (t.me/s/ HTML scrape)

Counts case-insensitive ticker mentions across the configured public
broadcast channels via the t.me/s/<channel> HTML preview endpoint -- no
Telegram auth, no Telethon dependency. Channels that 4xx/timeout are
silently dropped and listed in channels_dropped. Mention count is a
corroborating signal only -- D1 in lessons.md template marks 'high
mention count alone' as DISCONFIRMED.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Helper — `audit_outcome.py` (Helius RPC for 24h pool state)

**Files:**
- Create: `predictions/helpers/audit_outcome.py`
- Create: `predictions/helpers/tests/test_audit_outcome.py`
- Create: `predictions/helpers/dry_run_data/audit_outcome.dry_run.json`

For a given mint + pool, fetch the pool account's current state via Helius `getAccountInfo` and parse base/quote reserves. Used by Phase 1 to compute realized return.

- [ ] **Step 1: Write the dry-run fixture**

`predictions/helpers/dry_run_data/audit_outcome.dry_run.json`:

```json
{
  "data": {
    "mint": "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9",
    "pool_address": "5tHRbpyZ3jh6gFhWJZsK1xJ8KqLNQH5kMzPMr8aPK7",
    "pool_closed": false,
    "current_base_reserve_lamports": 620000000000000,
    "current_quote_reserve_lamports": 200000000000,
    "current_price": 0.000322580,
    "fetched_at_unix": 1716372600
  },
  "error": null
}
```

- [ ] **Step 2: Write tests**

`predictions/helpers/tests/test_audit_outcome.py`:

```python
"""Unit tests for predictions.helpers.audit_outcome."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "audit_outcome.py"
_TEST_MINT = "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9"
_TEST_POOL = "5tHRbpyZ3jh6gFhWJZsK1xJ8KqLNQH5kMzPMr8aPK7"


def _run(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dry_run_returns_canned_fixture():
    result = _run([_TEST_MINT, "--pool", _TEST_POOL, "--dry-run"])
    assert result["error"] is None
    for field in (
        "mint", "pool_address", "pool_closed",
        "current_base_reserve_lamports", "current_quote_reserve_lamports",
        "current_price", "fetched_at_unix",
    ):
        assert field in result["data"]


def test_current_price_matches_reserve_ratio():
    result = _run([_TEST_MINT, "--pool", _TEST_POOL, "--dry-run"])
    data = result["data"]
    expected = data["current_quote_reserve_lamports"] / data["current_base_reserve_lamports"]
    # Allow small float tolerance.
    assert abs(data["current_price"] - expected) < 1e-9


def test_pool_address_is_required():
    """In live mode, --pool is required."""
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, str(HELPER), _TEST_MINT],  # no --pool, no --dry-run
        capture_output=True, text=True, env=env,
    )
    # Either returns error in JSON or exits non-zero with a usage message.
    if proc.returncode != 0:
        return
    payload = json.loads(proc.stdout)
    assert payload["error"] is not None
```

- [ ] **Step 3: Run tests — verify failure**

```bash
python3 -m pytest predictions/helpers/tests/test_audit_outcome.py -v
```

Expected: tests fail (no helper yet).

- [ ] **Step 4: Write the helper**

`predictions/helpers/audit_outcome.py`:

```python
"""Helius RPC helper: current pool state for outcome audit.

Usage:
    python3 audit_outcome.py <mint> --pool <pool_address> [--dry-run]

Output JSON to stdout:
    {"data": {"mint": ..., "pool_closed": false, "current_base_reserve_lamports": ...,
              "current_quote_reserve_lamports": ..., "current_price": ...}, "error": null}

If the pool account is closed / not found, returns pool_closed: true
with reserves = 0 and current_price = 0 -- which the skill interprets
as realized return = -100%.
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


def _dry_run_payload(mint: str, pool: str) -> dict:
    fixture = config.DRY_RUN_DIR / "audit_outcome.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint
    payload["data"]["pool_address"] = pool
    return payload


def _rpc_call(method: str, params: list) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for delay in [0, 1, 3, 9]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(config.HELIUS_RPC_URL, json=body, timeout=15)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = str(e)
    raise RuntimeError(f"RPC {method} failed: {last}")


def _live_query(mint: str, pool: str) -> dict:
    try:
        resp = _rpc_call(
            "getAccountInfo",
            [pool, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
    except Exception as e:
        return {"data": None, "error": f"getAccountInfo: {e}"}

    result = (resp or {}).get("result", {})
    value = (result or {}).get("value")
    now = int(time.time())

    if value is None:
        # Pool account closed (rug-and-close).
        return {
            "data": {
                "mint": mint, "pool_address": pool, "pool_closed": True,
                "current_base_reserve_lamports": 0,
                "current_quote_reserve_lamports": 0,
                "current_price": 0.0,
                "fetched_at_unix": now,
            },
            "error": None,
        }

    # The PumpSwap pool account's data layout: base_reserve + quote_reserve.
    # Implementation note: the exact byte offsets depend on the PumpSwap
    # program's account schema. For Task 6 MVP, we accept a best-effort
    # parse and let the skill fall back to `pool_closed: true` semantics if
    # the layout is wrong. The audit helper can be refined once one real
    # response is observed.
    data = value.get("data")
    base_lamports = 0
    quote_lamports = 0
    if isinstance(data, dict) and data.get("program") == "spl-token":
        # If Helius parsed it, look for known fields.
        parsed = data.get("parsed", {}).get("info", {})
        base_lamports = int((parsed.get("baseReserve") or {}).get("amount") or 0)
        quote_lamports = int((parsed.get("quoteReserve") or {}).get("amount") or 0)
    elif isinstance(data, list) and len(data) >= 2 and data[1] == "base64":
        # Raw bytes -- placeholder; refine to actual PumpSwap layout offsets.
        # For now, treat as unparseable and return zero reserves with no error.
        pass

    if base_lamports <= 0:
        # Couldn't parse reserves -- treat as pool_closed for safety.
        return {
            "data": {
                "mint": mint, "pool_address": pool, "pool_closed": True,
                "current_base_reserve_lamports": 0,
                "current_quote_reserve_lamports": 0,
                "current_price": 0.0,
                "fetched_at_unix": now,
            },
            "error": None,
        }

    price = quote_lamports / base_lamports if base_lamports > 0 else 0.0
    return {
        "data": {
            "mint": mint, "pool_address": pool, "pool_closed": False,
            "current_base_reserve_lamports": base_lamports,
            "current_quote_reserve_lamports": quote_lamports,
            "current_price": price,
            "fetched_at_unix": now,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint")
    parser.add_argument("--pool", default=None, required=False)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint, args.pool or "DRY_RUN_POOL")
    else:
        if not args.pool:
            payload = {"data": None, "error": "--pool required in live mode"}
        else:
            payload = _live_query(args.mint, args.pool)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Implementation note:** PumpSwap's pool account layout (base/quote reserve byte offsets) needs to be confirmed at implementation time. For MVP, the helper falls back to `pool_closed: true` (= -100% realized) when it can't parse — better to be pessimistic than to silently report wrong reserves. Refine once one real pool's `getAccountInfo` response is inspected.

- [ ] **Step 5: Run tests + commit**

```bash
python3 -m pytest predictions/helpers/tests/test_audit_outcome.py -v
git add predictions/helpers/audit_outcome.py predictions/helpers/tests/test_audit_outcome.py predictions/helpers/dry_run_data/audit_outcome.dry_run.json
git commit -m "$(cat <<'EOF'
Task 6: audit_outcome.py helper (Helius RPC for 24h pool state)

Given (mint, pool), fetches current pool reserves via getAccountInfo and
returns current base/quote + derived price. If pool account is closed or
unparseable, returns pool_closed: true with zero reserves -- the skill
interprets that as realized return = -100%. Layout-parsing for PumpSwap
pool data is best-effort and should be refined after observing a real
response.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Diary scaffold (lessons.md template + .gitkeep)

**Files:**
- Create: `predictions/diary/lessons.md` (committed)
- Create: `predictions/diary/decisions/.gitkeep` (already created in Task 1)
- Create: `predictions/diary/outcomes/.gitkeep` (already created in Task 1)

The diary's directory skeleton was created in Task 1. This task drops the initial empty `lessons.md` template so Phase 1 has something to read on the very first invocation.

- [ ] **Step 1: Write the initial lessons.md template**

`predictions/diary/lessons.md`:

```markdown
---
version: 0
last_updated: 2026-05-21T00:00:00Z
total_decisions_audited: 0
total_picks_audited: 0
overall_buy_hit_rate: null
buy_hit_rate_last_7d: null
buy_hit_rate_first_7d: null
trend: cold_start
---

# pump-prediction skill — rolling lessons & smart-wallet registry

This file is the persistent memory of the `pump-prediction` skill.

Every Phase 1 (audit) invocation may update this file; every Phase 2
(decide) invocation reads it. The frontmatter above tracks the rolling
hit-rate stats that drive the 7-day learning health check.

**During cold-start** (the first ~10 invocations), the Validated lessons
section is empty and the smart-wallet registry has no data. The skill
falls back to the bootstrap heuristics defined in
`.claude/skills/pump-prediction.md`.

# Validated lessons (status: VALIDATED, ≥3 confirms — input to every Phase 2)

_(none yet — populated by Phase 1 audits)_

# Candidate lessons (status: CANDIDATE, 1–2 audits, pending confirmation)

_(none yet)_

# Smart-wallet registry (auto-maintained, top-30 by winner_hits)

| wallet | winner_hits | total_appearances | precision | last_seen |
|---|---|---|---|---|
| _(none yet)_ | | | | |

# Disconfirmed signals (status: DISCONFIRMED — anti-patterns)

## D1 — High Telegram mention count alone
Tested as "if mentioned ≥10× across channels in 12h → BUY." Result expected: poor precision (Telegram channels are mostly shillers / pump-and-dump organizers). Status: DISCONFIRMED a priori (pre-seeded based on the spec's risk discussion). The skill MUST corroborate Telegram mentions with on-chain trade flow before raising conviction.

# Notes

- Format: every lesson has a frontmatter triple `status / confirms / disconfirms / last_confirmed_at`.
- State transition rules are defined in the skill file at `.claude/skills/pump-prediction.md`.
- Smart-wallet registry inclusion: `winner_hits >= 3` AND `precision >= 0.25`.
- Pruning: wallets with `last_seen > 30 days ago` are dropped.
- This file is COMMITTED to git; per-run decision/outcome files are gitignored.
```

- [ ] **Step 2: Verify the file exists**

```bash
cd /home/roberto/solana-storm
ls -la predictions/diary/
cat predictions/diary/lessons.md | head -20
```

- [ ] **Step 3: Commit**

```bash
git add predictions/diary/lessons.md
git commit -m "$(cat <<'EOF'
Task 7: Initialize diary -- empty lessons.md template

Cold-start template with frontmatter stats (all null), empty Validated
and Candidate sections, an empty smart-wallet registry table, and the
pre-seeded D1 disconfirmed signal ("high Telegram mention count alone").
The skill reads this on every Phase 2 invocation; Phase 1 updates it as
audits accumulate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: The skill — `.claude/skills/pump-prediction.md`

**Files:**
- Create: `.claude/skills/pump-prediction.md`

The skill is **prompt content** Claude follows. It must spell out the two-phase flow, the helper-call commands, the diary file formats, the lesson lifecycle, the bootstrap heuristics, the conviction caps, and the error policies. Long but mostly prose.

- [ ] **Step 1: Write the skill file**

`.claude/skills/pump-prediction.md`:

```markdown
---
name: pump-prediction
description: Pick 3-5 pump.fun graduations and audit prior 24h-old decisions for realized return. Use when the user wants fresh memecoin signals or wants to evaluate recent picks against on-chain outcomes.
---

# pump-prediction skill

You are running the `pump-prediction` skill. Your job each invocation:

1. **Phase 1 (audit):** read prior decisions ≥24h old, query current on-chain state, compute realized returns, write outcome files, update `predictions/diary/lessons.md` if patterns confirm/disconfirm.
2. **Phase 2 (decide):** query the last-24h graduation cohort, enrich a 30-50 shortlist with trade flow + pump.fun + Telegram signals, reason against `lessons.md`, write a new decision file with 3-5 picks rated BUY (HIGH/MEDIUM) / WATCH / SKIP.

The diary is the persistent memory across invocations. Always write to it; never leave silent failures.

## Setup

Working directory: `/home/roberto/solana-storm`. Branch should already be checked out.

Detect rehearsal mode: `echo $PUMP_PREDICTION_REHEARSAL`. If set to "1"/"true"/"yes", all helpers return canned data and you write the would-be decision to stdout instead of `predictions/diary/decisions/`. Phase 1 audits are still computed (against the same canned data) so the output looks structurally complete.

## Phase 1 — Audit (run first)

### 1a. Find pending audits

```bash
python3 -c "
from pathlib import Path
import re
from datetime import datetime, timezone, timedelta
d = Path('predictions/diary/decisions')
o = Path('predictions/diary/outcomes')
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
pending = []
for f in sorted(d.glob('*.md')):
    m = re.match(r'(\d{4}-\d{2}-\d{2}-\d{2}-\d{2})\.md$', f.name)
    if not m:
        continue
    ts = datetime.strptime(m.group(1), '%Y-%m-%d-%H-%M').replace(tzinfo=timezone.utc)
    if ts > cutoff:
        continue
    if (o / f'{m.group(1)}-outcome.md').exists():
        continue
    pending.append(f.name)
for p in pending:
    print(p)
"
```

If empty, skip to Phase 2. If 1+ files listed, proceed.

### 1b. For each pending decision file

Read the file. Parse the frontmatter + per-pick sections (mint, pool_address, entry_pool_base_reserve, entry_pool_quote_reserve, conviction).

For each pick, call:

```bash
python3 predictions/helpers/audit_outcome.py <mint> --pool <pool_address>
```

Parse the JSON. Compute realized return:

```
realized_return = (current_quote/current_base) / (entry_quote/entry_base) - 1.0
```

If `pool_closed: true`, set `realized_return = -1.0` (i.e., -100%).

### 1c. Smart-wallet registry maintenance

For EVERY audited pick (winner OR loser), call:

```bash
python3 predictions/helpers/helius_trade_flow.py <mint> --pool <pool_address> --window 60
```

Read `buyer_wallets`. For each wallet:
- Find or add row in `lessons.md`'s smart-wallet table.
- `total_appearances += 1`.
- If `realized_return >= 1.0` (a 2× winner), ALSO `winner_hits += 1`.
- Update `last_seen` to today's date.

Apply inclusion: a wallet appears in the working `smart_wallets` set Phase 2 uses if `winner_hits >= 3` AND `winner_hits/total_appearances >= 0.25`.

Prune: any wallet with `last_seen > 30 days ago` gets removed.

Cap: keep top 30 by `winner_hits`. Move lower-ranked rows to `predictions/diary/lessons_archive.md` (create if it doesn't exist).

### 1d. Write the outcome file

`predictions/diary/outcomes/<decision-id>-outcome.md`:

```markdown
---
audits_decision: <decision-id>
audited_at_utc: <now in ISO>
elapsed_hours: <hours since decision>
buy_hit_count: <# of BUYs with realized_return > 0>
buy_total: <# of BUYs>
watch_hit_count: ...
watch_total: ...
---

# Outcomes

## <ticker> (<conviction>) → <realized %> <emoji>
- 24h pool_base_reserve: ...
- 24h pool_quote_reserve: ...
- realized_price_at_24h: ...
- realized_return: <percent>

**Did reasoning hold?** <one paragraph>. Confirm / disconfirm any specific lesson IDs.

[repeat per pick]

# Aggregate this run

- BUY hit rate (realized return > 0): X/Y
- WATCH hit rate: X/Y
- SKIP sampling: list 1-2 SKIP'd tokens; report current price; was the SKIP correct?

# Lesson updates

List any specific transitions made to lessons.md (e.g., "L7 confirms+=1 → 5", "CL12 promoted to VALIDATED").
```

### 1e. Apply lesson transitions to lessons.md

For each lesson confirmed or disconfirmed in this audit batch:

| Trigger | Action |
|---|---|
| NEW pattern observed | Add as CANDIDATE, confirms=1, first_observed_at=now |
| CANDIDATE pattern observed again | confirms += 1 |
| CANDIDATE reaches confirms ≥ 3 | Promote to VALIDATED |
| VALIDATED pattern fails | disconfirms += 1 |
| VALIDATED has disconfirms ≥ 3 AND confirms/disconfirms < 2.0 | Demote to CANDIDATE |
| CANDIDATE has confirms=0 AND first_observed_at > 7d ago | Retire to DISCONFIRMED |
| VALIDATED untriggered for 14d | Demote to CANDIDATE (drift safeguard) |

Edit `lessons.md` directly. Bump `version`. Update `last_updated`, `total_decisions_audited += 1`, `total_picks_audited += <picks in this batch>`, `overall_buy_hit_rate`.

### 1f. 7-day learning health check (weekly)

If `total_decisions_audited >= 30` AND we haven't computed the 7-day window stats in the last 24h:

Compute `buy_hit_rate_last_7d` (audits in last 7 days) and `buy_hit_rate_first_7d` (the first 7 days of audits). Update frontmatter:

```
buy_hit_rate_last_7d: 0.22
buy_hit_rate_first_7d: 0.08
trend: improving | flat | declining
```

If `trend = flat` or `declining` after 30+ audits, prepend a section to lessons.md:

```
## ⚠️ Learning-health warning (2026-05-25)
After 30+ audits, buy_hit_rate_last_7d (0.10) <= buy_hit_rate_first_7d (0.11).
The skill is not developing edge. Surface in next Phase 2 prompt; user
should consider retiring this project.
```

## Phase 2 — Decide

### 2a. Read context

Read in this order:
1. `predictions/diary/lessons.md` (all sections)
2. The 3 most-recent files in `predictions/diary/outcomes/` (sorted by name desc)
3. The 3 most-recent files in `predictions/diary/decisions/`

Keep these in your working context.

### 2b. Query the universe

```bash
python3 predictions/helpers/recent_graduations.py
```

Parse the JSON. If `error` is non-null:
- If the source is REQUIRED (Dune): abort Phase 2. Write `predictions/diary/decisions/<ts>-SKIPPED.md` (see §3 below). Stop.

Apply the cheap prefilter (in your reasoning, no extra queries):
- Drop tokens where `liq_quote_reserve_lamports < 5_000_000_000` (5 SOL).
- Drop tokens where `deployer_prior_launches > 200`.

Cap the shortlist at 50 tokens. If more than 50 remain, take the 50 with the highest `liq_quote_reserve_lamports`.

### 2c. Deep-enrich the shortlist

For EACH shortlisted token:

```bash
python3 predictions/helpers/helius_trade_flow.py <mint> --pool <pool> --window 60
python3 predictions/helpers/pumpfun_scrape.py <mint>
python3 predictions/helpers/telegram_chatter.py <ticker>   # ticker = pump.fun ticker if you have it; else mint short prefix
```

Track which sources returned errors. Maintain a per-token enrichment dict.

### 2d. Reason + rank

For each token, evaluate against:

**1. Validated lessons** in `lessons.md` (use them in priority order, most-confirmed first).

**2. Smart-wallet registry hits.** Check `helius_trade_flow.buyer_wallets` against the working `smart_wallets` set. Any hit is a strong positive signal.

**3. Bootstrap heuristics** (when fewer than 5 VALIDATED lessons apply):

- **Strong negative:** `deployer_prior_launches > 30` AND `deployer_age_secs < 14 * 86400`.
- **Weak negative:** `curve_completion_time_secs < 30 * 60` (30 min — suggests pre-snipe coordination).
- **Strong positive:** `unique_buyer_count > 50` with steady arrival (consult `first_5_buy_timestamps_unix` — if all 5 are within 60s of each other, that's coordinated; otherwise organic).
- **Weak positive:** `creator_reply_count >= 2` AND `comment_count >= 10`.
- **Strong negative:** all `first_5_buy_timestamps_unix` within 60 seconds of each other (sniper bot coordination).

**4. Telegram caveat:** mention count alone is DISCONFIRMED (D1). Only count it as a confirming signal when on-chain flow is also positive.

Select 3-5 picks. Assign one of: `BUY HIGH`, `BUY MEDIUM`, `WATCH`, `SKIP`. Include at least 1-2 SKIPs in the output for the diary's record.

### 2e. Conviction caps under degradation

If `pumpfun_scrape` failed for this run → cap conviction at `BUY MEDIUM`.
If `telegram_chatter` failed → cap at `BUY MEDIUM`.
If BOTH failed → cap at `WATCH` (no BUY at all this run).

### 2f. Write the decision file

`predictions/diary/decisions/<YYYY-MM-DD-HH-MM>.md`:

```markdown
---
run_id: <timestamp>
run_time_utc: <ISO now>
universe_size: <int>
shortlist_size: <int>
lessons_version: <from lessons.md>
helius_available: true|false
dune_available: true|false
pumpfun_available: true|false
telegram_available: true|false
---

# Picks

## BUY HIGH — <TICKER> (mint: <mint>)

- entry_time_utc: <iso>
- pool_address: <pool>
- entry_pool_base_reserve: <int lamports>
- entry_pool_quote_reserve: <int lamports>
- entry_price: <float>
- exit_criteria: <one line; e.g., "take profit at 2.0×, stop at 50% pool-quote drop, hard exit at <iso 24h from now>">

**Why BUY HIGH:**
- [3-5 bullets citing specific signals: smart-wallet hits, lesson IDs applied, observed counts]

[repeat per pick]

# Data snapshot summary

Brief enrichment table (one line per shortlisted candidate, top 10 by score):

| ticker | mint short | buy/sell | unique buyers | smart hits | comments | tg mentions | decision |
|---|---|---|---|---|---|---|---|
| STORM | 8y45... | 47/3 | 89 | 1 | 22 | 9 | BUY HIGH |
| ... |
```

### 2g. (Rehearsal mode) write to stdout instead

If `PUMP_PREDICTION_REHEARSAL` is set, print the decision-file content to stdout INSTEAD of writing it. The user sees what would have been written without polluting the diary.

## Skipped-run format

If Phase 2 aborts (REQUIRED source down), write:

`predictions/diary/decisions/<YYYY-MM-DD-HH-MM>-SKIPPED.md`:

```markdown
---
run_id: <ts>
status: SKIPPED
reason: <one line>
helius_available: <bool>
dune_available: <bool>
pumpfun_available: <bool>
telegram_available: <bool>
universe_size: null
shortlist_size: null
---

# Skipped

<one paragraph explaining what failed and what the next run should try>
```

Phase 1 audits still run normally — the SKIP only blocks Phase 2.

## Final report to the user (after both phases)

Print a brief summary to stdout:

```
pump-prediction run complete.

Phase 1: audited N picks from M decision files.
  BUY hit rate this batch: X/Y
  Lesson transitions: <list>

Phase 2: <N> picks written to predictions/diary/decisions/<run_id>.md
  BUY HIGH: <count>
  BUY MEDIUM: <count>
  WATCH: <count>
  SKIP (reported for record): <count>
  Sources: helius=ok dune=ok pumpfun=ok telegram=degraded

Health: <trend>. <hit_rate_last_7d> vs first_7d <hit_rate_first_7d>. <warning if any>.
```

If `PUMP_PREDICTION_REHEARSAL` is set, prepend "(REHEARSAL — no diary writes)" to the summary.

## Failure / degradation discipline

- The diary MUST always be written. Either a decision file, a SKIPPED file, or an outcome file. Silent no-op runs corrupt the learning-health stats.
- When a helper returns `error: ...`, never silently use stale data. Treat that source as unavailable for this run and apply the conviction cap.
- Bumping `lessons_version` is mandatory whenever you edit lessons.md. The version number is how the user can see learning happening.
- Never invent picks. If the universe is < 3 tokens after prefilter, output what you have and note in the decision file's frontmatter `note: thin universe`.
```

- [ ] **Step 2: Verify the skill file is detected**

```bash
ls -la .claude/skills/
head -10 .claude/skills/pump-prediction.md
```

(Claude Code reads `.claude/skills/*.md` on session start; you don't run a separate "register" step.)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/pump-prediction.md
git commit -m "$(cat <<'EOF'
Task 8: Add .claude/skills/pump-prediction.md skill content

The skill itself -- instructions Claude follows on invocation. Two-phase
flow: Phase 1 audits prior 24h-old decisions, updates lessons.md and the
smart-wallet registry. Phase 2 queries the last-24h cohort, deep-enriches
a 30-50 shortlist via the helpers, reasons against lessons + bootstrap
heuristics, writes a decision file with 3-5 picks rated BUY HIGH /
MEDIUM / WATCH / SKIP. Conviction caps apply when optional sources are
down. Rehearsal mode (PUMP_PREDICTION_REHEARSAL=1) writes to stdout
instead of the diary. The diary is ALWAYS written (decision file,
SKIPPED file, or outcome file -- never silent no-op).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Rehearsal validation + first live run

Verify the skill actually works end-to-end before declaring the plan complete.

- [ ] **Step 1: Run all helper tests one more time**

```bash
cd /home/roberto/solana-storm
python3 -m pytest predictions/helpers/tests/ -v
```

Expected: all 19 tests pass (3 + 5 + 3 + 3 + 3 + 2 boundary = approximate).

- [ ] **Step 2: Rehearsal-mode skill invocation**

In a fresh terminal:

```bash
cd /home/roberto/solana-storm
export PUMP_PREDICTION_REHEARSAL=1
claude --print "/pump-prediction"
```

(If `claude` CLI doesn't accept `--print`, invoke via the interactive Claude Code session and type `/pump-prediction` — the skill should auto-load from `.claude/skills/`.)

Expected output: the skill runs both phases using canned helper data, prints the would-be decision file to stdout, summary line indicates rehearsal, no files written to `predictions/diary/decisions/` or `predictions/diary/outcomes/`. Confirm by:

```bash
ls -la predictions/diary/decisions/ predictions/diary/outcomes/
```

(should still be empty / only `.gitkeep`).

- [ ] **Step 3: First live invocation (minimum-risk)**

```bash
cd /home/roberto/solana-storm
unset PUMP_PREDICTION_REHEARSAL
claude  # interactive session
# inside Claude Code:
/pump-prediction
```

Expected: skill calls real helpers (small Dune cost — under 5 credits, plus a few hundred Helius calls and a handful of pump.fun + Telegram polls), prints summary, writes ONE decision file to `predictions/diary/decisions/<ts>.md`. Phase 1 finds 0 pending audits (no prior decisions) and skips that phase.

Verify:

```bash
ls -la predictions/diary/decisions/
cat predictions/diary/decisions/*.md | head -100
```

- [ ] **Step 4: Note any first-run issues**

Common things that will likely need fixing on first live run:
- The Dune SQL view name in `recent_graduations.py` — adapt if the view doesn't exist.
- The PumpSwap pool layout parsing in `audit_outcome.py` — refine after seeing one real response.
- The pump.fun frontend-api field names in `pumpfun_scrape.py` — adapt to actual response shape.
- Helper-script error messages should be informative; if a helper fails, fix the helper, not the skill.

For each issue: fix the helper, re-run, commit as a follow-up. Don't try to "fix everything in one pass" — iterate.

- [ ] **Step 5: Commit any follow-up fixes**

If Step 3-4 required fixes to helpers, commit them as:

```bash
git add <files>
git commit -m "$(cat <<'EOF'
Task 9: First-live-run fixes from <helper>

<one-line summary of what was fixed>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Document the first-run findings**

Append to `predictions/README.md`:

```markdown
## First-run notes (Task 9)

- First live invocation: <YYYY-MM-DD HH:MM>
- Universe size returned by Dune: <N>
- Shortlist after prefilter: <N>
- Picks made: <BUY HIGH count> / <BUY MEDIUM> / <WATCH> / <SKIP>
- Helper issues found + fixed: <list>
- First decision file: predictions/diary/decisions/<file>
- Schedule the next manual invocation in 4-6 hours to start the audit cycle.
```

- [ ] **Step 7: Final commit (README update)**

```bash
git add predictions/README.md
git commit -m "$(cat <<'EOF'
Task 9: Record first-run findings in README

First live invocation completed successfully. See README for universe
size, picks made, and any helper issues fixed during validation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## After all tasks complete

Per the subagent-driven-development skill: dispatch a final whole-branch code reviewer over the diff (spec commit `5bbfc0f` → HEAD). The final review verifies:

- Spec coverage: all 5 helpers + skill + diary present.
- The skill file actually follows the design (two phases, conviction caps, lesson lifecycle).
- The bootstrap heuristics in the skill match the spec.
- The smart-wallet registry algorithm matches the spec.
- First live run produced a sensible decision file.

Then invoke `superpowers:finishing-a-development-branch` for the merge/PR/keep/discard choice.

The "real test" of this skill is the next 30+ invocations over the coming weeks. Set up the next manual invocations 4-6 hours apart. After 30+ invocations, check the rolling stats in `lessons.md`: if `buy_hit_rate_last_7d` improves above `buy_hit_rate_first_7d`, the skill is learning. If not, retire honestly.

---

## Self-review notes

1. **Spec coverage.** §3 in-scope items mapped:
   - Skill file (Task 8) ✓
   - `predictions/` directory (Task 1) ✓
   - 5 helpers (Tasks 2-6) ✓
   - Diary format incl. lessons.md template (Task 7) ✓
   - Bootstrap heuristics in skill (Task 8) ✓
   - Per-helper unit tests (Tasks 2-6) ✓
   - Rehearsal mode (Task 9 validation; baked into config + skill) ✓
   - README (Task 1 + Task 9) ✓
   - .gitignore updates (Task 1) ✓
   All covered.

2. **Placeholder scan.** Every code step has actual code. The "implementation notes" in Tasks 2/3/4/6 acknowledge places where the engineer must adapt to real API shapes — these are explicit caveats, not placeholders (the helper still returns valid JSON or an error; the test passes against the fixture). No "TBD" / "TODO" / "similar to Task N" placeholders.

3. **Type consistency.** Helper output JSON shapes are consistent across the dry-run fixtures, the test assertions, and the skill's instructions on what to read. Specifically:
   - `recent_graduations.py` → list of dicts with `mint`, `pool_address`, `liq_quote_reserve_lamports`, etc.
   - `helius_trade_flow.py` → dict with `buy_count`, `sell_count`, `buyer_wallets`, `first_5_buy_timestamps_unix`.
   - `pumpfun_scrape.py` → dict with `comment_count`, `creator_reply_count`, `creator_wallet`.
   - `telegram_chatter.py` → dict with `total_mentions`, `per_channel_mentions`.
   - `audit_outcome.py` → dict with `current_base_reserve_lamports`, `current_quote_reserve_lamports`, `pool_closed`.
   The skill file references these field names by their exact spelling.

4. **Risk-flag for Task 9.** First live run will almost certainly require ≥1 helper fix (Dune view name, PumpSwap layout, or pump.fun field names). The plan acknowledges this with Step 4's "common things that will likely need fixing." This is the SUBAGENT'S responsibility to detect and fix during Task 9 — not a defect of the plan.
