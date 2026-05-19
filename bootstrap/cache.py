"""Stage-result disk cache -- gives the ETL crash-resumability.

Each Dune stage writes its raw parsed result to a deterministically-named JSON
file under the cache dir. On a re-run a stage reads the file instead of
re-querying Dune, so a crash never re-spends Dune credits.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


def cache_path(cache_dir: str, stage: str, batch: Optional[int] = None) -> str:
    """Deterministic JSON path for a stage (optionally a batch within it)."""
    if batch is None:
        name = f"{stage}.json"
    else:
        name = f"{stage}_batch{batch:03d}.json"
    return os.path.join(cache_dir, name)


def has_cache(cache_dir: str, stage: str, batch: Optional[int] = None) -> bool:
    """True if a cache file for this stage/batch already exists."""
    return os.path.isfile(cache_path(cache_dir, stage, batch))


def write_cache(
    cache_dir: str,
    stage: str,
    payload: Any,
    batch: Optional[int] = None,
) -> None:
    """Write a JSON-serialisable payload to the stage's cache file.

    Creates the cache directory if it does not exist.
    """
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_path(cache_dir, stage, batch)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def read_cache(
    cache_dir: str,
    stage: str,
    batch: Optional[int] = None,
) -> Optional[Any]:
    """Return the cached payload for a stage/batch, or None if absent."""
    path = cache_path(cache_dir, stage, batch)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
