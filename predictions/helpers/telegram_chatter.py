"""Telegram public-channel chatter helper -- counts ticker mentions.

Uses the unauthenticated `https://t.me/s/<channel>` HTML preview endpoint
which returns the channel's recent ~20 messages. Counts case-insensitive
substring matches of the ticker (with word-boundary leniency: a $TICKER
or #TICKER prefix is common).

Usage:
    python3 telegram_chatter.py <ticker> [--dry-run]

Output JSON to stdout:
    {"data": {"ticker": ..., "total_mentions": N, "per_channel_mentions": {...}}, "error": null}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT}
_TIMEOUT = 15


def _dry_run_payload(ticker: str) -> dict:
    fixture = config.DRY_RUN_DIR / "telegram_chatter.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["ticker"] = ticker.upper()
    return payload


def _fetch_channel_text(channel: str) -> str | None:
    """Fetch the t.me/s/<channel> page and return concatenated message text."""
    url = f"https://t.me/s/{channel}"
    for delay in [0, 1, 3]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 429:
                time.sleep(5)
                continue
            if r.status_code >= 400:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            messages = soup.select(".tgme_widget_message_text")
            return "\n".join(m.get_text(separator=" ") for m in messages)
        except Exception:
            continue
    return None


def _count_ticker_mentions(text: str, ticker: str) -> int:
    """Case-insensitive count, lenient on $ / # prefix."""
    if not text:
        return 0
    pat = re.compile(rf"[\$#]?{re.escape(ticker)}\b", re.IGNORECASE)
    return len(pat.findall(text))


def _live_query(ticker: str) -> dict:
    channels = config.TELEGRAM_CHANNELS
    per_channel = {}
    dropped = []
    for ch in channels:
        text = _fetch_channel_text(ch)
        if text is None:
            dropped.append(ch)
            continue
        per_channel[ch] = _count_ticker_mentions(text, ticker)
        time.sleep(1)
    total = sum(per_channel.values())
    return {
        "data": {
            "ticker": ticker.upper(),
            "channels_polled": len(channels),
            "channels_available": len(per_channel),
            "channels_dropped": dropped,
            "total_mentions": total,
            "per_channel_mentions": per_channel,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.ticker)
    else:
        payload = _live_query(args.ticker)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
