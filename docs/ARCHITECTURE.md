# Edge Intelligence: Stage 2 architecture

Nov 7, 2026. This covers Stage 2 of the build plan: one shared ledger for NFL, NBA and CFB, the database layout, the app layout, and how data gets from the existing models to a member's phone. It reuses the existing models as they are. Nothing in Stage 2 changes a probability, weight or threshold.

## 1. The system today and the target

**Today** there are four separate parts, built in different places:

| Part | Where it runs | Where its records live |
|---|---|---|
| NBA first-basket model (V7, `fb_engine.py`) | Your Windows PC, Task Scheduler | `output/results_ledger.csv`, `history/predictions_*.csv`, registry SQLite |
| NFL TD and props model (`nfl_builder.py` v39) | Scheduled Claude sessions | Paper-bet JSON, weekly grade JSON, artifact database |
| CFB model and Edge Scanner | Scheduled Claude sessions, another project | Its own SQLite and board data |
| NBA First Basket API (this repo) | Render (free web service + free Postgres) | Postgres: first baskets, starters, prediction sets |

**Target.** The models keep running where they run now. Each one sends its records to one ledger in this API. The app reads only from the API.

```
 NBA V7 (PC)      NFL builder      CFB pipeline
     |                 |                 |
 from_nba_v7.py   from_nfl_paper.py  from_csv.py      <- importers, read-only on the source
     \                 |                 /
      +----- POST /ledger/entries, /prices, /results  (write key)
                       |
          Edge Intelligence API (this repo, FastAPI)
          Postgres: Render now, Supabase after the Phase 1 migration
                       |
      GET /ledger/board, /performance, /entries, /markets  (read key)
                       |
          Phone app (PWA) on Cloudflare Pages
          Home · NFL · NBA · CFB · Performance · Membership
```

Why one API and not three: the three sports then share one definition of "pregame", one staking rule, one CLV formula and one append-only guarantee. The Performance Center is then a query, not a reconciliation job.

## 2. The ledger rules

These are enforced in code and covered by tests, not just written down.

1. **Append-only.** Database triggers refuse `UPDATE` and `DELETE` on the three ledger tables, on both SQLite and Postgres. A losing pick cannot be edited away. A result correction is a new row; the latest counts and the old one stays, with a note saying what it corrects.
2. **Re-sending is safe.** Every row has an `entry_key` chosen by the importer. Sending the same row again is a duplicate (no change). Sending the same key with a different probability, price, stake or time is refused with 409, and nothing in that batch is written.
3. **Pregame only.** `pregame_verified` is computed by the server: 1 when `predicted_at` is before `event_start_utc`, 0 when not, empty when the start is unknown. Only 1 counts toward performance. The others are listed as excluded, with the reason. A time without a zone is read as UTC and flagged, the same rule NBA V7 uses. A prediction dated in the future is refused. History imported after the game is flagged `backfilled_after_start`, so live-recorded and backfilled records can be told apart.
4. **Three kinds, never mixed.** `prediction` (the model's view), `paper_bet` (a recommended bet, not real money) and `real_bet` (an actual wager). The performance report always gives each kind its own block.
5. **Explicit staking.** A paper bet is staked only when its decision is exactly `BET`, it has a price and a positive stake. A `BET` without a price is refused. A real bet must have the odds taken, the book and a stake (this closes the "parlays logged with no odds" gap from the audit).
6. **Markets carry their settlement rule.** `nba_first_fg` and `nba_first_point` are separate markets. A first-basket price from FanDuel is refused on `nba_first_fg`, and one from DraftKings or BetMGM is refused on `nba_first_point`. Other books are accepted but flagged `settlement_rule_unconfirmed`. These book rules come from the Stage 1 audit and should be checked against each book's current house rules.
7. **Source exclusions are honoured.** A row the source excluded (NBA V7's Nov 5 postgame games) is imported with `excluded_by_v7` so it stays visible but never counts.
8. **Nothing invented.** Every row needs a `source`. Importers leave a missing value empty and count what they skipped.

## 3. Database layout

All tables live in one Postgres database (SQLite for local runs and tests).

### Existing tables (unchanged)

| Table | Holds |
|---|---|
| `first_baskets` | One row per NBA game: first made FG scorer, tip winner, flags |
| `game_starters` | The 10 starters per NBA game |
| `prediction_sets`, `prediction_items` | NBA champion and baseline predictions posted to `/predictions` |

### New: the shared ledger

**`ledger_entries`**: one row per prediction, paper bet or real bet.

| Column | Meaning |
|---|---|
| `id`, `entry_key` | Internal id; caller's unique key, e.g. `nba:2026-11-06:ORL@PHX:nba_first_fg:1626164:e188ce00fd` |
| `kind` | `prediction`, `paper_bet` or `real_bet` |
| `sport`, `market` | `nfl` / `nba` / `cfb`, and a market from the list below |
| `event_id`, `event_date`, `event_label`, `home`, `away`, `event_start_utc` | The game |
| `selection`, `selection_id`, `side`, `line` | The player or team, the side (Over, Yes, team) and the line |
| `model_prob`, `market_prob` | Model probability; no-vig market probability when the model computes one |
| `model_version` | The model version or champion hash |
| `decision` | `BET` or `PASS` |
| `odds_american`, `book`, `price_at` | The price shown or taken, its book and time |
| `stake_units` | Units staked (0 for predictions and PASS) |
| `predicted_at`, `recorded_at`, `pregame_verified` | When the model made it, when the ledger received it, and the pregame check |
| `source`, `source_ref`, `data_flags` | Where it came from, and any flags |
| `linked_entry_id` | A bet's link to the prediction it came from |

**`ledger_prices`**: price snapshots for an entry: `price_type` (`open`, `bet`, `current`, `close`), `odds_american`, `line`, `book`, `observed_at`, `source`, `data_flags`. One snapshot per entry, type, book and time.

**`ledger_results`**: settlements: `outcome` (`win`, `loss`, `push`, `void`), `actual_value` (e.g. the yards gained), `settled_at`, `source`, `note`.

### Markets

| Sport | Markets |
|---|---|
| NBA | `nba_first_fg`, `nba_first_point`, `nba_team_first_score`, `nba_tip`, `nba_player_prop`, `nba_three_pointers` |
| NFL | `nfl_anytime_td`, `nfl_first_td`, `nfl_2plus_td`, `nfl_3plus_td`, `nfl_player_prop`, `nfl_spread`, `nfl_total`, `nfl_moneyline` |
| CFB | `cfb_spread`, `cfb_total`, `cfb_moneyline` |

`GET /ledger/markets` returns this list and the NBA book rules.

### Later stages (designed, not built)

| Table | Stage | Holds |
|---|---|---|
| `members` | 6 | Supabase Auth user id, tier (`free`, `pro`, `elite`), created, age confirmed, deleted_at |
| `subscriptions` | 6 | Stripe customer and subscription ids and status only; card data never touches us |
| `member_bets` | 6 | A member's own tracked bets, same shape as `real_bet` rows but private to them |
| `audit_log` | 6 | Who changed a tier or ran an import, and when |

Supabase Auth handles sign-in. Paid content is filtered in the API by tier, never only hidden in the page.

## 4. API

| Endpoint | Key | Purpose |
|---|---|---|
| `POST /ledger/entries` | write | Up to 500 entries per call |
| `POST /ledger/prices` | write | Up to 500 price snapshots |
| `POST /ledger/results` | write | Up to 500 settlements or corrections |
| `GET /ledger/board?date=&sport=` | read | One day's predictions for the Edge Board, live rows first, BET before PASS, then by EV |
| `GET /ledger/performance?sport=&kind=&market=&model_version=&since=&until=&group_by=` | read | Per kind: counts, exclusions, hit rate, Brier, log loss, calibration bins, units, ROI, price CLV, beat-close rate, line CLV; optional breakdown by sport, market, model version or odds band |
| `GET /ledger/entries` | read | The raw rows, with result history and prices |
| `GET /ledger/markets` | read | Market list and NBA book rules |

Existing NBA endpoints (`/board`, `/predictions`, `/first-basket/*`, `/nba/feed`) are unchanged.

**How figures are computed.** Win % excludes pushes and voids. Units on a win are `stake × (decimal odds − 1)`. ROI is units over units risked. Price CLV is the implied probability of the closing price minus that of the price taken (positive is good), matching NBA V7's `imp(close) − imp(open)`. Line CLV is in points: for a spread, line taken minus closing line; for an Over, close minus taken; for an Under, taken minus close (the same as `nfl_builder.py`). The closing price used is the same book's when there is one. Brier and log loss treat each selection as a yes/no event. Odds bands: −200 or shorter, −199 to −101, −100 to +199, +200 to +499, +500 to +999, +1000 or longer.

## 5. Getting each sport's records in

| Sport | Importer | Reads | Status |
|---|---|---|---|
| NBA | `scripts/ledger/from_nba_v7.py <V7 folder>` | `output/results_ledger.csv` and `output/ledger_exclusions.csv` | Tested on synthetic rows in V7's exact columns. Not yet run on your real ledger |
| NFL | `scripts/ledger/from_nfl_paper.py <paper bet JSON> --kickoffs k.json` | `NFL_Week*_paper_bets_*.json` and graded copies | Tested on synthetic rows in the builder's exact fields. The builder's records have no kickoff time, so a kickoff map is needed for them to count |
| CFB | `scripts/ledger/from_csv.py <export.csv>` | A CSV in the ledger's own columns | Tested. The CFB project needs a small export step that writes this CSV |

Each importer has `--dry-run` (convert and summarise only) and `--out file.json`. To send, set `EDGE_LEDGER_URL` and `NBA_API_KEY` in the environment. Keys are never written to files.

What the NBA importer does with V7's rows: each row becomes a `prediction`. A row V7 staked (decision `BET` with a price and `bet = 1`) also becomes a linked `paper_bet` of 1 unit. A FanDuel price on first FG (the Stage 1 routing bug) is dropped from that row and counted, and the prediction is kept. The close time is not recorded by V7, so the tip time is used and the source says so.

## 6. App layout (for Stage 3)

One installable PWA, dark mode, built for a 390 px wide phone first, with a bottom tab bar on phones and a side bar on desktop. It reads only the read endpoints. Every screen shows the data time and the model version, and shows "unavailable" when a source is down.

| Tab | What it shows | Fed by |
|---|---|---|
| **Home: Today's Edge Board** | Date; today's games across sports; the top live rows (BET first); model vs market probability, fair odds, EV, BET/PASS; lineup or injury flags; last update and model version. Rows that aren't "live" show their status ("start time not verified", "started") instead of looking current | `/ledger/board` |
| **NFL** | Anytime and first TD board, props, spreads and totals; red-zone and injury notes from the builder's reasons; five-book prices only where verified | `/ledger/board?sport=nfl` |
| **NBA** | First FG and first point side by side as separate markets; team-first and tip; starter status; props and threes when verified | `/ledger/board?sport=nba`, existing `/board` |
| **College Football** | Model spread vs open, current and close; moneyline and total; edge and confidence | `/ledger/board?sport=cfb`, prices |
| **Performance** | Per sport, market, model version and odds band: record, units, ROI, Brier, log loss, calibration chart, CLV; predictions, paper bets and real bets on separate tabs; every losing pick visible; "paper results" label everywhere until there are real results | `/ledger/performance`, `/ledger/entries` |
| **Membership** | Free, Pro, Elite; sign in, manage, cancel; age confirmation and responsible-gambling links | Supabase Auth, Stripe (Stage 6) |

Free vs paid (proposed): Free sees the Performance Center in full (it is the trust signal) and a limited board; Pro and Elite see full probabilities, fair odds and EV. The API enforces this in Stage 6.

## 7. What is open

| Item | Owner | Blocks |
|---|---|---|
| Migrate the Render Postgres to Supabase before it expires Nov 30 (PR #8, Phase 1) | Daniel: secrets and the go-ahead | Durable ledger |
| Rotate the TheRundown key | Daniel | Security |
| Run `from_nba_v7.py --dry-run` on the real V7 folder | Next step here, needs your PC connected | NBA history in the ledger |
| Kickoff times in the NFL paper-bet records, or a kickoff map per week | NFL builder | NFL rows counting |
| A CSV export in the CFB project; reconcile the 167-162-5 vs 115-103-4 record | CFB project | CFB rows |
| NBA routing fix in the engine itself (`nba_routing_fix_prompt.md`) | NBA project | Correct FG prices at the source; the ledger already refuses the wrong ones |
| Champion `engine.pkl` decision | Daniel (NBA V7 thread) | NBA model version label |

**Known limits.** `/ledger/performance` loads the filtered rows into memory to compute figures. That is fine to tens of thousands of rows; past that it should move to SQL aggregates. The NBA first-scorer book rules are from the audit and need checking against current house rules. Nothing in the ledger shows the models beat the market; the Stage 1 figures still apply.
