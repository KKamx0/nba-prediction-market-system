# edgebot

[![CI](https://github.com/KKamx0/nba-prediction-market-system/actions/workflows/ci.yml/badge.svg)](https://github.com/KKamx0/nba-prediction-market-system/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/KKamx0/nba-prediction-market-system/python-coverage-comment-action-data/badge.svg)](https://github.com/KKamx0/nba-prediction-market-system/tree/python-coverage-comment-action-data)

An autonomous research agent for NBA moneyline markets on
[Polymarket US](https://docs.polymarket.us), the CFTC-regulated US exchange.
A statistical model sets every probability. The market price is treated as a
strong prior. The default action is **no bet**.

> **Status:** early development. Done so far: repo setup,
> [Phase 0 platform verification](docs/phase-0-verification.md), data
> ingestion ([data sources](docs/data.md)), player ratings and minutes
> projection, and the game model with a walk-forward backtest and leakage
> test. The trading layer is next. Nothing here has traded real money.

## Why the LLM is kept away from the numbers

Recent benchmarks of LLM betting agents point the same way:

- **WC2026-Agents** ([arXiv:2607.17765](https://arxiv.org/abs/2607.17765)) ran
  four frontier models as forecasting agents on all 104 matches of the 2026
  World Cup, with the pre-match betting market as a fifth competitor. None of
  the agents beat the market's Brier score. Fading the market was unprofitable
  for all four, and a flat stake on the market favorite out-earned every agent.
- **KellyBench** ([arXiv:2604.27865](https://arxiv.org/abs/2604.27865)) simulated
  a full Premier League season with historical data and public odds. Every
  frontier model lost money on average, the best returned -8%, and several
  were ruined.

So edgebot uses the LLM for one narrow job: turning unstructured news into
structured availability fields. The LLM **never** outputs a probability, a
pick, or a bet size.

| Component | Produces | Never produces |
| - | - | - |
| Statistical model | calibrated win probability | — |
| LLM news extractor | `{player, team, status, minutes_impact_estimate, confidence, source_url, published_at}` | probabilities, picks, sizes |
| Market | executable price, strong prior | — |
| Risk engine | allow / block, size cap | — |

## Architecture

```mermaid
flowchart LR
    subgraph Ingest["Ingestion (timestamped, as-of queryable)"]
        PM[Polymarket US<br/>public order books]
        SB[Sportsbook odds<br/>de-vigged]
        NBA[nba_api<br/>box scores, lineups]
        INJ[Official injury report<br/>+ news RSS]
    end

    INJ --> LLM[LLM extractor<br/>strict JSON, schema-validated]
    LLM --> MIN

    NBA --> RAT[Player ratings<br/>Bayesian RAPM-style]
    NBA --> MIN[Minutes projection]
    RAT --> TS[Team strength tonight]
    MIN --> TS
    TS --> GM[Game model<br/>logistic / LightGBM<br/>+ calibration]
    SB --> GM

    GM --> EDGE[edge.py<br/>prob - executable price - fees]
    PM --> EDGE
    EDGE --> RISK[risk.py<br/>limits, disagreement cap,<br/>kill switch]
    RISK --> SIZE[sizing.py<br/>0.25 Kelly, shrunk prob]
    SIZE --> EXE{Executor}
    EXE --> PAPER[PaperExecutor<br/>walks the live book]
    EXE -.gated.-> LIVE[LiveExecutor]

    PAPER --> DB[(SQLite log)]
    LIVE --> DB
    DB --> REP[report.py<br/>CLV, Brier, log loss]
```

## Setup

Requires Python 3.12+.

```bash
git clone https://github.com/KKamx0/nba-prediction-market-system.git
cd nba-prediction-market-system
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
cp .env.example .env   # fill in locally; never commit
pytest
```

Load data (see [docs/data.md](docs/data.md) for every source and its
availability rule):

```bash
edgebot init-db
edgebot ingest nba --season 2023-24 --season 2024-25 --season 2025-26
edgebot ingest schedule --season 2026-27
edgebot ingest injuries
edgebot snapshot markets
```

NBA stats endpoints block many datacenter IPs, so run ingestion from a home
connection.

## How team strength is built

1. **Player ratings** (`model/ratings.py`): offensive and defensive impact
   per 100 possessions from a ridge regression on game-level efficiency,
   weighted by each player's share of minutes. The ridge pulls toward last
   season's rating × 0.75, so the prior fades as current-season minutes
   accumulate. On synthetic leagues, one season recovers individual player
   value at ~0.5 correlation and team strength at > 0.85. Individual ratings
   are noisy; the team aggregate is what the model uses.
2. **Minutes** (`model/minutes.py`): a blend of last game, rolling averages
   and season average, trimmed on back-to-backs. Injury status sets a
   probability of playing, and freed minutes go to teammates in proportion to
   their role, capped at 42.
3. **Tonight's strength** (`model/strength.py`): Σ (minutes / 48) × net
   rating, computed for the expected lineup, the "every questionable player
   plays" and "every questionable player sits" cases, and full health.

## Paper mode

There is no Polymarket US sandbox, so paper mode is a local simulator. It pulls
the real order book from the public gateway and "fills" each order by walking
the book at our size, including taker fees. No account or API key is needed.
Paper mode is the default. Live trading is behind the gate described below.

## Risk controls

These are enforced in code (`risk.py`), not left to configuration:

- Minimum edge of 4% **after** fees and slippage, plus a minimum liquidity.
- Disagreement cap: if the model and the market differ by more than 15 points,
  no bet. The game is flagged for review, since this usually means stale data
  or a missed injury.
- Sizing: 0.25 Kelly on a probability shrunk toward the market.
- At most 2% of bankroll per position and 10% total open exposure per night.
- At most one correlated position per game. No averaging down, no chasing.
- Daily loss limit of 5%, which halts trading until the next day.
- A 20% drawdown from peak triggers full shutdown; only a manual restart resumes.
- Kill switch (CLI command or flag file) that cancels open orders and stops the loop.

**Paper → live gate.** `LiveExecutor` refuses to start unless the database
shows at least 300 paper bets over at least 6 weeks, positive average CLV with
a confidence interval excluding zero, model log loss better than the
market-only baseline, positive ROI after fees, and max drawdown under 15%.
It also requires `LIVE_TRADING=true`, `JURISDICTION_CLEARED=true`, and the
`--confirm-live` flag.

## Backtest

```bash
edgebot backtest --validate 2023-24 --validate 2024-25 --test 2025-26
edgebot train   # fit the selected model on all data for live use
```

- **Strict walk-forward by date.** Models are refit every 30 days on games
  dated before the block they predict. Features are built as of 7pm ET on
  game day, and games that tip earlier are skipped.
- **Models:** the market-only baseline (calibrated sportsbook probability),
  logistic regression, and LightGBM with the market as an offset, so the
  trees can only learn corrections to it. Each is Platt- or
  isotonic-calibrated on the newest 20% of its training window.
- **Selection by log loss on validation seasons only.** Test seasons are
  reported, never tuned on. Brier score and ECE are reported too; accuracy
  is shown for context only.
- **Leakage test (CI).** For games across a season, every row known after
  the decision time is corrupted (results, box scores, late injury reports,
  late odds), and the feature vector must not change. A deliberately leaky
  feature proves the check has teeth, and a label-shuffle test confirms
  skill collapses without real outcomes.

**Results on real data: not yet run.** It needs NBA box scores and free
archived closing lines loaded from a home connection (see
[docs/data.md](docs/data.md)). The report and calibration plot land in
[`docs/backtest/`](docs/backtest/).

What synthetic tests already show: when the market is about as informed as
a real sportsbook, the feature models shave little or nothing off its log
loss, and the selector sometimes picks the market itself. The report then
says plainly that no feature model beat the baseline. Expect the same on
real data.

## Results so far

_No paper trading yet._ This section is generated from `report.py` into
[`docs/results.md`](docs/results.md). Closing line value is the primary
metric; ROI over a small sample is mostly noise.

## Disclaimer

This is a personal research project, not financial advice. The legality of
prediction markets varies by state and is being litigated. See
[Phase 0](docs/phase-0-verification.md) for the situation in Washington. Check
the rules where you live before trading.
