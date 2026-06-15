"""Per-method circuit breaker for the Helius RPC.

Bug/cost (tick-138, 2026-06-15): Helius free-tier getTokenSupply went into a
method-level timeout (15s × 3 retries ≈ 45s PER symbol) while getHealth and
getTokenLargestAccounts still responded. With 12 symbols, staging burned ~9 min
re-confirming the same dead method. getHealth/largestAccounts successes
interleave, so a GLOBAL consecutive-failure breaker never trips — the breaker
must be PER METHOD.

Fix: after N consecutive full failures of a given method in one process, open
the breaker for THAT method only — subsequent calls return None immediately
without hitting the network. A success resets that method's counter. Each tick
is a fresh process, so the breaker re-probes Helius every tick.
"""
from __future__ import annotations

import predictions.fund.helpers.onchain_stats as oc


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {"result": "ok"}

    def json(self):
        return self._body


def _setup(monkeypatch, *, fail_methods=(), call_log=None):
    """Patch _rpc's deps: a real URL, and a requests.post that fails for
    fail_methods (raises Timeout) and succeeds otherwise. Records every method
    that actually reaches the network in call_log."""
    monkeypatch.setattr(oc, "_get_rpc_url", lambda: "https://mainnet.helius-rpc.com/?api-key=real")
    oc._reset_circuit_breaker()

    import requests

    def fake_post(url, json=None, timeout=None, **kw):
        method = json.get("method")
        if call_log is not None:
            call_log.append(method)
        if method in fail_methods:
            raise requests.exceptions.Timeout("read timed out")
        return _Resp(200, {"result": {"ok": True}})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    # No real sleeps during retry backoff.
    monkeypatch.setattr(oc.time, "sleep", lambda *a, **k: None)


def test_breaker_opens_after_threshold_and_skips_network(monkeypatch):
    calls = []
    _setup(monkeypatch, fail_methods={"getTokenSupply"}, call_log=calls)
    # Drive failures up to threshold.
    for _ in range(oc._BREAKER_THRESHOLD):
        assert oc._rpc("getTokenSupply", ["mint"]) is None
    calls_before = len(calls)
    # Next call should short-circuit — no new network hit.
    assert oc._rpc("getTokenSupply", ["mint2"]) is None
    assert len(calls) == calls_before, "breaker open: no further network calls for the method"


def test_breaker_is_per_method(monkeypatch):
    calls = []
    _setup(monkeypatch, fail_methods={"getTokenSupply"}, call_log=calls)
    for _ in range(oc._BREAKER_THRESHOLD):
        oc._rpc("getTokenSupply", ["mint"])
    # getTokenSupply breaker is open, but a DIFFERENT method still goes through.
    calls.clear()
    res = oc._rpc("getHealth", [])
    assert res == {"ok": True}
    assert "getHealth" in calls, "a healthy method is not blocked by another method's breaker"


def test_success_resets_counter(monkeypatch):
    calls = []
    # getTokenSupply fails the first two times then we flip it to succeed.
    state = {"fail": True}
    monkeypatch.setattr(oc, "_get_rpc_url", lambda: "https://mainnet.helius-rpc.com/?api-key=real")
    oc._reset_circuit_breaker()
    import requests

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append(json.get("method"))
        if state["fail"]:
            raise requests.exceptions.Timeout("t")
        return _Resp(200, {"result": {"ok": True}})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    monkeypatch.setattr(oc.time, "sleep", lambda *a, **k: None)

    assert oc._rpc("getTokenSupply", ["m"]) is None  # 1 consecutive failure
    state["fail"] = False
    assert oc._rpc("getTokenSupply", ["m"]) == {"ok": True}  # success resets
    state["fail"] = True
    # Counter was reset, so one more failure should NOT have opened the breaker yet;
    # the call still reaches the network.
    calls.clear()
    oc._rpc("getTokenSupply", ["m"])
    assert "getTokenSupply" in calls
