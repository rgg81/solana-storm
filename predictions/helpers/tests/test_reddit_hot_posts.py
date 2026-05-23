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
