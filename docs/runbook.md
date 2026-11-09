# Runbook: paper trading this season

From a fresh clone to a loop that paper-trades every game night. Everything
here is free. Run it on your own machine: NBA stats endpoints block most
cloud IPs.

## 1. Install

```bash
git clone https://github.com/KKamx0/nba-prediction-market-system.git
cd nba-prediction-market-system
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
edgebot init-db
```

## 2. Keys (free)

| Key | Where | Needed for |
| - | - | - |
| `ODDS_API_KEY` | the-odds-api.com, free plan (500 credits/month) | **Required.** The sportsbook line is the model's market prior. The loop uses about 1 credit an hour on game days. |
| `ANTHROPIC_API_KEY` | console.anthropic.com | Optional. LLM news extraction (a few cents a day). Without it the loop uses the official injury report only. |
| `DISCORD_WEBHOOK_URL` | Discord channel → Integrations → Webhooks | Optional. Daily report. |

Put them in `.env`, which is gitignored. Never paste them anywhere else.

## 3. Load history

```bash
# box scores and minutes: one request per season-type, ~1.5s apart, cached under cache/
edgebot ingest nba --season 2021-22 --season 2022-23 --season 2023-24 \
                   --season 2024-25 --season 2025-26 --season 2026-27
edgebot ingest schedule --season 2026-27
```

Historical closing lines come from free archives (see [data.md](data.md)).
Download the CSVs with a free Kaggle account, look at their headers, and map
the columns:

```bash
edgebot ingest odds-history ~/Downloads/nba_odds.csv --layout wide --source kaggle \
  --date-col date --home-col home --away-col away \
  --home-odds-col moneyline_home --away-odds-col moneyline_away
```

The import refuses files where more than 5% of rows fail validation, which
usually means a wrong column mapping.

## 4. Backtest, then train

```bash
edgebot backtest --validate 2023-24 --validate 2024-25 --test 2025-26
edgebot train
```

Read `docs/backtest/README.md` before going further. If it says no feature
model beat the market-only baseline, expect very few bets. That's the
system working as designed, not a bug.

## 5. Check readiness

```bash
edgebot doctor --network
```

Fix every `FAIL` it reports.

## 6. Run the loop

Try a single cycle first:

```bash
edgebot run --once
edgebot report
```

Then keep it running through the season (every 5 minutes is the default):

```bash
# simplest: a terminal multiplexer
tmux new -s edgebot 'source .venv/bin/activate && edgebot run --interval 300'
```

Or as a systemd user service (Linux), `~/.config/systemd/user/edgebot.service`:

```ini
[Unit]
Description=edgebot paper trading loop

[Service]
WorkingDirectory=%h/nba-prediction-market-system
ExecStart=%h/nba-prediction-market-system/.venv/bin/edgebot run --interval 300
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now edgebot
journalctl --user -u edgebot -f
```

On macOS, a `launchd` agent with the same command works. The loop survives
restarts: risk state, positions, and the drawdown shutdown all live in SQLite.

## 7. Daily routine (about 2 minutes)

- `edgebot report`: check CLV first. Over hundreds of bets, ROI is mostly noise.
- Look at games flagged `review` (model vs market > 15 points). Usually it's
  stale data or a missed injury. That's the point of the flag.
- Weekly: `edgebot report --export docs/results.md`, review the diff, and
  commit it. The export holds aggregates only.

## 8. Stopping

```bash
edgebot kill            # cancel open orders, stop the loop
edgebot kill --clear    # allow it to start again
edgebot risk status     # bankroll, drawdown, breakers
edgebot risk reset --confirm   # only after a drawdown shutdown you've investigated
```

## 9. Going live (only if the gate passes)

```bash
edgebot run --live --confirm-live --once   # prints every gate check, then exits
```

The gate is computed from the database: 300+ settled paper bets over 6+
weeks this season, CLV with a CI above zero, log loss better than the market,
positive ROI, and drawdown under 15%. On top of that, only you can set these:

1. Confirm eligibility in the Polymarket US app for where you live, then set
   `JURISDICTION_CLEARED=true`. Washington's regulator considers participation
   unauthorized (see [phase-0-verification.md](phase-0-verification.md)).
2. Use a dedicated Polymarket US account funded with the trading bankroll only.
   Create an API key at polymarket.us/developer and put it in `.env`.
3. Set `LIVE_BANKROLL_CAP` (dollars) and `LIVE_TRADING=true`.
4. Leave `LIVE_ALLOW_SHORT=false` until you've previewed a one-contract
   `ORDER_INTENT_BUY_SHORT` order and confirmed its price is on the NO scale.
   Until then edgebot only takes long-side positions.

Then run `edgebot run --live --confirm-live` in place of the paper command.
`edgebot report --mode live` reports live results separately.
