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
