# pump-prediction — Claude skill for pump.fun graduation signals

A Claude Code skill that picks 3–5 pump.fun graduations every invocation,
audits 24h-old prior picks for realized return, and maintains a self-improving
diary (rolling lessons + auto-built smart-wallet registry).

**Spec:** `docs/superpowers/specs/2026-05-21-pump-prediction-skill-design.md`

## Run

```bash
# normal mode — calls live APIs, writes to predictions/diary/
claude --skill pump-prediction

# rehearsal mode — canned data, output to stdout
PUMP_PREDICTION_REHEARSAL=1 claude --skill pump-prediction
```

## Required env (in repo `.env`)

- `HELIUS_API_KEY` — for Helius RPC
- `DUNE_API_KEY` — for Dune SQL

## Interpret output

Each invocation writes:
- `predictions/diary/decisions/<YYYY-MM-DD-HH-MM>.md` — picks + reasoning
- `predictions/diary/outcomes/<original-decision-id>-outcome.md` — audits of 24h-old picks
- May update `predictions/diary/lessons.md` (rolling synthesis, COMMITTED)

Read `lessons.md` to see what the skill has learned. The frontmatter shows
`buy_hit_rate_last_7d` vs `buy_hit_rate_first_7d` — if not improving after 30+
audits, the approach isn't working and the project should be retired.

## Run the helper tests

```bash
python3 -m pytest predictions/helpers/tests/ -v
```
