import json
from unittest.mock import patch
from predictions.agents import dispatch


def test_dispatch_specialist_builds_correct_call():
    with patch.object(dispatch, "_invoke_agent") as mock_inv:
        mock_inv.return_value = '{"specialist": "late_curve", "picks": []}'
        result = dispatch.dispatch_specialist(
            "late_curve",
            universe_data={"data": []},
            curve_history={},
            extras={},
        )
        mock_inv.assert_called_once()
        call_args = mock_inv.call_args
        assert "Late-Curve Momentum Agent" in call_args.kwargs["prompt"]
        assert result["specialist"] == "late_curve"


def test_dispatch_fm_includes_specialist_files():
    with patch.object(dispatch, "_invoke_agent") as mock_inv:
        mock_inv.return_value = '{"specialist": "fund_manager", "final_decisions": []}'
        with patch.object(dispatch, "_collect_specialist_files") as mock_collect:
            mock_collect.return_value = [{"specialist": "late_curve", "picks": []}]
            result = dispatch.dispatch_fund_manager()
            assert result["specialist"] == "fund_manager"


def test_dispatch_fm_computes_scored_picks_from_specialist_files(tmp_path, monkeypatch):
    # arrange: fake specialist decision file with 1 BUY pick
    decisions_dir = tmp_path / "predictions" / "diary" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    fake_late = decisions_dir / "2026-05-23-10-00-late_curve.md"
    fake_late.write_text(
        '---\n'
        'specialist: "late_curve"\n'
        f'picks: {json.dumps([{"mint": "A"*44, "ticker": "TST", "conviction": "BUY HIGH", "recommended_exit": {"rule": "graduation_or_30pct_or_6h"}}])}\n'
        '---\n'
    )
    lessons = tmp_path / "predictions" / "diary" / "lessons.md"
    lessons.write_text("---\nversion: 1\ntotal_picks_audited: 0\nlate_curve:\n  picks_audited: 0\n---\nbody\n")

    monkeypatch.setattr(dispatch.config, "_REPO_ROOT", tmp_path)

    with patch.object(dispatch, "_invoke_agent") as mock_inv:
        mock_inv.return_value = '{"specialist": "fund_manager", "final_decisions": []}'
        result = dispatch.dispatch_fund_manager()

    # Verify the prompt actually included scored_picks
    call_args = mock_inv.call_args
    prompt = call_args.kwargs["prompt"]
    assert "scored_picks" in prompt
    assert "late_curve-AAAA" in prompt  # pick_id mention
    assert result["specialist"] == "fund_manager"
