from predictions.agents import fm_allocation


def test_cold_start_equal_weight():
    weights = fm_allocation.specialist_weights(
        stats={"late_curve": {"picks_audited": 5}, "early_curve": {"picks_audited": 3}}
    )
    assert weights["late_curve"] == 1.0 and weights["early_curve"] == 1.0


def test_mature_uses_hit_rate():
    weights = fm_allocation.specialist_weights(
        stats={"late_curve": {"picks_audited": 50, "hit_rate_last_30d": 0.25},
               "early_curve": {"picks_audited": 50, "hit_rate_last_30d": 0.10}}
    )
    assert weights["late_curve"] == 0.25
    assert weights["early_curve"] == 0.10


def test_floor_is_0_1():
    weights = fm_allocation.specialist_weights(
        stats={"late_curve": {"picks_audited": 50, "hit_rate_last_30d": 0.02}}
    )
    assert weights["late_curve"] == 0.1


def test_score_pick_basic():
    s = fm_allocation.score_pick(
        specialist="late_curve", conviction="BUY HIGH",
        specialist_weight=1.0,
        validated_lesson_fires=False, candidate_lesson_fires=0,
        convergence_count=1,
    )
    assert s == 1.0


def test_score_validated_veto():
    s = fm_allocation.score_pick(
        specialist="late_curve", conviction="BUY HIGH", specialist_weight=1.0,
        validated_lesson_fires=True, candidate_lesson_fires=0, convergence_count=1,
    )
    assert s == 0.0


def test_score_convergence_bonus():
    s_single = fm_allocation.score_pick("c", "BUY HIGH", 1.0, False, 0, 1)
    s_double = fm_allocation.score_pick("c", "BUY HIGH", 1.0, False, 0, 2)
    assert s_double > s_single
    assert abs(s_double - 1.1) < 0.001


def test_sizing_caps():
    sizes = fm_allocation.compute_sizes(
        scored_picks=[("p1", 1.0), ("p2", 0.5), ("p3", 0.1)],
        cold_start=False,
    )
    assert sum(sizes.values()) <= 0.80 + 1e-6
    assert max(sizes.values()) <= 0.20 + 1e-6


def test_cold_start_more_conservative():
    sizes = fm_allocation.compute_sizes(
        scored_picks=[("p1", 1.0)],
        cold_start=True,
    )
    assert max(sizes.values()) <= 0.10 + 1e-6
