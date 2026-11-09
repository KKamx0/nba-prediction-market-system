# Data sources

Every source is free. Everything lands in the SQLite database
(`data/edgebot.db`, gitignored) with a `known_at` timestamp: the earliest UTC
instant edgebot could have known that row. Features are only ever computed
from rows with `known_at <= decision time`.

| Source | What | `known_at` | Command |
| - | - | - | - |
| nba_api (stats.nba.com) | final scores, player minutes, plus-minus | 08:00 UTC the day after the game | `edgebot ingest nba --season 2025-26` |
| nba_api schedule | upcoming games, tip times | earlier of fetch time and 24h before tip | `edgebot ingest schedule --season 2026-27` |
| Official NBA injury report | status per player (Out … Available) | report time (ET, from file name) + 15 min | `edgebot ingest injuries` |
| Polymarket US public gateway | moneyline order books | fetch time | `edgebot snapshot markets` |
| The Odds API (free tier) | live US sportsbook moneylines | fetch time | `edgebot snapshot odds` |
| Free odds archives (CSV) | historical closing lines | game date 7pm ET (assumed decision time) | `edgebot ingest odds-history …` |

## Where to run ingestion

`stats.nba.com`, `cdn.nba.com` and `official.nba.com` block many cloud and
datacenter IPs. Run `ingest nba`, `ingest schedule` and `ingest injuries`
from a home connection. Polymarket's public gateway and The Odds API work
from anywhere.

## Historical closing lines (free)

The backtest needs sportsbook closing lines for past seasons, to build the
market-only baseline and the market-prior feature. No single free source
covers every season. A workable combination:

1. **Kaggle "NBA Odds Data"** (2008 to 2023, derived from the old
   sportsbook-archive files). This needs a free Kaggle account.
2. **Kaggle "NBA 2025-26 Closing Lines and ATS Results"** for last season.
3. For any season with no free archive, the backtest simply skips it. This
   is reported, never filled in with made-up lines.

Look at the CSV header, then map its columns:

```bash
# one row per game ("wide")
edgebot ingest odds-history nba_odds.csv --layout wide --source kaggle-nba-odds \
  --date-col date --home-col home_team --away-col away_team \
  --home-odds-col home_moneyline --away-odds-col away_moneyline

# classic two-rows-per-game archive layout (Date as MMDD, VH, Team, ML)
edgebot ingest odds-history nba_odds_2019-20.csv --layout sbr \
  --source sbr --season-start-year 2019
```

The import rejects rows with unknown teams or invalid odds, prints the
reasons, and aborts if more than 5% of rows fail, which usually means a
wrong column mapping.

## Polymarket data and the terms of service

The Polymarket US terms prohibit using their data "to train, fine tune or
otherwise improve any artificial intelligence (AI) or machine learning (ML)
model", and prohibit redistribution. So:

- Models train only on NBA stats and **sportsbook** lines, never on
  Polymarket prices.
- Polymarket books are used at decision time (edge and execution) and for
  measuring our own closing line value.
- Raw Polymarket data never leaves the local database. Only aggregate,
  account-free results are published in `docs/results.md`.
