"""Established Solana token universe — Pivot A (2026-05-23).

10 tokens with real liquidity, real price discovery, established
multi-month price history. Inverse-vol-weighted basket strategy.
"""
from __future__ import annotations

# (coingecko_id, ticker, on-chain mint) — minted addresses for live Helius lookups
UNIVERSE = [
    ("jupiter-exchange-solana", "JUP", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),
    ("bonk",                    "BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),
    ("dogwifcoin",              "WIF", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"),
    ("jito-governance-token",   "JTO", "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"),
    ("raydium",                 "RAY", "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"),
    ("orca",                    "ORCA", "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE"),
    ("pyth-network",            "PYTH", "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"),
    ("popcat",                  "POPCAT", "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"),
    ("cat-in-a-dogs-world",     "MEW", "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5"),
    ("drift-protocol",          "DRIFT", "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7"),
]

TICKERS = [t for _, t, _ in UNIVERSE]
TICKER_TO_MINT = {t: m for _, t, m in UNIVERSE}
TICKER_TO_CGID = {t: c for c, t, _ in UNIVERSE}
