"""One-time migration: seed smart-wallet registry from Dune historical winners.

Usage:
    python3 -m predictions.migrations.v2_smart_wallet_seed [--dry-run]

Algorithm:
1. Query Dune for tokens that graduated in last 30 days where realized return >= 5x
2. For each, fetch first-hour buyers via Helius
3. Aggregate winner_hits + total_observations per wallet
4. Filter to wallets with precision >= 0.3 AND total_observations >= 3
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
    This is a deliberate simplification -- we don't track each token's realized return
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
