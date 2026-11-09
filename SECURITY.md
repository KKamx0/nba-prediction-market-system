# Security

edgebot can place real orders with real money, so it is built on the
assumption that anything outside the process can be hostile: news text, API
responses, and its own configuration.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private vulnerability reporting
on this repository (Security → Report a vulnerability).

## Secrets

- Every secret lives in a local `.env`, loaded with `python-dotenv`. `.env` is
  gitignored. Only `.env.example`, which holds placeholder names, is committed.
- Secrets are never logged. Logging goes through a redaction filter. Config
  objects hold keys as `SecretStr`-style values whose `repr` is masked.
- Secret scanning runs in two places: gitleaks in a pre-commit hook, and
  gitleaks over the **full git history** in CI, from a checksum-verified binary.
- If a secret is ever committed, the response is: revoke the key first
  (polymarket.us/developer for exchange keys), then rewrite history, then
  confirm with a fresh gitleaks scan. Rewriting history alone is not enough;
  assume the key was copied the moment it was pushed.

## Exchange account

- A dedicated Polymarket US account, funded only with the trading bankroll.
- Polymarket US API keys have no permission scopes (no read-only key), so
  least privilege comes from the account itself: a small balance and no other
  holdings. The live bankroll is also capped in config.
- Every live order is a limit order and carries
  `manualOrderIndicator = MANUAL_ORDER_INDICATOR_AUTOMATIC`.

## LLM prompt-injection defenses

News and injury text is treated as untrusted input, since anyone can publish
an article or a post.

1. **The LLM has no authority.** It only fills a fixed schema:
   `{player, team, status, minutes_impact_estimate, confidence, source_url, published_at}`.
   It has no tools and cannot place orders, set sizes, or change config.
2. **The input is delimited and declared to be data.** The system prompt says
   any instructions inside the article are content to ignore.
3. **The output is validated strictly.** The JSON is parsed against a schema
   with closed enums for `status`, bounded numeric ranges, and no extra keys.
   The player and team must match the known roster for that game. Anything
   malformed is rejected and logged, never repaired by guessing.
4. **The blast radius is bounded.** A valid but wrong extraction can only
   change a minutes projection. That change still passes through the
   statistical model, the 15-point disagreement cap, and every risk limit
   below, so a successful injection can at worst cause a pass or a small,
   capped bet.

## Risk controls (enforced in code)

| Control | Default |
| - | - |
| Max per position | 2% of bankroll |
| Max open exposure per night | 10% of bankroll |
| Correlated positions per game | 1 |
| Daily loss limit | 5% → halt until next day |
| Drawdown from peak | 20% → full shutdown, manual restart only |
| Model vs. market disagreement | > 15 points → no bet, flag for review |
| Averaging down / chasing | blocked |
| Kill switch | CLI command or flag file: cancel open orders, stop loop |

`LiveExecutor` refuses to run unless the paper-trading record passes the
live gate (see README), and also requires `LIVE_TRADING=true`,
`JURISDICTION_CLEARED=true`, and `--confirm-live`.

## External data

Every external payload (exchange, odds, NBA stats, RSS) is validated before
use. Prices must be in [0, 1], timestamps must be timezone-aware, and IDs must
resolve. Invalid records are rejected and logged, not coerced. Dependencies
are pinned to exact versions and kept current by Dependabot. CI actions are
pinned to commit SHAs.

## What is never committed

API keys, wallet or account details, `.env`, the SQLite database, trade logs,
and raw cached data, including Polymarket market data (its terms prohibit
redistribution). Only aggregated, account-free summaries go into
`docs/results.md`.
