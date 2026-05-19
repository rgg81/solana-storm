"""Unit tests for bootstrap.dune_client, using a fake transport.

The fake transport is a callable with the same signature as the real one:
    transport(method, url, headers, body) -> (status_code, response_dict)
It is scripted with a queue of canned responses so no network call happens.
"""

import pytest

from bootstrap.config import Config
from bootstrap.dune_client import DuneClient, DuneError, DuneTimeout


def make_config():
    return Config(dune_api_key="test-key")


class FakeTransport:
    """A scripted transport: returns queued responses, records every call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if not self._responses:
            raise AssertionError(f"unexpected extra call: {method} {url}")
        return self._responses.pop(0)


def test_run_sql_happy_path_returns_rows_and_credits():
    rows = [{"mint": "M1", "x": 1}, {"mint": "M2", "x": 2}]
    transport = FakeTransport(
        [
            (200, {"query_id": 42}),  # create_query
            (200, {}),  # update_query_sql (PATCH)
            (200, {"execution_id": "EX1"}),  # execute_query
            (200, {"state": "QUERY_STATE_EXECUTING"}),  # poll #1
            (
                200,
                {"state": "QUERY_STATE_COMPLETED",
                 "execution_cost_credits": 3.5},
            ),  # poll #2 -> done
            (200, {"result": {"rows": rows}}),  # get_results
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    got_rows, credits = client.run_sql("SELECT 1")
    assert got_rows == rows
    assert credits == 3.5
    # the API key rode on every request header.
    for _method, _url, headers, _body in transport.calls:
        assert headers["X-Dune-API-Key"] == "test-key"


def test_run_sql_reuses_an_already_created_query():
    """A second run_sql does not re-create the query -- only PATCH/execute."""
    rows = [{"mint": "M3"}]
    transport = FakeTransport(
        [
            (200, {"query_id": 7}),  # create (first call only)
            (200, {}),  # patch #1
            (200, {"execution_id": "E1"}),  # execute #1
            (200, {"state": "QUERY_STATE_COMPLETED",
                   "execution_cost_credits": 1.0}),
            (200, {"result": {"rows": rows}}),
            (200, {}),  # patch #2 (no second create)
            (200, {"execution_id": "E2"}),  # execute #2
            (200, {"state": "QUERY_STATE_COMPLETED",
                   "execution_cost_credits": 1.0}),
            (200, {"result": {"rows": rows}}),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    client.run_sql("SELECT 1")
    client.run_sql("SELECT 2")
    creates = [
        c for c in transport.calls
        if c[0] == "POST" and c[1].endswith("/query")
    ]
    assert len(creates) == 1, "query should be created exactly once"


def test_timeout_state_raises_dune_timeout():
    transport = FakeTransport(
        [
            (200, {"query_id": 1}),
            (200, {}),
            (200, {"execution_id": "EX"}),
            (
                200,
                {
                    "state": "QUERY_STATE_FAILED",
                    "error": {"message": "Query exceeded maximum execution "
                                         "time of 120 seconds"},
                },
            ),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneTimeout):
        client.run_sql("SELECT slow")


def test_resource_cap_failure_also_raises_dune_timeout():
    # The free engine's per-query resource cap is treated like a timeout:
    # both mean the batch is too heavy and the caller should skip it.
    transport = FakeTransport(
        [
            (200, {"query_id": 1}),
            (200, {}),
            (200, {"execution_id": "EX"}),
            (
                200,
                {
                    "state": "QUERY_STATE_FAILED",
                    "error": {"message": "Query execution has exceeded the "
                                         "user defined maximum amount of "
                                         "resources"},
                },
            ),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneTimeout):
        client.run_sql("SELECT heavy")


def test_non_timeout_failure_raises_dune_error():
    transport = FakeTransport(
        [
            (200, {"query_id": 1}),
            (200, {}),
            (200, {"execution_id": "EX"}),
            (
                200,
                {"state": "QUERY_STATE_FAILED",
                 "error": {"message": "syntax error near SELCT"}},
            ),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneError) as excinfo:
        client.run_sql("SELCT bad")
    assert not isinstance(excinfo.value, DuneTimeout)


def test_http_error_status_raises_dune_error():
    transport = FakeTransport([(401, {"error": "invalid API key"})])
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneError, match="401"):
        client.create_query("q", "SELECT 1")


def test_poll_treats_pending_then_completed_as_done():
    transport = FakeTransport(
        [
            (200, {"state": "QUERY_STATE_PENDING"}),
            (200, {"state": "QUERY_STATE_PENDING"}),
            (200, {"state": "QUERY_STATE_COMPLETED",
                   "execution_cost_credits": 0.0}),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    status = client.poll_until_done("EX", poll_interval=0)
    assert status["state"] == "QUERY_STATE_COMPLETED"
