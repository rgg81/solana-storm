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
