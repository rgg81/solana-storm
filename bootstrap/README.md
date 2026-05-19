# bootstrap — Phase 2 historical-graduation ETL

A one-time Python ETL that pulls a historical pump.fun graduation dataset from
the [Dune Analytics](https://dune.com) API into a single self-contained SQLite
table, `historical_graduations`, which the Phase 3 ML backtest runs on.

This package is **decoupled from the Rust workspace**: no crate, no `Cargo.toml`,
no `storm-store` migration. The table is created by this ETL itself.

## Prerequisites

- Python 3.11+.
- A Dune API key in the repo `.env` as `DUNE_API_KEY=...` (the Dune free plan is
  sufficient — the full run costs ~341 of the 2,500 free credits).

## Install

    python3 -m pip install -r bootstrap/requirements.txt

## Run the tests

    python3 -m pytest bootstrap/tests -q

The test suite is fully offline — the Dune client is exercised with a fake
transport.

## Run the ETL

A **pilot run** validates the whole pipeline end-to-end against real Dune on a
tiny sample before the full extraction:

    python3 -m bootstrap.run --pilot      # ~50-100 graduations, minimal credits

The **full run** assembles the ~5,000-token dataset:

    python3 -m bootstrap.run              # the full month-stratified sample

Both are idempotent and resumable: results are keyed on `mint`, every Dune stage
caches its raw output under `bootstrap/data/` (gitignored), and a re-run skips
already-loaded mints and reuses cached stage results — a crash never re-spends
Dune credits.

## Output

A `historical_graduations` table in the project SQLite database (`./storm.db` by
default — configurable). One flat row per graduated token: graduation facts, the
history-native feature set, and the settled `survived` outcome label.
