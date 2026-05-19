"""Unit tests for bootstrap.cache."""

from bootstrap.cache import cache_path, has_cache, read_cache, write_cache


def test_round_trip_a_payload(tmp_path):
    payload = {"rows": [{"mint": "abc", "n": 1}], "credits": 2.0}
    write_cache(str(tmp_path), "graduations", payload)
    assert has_cache(str(tmp_path), "graduations") is True
    assert read_cache(str(tmp_path), "graduations") == payload


def test_missing_cache_reads_none(tmp_path):
    assert has_cache(str(tmp_path), "nope") is False
    assert read_cache(str(tmp_path), "nope") is None


def test_batch_index_makes_a_distinct_file(tmp_path):
    write_cache(str(tmp_path), "holders", {"v": 0}, batch=0)
    write_cache(str(tmp_path), "holders", {"v": 1}, batch=1)
    assert read_cache(str(tmp_path), "holders", batch=0) == {"v": 0}
    assert read_cache(str(tmp_path), "holders", batch=1) == {"v": 1}
    # no-batch and batch-0 are different files.
    assert has_cache(str(tmp_path), "holders") is False


def test_cache_path_is_deterministic(tmp_path):
    p1 = cache_path(str(tmp_path), "outcome", batch=3)
    p2 = cache_path(str(tmp_path), "outcome", batch=3)
    assert p1 == p2
    assert p1.endswith("outcome_batch003.json")


def test_write_creates_the_cache_dir(tmp_path):
    nested = tmp_path / "deep" / "data"
    write_cache(str(nested), "stage", {"ok": True})
    assert (nested / "stage.json").is_file()
