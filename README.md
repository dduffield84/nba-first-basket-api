# NBA First Basket API

Private FastAPI service for NBA first-basket research: historical first
scorers and starters imported from NBA.com, matchup probabilities from that
history, and a prediction ledger graded against actual results. Version 1.1.0.

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

With `save_prediction`, the result is logged to the ledger. Grading uses one
prediction per game and model (the latest one made before `tip_time`).
Predictions logged after tip-off are excluded. Predictions without a
`tip_time` are included and counted separately.

Example:

```
curl -X POST http://127.0.0.1:8000/first-basket/probabilities \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_SECRET_KEY" \
  -d '{"candidates":[
        {"player_id":"P001","player_name":"Example Player A","first_basket_count":12,"eligible_game_count":60},
        {"player_id":"P002","player_name":"Example Player B","first_basket_count":8,"eligible_game_count":40}]}'
```

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
