"""Configuration constants for the pump-prediction skill.

These are stable defaults. Override per-invocation via env vars where useful.
"""

from __future__ import annotations

import os
import pathlib as _pathlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Auto-load .env so helpers work when invoked directly (e.g. python3 helper.py).
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_pathlib.Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment

# --- Universe + cohort ---
UNIVERSE_HOURS_BACK = 24                  # scan last 24h of graduations
SHORTLIST_MAX = 50                        # cap on deep-enriched candidates per run
# NOTE: liq_quote_reserve_lamports is currently 0 for all tokens (source Dune
# view doesn't expose initial pool reserves). The skill prefilters on
# curve_real_sol_reserves_lamports instead -- the floor below matches what the
# skill file documents.
PREFILTER_MIN_CURVE_SOL_LAMPORTS = 50_000_000_000  # 50 SOL on the curve at graduation
PREFILTER_MAX_DEPLOYER_PRIOR_LAUNCHES = 200
# The original liq_quote field is retained as a constant for future use,
# but defaults to 0 (effectively unused while the source data gap persists).
PREFILTER_MIN_LIQ_QUOTE_LAMPORTS = 0

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
# Prefer HELIUS_API_KEY; fall back to SOLANA_RPC_URL which already embeds the key.
_helius_api_key = os.environ.get("HELIUS_API_KEY", "").strip()
_solana_rpc = os.environ.get("SOLANA_RPC_URL", "").strip()
if _helius_api_key and not _helius_api_key.startswith("PASTE_"):
    HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={_helius_api_key}"
elif _solana_rpc:
    HELIUS_RPC_URL = _solana_rpc
else:
    HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={_helius_api_key}"
PUMPFUN_API_BASE = "https://frontend-api-v3.pump.fun"  # v3 -- v1 host has Cloudflare 1016 DNS error
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
