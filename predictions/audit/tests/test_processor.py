import json
from pathlib import Path
from predictions.audit import processor

def test_enqueue_and_pop_due():
    pending = Path("/tmp/storm_test_pending.jsonl")
    pending.unlink(missing_ok=True)
    processor.enqueue(pending, {
        "pick_id": "p1", "mint": "A" * 44, "pool": "B" * 44,
        "specialist": "late_curve", "entry_quote_lamports": 1_000_000,
        "entry_base_lamports": 2_000_000, "due_unix": 1, "exit_rule": "graduation_or_30pct_or_6h"
    })
    processor.enqueue(pending, {
        "pick_id": "p2", "mint": "C" * 44, "pool": "D" * 44,
        "specialist": "catalyst", "entry_quote_lamports": 1_000,
        "entry_base_lamports": 2_000, "due_unix": 9999999999, "exit_rule": "narrative"
    })
    due, remaining = processor.partition_due(pending, now_unix=10)
    assert len(due) == 1 and due[0]["pick_id"] == "p1"
    assert len(remaining) == 1 and remaining[0]["pick_id"] == "p2"

def test_compute_return_normal_pool():
    ret = processor.compute_realized_return(
        entry_quote=100_000_000, entry_base=200_000_000,
        current_quote=80_000_000, current_base=240_000_000, pool_closed=False)
    # entry_price = 0.5; current_price = 0.333...; ret ≈ -0.333
    assert abs(ret - (-0.3333)) < 0.001

def test_compute_return_rugged_pool():
    ret = processor.compute_realized_return(0, 0, 0, 0, pool_closed=True)
    assert ret == -1.0
