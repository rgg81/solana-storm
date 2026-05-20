"""The intraperiod-snapshots ETL orchestrator (Task 8 of the stop-loss plan).

Wires: config -> read existing graduations -> for each snapshot index 1..14,
run batched cached Dune queries (the intraperiod_snapshot_sql template) ->
remap pool_address to mint -> idempotent insert into intraperiod_snapshots.

Like bootstrap/run.py, this is idempotent and resumable: each (mint,
snapshot_index) already present in the table is skipped on insert; each
per-day-per-batch Dune query is disk-cached, so a re-run never re-spends
Dune credits on stages already completed.

Batching: each per-day stage is split into batches of `snapshot_batch_size`
pools (default 50). Each batch is a separate Dune query, cached separately
as `snapshot_NN_batch000.json`. This keeps each query under the free-engine
2-minute timeout.

Usage:
    python3 -m bootstrap.run_snapshots                # all 14 days, all mints
    python3 -m bootstrap.run_snapshots --pilot        # first 50 mints, all 14 days
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from bootstrap import cache, queries, transform
from bootstrap.config import load_config
from bootstrap.dune_client import DuneClient
from bootstrap.load import (
    create_snapshots_table,
    existing_snapshots,
    insert_snapshots,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bootstrap.run_snapshots")

_NUM_SNAPSHOT_DAYS = 14
# Max pools per single Dune query. Liquidity queries in run.py use 150;
# snapshot queries scan two event tables over a full 24-h window so are
# heavier — use a smaller batch to stay under the free-engine 2-min cap.
_SNAPSHOT_BATCH_SIZE = 25


def _batched(items: list, size: int) -> List[List]:
    """Split a list into consecutive chunks of at most `size`."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _static_bounds(
    pair_batch: List[tuple], snapshot_day_offset: int
) -> tuple:
    """Compute (static_lower, static_upper) timestamp strings for a batch.

    Given a batch of (pool, grad_time_str) pairs and a snapshot day offset N,
    returns static strings for the tightest possible global time bounds:
      lower = min(grad_time) + N days
      upper = max(grad_time) + (N+1) days

    These static bounds let the Dune engine skip event-table partitions outside
    the batch's time range, greatly reducing the scan cost.
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    grad_times = [
        datetime.strptime(grad_str, fmt).replace(tzinfo=timezone.utc)
        for _, grad_str in pair_batch
    ]
    min_grad = min(grad_times)
    max_grad = max(grad_times)
    lower = (min_grad + timedelta(days=snapshot_day_offset)).strftime(fmt)
    upper = (max_grad + timedelta(days=snapshot_day_offset + 1)).strftime(fmt)
    return lower, upper


class CreditMeter:
    """Accumulates Dune execution credits across the 14 snapshot stages."""

    def __init__(self) -> None:
        self.total = 0.0

    def add(self, credits: float, stage: str) -> None:
        self.total += credits
        log.info(
            "stage %s +%.2f credits (running total %.2f)",
            stage, credits, self.total,
        )


def _fmt_grad_time(unix_secs: int) -> str:
    """Unix seconds -> Dune 'YYYY-MM-DD HH:MM:SS' UTC string."""
    return datetime.fromtimestamp(int(unix_secs), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _load_pool_to_mint(conn: sqlite3.Connection) -> Dict[str, str]:
    """Build a pool_address -> mint map from historical_graduations."""
    rows = conn.execute(
        "SELECT mint, pool_address FROM historical_graduations"
    ).fetchall()
    return {pool: mint for mint, pool in rows}


def _load_pool_grad_pairs(
    conn: sqlite3.Connection, pilot: bool
) -> List[tuple]:
    """List of (pool_address, grad_time_str) tuples for all (or piloted) graduations."""
    sql = (
        "SELECT pool_address, graduation_time FROM historical_graduations "
        "ORDER BY graduation_time"
    )
    if pilot:
        sql += " LIMIT 50"
    return [
        (pool, _fmt_grad_time(grad_time))
        for pool, grad_time in conn.execute(sql).fetchall()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intra-period snapshots ETL"
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="run on a 50-mint pilot subset",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="path to the SQLite DB (default: ./storm.db)",
    )
    args = parser.parse_args()

    config = load_config(pilot=args.pilot)
    db_path = args.db if args.db else config.db_path
    conn = sqlite3.connect(db_path)

    log.info("creating intraperiod_snapshots table if missing")
    create_snapshots_table(conn)

    log.info("loading graduation pairs and pool->mint map")
    pairs = _load_pool_grad_pairs(conn, pilot=args.pilot)
    pool_to_mint = _load_pool_to_mint(conn)
    log.info("graduations to query: %d", len(pairs))

    loaded = existing_snapshots(conn)
    log.info(
        "existing snapshots: %d mints already fully loaded (all 14 days)",
        sum(1 for mints in loaded.values() if len(mints) == _NUM_SNAPSHOT_DAYS),
    )

    # DuneClient takes a Config object (not api_key kwarg directly).
    client = DuneClient(config)
    meter = CreditMeter()

    batches = _batched(pairs, _SNAPSHOT_BATCH_SIZE)
    log.info(
        "splitting %d graduations into %d batches of up to %d",
        len(pairs), len(batches), _SNAPSHOT_BATCH_SIZE,
    )

    for day in range(1, _NUM_SNAPSHOT_DAYS + 1):
        stage_name = f"snapshot_{day:02d}"
        log.info(
            "running Dune stage %s (day %d / %d, %d batches)",
            stage_name, day, _NUM_SNAPSHOT_DAYS, len(batches),
        )

        all_rows: List[dict] = []
        for batch_index, pair_batch in enumerate(batches):
            cached = cache.read_cache(config.cache_dir, stage_name, batch_index)
            if cached is not None:
                batch_rows = cached.get("rows", [])
                log.info(
                    "stage %s batch %d/%d loaded %d rows from cache",
                    stage_name, batch_index, len(batches) - 1, len(batch_rows),
                )
            else:
                static_lower, static_upper = _static_bounds(pair_batch, day)
                sql = queries.intraperiod_snapshot_sql(
                    pair_batch,
                    snapshot_day_offset=day,
                    window_start=config.window_start,
                    static_lower=static_lower,
                    static_upper=static_upper,
                )
                batch_rows, credits = client.run_sql(sql)
                cache.write_cache(
                    config.cache_dir, stage_name, {"rows": batch_rows}, batch_index
                )
                log.info(
                    "stage %s batch %d/%d fetched %d rows (%.2f credits, cached)",
                    stage_name, batch_index, len(batches) - 1,
                    len(batch_rows), credits,
                )
                meter.add(credits, f"{stage_name}[{batch_index}]")
            all_rows.extend(batch_rows)

        records = transform.parse_snapshots(all_rows, snapshot_index=day)

        # Remap pool_address -> mint. Records from parse_snapshots have
        # mint=None and pool_address=<the pool>. Drop records with no mapping.
        remapped: list = []
        unmapped = 0
        for r in records:
            mint = pool_to_mint.get(r.pool_address)
            if mint is None:
                unmapped += 1
                continue
            r.mint = mint
            remapped.append(r)
        if unmapped:
            log.warning(
                "stage %s: %d records had no pool->mint mapping (dropped)",
                stage_name, unmapped,
            )

        insert_snapshots(conn, remapped)
        log.info(
            "stage %s inserted %d records (skipping any already-loaded)",
            stage_name, len(remapped),
        )

    conn.close()
    log.info("intraperiod_snapshots ETL complete (total credits %.2f)", meter.total)


if __name__ == "__main__":
    main()
