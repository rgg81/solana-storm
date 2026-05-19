"""Unit tests for bootstrap.sample -- pure month-stratified sampling."""

from bootstrap.sample import month_key, stratified_sample
from bootstrap.transform import GraduationRecord


def rec(mint: str, ts: int) -> GraduationRecord:
    """A GraduationRecord with only the fields sampling needs."""
    return GraduationRecord(
        mint=mint,
        pool_address=f"pool-{mint}",
        bonding_curve_address="bc",
        lp_mint="lp",
        migrator_wallet="mig",
        graduation_time=ts,
        graduation_slot=1,
    )


# Unix seconds inside specific months.
NOV_2025 = 1763000000  # 2025-11
DEC_2025 = 1765600000  # 2025-12
JAN_2026 = 1768200000  # 2026-01


def test_month_key_formats_year_month():
    assert month_key(NOV_2025) == "2025-11"
    assert month_key(JAN_2026) == "2026-01"


def make_pool(prefix: str, ts: int, n: int):
    return {f"{prefix}{i}": rec(f"{prefix}{i}", ts) for i in range(n)}


def test_every_month_is_represented():
    records = {}
    records.update(make_pool("nov", NOV_2025, 100))
    records.update(make_pool("dec", DEC_2025, 100))
    records.update(make_pool("jan", JAN_2026, 100))
    picked = stratified_sample(records, sample_size=30, seed=7)
    months = {month_key(r.graduation_time) for r in picked}
    assert months == {"2025-11", "2025-12", "2026-01"}
    # 30 across 3 months -> 10 each.
    assert len(picked) == 30


def test_is_deterministic_for_a_fixed_seed():
    records = {}
    records.update(make_pool("nov", NOV_2025, 50))
    records.update(make_pool("dec", DEC_2025, 50))
    a = stratified_sample(records, sample_size=20, seed=42)
    b = stratified_sample(records, sample_size=20, seed=42)
    assert [r.mint for r in a] == [r.mint for r in b]


def test_different_seeds_pick_different_subsets():
    records = make_pool("nov", NOV_2025, 100)
    a = {r.mint for r in stratified_sample(records, sample_size=10, seed=1)}
    b = {r.mint for r in stratified_sample(records, sample_size=10, seed=2)}
    assert a != b


def test_a_thin_month_contributes_all_it_has():
    records = {}
    records.update(make_pool("nov", NOV_2025, 3))  # thin month
    records.update(make_pool("dec", DEC_2025, 100))
    picked = stratified_sample(records, sample_size=20, seed=5)
    nov = [r for r in picked if month_key(r.graduation_time) == "2025-11"]
    # quota per month is 10, but November only has 3 -> all 3 taken.
    assert len(nov) == 3


def test_sample_larger_than_population_returns_everything():
    records = make_pool("nov", NOV_2025, 5)
    picked = stratified_sample(records, sample_size=1000, seed=0)
    assert len(picked) == 5


def test_empty_input_returns_empty_list():
    assert stratified_sample({}, sample_size=100, seed=1) == []
