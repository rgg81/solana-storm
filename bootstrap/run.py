"""The historical-graduation ETL orchestrator.

Wires: config -> graduations list -> month-stratified sample -> skip
already-loaded mints -> per-stage Dune queries (each batch disk-cached) ->
transform -> load into historical_graduations. Idempotent and resumable: a
re-run skips loaded mints and reuses cached stage results, so a crash never
re-spends Dune credits. Holder distribution is best-effort: a DuneTimeout on a
batch leaves that batch's holder columns NULL and the run continues.

Usage:
    python3 -m bootstrap.run            # full ~5,000-token run
    python3 -m bootstrap.run --pilot    # ~75-token end-to-end pilot
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from bootstrap import cache, queries, transform
from bootstrap.config import Config, load_config
from bootstrap.dune_client import DuneClient, DuneTimeout
from bootstrap.load import create_table, existing_mints, load_records
from bootstrap.sample import stratified_sample
from bootstrap.transform import GraduationRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bootstrap")

# A fixed seed so the stratified sample is reproducible across re-runs.
_SAMPLE_SEED = 20260519


class CreditMeter:
    """Accumulates Dune execution credits and logs a running total."""

    def __init__(self) -> None:
        self.total = 0.0

    def add(self, credits: float, label: str) -> None:
        self.total += credits
        log.info(
            "stage %-20s +%.2f credits (running total %.2f)",
            label, credits, self.total,
        )


def _batched(items: List[str], size: int) -> List[List[str]]:
    """Split a list into chunks of at most `size`."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _settle_cutoff(config: Config) -> str:
    """ISO date now - outcome_settle_days: tokens after it are not settled."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=config.outcome_settle_days
    )
    return cutoff.strftime("%Y-%m-%d")


def _fmt_ts(unix_secs: int) -> str:
    """A Unix timestamp as a Dune 'YYYY-MM-DD HH:MM:SS' UTC string."""
    return datetime.fromtimestamp(int(unix_secs), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _run_cached_stage(
    client: DuneClient,
    meter: CreditMeter,
    config: Config,
    stage: str,
    sql: str,
    batch: Optional[int] = None,
) -> List[dict]:
    """Run one Dune query, or load it from the disk cache if already run.

    Returns the stage's result rows. Raises DuneTimeout to the caller (the
    holder stage catches it; every other stage treats it as fatal).
    """
    cached = cache.read_cache(config.cache_dir, stage, batch)
    if cached is not None:
        log.info("stage %-20s cache hit (batch %s)", stage, batch)
        return cached.get("rows", [])
    rows, credits = client.run_sql(sql)
    label = f"{stage}[{batch}]" if batch is not None else stage
    meter.add(credits, label)
    cache.write_cache(config.cache_dir, stage, {"rows": rows}, batch)
    return rows


def fetch_graduations(
    client: DuneClient, meter: CreditMeter, config: Config
) -> List[dict]:
    """Stage 1: the full settled graduations list (single cached query)."""
    sql = queries.graduations_sql(
        window_start=config.window_start,
        settle_cutoff=_settle_cutoff(config),
    )
    return _run_cached_stage(client, meter, config, "graduations", sql)


def run_etl(config: Config, skip_holders: bool = False) -> None:
    """Run the whole ETL end-to-end for the given config."""
    meter = CreditMeter()
    # Allow a pre-existing query to be reused via DUNE_QUERY_ID env var.
    # This avoids a create_query call when the free-tier private query cap
    # has been reached (the client will PATCH the existing query's SQL).
    existing_query_id: Optional[int] = None
    _qid_env = os.environ.get("DUNE_QUERY_ID", "").strip()
    if _qid_env:
        try:
            existing_query_id = int(_qid_env)
            log.info("using pre-existing Dune query_id=%d", existing_query_id)
        except ValueError:
            log.warning("DUNE_QUERY_ID=%r is not an integer, ignoring", _qid_env)
    client = DuneClient(config, query_id=existing_query_id)
    conn = sqlite3.connect(config.db_path)
    create_table(conn)

    # --- stage 1: graduations list, then month-stratified sample ---
    grad_rows = fetch_graduations(client, meter, config)
    log.info("graduations list: %d settled graduations", len(grad_rows))
    all_records = transform.parse_graduations(grad_rows)
    sampled = stratified_sample(
        all_records, sample_size=config.sample_size, seed=_SAMPLE_SEED
    )
    log.info("sample: %d graduations across the month strata", len(sampled))

    # --- resumability: drop mints already in historical_graduations ---
    done = existing_mints(conn)
    records: Dict[str, GraduationRecord] = {
        r.mint: r for r in sampled if r.mint not in done
    }
    log.info("%d already loaded, %d to process", len(done), len(records))
    if not records:
        log.info("nothing to do -- dataset already complete for this sample")
        conn.close()
        return

    mints = sorted(records.keys())
    pool_pairs = sorted(
        {(r.pool_address, _fmt_ts(r.graduation_time)) for r in records.values()}
    )
    mint_slot_pairs = sorted(
        {(r.mint, r.graduation_slot) for r in records.values()}
    )

    # --- stage 2: outcome label (event-batched) ---
    outcome_rows: List[dict] = []
    for index, pair_batch in enumerate(
        _batched(pool_pairs, config.event_batch_size)
    ):
        outcome_rows += _run_cached_stage(
            client, meter, config, "outcome",
            queries.outcome_sql(pair_batch, window_start=config.window_start),
            batch=index,
        )
    transform.merge_outcome(
        records, outcome_rows,
        survival_min_quote_lamports=config.survival_min_quote_lamports,
    )

    # --- stage 3: liquidity at ~T0+12h (event-batched, timeout-resilient) ---
    # The T0+12h window covers a token's busy first hours, so liquidity
    # batches are smaller than outcome batches and a batch may still time
    # out -- on a timeout the batch's liq columns are left NULL and the run
    # continues (liq reserves are a nullable feature).
    liq_rows: List[dict] = []
    for index, pair_batch in enumerate(
        _batched(pool_pairs, config.liquidity_batch_size)
    ):
        marker = cache.read_cache(config.cache_dir, "liquidity", index)
        if marker is not None and marker.get("timed_out"):
            log.info("liquidity batch %d previously timed out -- skipping", index)
            continue
        try:
            liq_rows += _run_cached_stage(
                client, meter, config, "liquidity",
                queries.liquidity_sql(
                    pair_batch, window_start=config.window_start
                ),
                batch=index,
            )
        except DuneTimeout:
            log.warning(
                "liquidity batch %d timed out -- liq columns NULL for %d tokens",
                index, len(pair_batch),
            )
            cache.write_cache(
                config.cache_dir, "liquidity", {"timed_out": True}, index
            )
    # withdrawn_pools is left empty: the findings heuristic treats every
    # PumpSwap-era graduation as lp_burned unless a withdraw event is seen,
    # and the withdraw-event probe is not part of the bootstrap query set.
    transform.merge_liquidity(records, liq_rows, withdrawn_pools=set())

    # --- stage 4: bonding-curve final state ((mint,slot)-batched, resilient) ---
    bc_rows: List[dict] = []
    for index, pair_batch in enumerate(
        _batched(mint_slot_pairs, config.event_batch_size)
    ):
        marker = cache.read_cache(config.cache_dir, "bonding_curve", index)
        if marker is not None and marker.get("timed_out"):
            log.info("bonding_curve batch %d previously too heavy -- skipping", index)
            continue
        try:
            bc_rows += _run_cached_stage(
                client, meter, config, "bonding_curve",
                queries.bonding_curve_sql(pair_batch), batch=index,
            )
        except DuneTimeout:
            log.warning(
                "bonding_curve batch %d too heavy -- curve columns NULL for %d mints",
                index, len(pair_batch),
            )
            cache.write_cache(
                config.cache_dir, "bonding_curve", {"timed_out": True}, index
            )
    transform.merge_bonding_curve(records, bc_rows)

    # --- stage 5: contract flags (mint-batched, resilient) ---
    flag_rows: List[dict] = []
    for index, mint_batch in enumerate(
        _batched(mints, config.flag_batch_size)
    ):
        marker = cache.read_cache(config.cache_dir, "contract_flags", index)
        if marker is not None and marker.get("timed_out"):
            log.info("contract_flags batch %d previously too heavy -- skipping", index)
            continue
        try:
            flag_rows += _run_cached_stage(
                client, meter, config, "contract_flags",
                queries.contract_flags_sql(mint_batch), batch=index,
            )
        except DuneTimeout:
            log.warning(
                "contract_flags batch %d too heavy -- flag columns NULL for %d mints",
                index, len(mint_batch),
            )
            cache.write_cache(
                config.cache_dir, "contract_flags", {"timed_out": True}, index
            )
    transform.merge_contract_flags(records, flag_rows)

    # --- stage 6: deployer signal -- FIRST-CLASS (mint-batched) ---
    dep_rows: List[dict] = []
    for index, mint_batch in enumerate(
        _batched(mints, config.flag_batch_size)
    ):
        dep_rows += _run_cached_stage(
            client, meter, config, "deployer",
            queries.deployer_sql(mint_batch),
            batch=index,
        )
    transform.merge_deployer(records, dep_rows)

    # --- stage 7: holder distribution -- BEST-EFFORT (small batches) ---
    holder_rows: List[dict] = []
    if skip_holders:
        log.info("holder stage skipped (--skip-holders); holder columns NULL")
    else:
        for index, mint_batch in enumerate(
            _batched(mints, config.holder_batch_size)
        ):
            # one snapshot time per batch is an approximation: use the batch's
            # earliest graduation + 12h, good enough for a holder snapshot.
            batch_t0 = min(records[m].graduation_time for m in mint_batch)
            snapshot = datetime.fromtimestamp(
                batch_t0 + config.liquidity_snapshot_hours * 3600,
                tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%M:%S")
            marker = cache.read_cache(config.cache_dir, "holders", index)
            if marker is not None and marker.get("timed_out"):
                log.info("holder batch %d previously timed out -- skipping", index)
                continue
            try:
                holder_rows += _run_cached_stage(
                    client, meter, config, "holders",
                    queries.holders_sql(mint_batch, snapshot_time=snapshot),
                    batch=index,
                )
            except DuneTimeout:
                log.warning(
                    "holder batch %d timed out -- columns NULL for %d mints",
                    index, len(mint_batch),
                )
                cache.write_cache(
                    config.cache_dir, "holders", {"timed_out": True}, index
                )
    transform.merge_holders(records, holder_rows)

    # --- load ---
    inserted = load_records(conn, list(records.values()))
    conn.close()
    log.info(
        "DONE: inserted %d rows; total Dune credits spent %.2f",
        inserted, meter.total,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="solana-storm Phase 2 Dune historical-graduation ETL"
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="run on a tiny sample end-to-end (validates the pipeline)",
    )
    parser.add_argument(
        "--skip-holders",
        action="store_true",
        help="skip the holder-distribution stage (it times out on the free "
        "Dune engine and would waste credits; holder columns are left NULL)",
    )
    args = parser.parse_args()
    config = load_config(pilot=args.pilot)
    log.info(
        "starting ETL (%s): sample_size=%d db=%s cache=%s",
        "PILOT" if config.is_pilot else "FULL",
        config.sample_size, config.db_path, config.cache_dir,
    )
    run_etl(config, skip_holders=args.skip_holders)


if __name__ == "__main__":
    main()
