# pump-prediction v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-agent v1 pump-prediction skill with a multi-agent fund — 4 strategy specialists feeding a Fund Manager — that targets the pre-graduation pump.fun universe under a self-improving lessons.md memory model.

**Architecture:** Cron-fired Python runner dispatches to subagent prompts (Late-Curve / Early-Curve / Smart-Mirror / Catalyst / Fund Manager). Specialists query a shared universe layer (pump.fun /coins scrape + SQLite curve history). The Fund Manager consolidates picks every 4h, applies portfolio rules, runs an internal skeptic pass, and writes the final decision file. Audit machinery polls pending outcomes every 10 min, attributes returns back to the originating specialist, and updates `lessons.md` (the only git-tracked memory file).

**Tech Stack:** Python 3.11+ (existing), SQLite (stdlib), requests, python-dotenv, Helius RPC, Dune Analytics API, CryptoPanic API, Reddit public JSON. Claude `Agent` tool for subagent invocations. CronCreate for scheduling. No new heavy deps.

**Spec:** `docs/superpowers/specs/2026-05-23-pump-prediction-v2-design.md`

---

## File Structure Overview

```
predictions/
├── runner.py                              ← NEW: single cron entry point
├── universe.py                            ← NEW: shared universe API
├── config.py                              ← MODIFY: add CRYPTOPANIC_API_TOKEN, PUMP_V2_HALT
├── helpers/                               (existing)
│   ├── pumpfun_curve_universe.py          ← NEW: pump.fun /coins scrape
│   ├── cryptopanic_feed.py                ← NEW
│   ├── reddit_hot_posts.py                ← NEW
│   ├── dry_run_data/                      (existing pattern)
│   └── tests/                             (existing pattern)
├── agents/                                ← NEW dir
│   ├── __init__.py
│   ├── late_curve.md                      ← subagent prompt template
│   ├── early_curve.md
│   ├── smart_mirror.md
│   ├── catalyst.md
│   ├── fund_manager.md
│   ├── invoker.py                         ← Python harness that loads prompts + calls Agent tool
│   └── tests/
├── audit/                                 ← NEW dir
│   ├── __init__.py
│   ├── processor.py                       ← audit-tick logic
│   ├── pending.jsonl                      ← gitignored work queue
│   └── tests/
├── state/                                 ← NEW dir (gitignored)
│   ├── __init__.py
│   ├── curve_history.db                   ← SQLite (auto-created)
│   ├── smart_wallet_registry.db           ← SQLite (auto-created)
│   ├── specialist_stats.json
│   ├── last_fm_cycle.txt
│   └── error_log.jsonl
├── diary/                                 (existing)
│   ├── lessons.md                         ← MODIFY: new schema with per-specialist sections
│   ├── decisions/                         (existing)
│   ├── outcomes/                          (existing)
│   └── shadow_watches/                    ← NEW dir (gitignored)
├── migrations/                            ← NEW dir
│   ├── __init__.py
│   └── v2_smart_wallet_seed.py
└── tests/                                 ← NEW dir for cross-module integration tests
    ├── __init__.py
    └── test_v2_integration.py

.claude/skills/
├── pump-prediction.md                     ← MODIFY: add v1-deprecated banner
└── pump-fund.md                           ← NEW: v2 user-invokable skill

.env.example                               ← MODIFY: add CRYPTOPANIC_API_TOKEN, PUMP_V2_HALT
.gitignore                                 ← MODIFY: state/, shadow_watches/, audit/pending.jsonl
```

---

## Task 1: Bootstrap directories, gitignore, config, env

**Files:**
- Create: `predictions/agents/__init__.py`, `predictions/audit/__init__.py`, `predictions/state/__init__.py`, `predictions/migrations/__init__.py`, `predictions/tests/__init__.py`, `predictions/diary/shadow_watches/.gitkeep`
- Modify: `.gitignore`, `.env.example`, `predictions/config.py`

- [ ] **Step 1: Create directories with __init__.py files**

```bash
cd /home/roberto/solana-storm
mkdir -p predictions/agents/tests predictions/audit/tests predictions/state predictions/migrations predictions/tests predictions/diary/shadow_watches
touch predictions/agents/__init__.py predictions/agents/tests/__init__.py predictions/audit/__init__.py predictions/audit/tests/__init__.py predictions/state/__init__.py predictions/migrations/__init__.py predictions/tests/__init__.py
touch predictions/diary/shadow_watches/.gitkeep predictions/agents/tests/.gitkeep predictions/audit/tests/.gitkeep predictions/tests/.gitkeep
```

- [ ] **Step 2: Extend .gitignore**

Append to `.gitignore`:
```
# v2 additions
predictions/state/*.db
predictions/state/*.json
predictions/state/*.txt
predictions/state/*.jsonl
predictions/diary/shadow_watches/*.md
predictions/audit/pending.jsonl
```

- [ ] **Step 3: Extend .env.example**

Append to `.env.example`:
```
# pump-prediction v2 additions
CRYPTOPANIC_API_TOKEN=
PUMP_V2_HALT=0
```

- [ ] **Step 4: Add v2 config to predictions/config.py**

Append (do not modify existing constants):
```python
# --- v2 additions ---
CRYPTOPANIC_API_TOKEN = os.environ.get("CRYPTOPANIC_API_TOKEN", "").strip()
PUMP_V2_HALT = os.environ.get("PUMP_V2_HALT", "0").strip() == "1"

# Paths (no automatic mkdir; the relevant modules create their own dirs)
STATE_DIR = _REPO_ROOT / "predictions" / "state"
SHADOW_WATCH_DIR = _REPO_ROOT / "predictions" / "diary" / "shadow_watches"
PENDING_AUDIT_PATH = _REPO_ROOT / "predictions" / "audit" / "pending.jsonl"
CURVE_HISTORY_DB = STATE_DIR / "curve_history.db"
SMART_WALLET_DB = STATE_DIR / "smart_wallet_registry.db"
SPECIALIST_STATS_PATH = STATE_DIR / "specialist_stats.json"
LAST_FM_CYCLE_PATH = STATE_DIR / "last_fm_cycle.txt"
ERROR_LOG_PATH = STATE_DIR / "error_log.jsonl"

# v2 helper config
REDDIT_SUBS = ["CryptoCurrency", "solana", "Cryptomoonshots", "SatoshiStreetBets"]
PUMPFUN_CURVE_BASE = "https://frontend-api-v3.pump.fun"
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
CACHE_DIR = _REPO_ROOT / "predictions" / ".cache"
```

If `_REPO_ROOT` doesn't already exist in `config.py`, add at the top with the other imports:
```python
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[1]
```

- [ ] **Step 5: Verify config imports cleanly**

```bash
cd /home/roberto/solana-storm
python3 -c "from predictions import config; print('OK', config.PUMPFUN_CURVE_BASE, config.STATE_DIR)"
```
Expected: `OK https://frontend-api-v3.pump.fun /home/roberto/solana-storm/predictions/state`

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example predictions/config.py predictions/agents predictions/audit predictions/state predictions/migrations predictions/tests predictions/diary/shadow_watches
git commit -m "v2 bootstrap: directories, gitignore, config keys, env scaffolding

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Curve-history SQLite schema + DAO

**Files:**
- Create: `predictions/state/curve_history.py`
- Test: `predictions/state/tests/test_curve_history.py`, `predictions/state/tests/__init__.py`

- [ ] **Step 1: Write the failing test**

`predictions/state/tests/test_curve_history.py`:
```python
import tempfile
from pathlib import Path
from predictions.state import curve_history

def test_init_creates_schema_idempotently():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        curve_history.init_db(db)
        curve_history.init_db(db)  # idempotent
        with curve_history._connect(db) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"curve_snapshots", "smart_wallet_seed"}.issubset(tables)

def test_record_and_read_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        curve_history.init_db(db)
        curve_history.record_snapshot(db, mint="A" * 44, fetched_at_unix=1000, bonding_curve_pct=42.5,
                                      market_cap_sol=12.3, reply_count=4, recent_trades_count=17)
        rows = curve_history.read_snapshots(db, mint="A" * 44, since_unix=0)
        assert len(rows) == 1
        assert rows[0]["bonding_curve_pct"] == 42.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/roberto/solana-storm
mkdir -p predictions/state/tests && touch predictions/state/tests/__init__.py
pytest predictions/state/tests/test_curve_history.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'predictions.state.curve_history'`

- [ ] **Step 3: Implement curve_history.py**

`predictions/state/curve_history.py`:
```python
"""SQLite DAO for curve-state snapshots and smart-wallet seeds."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS curve_snapshots (
    mint TEXT NOT NULL,
    fetched_at_unix INTEGER NOT NULL,
    bonding_curve_pct REAL,
    market_cap_sol REAL,
    reply_count INTEGER,
    recent_trades_count INTEGER,
    PRIMARY KEY (mint, fetched_at_unix)
);
CREATE INDEX IF NOT EXISTS idx_mint_time ON curve_snapshots(mint, fetched_at_unix);
CREATE TABLE IF NOT EXISTS smart_wallet_seed (
    wallet TEXT PRIMARY KEY,
    first_seen_unix INTEGER,
    last_winner_at_unix INTEGER,
    winner_hits INTEGER DEFAULT 0,
    total_observations INTEGER DEFAULT 0,
    precision REAL DEFAULT 0.0,
    status TEXT DEFAULT 'seeded'
);
"""

@contextmanager
def _connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db(db_path: Path) -> None:
    with _connect(db_path) as con:
        con.executescript(_DDL)

def record_snapshot(db_path: Path, *, mint: str, fetched_at_unix: int,
                    bonding_curve_pct: float | None, market_cap_sol: float | None,
                    reply_count: int | None, recent_trades_count: int | None) -> None:
    with _connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO curve_snapshots(mint, fetched_at_unix, bonding_curve_pct, "
            "market_cap_sol, reply_count, recent_trades_count) VALUES (?,?,?,?,?,?)",
            (mint, fetched_at_unix, bonding_curve_pct, market_cap_sol, reply_count, recent_trades_count),
        )

def read_snapshots(db_path: Path, *, mint: str, since_unix: int) -> list[dict]:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT * FROM curve_snapshots WHERE mint = ? AND fetched_at_unix >= ? ORDER BY fetched_at_unix",
            (mint, since_unix),
        ).fetchall()
        return [dict(r) for r in rows]

def prune_older_than(db_path: Path, *, before_unix: int) -> int:
    with _connect(db_path) as con:
        cur = con.execute("DELETE FROM curve_snapshots WHERE fetched_at_unix < ?", (before_unix,))
        return cur.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest predictions/state/tests/test_curve_history.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add predictions/state/curve_history.py predictions/state/tests/
git commit -m "v2 curve-history: SQLite DAO for time-series snapshots

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: pumpfun_curve_universe helper

**Files:**
- Create: `predictions/helpers/pumpfun_curve_universe.py`
- Create: `predictions/helpers/dry_run_data/pumpfun_curve_universe.dry_run.json`
- Test: `predictions/helpers/tests/test_pumpfun_curve_universe.py`

- [ ] **Step 1: Create dry-run fixture**

`predictions/helpers/dry_run_data/pumpfun_curve_universe.dry_run.json`:
```json
{
  "data": [
    {
      "mint": "EXAMPLECurveMint11111111111111111111111111pump",
      "bonding_curve_pct": 72.4,
      "market_cap_sol": 48.2,
      "creator_wallet": "ExampleCreator111111111111111111111111111111",
      "created_timestamp_unix": 1716290000,
      "reply_count": 12,
      "recent_trades_count": 47,
      "last_trade_timestamp_unix": 1716293400,
      "name": "Example Curve Token",
      "symbol": "EXMP",
      "nsfw": false,
      "is_banned": false
    }
  ],
  "fetched_at_unix": 1716293500,
  "pages_fetched": 1,
  "error": null
}
```

- [ ] **Step 2: Write the failing test**

`predictions/helpers/tests/test_pumpfun_curve_universe.py`:
```python
import json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "predictions" / "helpers" / "pumpfun_curve_universe.py"

def test_dry_run_returns_fixture():
    result = subprocess.run([sys.executable, str(HELPER), "--dry-run"],
                            capture_output=True, text=True, cwd=REPO)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["error"] is None
    assert len(payload["data"]) >= 1
    row = payload["data"][0]
    for key in ("mint", "bonding_curve_pct", "market_cap_sol", "creator_wallet",
                "created_timestamp_unix", "reply_count", "recent_trades_count",
                "last_trade_timestamp_unix", "name", "symbol", "nsfw", "is_banned"):
        assert key in row, f"missing field: {key}"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest predictions/helpers/tests/test_pumpfun_curve_universe.py -v
```
Expected: FAIL with `FileNotFoundError` on the helper.

- [ ] **Step 4: Implement the helper**

`predictions/helpers/pumpfun_curve_universe.py`:
```python
"""pump.fun bonding-curve universe scraper.

Usage:
    python3 pumpfun_curve_universe.py [--dry-run] [--pages N] [--limit N]

Output JSON to stdout:
    {"data": [...], "fetched_at_unix": int, "pages_fetched": int, "error": null|str}
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15
DEFAULT_PAGES = 5
DEFAULT_LIMIT = 50


def _dry_run_payload() -> dict:
    fixture = config.DRY_RUN_DIR / "pumpfun_curve_universe.dry_run.json"
    return json.loads(fixture.read_text())


def _get(url: str) -> list | dict | None:
    for delay in (0, 1, 3, 9):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            if r.status_code >= 400:
                return None
            return r.json()
        except Exception:
            continue
    return None


def _live_query(pages: int, limit: int) -> dict:
    base = config.PUMPFUN_CURVE_BASE.rstrip("/")
    rows: list[dict] = []
    pages_fetched = 0
    for page in range(pages):
        url = (f"{base}/coins?offset={page * limit}&limit={limit}"
               f"&sort=created_timestamp&order=DESC&includeNsfw=false")
        body = _get(url)
        if body is None:
            if pages_fetched == 0:
                return {"data": [], "fetched_at_unix": int(time.time()),
                        "pages_fetched": 0, "error": "pumpfun /coins endpoint failed"}
            break
        if not isinstance(body, list):
            break
        for coin in body:
            if not isinstance(coin, dict):
                continue
            try:
                vs = coin.get("virtual_sol_reserves") or 0
                rs = coin.get("real_sol_reserves") or 0
                tot = coin.get("total_supply") or 0
                if tot:
                    cap_sol = (vs / tot) * (coin.get("total_supply") or 0) / 1e9
                else:
                    cap_sol = (coin.get("market_cap") or 0) / 1e9
            except Exception:
                cap_sol = 0.0
            try:
                progress = float(coin.get("bonding_curve_progress") or 0.0)
            except (TypeError, ValueError):
                progress = 0.0
            rows.append({
                "mint": str(coin.get("mint") or ""),
                "bonding_curve_pct": progress * 100 if progress <= 1.5 else progress,
                "market_cap_sol": float(coin.get("market_cap_sol") or cap_sol),
                "creator_wallet": str(coin.get("creator") or ""),
                "created_timestamp_unix": int((coin.get("created_timestamp") or 0) // 1000),
                "reply_count": int(coin.get("reply_count") or 0),
                "recent_trades_count": int(coin.get("recent_trades") or coin.get("buys", 0) + coin.get("sells", 0)),
                "last_trade_timestamp_unix": int((coin.get("last_trade_timestamp") or 0) // 1000),
                "name": str(coin.get("name") or ""),
                "symbol": str(coin.get("symbol") or ""),
                "nsfw": bool(coin.get("nsfw")),
                "is_banned": bool(coin.get("is_banned")),
            })
        pages_fetched += 1
        time.sleep(0.5)
    return {"data": rows, "fetched_at_unix": int(time.time()),
            "pages_fetched": pages_fetched, "error": None}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = p.parse_args()
    payload = _dry_run_payload() if (args.dry_run or config.is_rehearsal()) else _live_query(args.pages, args.limit)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify**

```bash
pytest predictions/helpers/tests/test_pumpfun_curve_universe.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add predictions/helpers/pumpfun_curve_universe.py predictions/helpers/dry_run_data/pumpfun_curve_universe.dry_run.json predictions/helpers/tests/test_pumpfun_curve_universe.py
git commit -m "v2 pumpfun_curve_universe: paginated /coins scrape for bonding-curve tokens

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: universe.py shared orchestrator

**Files:**
- Create: `predictions/universe.py`
- Test: `predictions/tests/test_universe.py`

- [ ] **Step 1: Write the failing test**

`predictions/tests/test_universe.py`:
```python
import os, subprocess
from predictions import universe

def test_fetch_pregrad_uses_dry_run_when_env_set(monkeypatch):
    monkeypatch.setenv("PUMP_PREDICTION_REHEARSAL", "1")
    result = universe.fetch_pregrad_universe()
    assert result["error"] is None
    assert isinstance(result["data"], list)

def test_fetch_graduated_returns_list(monkeypatch):
    monkeypatch.setenv("PUMP_PREDICTION_REHEARSAL", "1")
    result = universe.fetch_graduated_universe()
    assert result["error"] is None
    assert isinstance(result["data"], list)

def test_record_curve_snapshot_writes_db(tmp_path, monkeypatch):
    monkeypatch.setattr(universe.config, "CURVE_HISTORY_DB", tmp_path / "curve.db")
    universe.record_pregrad_universe([
        {"mint": "A" * 44, "bonding_curve_pct": 50.0, "market_cap_sol": 10.0,
         "reply_count": 1, "recent_trades_count": 2, "fetched_at_unix": 1000}
    ])
    from predictions.state import curve_history
    rows = curve_history.read_snapshots(tmp_path / "curve.db", mint="A" * 44, since_unix=0)
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest predictions/tests/test_universe.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'predictions.universe'`.

- [ ] **Step 3: Implement universe.py**

`predictions/universe.py`:
```python
"""Shared universe API: pregrad (pump.fun /coins) + graduated (Dune)."""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

from predictions import config
from predictions.state import curve_history

_HELPERS = Path(__file__).resolve().parent / "helpers"


def _run_helper(name: str, args: list[str] = ()) -> dict:
    cmd = [sys.executable, str(_HELPERS / name)] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        return {"data": None, "error": f"{name} exit={res.returncode}: {res.stderr[:300]}"}
    try:
        return json.loads(res.stdout)
    except Exception as e:
        return {"data": None, "error": f"{name} parse error: {e}"}


def fetch_pregrad_universe() -> dict:
    return _run_helper("pumpfun_curve_universe.py",
                        ["--dry-run"] if config.is_rehearsal() else [])


def fetch_graduated_universe() -> dict:
    return _run_helper("recent_graduations.py",
                        ["--dry-run"] if config.is_rehearsal() else [])


def record_pregrad_universe(rows: list[dict]) -> int:
    db = config.CURVE_HISTORY_DB
    curve_history.init_db(db)
    count = 0
    for r in rows:
        curve_history.record_snapshot(
            db,
            mint=r.get("mint", ""),
            fetched_at_unix=int(r.get("fetched_at_unix") or time.time()),
            bonding_curve_pct=r.get("bonding_curve_pct"),
            market_cap_sol=r.get("market_cap_sol"),
            reply_count=r.get("reply_count"),
            recent_trades_count=r.get("recent_trades_count"),
        )
        count += 1
    return count
```

- [ ] **Step 4: Run tests to verify**

```bash
pytest predictions/tests/test_universe.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add predictions/universe.py predictions/tests/test_universe.py
git commit -m "v2 universe.py: shared API over pregrad + graduated sources

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: cryptopanic_feed helper

**Files:**
- Create: `predictions/helpers/cryptopanic_feed.py`
- Create: `predictions/helpers/dry_run_data/cryptopanic_feed.dry_run.json`
- Test: `predictions/helpers/tests/test_cryptopanic_feed.py`

- [ ] **Step 1: Create dry-run fixture**

`predictions/helpers/dry_run_data/cryptopanic_feed.dry_run.json`:
```json
{
  "data": {
    "tickers_queried": ["STORM"],
    "posts": [
      {
        "title": "STORM token explodes 200% in 24h",
        "source_domain": "coindesk.com",
        "published_at_unix": 1716290000,
        "votes": {"positive": 14, "negative": 2, "important": 3, "liked": 7},
        "currencies_tagged": ["STORM"],
        "url": "https://cryptopanic.com/news/example"
      }
    ],
    "fetched_at_unix": 1716293000
  },
  "error": null
}
```

- [ ] **Step 2: Write the failing test**

`predictions/helpers/tests/test_cryptopanic_feed.py`:
```python
import json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "predictions" / "helpers" / "cryptopanic_feed.py"

def test_dry_run_returns_fixture():
    result = subprocess.run(
        [sys.executable, str(HELPER), "--tickers", "STORM", "--dry-run"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["error"] is None
    assert payload["data"]["tickers_queried"] == ["STORM"]
    assert len(payload["data"]["posts"]) >= 1
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest predictions/helpers/tests/test_cryptopanic_feed.py -v
```
Expected: FAIL (helper missing).

- [ ] **Step 4: Implement the helper**

`predictions/helpers/cryptopanic_feed.py`:
```python
"""CryptoPanic feed helper. Free tier: 1000 req/day.

Usage:
    python3 cryptopanic_feed.py --tickers STORM,PEPE [--filter hot] [--dry-run]
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15
CACHE_TTL_SEC = 600  # 10 min


def _dry_run_payload() -> dict:
    fixture = config.DRY_RUN_DIR / "cryptopanic_feed.dry_run.json"
    return json.loads(fixture.read_text())


def _cache_path(tickers: list[str], filter_: str) -> Path:
    cache_dir = config.CACHE_DIR / "cryptopanic"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = "_".join(sorted(tickers)) + f"_{filter_}"
    return cache_dir / f"{key}.json"


def _read_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > CACHE_TTL_SEC:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.rename(path)


def _live_query(tickers: list[str], filter_: str) -> dict:
    if not config.CRYPTOPANIC_API_TOKEN:
        return {"data": None, "error": "CRYPTOPANIC_API_TOKEN missing"}

    cache_path = _cache_path(tickers, filter_)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    currencies = ",".join(t.upper() for t in tickers)
    url = (f"{config.CRYPTOPANIC_BASE}/posts/?auth_token={config.CRYPTOPANIC_API_TOKEN}"
           f"&currencies={currencies}&filter={filter_}&public=true")
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return {"data": None, "error": f"cryptopanic http {r.status_code}"}
        body = r.json()
    except Exception as e:
        return {"data": None, "error": f"cryptopanic error: {e}"}

    posts = []
    for p in (body.get("results") or []):
        try:
            pub = datetime.fromisoformat(p.get("published_at", "").replace("Z", "+00:00"))
            published_unix = int(pub.timestamp())
        except Exception:
            published_unix = 0
        posts.append({
            "title": p.get("title") or "",
            "source_domain": ((p.get("source") or {}).get("domain")) or "",
            "published_at_unix": published_unix,
            "votes": p.get("votes") or {},
            "currencies_tagged": [c.get("code", "") for c in (p.get("currencies") or [])],
            "url": p.get("url") or "",
        })

    payload = {"data": {"tickers_queried": tickers, "posts": posts,
                        "fetched_at_unix": int(time.time())}, "error": None}
    _write_cache(cache_path, payload)
    return payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", required=False, default="")
    p.add_argument("--filter", default="hot")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload()
    elif not tickers:
        payload = {"data": None, "error": "--tickers required for live query"}
    else:
        payload = _live_query(tickers, args.filter)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test**

```bash
pytest predictions/helpers/tests/test_cryptopanic_feed.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add predictions/helpers/cryptopanic_feed.py predictions/helpers/dry_run_data/cryptopanic_feed.dry_run.json predictions/helpers/tests/test_cryptopanic_feed.py
git commit -m "v2 cryptopanic_feed: tagged-currency news with 10-min cache

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: reddit_hot_posts helper

**Files:**
- Create: `predictions/helpers/reddit_hot_posts.py`
- Create: `predictions/helpers/dry_run_data/reddit_hot_posts.dry_run.json`
- Test: `predictions/helpers/tests/test_reddit_hot_posts.py`

- [ ] **Step 1: Create dry-run fixture**

`predictions/helpers/dry_run_data/reddit_hot_posts.dry_run.json`:
```json
{
  "data": {
    "tickers_queried": ["STORM"],
    "posts": [
      {
        "subreddit": "Cryptomoonshots",
        "title": "$STORM is the next 100x — DYOR",
        "author": "exampleuser",
        "created_utc": 1716290000,
        "score": 47,
        "num_comments": 12,
        "permalink": "/r/Cryptomoonshots/comments/abc/example/",
        "matched_tickers": ["STORM"]
      }
    ],
    "fetched_at_unix": 1716293000
  },
  "error": null
}
```

- [ ] **Step 2: Write the failing test**

`predictions/helpers/tests/test_reddit_hot_posts.py`:
```python
import json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "predictions" / "helpers" / "reddit_hot_posts.py"

def test_dry_run_returns_fixture():
    result = subprocess.run(
        [sys.executable, str(HELPER), "--tickers", "STORM", "--dry-run"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["error"] is None
    assert payload["data"]["tickers_queried"] == ["STORM"]
    assert any("STORM" in p["matched_tickers"] for p in payload["data"]["posts"])
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest predictions/helpers/tests/test_reddit_hot_posts.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement helper**

`predictions/helpers/reddit_hot_posts.py`:
```python
"""Reddit public-JSON hot/new posts scanner.

Usage:
    python3 reddit_hot_posts.py --tickers STORM,PEPE [--max-age-sec 3600] [--dry-run]
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15
CACHE_TTL_SEC = 600


def _dry_run_payload() -> dict:
    fixture = config.DRY_RUN_DIR / "reddit_hot_posts.dry_run.json"
    return json.loads(fixture.read_text())


def _cache_path(sub: str, sort: str) -> Path:
    cache_dir = config.CACHE_DIR / "reddit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{sub}_{sort}.json"


def _read_cache(path: Path) -> dict | None:
    if not path.exists() or (time.time() - path.stat().st_mtime) > CACHE_TTL_SEC:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, body: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body))
    tmp.rename(path)


def _fetch_sub(sub: str, sort: str = "new", limit: int = 100) -> dict | None:
    cached = _read_cache(_cache_path(sub, sort))
    if cached is not None:
        return cached
    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return None
        body = r.json()
        _write_cache(_cache_path(sub, sort), body)
        return body
    except Exception:
        return None


def _extract_posts(body: dict, sub: str, since_unix: int, ticker_patterns: list[tuple[str, re.Pattern]]) -> list[dict]:
    out = []
    for child in (body.get("data", {}).get("children") or []):
        d = child.get("data") or {}
        created = int(d.get("created_utc") or 0)
        if created < since_unix:
            continue
        text = (d.get("title") or "") + " " + (d.get("selftext") or "")
        matched = [t for t, pat in ticker_patterns if pat.search(text)]
        if not matched:
            continue
        out.append({
            "subreddit": sub,
            "title": d.get("title") or "",
            "author": d.get("author") or "",
            "created_utc": created,
            "score": int(d.get("score") or 0),
            "num_comments": int(d.get("num_comments") or 0),
            "permalink": d.get("permalink") or "",
            "matched_tickers": matched,
        })
    return out


def _live_query(tickers: list[str], max_age_sec: int) -> dict:
    since = int(time.time()) - max_age_sec
    patterns = [(t, re.compile(rf"[\$#]?{re.escape(t)}\b", re.IGNORECASE)) for t in tickers]
    all_posts: list[dict] = []
    for sub in config.REDDIT_SUBS:
        for sort in ("new", "hot"):
            body = _fetch_sub(sub, sort)
            if body is None:
                continue
            all_posts.extend(_extract_posts(body, sub, since, patterns))
            time.sleep(1)
    return {"data": {"tickers_queried": tickers, "posts": all_posts,
                     "fetched_at_unix": int(time.time())}, "error": None}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", required=False, default="")
    p.add_argument("--max-age-sec", type=int, default=3600)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload()
    elif not tickers:
        payload = {"data": None, "error": "--tickers required for live query"}
    else:
        payload = _live_query(tickers, args.max_age_sec)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test**

```bash
pytest predictions/helpers/tests/test_reddit_hot_posts.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add predictions/helpers/reddit_hot_posts.py predictions/helpers/dry_run_data/reddit_hot_posts.dry_run.json predictions/helpers/tests/test_reddit_hot_posts.py
git commit -m "v2 reddit_hot_posts: ticker scan across 4 subs with 10-min cache

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Audit processor (pending.jsonl + outcome resolution)

**Files:**
- Create: `predictions/audit/processor.py`
- Test: `predictions/audit/tests/test_processor.py`

- [ ] **Step 1: Write failing test**

`predictions/audit/tests/test_processor.py`:
```python
import json
from pathlib import Path
from predictions.audit import processor

def test_enqueue_and_pop_due():
    pending = Path("/tmp/storm_test_pending.jsonl")
    pending.unlink(missing_ok=True)
    processor.enqueue(pending, {
        "pick_id": "p1", "mint": "A" * 44, "pool": "B" * 44,
        "specialist": "late_curve", "entry_quote_lamports": 1_000_000,
        "entry_base_lamports": 2_000_000, "due_unix": 1, "exit_rule": "graduation_or_30pct_or_6h"
    })
    processor.enqueue(pending, {
        "pick_id": "p2", "mint": "C" * 44, "pool": "D" * 44,
        "specialist": "catalyst", "entry_quote_lamports": 1_000,
        "entry_base_lamports": 2_000, "due_unix": 9999999999, "exit_rule": "narrative"
    })
    due, remaining = processor.partition_due(pending, now_unix=10)
    assert len(due) == 1 and due[0]["pick_id"] == "p1"
    assert len(remaining) == 1 and remaining[0]["pick_id"] == "p2"

def test_compute_return_normal_pool():
    ret = processor.compute_realized_return(
        entry_quote=100_000_000, entry_base=200_000_000,
        current_quote=80_000_000, current_base=240_000_000, pool_closed=False)
    # entry_price = 0.5; current_price = 0.333...; ret ≈ -0.333
    assert abs(ret - (-0.3333)) < 0.001

def test_compute_return_rugged_pool():
    ret = processor.compute_realized_return(0, 0, 0, 0, pool_closed=True)
    assert ret == -1.0
```

- [ ] **Step 2: Run to verify fail**

```bash
pytest predictions/audit/tests/test_processor.py -v
```
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement processor.py**

`predictions/audit/processor.py`:
```python
"""Audit-tick processor: read pending.jsonl, audit due items, write outcomes."""
from __future__ import annotations
import json, os, time
from pathlib import Path
from typing import Iterable

from predictions import config


def enqueue(pending_path: Path, entry: dict) -> None:
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    with pending_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _read_all(pending_path: Path) -> list[dict]:
    if not pending_path.exists():
        return []
    out = []
    for line in pending_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def partition_due(pending_path: Path, now_unix: int) -> tuple[list[dict], list[dict]]:
    items = _read_all(pending_path)
    due = [it for it in items if int(it.get("due_unix") or 0) <= now_unix]
    remaining = [it for it in items if int(it.get("due_unix") or 0) > now_unix]
    return due, remaining


def rewrite(pending_path: Path, items: list[dict]) -> None:
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pending_path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    tmp.replace(pending_path)


def compute_realized_return(entry_quote: int, entry_base: int,
                            current_quote: int, current_base: int,
                            pool_closed: bool) -> float:
    if pool_closed:
        return -1.0
    if not (entry_quote and entry_base and current_base):
        return 0.0
    entry_price = entry_quote / entry_base
    current_price = current_quote / current_base
    return (current_price / entry_price) - 1.0


def write_outcome(pick_id: str, payload: dict) -> Path:
    out_dir = config._REPO_ROOT / "predictions" / "diary" / "outcomes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{pick_id}-outcome.md"
    body = "---\n"
    for k, v in payload.items():
        body += f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v}\n"
    body += "---\n"
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out_path)
    return out_path
```

- [ ] **Step 4: Run tests**

```bash
pytest predictions/audit/tests/test_processor.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add predictions/audit/processor.py predictions/audit/tests/
git commit -m "v2 audit/processor: pending.jsonl queue + realized-return + outcome writer

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Late-Curve specialist (subagent prompt + Python invocation)

**Files:**
- Create: `predictions/agents/late_curve.md`
- Create: `predictions/agents/invoker.py`
- Test: `predictions/agents/tests/test_invoker.py`

- [ ] **Step 1: Write subagent prompt**

`predictions/agents/late_curve.md`:
```markdown
# Late-Curve Momentum Agent

You are the Late-Curve Momentum specialist in a multi-agent pump.fun fund. Your role: identify bonding-curve tokens at 60–95% completion with accelerating buy velocity, suitable for short-horizon momentum entry. Exit at graduation, −30% stop, or 6h timeout.

## Inputs you receive
- A JSON snapshot `universe` of pre-grad tokens with bonding_curve_pct, market_cap_sol, reply_count, recent_trades_count, etc.
- A JSON `curve_history` map: for each candidate mint, an array of historical snapshots (most recent first) so you can compute velocity.
- The full content of `lessons.md` — apply VALIDATED lessons as hard veto, candidate lessons as soft penalties.
- The `## Late-Curve Lessons` section of lessons.md is YOUR specialist-specific memory.

## Hard rules
1. NEVER emit a BUY when any VALIDATED global lesson fires (e.g., C1 ATH/MC > 10×). If a token would otherwise be BUY but a VALIDATED veto fires, emit a SHADOW_WATCH instead (record but don't BUY).
2. If the deployer is on the known-farmer registry (in lessons.md), SKIP unconditionally.
3. If first-5 buy timestamps span < 60s (sniper coordination, C3), SKIP.
4. Include at least one SKIP in your output for the diary record.

## Conviction tiers (use exactly these strings)
- `BUY HIGH`: strong positive velocity + ≥10 unique buyers + organic spread + no negative signals
- `BUY MEDIUM`: positive velocity + clean deployer + at least one mild positive
- `WATCH`: borderline — strong on one axis, weak elsewhere
- `SKIP`: any negative signal dominates

## Output format (STRICT — write to stdout as JSON)
```json
{
  "specialist": "late_curve",
  "run_time_utc": "<iso>",
  "universe_size": <int>,
  "shortlist_size": <int>,
  "lessons_version": <int>,
  "picks": [
    {
      "mint": "<base58>",
      "ticker": "<symbol>",
      "conviction": "BUY HIGH | BUY MEDIUM | WATCH | SKIP",
      "recommended_exit": {
        "rule": "graduation_or_30pct_or_6h",
        "take_profit_pct": null,
        "stop_loss_pct": -0.30,
        "hard_timeout_hours": 6
      },
      "reasoning": "<2-4 sentences citing specific numbers>",
      "lesson_citations": ["C1", "C2", ...]
    }
  ],
  "shadow_watches": [
    {"mint": "...", "would_be_conviction": "BUY MEDIUM", "vetoed_by": "C1", "reasoning": "..."}
  ]
}
```

## Reasoning skeleton (apply in order)
1. Filter universe to `bonding_curve_pct ∈ [60, 95]` and `created_timestamp` within last 24h.
2. For each candidate, compute Δ`bonding_curve_pct` over last 15 min (from `curve_history`). Flag if Δ > 5%.
3. Compute `recent_trades_count` rate of change. Flag if accelerating.
4. Check `creator_wallet` against the known-farmer registry. Veto on hit.
5. If `first_5_buy_timestamps` unavailable (would require Helius call) — leave that check to the FM skeptic pass.
6. Score remaining candidates by velocity × inverse(C1 ratio if known, else 1).
7. Emit top 3 candidates as picks with conviction tiers based on signal strength.
8. Always include at least 1 SKIP entry naming a specific rejection reason.
```

- [ ] **Step 2: Write failing test for invoker**

`predictions/agents/tests/test_invoker.py`:
```python
import json, os
from pathlib import Path
from predictions.agents import invoker

def test_load_prompt_returns_string():
    text = invoker.load_prompt("late_curve")
    assert "Late-Curve Momentum Agent" in text
    assert "BUY HIGH" in text

def test_build_context_includes_universe_and_lessons(tmp_path, monkeypatch):
    lessons = tmp_path / "lessons.md"
    lessons.write_text("# lessons\n")
    monkeypatch.setattr(invoker.config, "_REPO_ROOT", tmp_path)
    (tmp_path / "predictions" / "diary").mkdir(parents=True)
    (tmp_path / "predictions" / "diary" / "lessons.md").write_text("# lessons file\nC1 ...")
    ctx = invoker.build_context("late_curve", universe={"data": [{"mint": "A" * 44}]}, curve_history={})
    assert ctx["lessons_md"].startswith("# lessons file")
    assert ctx["universe"]["data"][0]["mint"] == "A" * 44
```

- [ ] **Step 3: Run failing test**

```bash
pytest predictions/agents/tests/test_invoker.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implement invoker.py**

`predictions/agents/invoker.py`:
```python
"""Subagent invocation harness. Loads markdown prompt templates and builds context dicts.

NOTE: actual Claude `Agent` tool invocation happens from the runner. This module is
the prep + parse layer so it can be unit-tested without hitting the LLM.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from predictions import config

_PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    return path.read_text()


def _load_lessons() -> str:
    p = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
    return p.read_text() if p.exists() else ""


def build_context(specialist: str, *, universe: dict, curve_history: dict | None = None,
                  extras: dict[str, Any] | None = None) -> dict:
    return {
        "specialist": specialist,
        "prompt_template": load_prompt(specialist),
        "lessons_md": _load_lessons(),
        "universe": universe,
        "curve_history": curve_history or {},
        "extras": extras or {},
    }


def parse_specialist_output(stdout: str) -> dict:
    """Parse the JSON the specialist subagent prints. Tolerant of leading/trailing text."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < 0:
        return {"error": "no JSON object found in subagent output", "raw": stdout[:500]}
    try:
        return json.loads(stdout[start:end + 1])
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse: {e}", "raw": stdout[start:end + 1][:500]}
```

- [ ] **Step 5: Run tests**

```bash
pytest predictions/agents/tests/test_invoker.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add predictions/agents/late_curve.md predictions/agents/invoker.py predictions/agents/tests/
git commit -m "v2 late_curve agent: subagent prompt + invocation harness

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Early-Curve specialist

**Files:**
- Create: `predictions/agents/early_curve.md`

- [ ] **Step 1: Write subagent prompt**

`predictions/agents/early_curve.md`:
```markdown
# Early-Curve Quality Agent

You are the Early-Curve Quality specialist. Your role: find tokens at 10-30% bonding-curve completion that look organic — active dev, healthy holder distribution, non-farmer deployer. Bet on curve completion + post-grad cushion. Exit at +200%, −50% stop, or 7-day timeout.

## Inputs
- Pre-grad universe filtered (by caller) to `bonding_curve_pct ∈ [10, 30]` AND age < 6h
- Per-candidate: `creator_wallet` history, `reply_count`, holder distribution (top-10 % via Helius)
- Full lessons.md; `## Early-Curve Lessons` is your specialist memory

## Hard rules
1. VALIDATED global lessons veto → SHADOW_WATCH not BUY.
2. Deployer on known-farmer registry → unconditional SKIP.
3. Top-1 holder > 25% → SKIP (concentrated supply = rug risk).
4. Include at least one SKIP per run.

## Conviction tiers
- `BUY HIGH`: reply_count ≥ 5 + creator previously graduated a token + top-10 holders < 40%
- `BUY MEDIUM`: 2 of those 3 positives
- `WATCH`: 1 positive
- `SKIP`: 0 positives or any negative trigger

## Output format
Same JSON schema as the late_curve specialist, with:
- `specialist: "early_curve"`
- `recommended_exit.rule: "+200pct_or_-50pct_or_7d"`
- `recommended_exit.take_profit_pct: 2.0`
- `recommended_exit.stop_loss_pct: -0.50`
- `recommended_exit.hard_timeout_hours: 168`

## Reasoning skeleton
1. Filter universe to early-curve window.
2. For each candidate, fetch top-10 holder distribution (via Helius `getTokenLargestAccounts`; caller passes this in `extras`).
3. Check creator wallet against known-farmer registry AND smart-wallet registry (for prior graduations).
4. Score: positives count − top1_holder_concentration_penalty − farmer_penalty.
5. Emit top 3 plus 1 SKIP.
```

- [ ] **Step 2: Verify file loads through invoker**

```bash
cd /home/roberto/solana-storm
python3 -c "from predictions.agents import invoker; print('OK' if 'Early-Curve' in invoker.load_prompt('early_curve') else 'FAIL')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add predictions/agents/early_curve.md
git commit -m "v2 early_curve agent: subagent prompt for 10-30%-curve quality picks

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Smart-Mirror specialist

**Files:**
- Create: `predictions/agents/smart_mirror.md`

- [ ] **Step 1: Write subagent prompt**

`predictions/agents/smart_mirror.md`:
```markdown
# Smart-Mirror Agent

You are the Smart-Mirror specialist. Your role: mirror entries by wallets that have consistently profited on pump.fun curve trades. Exit when the followed wallet exits, or −30% stop, or 7-day timeout. **Concurrent position cap: 5.**

## Dormancy gate
If the `smart_wallet_seed` table has < 5 wallets with `precision ≥ 0.3` AND `total_observations ≥ 10`, you are DORMANT. Output:
```json
{"specialist": "smart_mirror", "status": "dormant", "picks": [], "shadow_watches": [], "dormancy_reason": "..."}
```

## Active-mode inputs
- The current `smart_wallet_registry` (top 30 by precision)
- For each registry wallet: recent buys (via Helius `getSignaturesForAddress` filtered to last 1h, caller passes in `extras`)
- Pre-grad universe (so you can filter recent buys to tokens still on curve)
- Full lessons.md; `## Smart-Mirror Lessons` is your memory

## Hard rules
1. VALIDATED global lessons veto → SHADOW_WATCH not BUY.
2. SKIP if token's deployer is on known-farmer registry.
3. Higher conviction proportional to the followed wallet's `precision`.
4. If 2+ registry wallets bought the same token recently, BUY HIGH (convergence signal).
5. Cap at 5 concurrent picks.

## Conviction tiers
- `BUY HIGH`: ≥2 registry wallets bought, AND each wallet's precision ≥ 0.4
- `BUY MEDIUM`: 1 wallet with precision ≥ 0.4, OR 2+ wallets with precision ≥ 0.3
- `WATCH`: 1 wallet with precision 0.3-0.4
- `SKIP`: precision < 0.3 or other signal blocks

## Output format
Same as other specialists, with:
- `specialist: "smart_mirror"`
- `recommended_exit.rule: "mirror_followed_wallet"`
- `recommended_exit.followed_wallets: ["wallet1", "wallet2", ...]`
- `recommended_exit.stop_loss_pct: -0.30`
- `recommended_exit.hard_timeout_hours: 168`

## Reasoning skeleton
1. Check dormancy gate first. If dormant, emit dormant payload and exit.
2. For each token bought by ≥1 registry wallet in last 1h: compute conviction by precision-weighted convergence.
3. Filter out tokens with C1 firing (VALIDATED), known-farmer deployer.
4. Pick top by conviction-score, capped at 5.
```

- [ ] **Step 2: Verify load**

```bash
python3 -c "from predictions.agents import invoker; print('OK' if 'Smart-Mirror' in invoker.load_prompt('smart_mirror') else 'FAIL')"
```

- [ ] **Step 3: Commit**

```bash
git add predictions/agents/smart_mirror.md
git commit -m "v2 smart_mirror agent: mirror profitable curve traders with dormancy gate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Catalyst specialist

**Files:**
- Create: `predictions/agents/catalyst.md`

- [ ] **Step 1: Write subagent prompt**

`predictions/agents/catalyst.md`:
```markdown
# Catalyst Agent

You are the Catalyst specialist. Your role: identify tokens with active narrative tailwinds (CEX listing, viral Reddit posts, news mentions) on the hour-scale, BEFORE on-chain action fully prices them in. Exit at +50%, −20% stop, or 24h timeout.

## Inputs
- `cryptopanic_feed` results (tagged posts in last 1h)
- `reddit_hot_posts` results (matched-ticker posts in last 1h across 4 subs)
- Both pre-grad AND graduated universe (catalysts can hit either)
- Full lessons.md; `## Catalyst Lessons` is your memory

## Hard rules
1. VALIDATED global lessons veto → SHADOW_WATCH not BUY.
2. SKIP if deployer is known-farmer.
3. SKIP if ticker is a generic English word (high false-positive risk in Reddit regex). Examples to reject: `joy, hire, create, world, game` unless multiple high-signal sources confirm.
4. Include at least one SKIP per run.

## Conviction tiers
- `BUY HIGH`: ≥3 distinct sources mention ticker, mention_velocity > 2× (last-1h vs last-4h), sentiment_proxy > 0, on-chain trades reacting (>5 buys in last 1h)
- `BUY MEDIUM`: 2 sources, modest velocity, positive sentiment
- `WATCH`: 1 source only OR sentiment unclear
- `SKIP`: generic name, shill-only sources, negative sentiment

## Output format
Same schema, with:
- `specialist: "catalyst"`
- `recommended_exit.rule: "+50pct_or_-20pct_or_24h"`
- `recommended_exit.take_profit_pct: 0.5`
- `recommended_exit.stop_loss_pct: -0.20`
- `recommended_exit.hard_timeout_hours: 24`

## Reasoning skeleton
1. Aggregate mentions across CryptoPanic + Reddit by ticker.
2. Cross-reference to pump.fun universe (pre-grad or graduated): does the ticker correspond to a real mint?
3. Compute mention_velocity, source_diversity, sentiment_proxy.
4. Apply hard rules.
5. Emit top 3 picks + at least 1 SKIP with reason cited.
```

- [ ] **Step 2: Verify load**

```bash
python3 -c "from predictions.agents import invoker; print('OK' if 'Catalyst Agent' in invoker.load_prompt('catalyst') else 'FAIL')"
```

- [ ] **Step 3: Commit**

```bash
git add predictions/agents/catalyst.md
git commit -m "v2 catalyst agent: narrative-driven picks via CryptoPanic + Reddit

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Fund Manager (subagent prompt + allocation logic)

**Files:**
- Create: `predictions/agents/fund_manager.md`
- Create: `predictions/agents/fm_allocation.py`
- Test: `predictions/agents/tests/test_fm_allocation.py`

- [ ] **Step 1: Write failing test for allocation logic**

`predictions/agents/tests/test_fm_allocation.py`:
```python
from predictions.agents import fm_allocation

def test_cold_start_equal_weight():
    weights = fm_allocation.specialist_weights(
        stats={"late_curve": {"picks_audited": 5}, "early_curve": {"picks_audited": 3}}
    )
    assert weights["late_curve"] == 1.0 and weights["early_curve"] == 1.0

def test_mature_uses_hit_rate():
    weights = fm_allocation.specialist_weights(
        stats={"late_curve": {"picks_audited": 50, "hit_rate_last_30d": 0.25},
               "early_curve": {"picks_audited": 50, "hit_rate_last_30d": 0.10}}
    )
    assert weights["late_curve"] == 0.25
    assert weights["early_curve"] == 0.10

def test_floor_is_0_1():
    weights = fm_allocation.specialist_weights(
        stats={"late_curve": {"picks_audited": 50, "hit_rate_last_30d": 0.02}}
    )
    assert weights["late_curve"] == 0.1

def test_score_pick_basic():
    s = fm_allocation.score_pick(
        specialist="late_curve", conviction="BUY HIGH",
        specialist_weight=1.0,
        validated_lesson_fires=False, candidate_lesson_fires=0,
        convergence_count=1,
    )
    assert s == 1.0

def test_score_validated_veto():
    s = fm_allocation.score_pick(
        specialist="late_curve", conviction="BUY HIGH", specialist_weight=1.0,
        validated_lesson_fires=True, candidate_lesson_fires=0, convergence_count=1,
    )
    assert s == 0.0

def test_score_convergence_bonus():
    s_single = fm_allocation.score_pick("c", "BUY HIGH", 1.0, False, 0, 1)
    s_double = fm_allocation.score_pick("c", "BUY HIGH", 1.0, False, 0, 2)
    assert s_double > s_single
    assert abs(s_double - 1.1) < 0.001

def test_sizing_caps():
    sizes = fm_allocation.compute_sizes(
        scored_picks=[("p1", 1.0), ("p2", 0.5), ("p3", 0.1)],
        cold_start=False,
    )
    assert sum(sizes.values()) <= 0.80 + 1e-6
    assert max(sizes.values()) <= 0.20 + 1e-6

def test_cold_start_more_conservative():
    sizes = fm_allocation.compute_sizes(
        scored_picks=[("p1", 1.0)],
        cold_start=True,
    )
    assert max(sizes.values()) <= 0.10 + 1e-6
```

- [ ] **Step 2: Run failing test**

```bash
pytest predictions/agents/tests/test_fm_allocation.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement fm_allocation.py**

`predictions/agents/fm_allocation.py`:
```python
"""Pure functions for FM allocation math. No I/O — fully unit-testable."""
from __future__ import annotations

COLD_START_PICKS_AUDITED_THRESHOLD = 30
COLD_START_FM_TOTAL_THRESHOLD = 20
CONVICTION_MULT = {"BUY HIGH": 1.0, "BUY MEDIUM": 0.6, "WATCH": 0.2, "SKIP": 0.0}

MAX_POSITION_PCT_MATURE = 0.20
MAX_POSITION_PCT_COLD = 0.10
MAX_BOOK_DEPLOYED_MATURE = 0.80
MAX_BOOK_DEPLOYED_COLD = 0.50
MIN_POSITION_PCT = 0.02


def specialist_weights(stats: dict) -> dict[str, float]:
    out = {}
    for spec, s in stats.items():
        audited = int(s.get("picks_audited", 0))
        if audited < COLD_START_PICKS_AUDITED_THRESHOLD:
            out[spec] = 1.0
        else:
            hr = float(s.get("hit_rate_last_30d") or 0.0)
            out[spec] = max(0.1, hr)
    return out


def score_pick(specialist: str, conviction: str, specialist_weight: float,
               validated_lesson_fires: bool, candidate_lesson_fires: int,
               convergence_count: int) -> float:
    if validated_lesson_fires:
        return 0.0
    base = specialist_weight * CONVICTION_MULT.get(conviction, 0.0)
    penalty = min(1.0, 0.3 * candidate_lesson_fires)
    bonus = 0.1 if convergence_count >= 2 else 0.0
    return max(0.0, base * (1 - penalty) + bonus)


def compute_sizes(scored_picks: list[tuple[str, float]], cold_start: bool) -> dict[str, float]:
    """Inputs: [(pick_id, score)]. Returns {pick_id: size_pct} respecting caps."""
    if not scored_picks:
        return {}
    max_pos = MAX_POSITION_PCT_COLD if cold_start else MAX_POSITION_PCT_MATURE
    max_book = MAX_BOOK_DEPLOYED_COLD if cold_start else MAX_BOOK_DEPLOYED_MATURE

    scores = [s for _, s in scored_picks if s > 0]
    if not scores:
        return {}
    max_score = max(scores)
    raw = {pid: min(s / max_score * max_pos, max_pos) for pid, s in scored_picks if s > 0}
    raw = {pid: s for pid, s in raw.items() if s >= MIN_POSITION_PCT}

    total = sum(raw.values())
    if total > max_book:
        scale = max_book / total
        raw = {pid: s * scale for pid, s in raw.items()}
    return raw


def is_cold_start_total(total_picks_audited: int) -> bool:
    return total_picks_audited < COLD_START_FM_TOTAL_THRESHOLD
```

- [ ] **Step 4: Run tests**

```bash
pytest predictions/agents/tests/test_fm_allocation.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Write FM subagent prompt**

`predictions/agents/fund_manager.md`:
```markdown
# Pump Fund Manager

You are the Pump Fund Manager — the decider that consolidates 4 specialist outputs into a final allocation decision every 4h.

## Inputs
- All specialist decision JSONs from the current cycle (paths in `extras.specialist_outputs`)
- Allocation weights from `extras.specialist_weights` (pre-computed by Python; do NOT recompute)
- `extras.scored_picks` and `extras.recommended_sizes` (pre-computed)
- Full lessons.md including `## Fund Manager Lessons` (your memory)
- Current `total_picks_audited` (drives cold-start mode)

## Your job
1. Verify the pre-computed scores and sizes look reasonable. Flag anything suspicious in your reasoning.
2. Run an internal skeptic pass on EACH non-SKIP pick: ask yourself "what's the strongest argument this is wrong?" Cite at least one specific lesson, audit outcome, or diary pattern. If the challenge is convincing, downgrade the conviction one tier and document.
3. For each pick, write the final reasoning + skeptic challenge + resolution.
4. Emit the final decision JSON.

## Hard rules (mirror specialist hard rules)
1. ANY pick where a VALIDATED global lesson fires → SKIP regardless of upstream conviction. (Score should already be 0.0 from `fm_allocation`.)
2. Cold-start mode (total_picks_audited < 20): skeptic challenge MUST resolve to "kept" — any plausible disconfirm → downgrade.

## Output format (write to stdout as JSON)
```json
{
  "specialist": "fund_manager",
  "run_time_utc": "<iso>",
  "specialists_consulted": 4,
  "total_specialist_picks_received": <int>,
  "lessons_version": <int>,
  "cold_start_mode": <bool>,
  "specialist_cold_start_status": {"late_curve": "cold", ...},
  "specialist_weights_applied": {"late_curve": 1.0, ...},
  "final_decisions": [
    {
      "mint": "<base58>",
      "ticker": "<symbol>",
      "conviction": "BUY HIGH | BUY MEDIUM | WATCH | SKIP",
      "recommended_size_pct": 0.12,
      "specialist_recommendations": {"late_curve": "BUY HIGH", "catalyst": "WATCH"},
      "specialist_convergence_count": 2,
      "score": 0.42,
      "exit_rule": "graduation_or_30pct_or_6h",
      "skeptic_challenge": "...",
      "skeptic_resolution": "kept | downgraded_to_<tier>",
      "reasoning": "..."
    }
  ],
  "book_pct_deployed": 0.18,
  "summary_counts": {"buy_high": 0, "buy_medium": 1, "watch": 2, "skip": 7}
}
```

## Adversarial skeptic prompts to use
- "What's the historical outcome for picks with this profile?"
- "Does any disconfirmed signal (D1, D2) apply here?"
- "Is the specialist that recommended this one with a poor recent hit rate?"
- "Does the recommended exit horizon actually match the entry conditions?"
```

- [ ] **Step 6: Commit**

```bash
git add predictions/agents/fund_manager.md predictions/agents/fm_allocation.py predictions/agents/tests/test_fm_allocation.py
git commit -m "v2 Fund Manager: subagent prompt + pure-Python allocation math

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: lessons.md schema refactor + reader/writer module

**Files:**
- Create: `predictions/diary/lessons_io.py`
- Test: `predictions/diary/tests/test_lessons_io.py`, `predictions/diary/tests/__init__.py`
- Modify: `predictions/diary/lessons.md` (extend frontmatter + add per-specialist sections)

- [ ] **Step 1: Write failing test**

`predictions/diary/tests/test_lessons_io.py`:
```python
from pathlib import Path
from predictions.diary import lessons_io

def test_parse_frontmatter(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text("---\nversion: 9\ntotal_picks_audited: 5\nlate_curve:\n  picks_audited: 2\n  hit_rate_last_30d: 0.1\n---\nbody")
    fm = lessons_io.load_frontmatter(f)
    assert fm["version"] == 9
    assert fm["late_curve"]["picks_audited"] == 2

def test_update_frontmatter_preserves_body(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text("---\nversion: 1\n---\n# Body content\nstuff")
    lessons_io.update_frontmatter(f, {"version": 2, "total_picks_audited": 7})
    raw = f.read_text()
    assert "# Body content" in raw
    assert "version: 2" in raw

def test_update_specialist_stats(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text("---\nversion: 1\nlate_curve:\n  picks_audited: 0\n---\nbody")
    lessons_io.update_specialist_stats(f, "late_curve", {"picks_audited": 1, "hit_rate_last_30d": 0.5})
    fm = lessons_io.load_frontmatter(f)
    assert fm["late_curve"]["picks_audited"] == 1
    assert fm["late_curve"]["hit_rate_last_30d"] == 0.5
```

- [ ] **Step 2: Run failing test**

```bash
mkdir -p predictions/diary/tests && touch predictions/diary/tests/__init__.py
pytest predictions/diary/tests/test_lessons_io.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement lessons_io.py**

`predictions/diary/lessons_io.py`:
```python
"""Read/write lessons.md with YAML frontmatter, preserving body content."""
from __future__ import annotations
import re
from pathlib import Path
import yaml


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _split(path: Path) -> tuple[dict, str]:
    raw = path.read_text() if path.exists() else "---\n---\n"
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    fm_text, body = m.group(1), m.group(2)
    fm = yaml.safe_load(fm_text) or {}
    return fm, body


def load_frontmatter(path: Path) -> dict:
    fm, _ = _split(path)
    return fm


def load_body(path: Path) -> str:
    _, body = _split(path)
    return body


def write(path: Path, frontmatter: dict, body: str) -> None:
    tmp = path.with_suffix(".tmp")
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).rstrip("\n")
    tmp.write_text(f"---\n{fm_text}\n---\n{body}")
    tmp.rename(path)


def update_frontmatter(path: Path, updates: dict) -> None:
    fm, body = _split(path)
    fm.update(updates)
    write(path, fm, body)


def update_specialist_stats(path: Path, specialist: str, stats: dict) -> None:
    fm, body = _split(path)
    cur = dict(fm.get(specialist) or {})
    cur.update(stats)
    fm[specialist] = cur
    write(path, fm, body)
```

- [ ] **Step 4: Add PyYAML to requirements**

Append to `predictions/requirements.txt` if not already present:
```
PyYAML>=6.0
```

Run:
```bash
pip install -r predictions/requirements.txt
```

- [ ] **Step 5: Run tests**

```bash
pytest predictions/diary/tests/test_lessons_io.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Extend live lessons.md frontmatter with per-specialist sections**

```bash
cd /home/roberto/solana-storm
python3 << 'EOF'
from pathlib import Path
from predictions.diary import lessons_io

p = Path("predictions/diary/lessons.md")
fm = lessons_io.load_frontmatter(p)
for spec in ("late_curve", "early_curve", "smart_mirror", "catalyst"):
    if spec not in fm:
        fm[spec] = {
            "picks_audited": 0, "hit_rate_all_time": None,
            "hit_rate_last_7d": None, "hit_rate_last_30d": None,
            "cold_start_mode": True,
        }
if "smart_mirror" in fm:
    fm["smart_mirror"]["dormant"] = True
if "fund_manager" not in fm:
    fm["fund_manager"] = {"decisions_audited": 0, "override_hit_rate": None}
fm["version"] = (fm.get("version") or 9) + 1
fm["last_updated"] = "2026-05-23T00:00:00Z"
lessons_io.write(p, fm, lessons_io.load_body(p))
print("Updated lessons.md frontmatter to v2 schema")
EOF
```

- [ ] **Step 7: Verify lessons.md still parses**

```bash
python3 -c "from predictions.diary import lessons_io; from pathlib import Path; print(lessons_io.load_frontmatter(Path('predictions/diary/lessons.md')))"
```

- [ ] **Step 8: Commit**

```bash
git add predictions/diary/lessons.md predictions/diary/lessons_io.py predictions/diary/tests/ predictions/requirements.txt
git commit -m "v2 lessons_io: per-specialist sections in lessons.md + read/write API

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Shadow-watch infrastructure

**Files:**
- Create: `predictions/diary/shadow_watches.py`
- Test: `predictions/diary/tests/test_shadow_watches.py`

- [ ] **Step 1: Write failing test**

`predictions/diary/tests/test_shadow_watches.py`:
```python
from pathlib import Path
from predictions.diary import shadow_watches

def test_write_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_watches.config, "SHADOW_WATCH_DIR", tmp_path)
    pick_id = shadow_watches.write_shadow_watch(
        specialist="late_curve", mint="A" * 44, pool="B" * 44,
        would_be_conviction="BUY MEDIUM", vetoed_by="C1",
        entry_quote=1000, entry_base=2000,
        recommended_exit={"rule": "graduation_or_30pct_or_6h", "hard_timeout_hours": 6}
    )
    assert (tmp_path / f"{pick_id}-shadow.md").exists()
    items = shadow_watches.list_pending(now_unix=999999999999)
    assert any(it["pick_id"] == pick_id for it in items)
```

- [ ] **Step 2: Run failing test**

```bash
pytest predictions/diary/tests/test_shadow_watches.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement shadow_watches.py**

`predictions/diary/shadow_watches.py`:
```python
"""Shadow-watch storage. Tracks 'would-be BUY but vetoed by VALIDATED lesson' picks."""
from __future__ import annotations
import json, time
from pathlib import Path

from predictions import config


def write_shadow_watch(*, specialist: str, mint: str, pool: str,
                       would_be_conviction: str, vetoed_by: str,
                       entry_quote: int, entry_base: int,
                       recommended_exit: dict) -> str:
    config.SHADOW_WATCH_DIR.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    pick_id = f"{now}-{specialist}-{mint[:8]}"
    horizon_h = int(recommended_exit.get("hard_timeout_hours", 24))
    payload = {
        "pick_id": pick_id, "specialist": specialist,
        "mint": mint, "pool": pool,
        "would_be_conviction": would_be_conviction, "vetoed_by": vetoed_by,
        "entry_quote_lamports": entry_quote, "entry_base_lamports": entry_base,
        "entered_at_unix": now,
        "due_unix": now + horizon_h * 3600,
        "recommended_exit": recommended_exit,
    }
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in payload.items()) + "\n---\n"
    path = config.SHADOW_WATCH_DIR / f"{pick_id}-shadow.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(path)
    return pick_id


def list_pending(now_unix: int) -> list[dict]:
    """List shadow-watches with due_unix > now_unix (still pending audit)."""
    if not config.SHADOW_WATCH_DIR.exists():
        return []
    out = []
    for p in config.SHADOW_WATCH_DIR.glob("*-shadow.md"):
        lines = p.read_text().splitlines()
        d = {}
        for ln in lines:
            if ln in ("---", ""):
                continue
            if ":" in ln:
                k, _, v = ln.partition(":")
                try:
                    d[k.strip()] = json.loads(v.strip())
                except Exception:
                    d[k.strip()] = v.strip()
        if int(d.get("due_unix") or 0) > now_unix:
            out.append(d)
    return out
```

- [ ] **Step 4: Run tests**

```bash
pytest predictions/diary/tests/test_shadow_watches.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add predictions/diary/shadow_watches.py predictions/diary/tests/test_shadow_watches.py
git commit -m "v2 shadow_watches: write would-be-BUY-vetoed picks for lesson-evolution

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: runner.py — single cron entry point

**Files:**
- Create: `predictions/runner.py`
- Test: `predictions/tests/test_runner.py`

- [ ] **Step 1: Write failing test**

`predictions/tests/test_runner.py`:
```python
import subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "predictions" / "runner.py"

def test_runner_halts_on_kill_switch(monkeypatch):
    env = {"PUMP_V2_HALT": "1", "PATH": ""}
    r = subprocess.run([sys.executable, str(RUNNER), "late_curve"],
                       capture_output=True, text=True, env=env, cwd=REPO)
    assert r.returncode == 0
    assert "halted" in r.stdout.lower() or "halted" in r.stderr.lower()

def test_runner_rejects_unknown_command():
    r = subprocess.run([sys.executable, str(RUNNER), "nonsense"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode != 0
```

- [ ] **Step 2: Run failing test**

```bash
pytest predictions/tests/test_runner.py -v
```
Expected: FAIL (runner missing).

- [ ] **Step 3: Implement runner.py**

`predictions/runner.py`:
```python
"""Single cron entry point. Dispatches based on argv[1]."""
from __future__ import annotations
import os, sys, time, traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from predictions import config

KNOWN_COMMANDS = {
    "late_curve", "early_curve", "smart_mirror", "catalyst",
    "fund_manager", "audit_tick", "universe_fetch",
}


def _log_error(cmd: str, exc: Exception) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    import json
    entry = {
        "ts_unix": int(time.time()),
        "cmd": cmd,
        "error": str(exc),
        "traceback": traceback.format_exc()[:2000],
    }
    with config.ERROR_LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _run_universe_fetch():
    from predictions import universe
    res = universe.fetch_pregrad_universe()
    if res.get("error"):
        print(f"universe_fetch: error={res['error']}", file=sys.stderr)
        return 1
    rows = res.get("data") or []
    count = universe.record_pregrad_universe([{**r, "fetched_at_unix": res.get("fetched_at_unix") or int(time.time())} for r in rows])
    print(f"universe_fetch: recorded {count} snapshots")
    return 0


def _run_audit_tick():
    from predictions.audit import processor
    due, remaining = processor.partition_due(config.PENDING_AUDIT_PATH, now_unix=int(time.time()))
    print(f"audit_tick: {len(due)} due, {len(remaining)} pending")
    # Actual outcome resolution: delegated to per-pick logic in audit/processor.py extensions
    # (Stub here — Task 19's integration test covers end-to-end.)
    if due:
        processor.rewrite(config.PENDING_AUDIT_PATH, remaining)
    return 0


def _run_specialist(name: str):
    # Stub: real subagent invocation is performed by the calling harness (CronCreate command).
    # This runner just confirms the request is well-formed and emits a status file.
    print(f"specialist {name} dispatch — real subagent invocation should be wired by orchestrator")
    return 0


def _run_fund_manager():
    print("fund_manager dispatch — real subagent invocation should be wired by orchestrator")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: runner.py <command>", file=sys.stderr)
        return 2
    cmd = argv[1]
    if config.PUMP_V2_HALT:
        print(f"runner: halted by PUMP_V2_HALT (cmd={cmd})")
        return 0
    if cmd not in KNOWN_COMMANDS:
        print(f"runner: unknown command {cmd!r} (expected one of {sorted(KNOWN_COMMANDS)})", file=sys.stderr)
        return 2
    try:
        if cmd == "universe_fetch":
            return _run_universe_fetch()
        if cmd == "audit_tick":
            return _run_audit_tick()
        if cmd == "fund_manager":
            return _run_fund_manager()
        return _run_specialist(cmd)
    except Exception as e:
        _log_error(cmd, e)
        print(f"runner: error in {cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run tests**

```bash
pytest predictions/tests/test_runner.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add predictions/runner.py predictions/tests/test_runner.py
git commit -m "v2 runner: single cron entry point with kill switch + error log

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Specialist subagent invocation (wire runner → Agent tool)

**Files:**
- Modify: `predictions/runner.py:_run_specialist`, `predictions/runner.py:_run_fund_manager`
- Create: `predictions/agents/dispatch.py`
- Test: `predictions/agents/tests/test_dispatch.py`

This task wires the runner's stub `_run_specialist` to actually invoke a Claude subagent via the `Agent` tool. The dispatch module abstracts the Agent-tool call so it can be mocked in tests.

- [ ] **Step 1: Write failing test**

`predictions/agents/tests/test_dispatch.py`:
```python
from unittest.mock import patch
from predictions.agents import dispatch

def test_dispatch_specialist_builds_correct_call():
    with patch.object(dispatch, "_invoke_agent") as mock_inv:
        mock_inv.return_value = '{"specialist": "late_curve", "picks": []}'
        result = dispatch.dispatch_specialist("late_curve")
        mock_inv.assert_called_once()
        call_args = mock_inv.call_args
        assert "Late-Curve Momentum Agent" in call_args.kwargs["prompt"]
        assert result["specialist"] == "late_curve"

def test_dispatch_fm_includes_specialist_files():
    with patch.object(dispatch, "_invoke_agent") as mock_inv:
        mock_inv.return_value = '{"specialist": "fund_manager", "final_decisions": []}'
        with patch.object(dispatch, "_collect_specialist_files") as mock_collect:
            mock_collect.return_value = [{"specialist": "late_curve", "picks": []}]
            result = dispatch.dispatch_fund_manager()
            assert result["specialist"] == "fund_manager"
```

- [ ] **Step 2: Run failing test**

```bash
pytest predictions/agents/tests/test_dispatch.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement dispatch.py**

`predictions/agents/dispatch.py`:
```python
"""Dispatch layer: builds prompts for subagents and (in production) calls the Agent tool.

In test/dry-run, `_invoke_agent` is mockable. In production runtime, it's expected to be
overridden by the harness (e.g., a wrapper that calls Claude SDK's Agent tool).
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from predictions import config, universe
from predictions.agents import invoker, fm_allocation
from predictions.diary import lessons_io


def _invoke_agent(*, prompt: str, model: str = "sonnet") -> str:
    """Placeholder. Returns empty JSON object. Production overrides this with the Agent tool call.

    Tests mock this. The runner's actual production wiring sets this attribute to a function
    that calls the Claude Agent SDK or equivalent.
    """
    return '{"specialist": "_stub_", "picks": [], "error": "agent not wired"}'


def _format_subagent_prompt(specialist: str, context: dict) -> str:
    template = context["prompt_template"]
    inputs_block = json.dumps({
        "universe": context["universe"],
        "curve_history": context["curve_history"],
        "extras": context["extras"],
    }, indent=2)
    return (f"{template}\n\n## Current inputs (JSON)\n```json\n{inputs_block}\n```\n\n"
            f"## Current lessons.md\n```markdown\n{context['lessons_md']}\n```\n\n"
            f"Respond with the JSON output object only.")


def dispatch_specialist(specialist: str, *, universe_data: dict | None = None,
                        curve_history: dict | None = None,
                        extras: dict | None = None) -> dict:
    if universe_data is None:
        universe_data = universe.fetch_pregrad_universe()
    ctx = invoker.build_context(specialist, universe=universe_data,
                                 curve_history=curve_history, extras=extras)
    prompt = _format_subagent_prompt(specialist, ctx)
    output = _invoke_agent(prompt=prompt)
    parsed = invoker.parse_specialist_output(output)
    return parsed


def _collect_specialist_files() -> list[dict]:
    """Find the most recent specialist decision file per specialist."""
    decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
    if not decisions_dir.exists():
        return []
    by_specialist: dict[str, Path] = {}
    for p in decisions_dir.glob("*.md"):
        for spec in ("late_curve", "early_curve", "smart_mirror", "catalyst"):
            if f"-{spec}." in p.name:
                current = by_specialist.get(spec)
                if current is None or p.stat().st_mtime > current.stat().st_mtime:
                    by_specialist[spec] = p
    return [{"specialist": s, "path": str(p)} for s, p in by_specialist.items()]


def dispatch_fund_manager() -> dict:
    specialist_files = _collect_specialist_files()
    lessons_path = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
    fm = lessons_io.load_frontmatter(lessons_path)
    stats = {s: fm.get(s) or {} for s in ("late_curve", "early_curve", "smart_mirror", "catalyst")}
    weights = fm_allocation.specialist_weights(stats)
    total_audited = int(fm.get("total_picks_audited") or 0)
    cold_start = fm_allocation.is_cold_start_total(total_audited)

    extras = {
        "specialist_outputs": specialist_files,
        "specialist_weights": weights,
        "cold_start_mode": cold_start,
        "total_picks_audited": total_audited,
    }
    ctx = invoker.build_context("fund_manager", universe={"data": []}, curve_history=None, extras=extras)
    prompt = _format_subagent_prompt("fund_manager", ctx)
    output = _invoke_agent(prompt=prompt)
    return invoker.parse_specialist_output(output)
```

- [ ] **Step 4: Wire runner to dispatch**

Modify `predictions/runner.py` — replace the two stub functions:

```python
def _run_specialist(name: str):
    from predictions.agents import dispatch
    result = dispatch.dispatch_specialist(name)
    # Write specialist output to a decision file
    decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d-%H-%M", time.gmtime())
    out = decisions_dir / f"{ts}-{name}.md"
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v}"
                                for k, v in result.items()) + "\n---\n"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out)
    print(f"specialist {name}: wrote {out.name}")
    return 0


def _run_fund_manager():
    from predictions.agents import dispatch
    result = dispatch.dispatch_fund_manager()
    decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d-%H-%M", time.gmtime())
    out = decisions_dir / f"{ts}-fund_manager.md"
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v}"
                                for k, v in result.items()) + "\n---\n"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out)
    # Update last_fm_cycle marker
    config.LAST_FM_CYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.LAST_FM_CYCLE_PATH.write_text(str(int(time.time())))
    print(f"fund_manager: wrote {out.name}")
    return 0
```

Also add `import json` at the top of runner.py if not already there.

- [ ] **Step 5: Run tests**

```bash
pytest predictions/agents/tests/test_dispatch.py predictions/tests/test_runner.py -v
```
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add predictions/agents/dispatch.py predictions/agents/tests/test_dispatch.py predictions/runner.py
git commit -m "v2 dispatch: wire runner to subagent invocation + FM aggregation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Smart-wallet registry seed (one-time migration)

**Files:**
- Create: `predictions/migrations/v2_smart_wallet_seed.py`
- Test: `predictions/migrations/tests/test_v2_smart_wallet_seed.py`, `predictions/migrations/tests/__init__.py`

- [ ] **Step 1: Write failing test**

`predictions/migrations/tests/test_v2_smart_wallet_seed.py`:
```python
from unittest.mock import patch
from predictions.migrations import v2_smart_wallet_seed

def test_extract_candidates_filters_by_precision():
    raw = [
        {"wallet": "A" * 44, "winner_hits": 3, "total_observations": 5},  # precision 0.6 ✓
        {"wallet": "B" * 44, "winner_hits": 1, "total_observations": 10},  # precision 0.1 ✗
    ]
    out = v2_smart_wallet_seed.extract_candidates(raw, min_precision=0.3, min_observations=3)
    assert len(out) == 1
    assert out[0]["wallet"] == "A" * 44

def test_seed_writes_to_db(tmp_path, monkeypatch):
    monkeypatch.setattr(v2_smart_wallet_seed.config, "CURVE_HISTORY_DB", tmp_path / "curve.db")
    from predictions.state import curve_history
    curve_history.init_db(tmp_path / "curve.db")
    n = v2_smart_wallet_seed.seed_into_db([
        {"wallet": "X" * 44, "winner_hits": 4, "total_observations": 5}
    ])
    assert n == 1
```

- [ ] **Step 2: Run failing test**

```bash
mkdir -p predictions/migrations/tests && touch predictions/migrations/tests/__init__.py
pytest predictions/migrations/tests/test_v2_smart_wallet_seed.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement migration**

`predictions/migrations/v2_smart_wallet_seed.py`:
```python
"""One-time migration: seed smart-wallet registry from Dune historical winners.

Usage:
    python3 -m predictions.migrations.v2_smart_wallet_seed [--dry-run]

Algorithm:
1. Query Dune for tokens that graduated in last 30 days where realized return ≥ 5×
2. For each, fetch first-hour buyers via Helius
3. Aggregate winner_hits + total_observations per wallet
4. Filter to wallets with precision ≥ 0.3 AND total_observations ≥ 3
5. Insert into smart_wallet_seed table with status='seeded'
"""
from __future__ import annotations
import argparse
import time
from predictions import config
from predictions.state import curve_history


def extract_candidates(raw_aggregates: list[dict], *, min_precision: float = 0.3,
                       min_observations: int = 3) -> list[dict]:
    out = []
    for row in raw_aggregates:
        obs = int(row.get("total_observations") or 0)
        wins = int(row.get("winner_hits") or 0)
        if obs < min_observations:
            continue
        precision = wins / obs if obs else 0.0
        if precision < min_precision:
            continue
        out.append({**row, "precision": precision})
    return out


def seed_into_db(candidates: list[dict]) -> int:
    db = config.CURVE_HISTORY_DB
    curve_history.init_db(db)
    now = int(time.time())
    count = 0
    with curve_history._connect(db) as con:
        for c in candidates:
            con.execute(
                "INSERT OR REPLACE INTO smart_wallet_seed(wallet, first_seen_unix, "
                "last_winner_at_unix, winner_hits, total_observations, precision, status) "
                "VALUES (?,?,?,?,?,?,?)",
                (c["wallet"], now, now, int(c["winner_hits"]),
                 int(c["total_observations"]), float(c.get("precision") or 0.0), "seeded"),
            )
            count += 1
    return count


def _dune_recent_graduations(days: int = 30) -> list[dict]:
    """Pull all graduations in the last N days via the existing Dune client.

    Simpler heuristic than 'realized 5x return': we use *participation across multiple
    graduations* as a proxy for trader skill. A wallet that consistently appears as a
    first-hour buyer across many graduations is signal-bearing even if we can't
    cheaply compute realized returns on Dune's free engine.
    """
    from bootstrap.dune_client import DuneClient
    from bootstrap.config import load_config as load_bcfg
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = (
        "SELECT account_mint AS mint, account_pool AS pool, "
        "CAST(to_unixtime(call_block_time) AS BIGINT) AS grad_unix "
        f"FROM pumpdotfun_solana.pump_call_migrate "
        f"WHERE call_block_time >= TIMESTAMP '{cutoff.strftime('%Y-%m-%d %H:%M:%S')}' "
        f"ORDER BY call_block_time DESC LIMIT 2000"
    )
    client = DuneClient(load_bcfg())
    rows, _credits = client.run_sql(sql)
    return [{"mint": r["mint"], "pool": r["pool"], "grad_unix": int(r["grad_unix"])}
            for r in rows if r.get("mint") and r.get("pool")]


def _enumerate_buyers_for_mint(mint: str, pool: str) -> list[str]:
    """Call helius_trade_flow.py and return the unique buyer wallets list."""
    import json, subprocess, sys
    helper = config._REPO_ROOT / "predictions" / "helpers" / "helius_trade_flow.py"
    r = subprocess.run(
        [sys.executable, str(helper), mint, "--pool", pool, "--window", "60"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return []
    try:
        d = json.loads(r.stdout)["data"]
        return list(d.get("buyer_wallets") or d.get("unique_buyers") or [])
    except Exception:
        return []


def _aggregate_buyers(grads: list[dict], *, max_grads: int = 100) -> list[dict]:
    """Walk graduations, enumerate buyers, aggregate per-wallet counts.

    Treats a wallet as 'winner_hits=1' for any graduation it bought in the first hour.
    This is a deliberate simplification — we don't track each token's realized return
    here (too expensive on free Dune); the registry refines itself via Phase 1 audits
    once the live system runs.
    """
    from collections import defaultdict
    obs: dict[str, int] = defaultdict(int)
    wins: dict[str, int] = defaultdict(int)
    for g in grads[:max_grads]:
        buyers = _enumerate_buyers_for_mint(g["mint"], g["pool"])
        for w in set(buyers):
            obs[w] += 1
            wins[w] += 1  # graduation participation = provisional win until audits refine
    return [{"wallet": w, "winner_hits": wins[w], "total_observations": obs[w]} for w in obs]


def run(dry_run: bool = False) -> int:
    """Production entry: query Dune + Helius, aggregate, seed. Dry-run skips network."""
    if dry_run:
        candidates = extract_candidates([
            {"wallet": "S" * 44, "winner_hits": 4, "total_observations": 8}
        ])
        return seed_into_db(candidates)
    grads = _dune_recent_graduations(days=30)
    print(f"seed: pulled {len(grads)} graduations from Dune")
    raw = _aggregate_buyers(grads)
    print(f"seed: aggregated {len(raw)} unique buyer wallets")
    candidates = extract_candidates(raw, min_precision=0.3, min_observations=3)
    print(f"seed: {len(candidates)} candidates pass precision/obs filter")
    return seed_into_db(candidates)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    n = run(dry_run=args.dry_run)
    print(f"seeded {n} wallet(s)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
pytest predictions/migrations/tests/test_v2_smart_wallet_seed.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add predictions/migrations/v2_smart_wallet_seed.py predictions/migrations/tests/
git commit -m "v2 smart-wallet seed: migration scaffolding (dry-run path; live path documented)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: v1 deprecation + new pump-fund skill markdown

**Files:**
- Modify: `.claude/skills/pump-prediction.md` (add deprecation banner at top)
- Create: `.claude/skills/pump-fund.md`
- Modify: top-level `README.md` if it documents v1 — check first

- [ ] **Step 1: Prepend deprecation banner to v1 skill**

Read the first 5 lines of `.claude/skills/pump-prediction.md`, then prepend:

```bash
cd /home/roberto/solana-storm
python3 << 'EOF'
from pathlib import Path
p = Path(".claude/skills/pump-prediction.md")
content = p.read_text()
banner = """> **⚠️ DEPRECATED 2026-05-23.** This skill is v1 (post-graduation picker). v1 reached its
> structural verdict — C1 VALIDATED, 5/5 audited WATCHes all decayed (cohort avg -81.9%).
> See `.claude/skills/pump-fund.md` for the v2 multi-agent replacement.
> v1's lessons.md (in `predictions/diary/lessons.md`) carries forward into v2 unchanged.

---

"""
if "DEPRECATED" not in content[:200]:
    p.write_text(banner + content)
    print("banner prepended")
else:
    print("already deprecated")
EOF
```

- [ ] **Step 2: Write the v2 skill markdown**

`.claude/skills/pump-fund.md`:
```markdown
---
name: pump-fund
description: User-invokable status + manual-trigger for the v2 multi-agent pump.fun fund (Late-Curve / Early-Curve / Smart-Mirror / Catalyst specialists + Fund Manager).
---

# pump-fund — v2 multi-agent skill

This skill is the user-facing surface for the v2 pump-prediction system. Most operation is autonomous via cron (see Section 7 of the v2 design spec). This file documents how the user inspects status and manually triggers components when needed.

## Quick status

```bash
cd /home/roberto/solana-storm
python3 -c "
from pathlib import Path
from predictions.diary import lessons_io
fm = lessons_io.load_frontmatter(Path('predictions/diary/lessons.md'))
print(f\"lessons.md version: {fm.get('version')}\")
print(f\"total_picks_audited: {fm.get('total_picks_audited')}\")
print(f\"overall_buy_hit_rate: {fm.get('overall_buy_hit_rate')}\")
for s in ('late_curve','early_curve','smart_mirror','catalyst'):
    st = fm.get(s) or {}
    print(f\"  {s}: audited={st.get('picks_audited')} hr30d={st.get('hit_rate_last_30d')} cold={st.get('cold_start_mode')}\")
"
```

## Manual invocations (debug / explore)

| Command | Purpose |
|---|---|
| `python3 predictions/runner.py universe_fetch` | Pull current pre-grad universe into SQLite |
| `python3 predictions/runner.py late_curve` | Trigger late-curve specialist once |
| `python3 predictions/runner.py early_curve` | Trigger early-curve specialist once |
| `python3 predictions/runner.py smart_mirror` | Trigger smart-mirror (dormant unless seeded) |
| `python3 predictions/runner.py catalyst` | Trigger catalyst once |
| `python3 predictions/runner.py fund_manager` | Trigger FM consolidation once |
| `python3 predictions/runner.py audit_tick` | Process due audits once |

## Kill switch

`export PUMP_V2_HALT=1` — all crons exit immediately on next fire. No cron uninstall needed. Use during build windows, debugging, or vacation.

## Cron schedule (managed via CronCreate)

- `*/15 * * * *` late_curve + universe_fetch
- `0 * * * *` catalyst
- `0 */4 * * *` early_curve
- `15 */4 * * *` smart_mirror
- `30 */4 * * *` fund_manager
- `*/10 * * * *` audit_tick

## Diary structure

- `predictions/diary/lessons.md` (git-tracked) — the team's central memory
- `predictions/diary/decisions/<ts>-<specialist>.md` (gitignored) — per-specialist per-cycle output
- `predictions/diary/outcomes/<pick_id>-outcome.md` (gitignored) — audit results
- `predictions/diary/shadow_watches/<pick_id>-shadow.md` (gitignored) — vetoed-by-C1 tracking

## Verdict horizon

Per spec §9, v2 has a 30-day verdict window from cutover. Re-evaluate viability on 2026-06-23.
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/pump-prediction.md .claude/skills/pump-fund.md
git commit -m "v2 skill markdown: deprecate v1 + add pump-fund user-facing skill

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 19: Audit-tick attribution (the learning loop)

The audit machinery needs to: (1) read pending audits, (2) fetch current pool state, (3) compute realized returns, (4) attribute results to the specialist that recommended each pick, (5) update `lessons.md` frontmatter per-specialist stats, (6) process shadow-watches.

**Files:**
- Modify: `predictions/audit/processor.py` (add `process_due_audits` orchestration)
- Modify: `predictions/runner.py` (replace `_run_audit_tick` stub with real call)
- Test: `predictions/audit/tests/test_processor_attribution.py`

- [ ] **Step 1: Write failing test**

`predictions/audit/tests/test_processor_attribution.py`:
```python
import json
from pathlib import Path
from unittest.mock import patch
from predictions.audit import processor


def test_process_due_audits_writes_outcomes_and_updates_lessons(tmp_path, monkeypatch):
    # arrange: stub config dirs
    monkeypatch.setattr(processor.config, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(processor.config, "PENDING_AUDIT_PATH", tmp_path / "pending.jsonl")
    lessons = tmp_path / "predictions" / "diary" / "lessons.md"
    lessons.parent.mkdir(parents=True, exist_ok=True)
    lessons.write_text(
        "---\nversion: 1\ntotal_picks_audited: 0\n"
        "late_curve:\n  picks_audited: 0\n  hit_rate_all_time: null\n---\nbody\n"
    )
    # one due pick from late_curve specialist
    processor.enqueue(processor.config.PENDING_AUDIT_PATH, {
        "pick_id": "1779000000-late_curve-AAAAAAAA",
        "mint": "A" * 44, "pool": "B" * 44,
        "specialist": "late_curve",
        "entry_quote_lamports": 200_000_000,
        "entry_base_lamports": 100_000_000_000,
        "due_unix": 1,
        "recommended_exit": {"rule": "graduation_or_30pct_or_6h"},
    })

    # mock the on-chain fetch
    with patch.object(processor, "_fetch_current_pool_state") as fetcher:
        fetcher.return_value = {
            "current_quote_reserve_lamports": 100_000_000,  # halved
            "current_base_reserve_lamports": 100_000_000_000,
            "pool_closed": False,
        }
        n = processor.process_due_audits(now_unix=10, lessons_path=lessons)

    assert n == 1
    # outcome file written
    out_dir = tmp_path / "predictions" / "diary" / "outcomes"
    outcomes = list(out_dir.glob("*-outcome.md"))
    assert len(outcomes) == 1
    body = outcomes[0].read_text()
    assert "realized_return" in body
    assert "late_curve" in body

    # lessons.md updated
    from predictions.diary import lessons_io
    fm = lessons_io.load_frontmatter(lessons)
    assert fm["total_picks_audited"] == 1
    assert fm["late_curve"]["picks_audited"] == 1
```

- [ ] **Step 2: Run failing test**

```bash
pytest predictions/audit/tests/test_processor_attribution.py -v
```
Expected: FAIL (`process_due_audits` / `_fetch_current_pool_state` missing).

- [ ] **Step 3: Extend processor.py**

Append to `predictions/audit/processor.py`:

```python
import json as _json
import subprocess as _subprocess
import sys as _sys
import time as _time

from predictions.diary import lessons_io


def _fetch_current_pool_state(mint: str, pool: str) -> dict:
    """Run audit_outcome.py to get current pool state."""
    helper = config._REPO_ROOT / "predictions" / "helpers" / "audit_outcome.py"
    r = _subprocess.run(
        [_sys.executable, str(helper), mint, "--pool", pool],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return {"current_quote_reserve_lamports": 0, "current_base_reserve_lamports": 0,
                "pool_closed": True}
    try:
        d = _json.loads(r.stdout).get("data") or {}
        return {
            "current_quote_reserve_lamports": int(d.get("current_quote_reserve_lamports") or 0),
            "current_base_reserve_lamports": int(d.get("current_base_reserve_lamports") or 0),
            "pool_closed": bool(d.get("pool_closed")),
        }
    except Exception:
        return {"current_quote_reserve_lamports": 0, "current_base_reserve_lamports": 0,
                "pool_closed": True}


def _recompute_hit_rate(specialist_stats: dict, won: bool) -> dict:
    """Increment picks_audited and recompute all-time hit rate. Returns new stats dict."""
    picks = int(specialist_stats.get("picks_audited") or 0) + 1
    hits = int(specialist_stats.get("_hits_all_time") or 0)
    if won:
        hits += 1
    hr = (hits / picks) if picks else 0.0
    return {
        **specialist_stats,
        "picks_audited": picks,
        "_hits_all_time": hits,  # private accumulator for honest division
        "hit_rate_all_time": round(hr, 4),
        "cold_start_mode": picks < 30,
    }


def process_due_audits(*, now_unix: int, lessons_path) -> int:
    """Process due audits: fetch current state, write outcomes, update lessons.md.

    Returns count processed.
    """
    due, remaining = partition_due(config.PENDING_AUDIT_PATH, now_unix=now_unix)
    processed = 0
    fm = lessons_io.load_frontmatter(lessons_path)
    total = int(fm.get("total_picks_audited") or 0)

    for item in due:
        specialist = item.get("specialist", "unknown")
        mint = item.get("mint", "")
        pool = item.get("pool", "")
        state = _fetch_current_pool_state(mint, pool)
        ret = compute_realized_return(
            entry_quote=int(item.get("entry_quote_lamports") or 0),
            entry_base=int(item.get("entry_base_lamports") or 0),
            current_quote=state["current_quote_reserve_lamports"],
            current_base=state["current_base_reserve_lamports"],
            pool_closed=state["pool_closed"],
        )
        # 'won' = realized return >= specialist's effective target.
        # Simplification: treat any return >= +0.5 as a win across specialists.
        # Future iteration: read specialist's exit rule from item['recommended_exit'].
        won = ret >= 0.5

        write_outcome(item["pick_id"], {
            "pick_id": item["pick_id"],
            "specialist": specialist,
            "mint": mint, "pool": pool,
            "audited_at_unix": now_unix,
            "realized_return": round(ret, 4),
            "pool_closed": state["pool_closed"],
            "won": won,
        })

        cur_stats = dict(fm.get(specialist) or {})
        fm[specialist] = _recompute_hit_rate(cur_stats, won)
        total += 1
        processed += 1

    fm["total_picks_audited"] = total
    fm["version"] = int(fm.get("version") or 0) + 1
    fm["last_updated"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now_unix))
    lessons_io.write(lessons_path, fm, lessons_io.load_body(lessons_path))

    rewrite(config.PENDING_AUDIT_PATH, remaining)
    return processed
```

- [ ] **Step 4: Wire runner._run_audit_tick to call it**

Modify `predictions/runner.py:_run_audit_tick`:

```python
def _run_audit_tick():
    from predictions.audit import processor
    lessons = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
    n = processor.process_due_audits(now_unix=int(time.time()), lessons_path=lessons)
    print(f"audit_tick: processed {n} due audits")
    return 0
```

- [ ] **Step 5: Run tests**

```bash
pytest predictions/audit/tests/test_processor_attribution.py predictions/audit/tests/test_processor.py predictions/tests/test_runner.py -v
```
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add predictions/audit/processor.py predictions/audit/tests/test_processor_attribution.py predictions/runner.py
git commit -m "v2 audit-tick: full attribution loop — outcomes + per-specialist stats

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 20: Final integration smoke test

**Files:**
- Create: `predictions/tests/test_v2_integration.py`

- [ ] **Step 1: Write integration smoke test**

`predictions/tests/test_v2_integration.py`:
```python
"""End-to-end smoke test in REHEARSAL mode (no live network)."""
import json, os, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "predictions" / "runner.py"


def _run(cmd: str, env_extra: dict = None):
    env = {**os.environ, "PUMP_PREDICTION_REHEARSAL": "1", "PUMP_V2_HALT": "0"}
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), cmd],
                       capture_output=True, text=True, env=env, cwd=REPO)
    return r


def test_universe_fetch_rehearsal_writes_db():
    r = _run("universe_fetch")
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "recorded" in r.stdout

def test_audit_tick_completes_without_pending():
    r = _run("audit_tick")
    assert r.returncode == 0, r.stderr
    assert "audit_tick" in r.stdout

def test_kill_switch_blocks_all_commands():
    r = _run("late_curve", env_extra={"PUMP_V2_HALT": "1"})
    assert r.returncode == 0
    assert "halted" in r.stdout.lower()
```

- [ ] **Step 2: Run integration test**

```bash
cd /home/roberto/solana-storm
pytest predictions/tests/test_v2_integration.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Full test suite green**

```bash
pytest predictions/ -v
```
Expected: every test passes. If anything fails, fix before commit.

- [ ] **Step 4: Commit**

```bash
git add predictions/tests/test_v2_integration.py
git commit -m "v2 integration: rehearsal-mode smoke test exercising runner end-to-end

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final integration: CronCreate installation (out-of-band, runs once at cutover)

After all tasks merge to main, the operator runs (one-time, not part of an automated task):

```bash
# Replace with the user's actual CronCreate invocation syntax.
# Example shell forms:
CronCreate pump-v2-universe-fetch "*/15 * * * *" "cd /home/roberto/solana-storm && python3 predictions/runner.py universe_fetch"
CronCreate pump-v2-late-curve     "*/15 * * * *" "cd /home/roberto/solana-storm && python3 predictions/runner.py late_curve"
CronCreate pump-v2-catalyst       "0 * * * *"    "cd /home/roberto/solana-storm && python3 predictions/runner.py catalyst"
CronCreate pump-v2-early-curve    "0 */4 * * *"  "cd /home/roberto/solana-storm && python3 predictions/runner.py early_curve"
CronCreate pump-v2-smart-mirror   "15 */4 * * *" "cd /home/roberto/solana-storm && python3 predictions/runner.py smart_mirror"
CronCreate pump-v2-fund-manager   "30 */4 * * *" "cd /home/roberto/solana-storm && python3 predictions/runner.py fund_manager"
CronCreate pump-v2-audit-tick     "*/10 * * * *" "cd /home/roberto/solana-storm && python3 predictions/runner.py audit_tick"
```

Then run the smart-wallet seed migration (live mode requires a working Dune query + Helius pagination — flesh out the `NotImplementedError` path in `v2_smart_wallet_seed.py` before this step):

```bash
python3 -m predictions.migrations.v2_smart_wallet_seed
```

Finally:

```bash
unset PUMP_V2_HALT  # or export PUMP_V2_HALT=0
```

System is live.

---

## Wiring note for the implementing engineer

`dispatch._invoke_agent` is a stub. The actual production wiring depends on the harness — in Claude Code, the cron handler would itself BE a Claude session that uses the `Agent` tool. The `_invoke_agent` stub is overridden in that context. For local testing / non-Claude-runtime use, the stub returns empty picks and the system runs gracefully degraded.

If running outside Claude Code: replace `_invoke_agent` with a call to `claude --agent <name>` or the Claude SDK's `Agent` API. The contract is: input = full prompt string; output = JSON string per the specialist's output spec.
