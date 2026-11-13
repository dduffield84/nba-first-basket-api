# NBA First Basket API

Private FastAPI service for NBA first-basket research: historical first
scorers and starters imported from NBA.com, matchup probabilities from that
history, and a prediction ledger graded against actual results. Version 1.3.0.

The probabilities are a research baseline, not a trained or calibrated model.
Do not treat their output as betting probabilities.

## How data flows

1. `scripts/import_first_baskets.py` uses nba_api to find completed games, the
   first made field goal (from play-by-play), the starters (from the box
   score) and the tip-off winner. It merges them into `data/first_baskets.csv`
   and `data/game_starters.csv`.
2. A GitHub Action (`.github/workflows/import.yml`) runs the importer daily
   and commits the CSVs. The commit makes Render redeploy.
3. On startup the API loads those CSVs into its database, skipping games it
   already has. Historical data therefore survives Render's ephemeral disk
   even on SQLite.

Imported records are stored with `verified: false` and a `data_flags` list
(for example `free_throw_scored_before_first_fg`, `tip_winner_unmatched`,
`found_9_starters`). Check flagged games before trusting them.

"First basket" here means the first made field goal. A free throw scored
earlier is flagged, not counted, because sportsbooks differ on this rule.

## Edge Intelligence shared ledger (NFL, NBA, CFB)

Version 1.3.0 adds one append-only ledger for every sport's predictions,
paper bets and real bets, under `/ledger/*`. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the rules, tables and
importers. In short: rows can't be edited or deleted (database triggers),
only pregame rows count, the three kinds are reported separately, and a
paper bet is staked only on an explicit BET with a price.

Importers (read-only on the source; `--dry-run` sends nothing):

```
python scripts/ledger/from_nba_v7.py <NBA_Complete_V7 folder> --dry-run
python scripts/ledger/from_nfl_paper.py "out/NFL_Week*_paper_bets_*.json" --kickoffs kickoffs.json --dry-run
python scripts/ledger/from_csv.py cfb_export.csv --dry-run
```

To send, set `EDGE_LEDGER_URL` and `NBA_API_KEY` in the environment.

## Run locally

Requires Python 3.10 or newer (tested on 3.13).

Generate a secret key once and keep it private:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Windows PowerShell:

```
$env:NBA_API_KEY = "PASTE_YOUR_GENERATED_SECRET_HERE"
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

macOS / Linux:

```
export NBA_API_KEY="PASTE_YOUR_GENERATED_SECRET_HERE"
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Then open http://127.0.0.1:8000/docs.

### Import history

Run from a home connection if GitHub Actions gets blocked by NBA.com:

```
python scripts/import_first_baskets.py --season 2025-26 --max-games 300
```

Games already in the CSV are skipped, so run it repeatedly to backfill. Commit
the updated `data/*.csv` files to publish them. Add `--post` with `API_URL`
and `NBA_API_KEY` set to also send each game straight to a running API.

### Tests

```
pip install -r requirements.txt -r requirements-dev.txt
pytest
# Against Postgres too:
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/db pytest
```

## Environment variables

| Name          | Required | Default            | Purpose                              |
|---------------|----------|--------------------|--------------------------------------|
| `NBA_API_KEY` | yes      | (none)             | Secret sent in the `X-API-Key` header |
| `DATABASE_URL`| no       | (none)             | Postgres URL; overrides `DB_PATH`    |
| `DB_PATH`     | no       | `data/nba_api.db`  | SQLite database location             |
| `NBA_READ_KEY`| no       | (none)             | Read-only key for apps: `/board`, `/predictions/grades`, `/predictions/compare` |

Without `NBA_API_KEY`, protected endpoints return 503.

## Endpoints

| Method | Path                          | Auth | Purpose                               |
|--------|-------------------------------|------|---------------------------------------|
| GET    | `/`                           | no   | Service name and version              |
| GET    | `/health`                     | no   | Database status                       |
| GET    | `/first-basket/records`       | yes  | List records (`limit`, `team`, `season`) |
| POST   | `/first-basket/records`       | yes  | Save or update a record (optionally with `starters`) |
| GET    | `/first-basket/stats`         | yes  | How much history is loaded            |
| POST   | `/first-basket/probabilities` | yes  | Baseline from counts you supply       |
| POST   | `/first-basket/matchup`       | yes  | Probabilities for two teams from stored history |
| POST   | `/predictions`                | yes  | Log a prediction before tip-off       |
| GET    | `/predictions`                | yes  | List predictions with their grades    |
| GET    | `/predictions/grades`         | yes  | Hit rate, log loss and Brier score per model |
| GET    | `/predictions/compare`        | yes  | Head-to-head of two models on the games both predicted (`model_a`, `model_b`) |
| GET    | `/nba/feed?url=`              | yes  | Relay of a fixed set of NBA.com JSON feeds |
| GET    | `/board`                      | read | Tonight's champion picks per game for an app (`date`, default latest) |

### Matchup model

For each starter, `rate = (first_baskets + prior_games × 0.1) / (starts +
prior_games)`, counting only games the player started. Rates are normalized
across the ten starters. Starters default to each team's last stored lineup;
pass `home_starters` / `away_starters` (NBA player IDs) for tonight's lineup.
Bench players and tip-off are not modelled yet.

```
curl -X POST https://YOUR-SERVICE.onrender.com/first-basket/matchup \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_SECRET_KEY" \
  -d '{"home_team":"BOS","away_team":"NYK","save_prediction":true,
       "game_id":"0022500123","game_date":"2025-11-20",
       "tip_time":"2025-11-21T00:30:00Z"}'
```

With `save_prediction`, the result is logged to the ledger. When `game_date`
is given, only games before that date count toward each starter's history,
so the baseline never sees the game it predicts. Grading uses one
prediction per game and model (the latest one made before `tip_time`).
Predictions logged after tip-off are excluded. Predictions without a
`tip_time` are included and counted separately.

A prediction can carry `home_team` and `away_team`. If its `game_id` has no
imported result (for example a made-up id because NBA.com's schedule was
unreachable), it is graded against the game with the same date and teams.
`GET /predictions` takes `offset` to page through more than 500 predictions.

`/predictions/compare` pairs each game's last pre-tip prediction from both
models and reports each model's log loss, Brier and top-pick hit rate, the
mean log-loss difference (A minus B, negative means A is better) with its
paired standard error, and a verdict once 30 games are shared.

Example:

```
curl -X POST http://127.0.0.1:8000/first-basket/probabilities \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_SECRET_KEY" \
  -d '{"candidates":[
        {"player_id":"P001","player_name":"Example Player A","first_basket_count":12,"eligible_game_count":60},
        {"player_id":"P002","player_name":"Example Player B","first_basket_count":8,"eligible_game_count":40}]}'
```

### NBA.com relay

Callers that cannot reach NBA.com (the model's cloud runs) can fetch a feed
through `GET /nba/feed?url=<full NBA.com URL>`. Only these are relayed, and
the JSON comes back unchanged under `data`:

- `cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json` (cached 1 min)
- `cdn.nba.com/static/json/staticData/scheduleLeagueV2.json` (30 min)
- `cdn.nba.com/static/json/liveData/boxscore/boxscore_<game id>.json` (1 min)
- `cdn.nba.com/static/json/liveData/playbyplay/playbyplay_<game id>.json` (1 min)
- `stats.nba.com/js/data/leaders/00_daily_lineups_<YYYYMMDD>.json` (2 min)

Anything else gets 400. If NBA.com refuses Render's servers, the relay
answers 502 and callers fall back to their other sources.

### Board for an app

`GET /board?date=YYYY-MM-DD` (default: the latest date the champion
predicted) lists each game with the champion's last pick before tip-off
(players sorted by probability, with fair American odds), the baseline's
pick for the same game, and the actual first scorer and grades once the
game is imported. An app can use the read-only `NBA_READ_KEY`, which can
read the board, grades and compare but cannot write anything. "Read" in the
auth column means either key works.

## Deploy on Render

- Runtime: Python 3 (version taken from `.python-version`)
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment: add `NBA_API_KEY` in the service's Environment settings

Free instances sleep after inactivity and their filesystem is ephemeral.
Imported history is reloaded from the CSVs on every start. **Predictions are
not**, so set `DATABASE_URL` to a Postgres database before relying on the
ledger. Render's free Postgres expires after 30 days. Neon and Supabase have
free tiers that don't expire.

Never commit your key, a `.env` file, or the database.
