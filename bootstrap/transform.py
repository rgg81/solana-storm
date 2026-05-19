"""Pure transforms: raw Dune result rows -> typed GraduationRecord objects.

GraduationRecord mirrors the historical_graduations table. Large u64 on-chain
values are kept as strings (the repo SQLite convention -- SQLite's max integer
is i64). Booleans and the outcome are ints (0/1). NULL-able feature fields
default to None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set


@dataclass
class GraduationRecord:
    """One row of historical_graduations -- all features for one token."""

    # --- graduation facts (always present) ---
    mint: str
    pool_address: str
    bonding_curve_address: str
    lp_mint: str
    migrator_wallet: str
    graduation_time: int  # Unix seconds
    graduation_slot: int

    # --- outcome label (filled by merge_outcome) ---
    survived: Optional[int] = None  # 0 / 1
    outcome_base_reserve: Optional[str] = None  # u64 as str
    outcome_quote_reserve: Optional[str] = None  # u64 as str
    outcome_checked_at: Optional[int] = None  # Unix seconds

    # --- liquidity at ~T0+12h (merge_liquidity) ---
    liq_base_reserve: Optional[str] = None  # u64 as str
    liq_quote_reserve: Optional[str] = None  # u64 as str
    lp_burned: int = 1  # findings heuristic default; cleared if withdrawn
    pool_supply_fraction: Optional[float] = None  # Dune cannot supply -> NULL

    # --- bonding-curve final state (merge_bonding_curve) ---
    curve_real_sol_reserves: Optional[str] = None  # u64 as str
    curve_real_token_reserves: Optional[str] = None  # u64 as str
    curve_token_total_supply: Optional[str] = None  # u64 as str

    # --- contract flags (merge_contract_flags) ---
    mint_authority_present: Optional[int] = None  # 0 / 1
    freeze_authority_present: int = 0  # cohort constant (findings 3.4)

    # --- holder distribution, best-effort (merge_holders) ---
    visible_holder_count: Optional[int] = None
    top10_concentration: Optional[float] = None
    top20_concentration: Optional[float] = None
    creator_bag_fraction: Optional[float] = None  # Dune cannot supply -> NULL

    # --- deployer signal, FIRST-CLASS (merge_deployer) ---
    deployer_wallet: Optional[str] = None
    deployer_prior_launches: Optional[int] = None
    deployer_age_secs: Optional[int] = None


def parse_dune_time(value: str) -> int:
    """Parse a Dune timestamp string to Unix seconds (UTC).

    Handles 'YYYY-MM-DD HH:MM:SS[.fff] UTC' and ISO 'YYYY-MM-DDTHH:MM:SSZ'.
    """
    text = str(value).strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
        fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in text else "%Y-%m-%d %H:%M:%S"
        dt = datetime.strptime(text, fmt)
    else:
        # ISO 8601, with or without a trailing Z.
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_graduations(rows: List[dict]) -> Dict[str, GraduationRecord]:
    """Build seed GraduationRecords keyed by mint from graduations-list rows."""
    records: Dict[str, GraduationRecord] = {}
    for row in rows:
        mint = str(row["mint"])
        records[mint] = GraduationRecord(
            mint=mint,
            pool_address=str(row["pool_address"]),
            bonding_curve_address=str(row["bonding_curve_address"]),
            lp_mint=str(row["lp_mint"]),
            migrator_wallet=str(row["migrator_wallet"]),
            graduation_time=parse_dune_time(row["graduation_time"]),
            graduation_slot=int(row["graduation_slot"]),
        )
    return records


def merge_outcome(
    records: Dict[str, GraduationRecord],
    rows: List[dict],
    survival_min_quote_lamports: int,
) -> None:
    """Fold outcome rows into records and derive `survived`.

    A pool with no row in `rows` was abandoned (findings 3.1): survived = 0,
    reserves 0.
    """
    by_pool = {str(r["pool_address"]): r for r in rows}
    for record in records.values():
        row = by_pool.get(record.pool_address)
        if row is None:
            record.survived = 0
            record.outcome_base_reserve = "0"
            record.outcome_quote_reserve = "0"
            record.outcome_checked_at = record.graduation_time
            continue
        quote = str(row["outcome_quote_reserve"])
        base = str(row["outcome_base_reserve"])
        record.outcome_base_reserve = base
        record.outcome_quote_reserve = quote
        record.outcome_checked_at = parse_dune_time(row["outcome_event_time"])
        record.survived = (
            1 if int(quote) >= survival_min_quote_lamports else 0
        )


def merge_liquidity(
    records: Dict[str, GraduationRecord],
    rows: List[dict],
    withdrawn_pools: Set[str],
) -> None:
    """Fold T0+12h liquidity rows into records.

    lp_burned is the findings heuristic: True unless the pool had a withdraw
    event (its pool address is in `withdrawn_pools`).
    """
    by_pool = {str(r["pool_address"]): r for r in rows}
    for record in records.values():
        if record.pool_address in withdrawn_pools:
            record.lp_burned = 0
        row = by_pool.get(record.pool_address)
        if row is None:
            continue
        record.liq_base_reserve = str(row["liq_base_reserve"])
        record.liq_quote_reserve = str(row["liq_quote_reserve"])


# pump_evt_tradeevent reserve columns; a NULL means Dune left the row
# undecoded (~1% of rows), so it is not a usable bonding-curve snapshot.
_CURVE_RESERVE_KEYS = (
    "real_sol_reserves",
    "real_token_reserves",
    "virtual_token_reserves",
)


def merge_bonding_curve(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold bonding-curve trade rows; keep the last trade before migration.

    "Before migration" is by slot, not timestamp (findings caveat 4): only
    trades whose evt_block_slot is strictly less than the mint's
    graduation_slot count. Trade rows with a NULL reserve are skipped (Dune
    leaves ~1% of pump_evt_tradeevent rows undecoded); the latest fully
    populated pre-migration trade is used instead.
    """
    best_by_mint: Dict[str, dict] = {}
    for row in rows:
        mint = str(row["mint"])
        record = records.get(mint)
        if record is None:
            continue
        if any(row[key] is None for key in _CURVE_RESERVE_KEYS):
            continue  # undecoded trade row -- not a usable curve snapshot
        slot = int(row["evt_block_slot"])
        if slot >= record.graduation_slot:
            continue  # at/after migration -> not the pre-graduation state
        best = best_by_mint.get(mint)
        if best is None or slot > int(best["evt_block_slot"]):
            best_by_mint[mint] = row
    for mint, row in best_by_mint.items():
        record = records[mint]
        real_token = str(row["real_token_reserves"])
        virtual_token = str(row["virtual_token_reserves"])
        record.curve_real_sol_reserves = str(row["real_sol_reserves"])
        record.curve_real_token_reserves = real_token
        # total supply = virtual + real token reserves at the final trade.
        record.curve_token_total_supply = str(
            int(virtual_token) + int(real_token)
        )


def merge_contract_flags(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold contract-flag rows; set mint_authority_present (0/1)."""
    by_mint = {str(r["mint"]): r for r in rows}
    for mint, record in records.items():
        row = by_mint.get(mint)
        if row is None:
            continue
        record.mint_authority_present = int(row["mint_authority_present"])
        # freeze_authority_present stays the cohort constant 0.


def merge_deployer(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold deployer-signal rows -- the FIRST-CLASS deployer fingerprint."""
    by_mint = {str(r["mint"]): r for r in rows}
    for mint, record in records.items():
        row = by_mint.get(mint)
        if row is None:
            continue  # findings caveat 8: not every mint has a create row
        record.deployer_wallet = str(row["deployer_wallet"])
        record.deployer_prior_launches = int(row["deployer_prior_launches"])
        record.deployer_age_secs = int(row["deployer_age_secs"])


def merge_holders(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold holder-distribution rows (best-effort).

    Mints absent from `rows` keep their None holder fields -- the designed
    NULL fallback when a holder batch times out or has no transfers.
    """
    by_mint = {str(r["mint"]): r for r in rows}
    for mint, record in records.items():
        row = by_mint.get(mint)
        if row is None:
            continue
        record.visible_holder_count = int(row["visible_holder_count"])
        record.top10_concentration = float(row["top10_concentration"])
        record.top20_concentration = float(row["top20_concentration"])
