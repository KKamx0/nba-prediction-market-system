# Phase 0 — Verification report

Date checked: 2026-11-09. Sources: docs.polymarket.us (fetched directly), the
Polymarket US App Terms & Conditions (effective 2026-10-23), the official
`polymarket-us` Python SDK, live calls to the public gateway, and Washington
State primary/secondary legal sources.

**Result:** every check passed except Washington eligibility (§3). The
Washington State Gambling Commission says that taking part in these markets is
not authorized in the state, and Polymarket US publishes no state list.

**Owner decision (2026-11-09):** build the full system paper-first. Live
trading stays behind the paper → live gate plus a `JURISDICTION_CLEARED=true`
setting that the owner sets only after confirming eligibility in the app.

---

## 1. Platform and SDK — PASS

- Polymarket US only. Retail API docs at https://docs.polymarket.us. The
  international Gamma/CLOB APIs and the `Polymarket/agents` repo are not used.
- Official SDK: `pip install polymarket-us` (PyPI `polymarket-us` 2.4.1,
  released 2026-10-08, MIT, Python >= 3.10, depends on `httpx` + `PyNaCl`).

> "The Polymarket US API is split into two parts: an authenticated API for
> trading and a public API for reading market data."
> Authenticated: `https://api.polymarket.us`. Public: `https://gateway.polymarket.us`.
> "Use the public API to browse what's available on Polymarket US. No API key
> needed. … If you're building something that displays market information -
> prices, odds, order books - this is all you need."
> — https://docs.polymarket.us/api-reference/introduction

Public endpoints used (verified live, no auth): `GET /v2/leagues/nba/events`,
`GET /v1/events/slug/{slug}`, `GET /v1/markets/{slug}/book`,
`GET /v1/markets/{slug}/bbo`, `GET /v1/price-history`.

## 2. API details — PASS

### Authentication (Ed25519)

> "each request needs three headers: `X-PM-Access-Key` (Your Key ID),
> `X-PM-Timestamp` (Current time in milliseconds), `X-PM-Signature` (A signature
> generated from your secret key). The signature is built by combining the
> timestamp, HTTP method, and path, then signing it with your secret key.
> Timestamps must be within **30 seconds** of server time."
> — https://docs.polymarket.us/api-reference/authentication

Signed message is `f"{timestamp}{method}{path}"` (body not signed). Keys are
created at polymarket.us/developer after KYC in the iOS app; "Your secret key is
shown **only once**." The docs describe **no key scopes** (no read-only vs
trading keys), so least privilege has to come from a dedicated, minimally funded
account.

### REST rate limits

> "Exceeding a rate limit returns `429 Too Many Requests`. Limits are enforced
> per source IP and Cloudflare location, so requests using different API keys
> from the same IP share counters at that location."
> "The Retail API has a general limit of **25 requests/s**, shared across
> endpoints except quote creation and deletion."
> "During periods of increased latency, Polymarket US applies a **5-second
> stopgap** to inbound orders … These rejects carry the message **`Order
> Processing Max Delay Exceeded`** … not an actual rate limit."
> — https://docs.polymarket.us/api-reference/rate-limits

Observed: this cloud container got 429s at ~6 req/s, most likely because the
egress IP is shared. The bot should run from its own IP, with a client-side
token bucket well under 25/s.

### WebSocket

> Endpoints: `wss://api.polymarket.us/v1/ws/private` (orders, positions,
> balances) and `wss://api.polymarket.us/v1/ws/markets` (market data, order
> book, trades). **Both require API key auth** in the handshake; "The signature
> is constructed from: `timestamp + "GET" + path`".
> "You can subscribe to a maximum of 100 markets per subscription."
> "The server sends periodic heartbeat messages … Reconnect if heartbeats stop."
> — https://docs.polymarket.us/api-reference/websocket/overview,
> https://docs.polymarket.us/api-reference/websocket/markets

No connection-count limit is documented. Implication: without a KYC'd account,
live books have to come from REST polling of the public gateway.

### Fees (taker tiers changed 2026-11-06)

> "Standard taker fees … `Fee = Θ × C × p × (1 - p)`"
> | Tier 1 | Below \$10,000 | 0.095 | … | Tier 4 | \$250,000 or more | 0.0695 |
> "Days 1–7: taker fee coefficient `0.0695`, regardless of volume."
> Maker rebate Tier 1 (below \$10M maker volume): "No maker rebate".
> "All fees and rebates are rounded to the nearest \$0.01 using banker's rounding."
> — https://docs.polymarket.us/fees

Live NBA markets report `"feeCoefficient": 0.095`. At p = 0.50 a taker pays
0.095 × 0.25 = **2.4¢ per contract, about 4.75% of stake**. Resting (maker) fills
pay no fee. **Design consequence:** edge must be net of this fee. Post-only
limit orders (`participateDontInitiate`) are much cheaper than crossing the
spread.

### Automated-order flag

> `manualOrderIndicator`: "Indicates whether the order was placed manually or
> automatically", enum `MANUAL_ORDER_INDICATOR_MANUAL` |
> `MANUAL_ORDER_INDICATOR_AUTOMATIC` — "Indicates whether the order was placed
> manually by a user or automatically by a trading system"
> — https://docs.polymarket.us/api-reference/orders/create-order

The schema only requires `marketSlug`, and the docs don't say the flag is
mandatory. We will **always** send `MANUAL_ORDER_INDICATOR_AUTOMATIC`. ToS:

> "You agree not to: … (c) use bots or automation except through authorized APIs"
> — Polymarket App Terms & Conditions §(prohibited conduct), effective 2026-10-23

### Other order facts

Order types are `ORDER_TYPE_LIMIT` and `ORDER_TYPE_MARKET`. TIF can be GTC, GTD,
IOC, or FOK. Intents are `ORDER_INTENT_BUY_LONG`/`SELL_LONG`/`BUY_SHORT`/`SELL_SHORT`.
Price is a decimal string (`{"value": "0.55", "currency": "USD"}`). NBA markets:
`orderPriceMinTickSize` 0.01, `minimumTradeQty` 0.01. Weekly maintenance is
Thursday 6–8am ET, and "All open orders are canceled before maintenance begins."
**No sandbox exists.**

## 3. Washington eligibility — FAIL / NOT CONFIRMED

**Primary source, Washington State Gambling Commission**
(https://wsgc.wa.gov/node/14038, notice dated 2025-12-09):

> "Offering events-based contracts or participating in these markets is not
> authorized in Washington State."
> "While prediction markets are an unauthorized activity in Washington State,
> we acknowledge that the future of prediction markets, including those for
> sports, political events, etc., remains a subject of ongoing litigation…"

**Polymarket US terms** do not list states. They defer to the Rulebook and the
Participant Agreement:

> "You must be at least 18 years old … and legally permitted to access a DCM."
> "You may not use the App if you are … located in a restricted or embargoed
> jurisdiction, or in any other jurisdiction in which access is prohibited under
> the Rulebooks or your Participant Agreement."
> "The Company does not represent that the App is appropriate or available for
> use in any specific location."

**Litigation:**
- *State of Washington v. KalshiEX* (King County Superior Court). Kalshi was
  enjoined from sports, election and other contracts (amended PI 2026-08-12,
  geofence required).
- No Washington action against **Polymarket US** turned up. Secondary sources
  conflict: The Lines (Jul 2026) and polymarkettrader.com (Sep 2026, citing
  Covers) say Polymarket US is live in WA, but SharpFootballAnalysis (Nov 2026)
  says "sports are restricted in several [states], including Nevada, Washington,
  Michigan, and Utah". I could not verify that from a primary source.

**Conclusion:** I can't confirm that a WA resident can currently trade NBA
contracts on Polymarket US. Even if the app accepts WA KYC, the state regulator
calls participation unauthorized. That is a legal risk to you personally, not
only to the platform. Settling it takes (a) the in-app eligibility check with a
WA address and (b) a lawyer's view of your personal exposure. I can't do either.

## 4. NBA markets and liquidity — PASS

Each NBA game event lists ~686 markets. For LAL @ ATL, 2026-11-09:

| Type (`sportsMarketTypeV2`) | Count | Examples |
| - | - | - |
| MONEYLINE | 1 | `aec-nba-lal-atl-2026-11-09` |
| SPREAD | 117 | full game, halves, quarters (`asc-…-pos-1pt5`) |
| TOTAL | 188 | full game, halves, quarters, team totals (`tsc-…-236pt5`) |
| PROP | 380 | player points/rebounds/threes/PRA, quarter winners |

Liquidity snapshot at ~21:30 UTC on game day (public `/book`, 25 games):

| Market | Spread | $ within 2¢ of best ask | Notional traded |
| - | - | - | - |
| Moneyline, same day (8 games) | 1¢ | $111k – $577k | $10k – $439k |
| Moneyline, +1 day | 1–2¢ | $0.6k – $11k | $0.1k – $8k |
| Moneyline, +2–3 days | 1–5¢ | $0.1k – $3k | ~$0 – $2k |
| Main full-game spread / total | 1¢ | $26k – $65k | $2k – $22k |
| Quarter/half lines, most props | 1–6¢ | $1 – $2k | ~$0 |

Same-day moneylines are deep enough for a small bankroll. Liquidity builds
toward tip-off, so execution belongs on game day, after injury reports.

---

## Design decisions that came out of Phase 0

1. **Washington legality.** See §3. Handled by the jurisdiction flag above.
2. **ToS restriction on ML training.** The terms prohibit using "the App or any
   data, analytics or documentation to train, fine tune or otherwise improve any
   artificial intelligence (AI) or machine learning (ML) model, tool, agent or
   platform", and also "scraping, bulk downloads, and commercialization of
   derived works". The App includes "related websites, portals, and APIs".
   Adopted: train only on NBA stats plus de-vigged **sportsbook** lines. Use
   Polymarket prices only at decision time (edge, execution) and in our own
   trade log (CLV). Never publish raw Polymarket data, only aggregates.
3. **Historical prices for a multi-season walk-forward.** Polymarket US NBA
   markets are recent and off-limits for training anyway (item 2), so the
   backtest needs historical sportsbook closing lines. That usually means a paid
   tier (e.g. The Odds API historical), which needs the owner's approval
   before use.
4. **Market WebSocket needs an account.** Without a KYC'd account, paper mode
   polls public REST books, which is fine for ~15 games/night at <1 req/s.
5. **Fees.** About 4.75% of stake at p = 0.5 for takers. The 4% minimum edge is
   net of fees, and maker orders are preferred.
6. **NBA stats hosts.** `stats.nba.com` and `cdn.nba.com` return 403 from this
   cloud container, probably datacenter-IP blocking. Ingestion should run from a
   home or residential IP.
