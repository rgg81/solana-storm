"""Pure month-stratified sampling of the graduations list.

Phase 3 validation requires every market regime in the window to be present
(design spec 4.3). Records are grouped by calendar month; a per-month quota is
the target split evenly across months; within a month a deterministic,
seed-driven pseudo-random subset is taken so the result is reproducible and
not biased by Dune's row order.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List

from bootstrap.transform import GraduationRecord


def month_key(unix_secs: int) -> str:
    """The 'YYYY-MM' calendar month of a Unix timestamp (UTC)."""
    dt = datetime.fromtimestamp(int(unix_secs), tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _sort_rank(mint: str, seed: int) -> str:
    """A deterministic, seed-salted hash of a mint, used as a sort key."""
    return hashlib.sha256(f"{seed}:{mint}".encode("utf-8")).hexdigest()


def stratified_sample(
    records: Dict[str, GraduationRecord],
    sample_size: int,
    seed: int,
) -> List[GraduationRecord]:
    """Pick ~sample_size records spread evenly across calendar months.

    Deterministic for a fixed seed. A month with fewer records than its quota
    contributes all of them (the shortfall is not redistributed).
    """
    # group by month.
    by_month: Dict[str, List[GraduationRecord]] = {}
    for record in records.values():
        by_month.setdefault(
            month_key(record.graduation_time), []
        ).append(record)

    if not by_month:
        return []

    months = sorted(by_month.keys())
    base_quota = sample_size // len(months)
    remainder = sample_size % len(months)

    picked: List[GraduationRecord] = []
    for index, month in enumerate(months):
        # the earliest `remainder` months get one extra to use the full target.
        quota = base_quota + (1 if index < remainder else 0)
        bucket = sorted(
            by_month[month], key=lambda r: _sort_rank(r.mint, seed)
        )
        picked.extend(bucket[:quota])
    return picked
