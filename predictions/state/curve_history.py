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
