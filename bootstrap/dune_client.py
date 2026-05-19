"""Dune Analytics REST API client.

Wraps the create -> patch SQL -> execute -> poll -> fetch-results flow over
urllib. Every HTTP call goes through an injectable `transport` callable so
tests use a fake transport and never touch the network. The free Dune engine
is used (default performance), so its 2-minute timeout applies and caps any
runaway query.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Tuple

from bootstrap.config import Config

# transport(method, url, headers, body_dict_or_None) -> (status_code, resp_dict)
Transport = Callable[[str, str, dict, Optional[dict]], Tuple[int, dict]]

_TERMINAL_OK = "QUERY_STATE_COMPLETED"
_TERMINAL_BAD = ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED")


class DuneError(RuntimeError):
    """A Dune API error: a non-2xx HTTP status or a FAILED/CANCELLED run."""


class DuneTimeout(DuneError):
    """A FAILED execution whose message indicates a free-engine timeout."""


def _urllib_transport(
    method: str, url: str, headers: dict, body: Optional[dict]
) -> Tuple[int, dict]:
    """Default transport: a thin urllib.request wrapper."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload


def _looks_like_timeout(message: str) -> bool:
    """Heuristic: does a FAILED message indicate the free-engine timeout?"""
    low = message.lower()
    return "execution time" in low or "timeout" in low or "timed out" in low


class DuneClient:
    """A minimal Dune API client built on an injectable transport."""

    def __init__(self, config: Config, transport: Optional[Transport] = None):
        self._config = config
        self._transport: Transport = transport or _urllib_transport
        self._query_id: Optional[int] = None  # lazily created, then reused

    # --- low-level request ---------------------------------------------------

    def _headers(self) -> dict:
        return {
            "X-Dune-API-Key": self._config.dune_api_key,
            "Content-Type": "application/json",
        }

    def _request(
        self, method: str, path: str, body: Optional[dict] = None
    ) -> dict:
        url = f"{self._config.dune_base_url}{path}"
        status, payload = self._transport(method, url, self._headers(), body)
        if status < 200 or status >= 300:
            raise DuneError(
                f"Dune API {method} {path} -> HTTP {status}: {payload}"
            )
        return payload

    # --- API surface ---------------------------------------------------------

    def create_query(self, name: str, sql: str) -> int:
        """Create a private Dune query; return its query_id."""
        payload = self._request(
            "POST",
            "/api/v1/query",
            {"name": name, "query_sql": sql, "is_private": True},
        )
        query_id = payload.get("query_id")
        if query_id is None:
            raise DuneError(f"create_query: no query_id in response {payload}")
        return int(query_id)

    def update_query_sql(self, query_id: int, sql: str) -> None:
        """Replace a query's SQL (PATCH)."""
        self._request(
            "PATCH", f"/api/v1/query/{query_id}", {"query_sql": sql}
        )

    def execute_query(self, query_id: int) -> str:
        """Execute a query on the free engine; return the execution_id."""
        payload = self._request(
            "POST", f"/api/v1/query/{query_id}/execute", {}
        )
        execution_id = payload.get("execution_id")
        if execution_id is None:
            raise DuneError(f"execute_query: no execution_id in {payload}")
        return str(execution_id)

    def poll_until_done(
        self, execution_id: str, poll_interval: float = 3.0
    ) -> dict:
        """Poll execution status until a terminal state.

        Returns the final status dict on success. Raises DuneTimeout on a
        timeout-flavoured FAILED, or DuneError on any other FAILED/CANCELLED.
        """
        while True:
            status = self._request(
                "GET", f"/api/v1/execution/{execution_id}/status"
            )
            state = status.get("state")
            if state == _TERMINAL_OK:
                return status
            if state in _TERMINAL_BAD:
                message = ""
                err = status.get("error")
                if isinstance(err, dict):
                    message = str(err.get("message", ""))
                elif err is not None:
                    message = str(err)
                if state == "QUERY_STATE_FAILED" and _looks_like_timeout(
                    message
                ):
                    raise DuneTimeout(
                        f"execution {execution_id} timed out: {message}"
                    )
                raise DuneError(
                    f"execution {execution_id} {state}: {message}"
                )
            if poll_interval:
                time.sleep(poll_interval)

    def get_results(self, execution_id: str) -> List[dict]:
        """Fetch a completed execution's result rows."""
        payload = self._request(
            "GET", f"/api/v1/execution/{execution_id}/results"
        )
        result = payload.get("result") or {}
        return list(result.get("rows", []))

    def run_sql(self, sql: str) -> Tuple[List[dict], float]:
        """Run SQL end-to-end: (create once) -> patch -> execute -> poll ->
        fetch. Returns (rows, credits_spent).

        Raises DuneTimeout on a free-engine timeout (the caller decides
        whether that is fatal or a NULL-fallback).
        """
        if self._query_id is None:
            self._query_id = self.create_query(
                "solana-storm historical bootstrap ETL", sql
            )
        self.update_query_sql(self._query_id, sql)
        execution_id = self.execute_query(self._query_id)
        status = self.poll_until_done(execution_id)
        credits = float(status.get("execution_cost_credits", 0.0) or 0.0)
        rows = self.get_results(execution_id)
        return rows, credits
