# NBA First Basket API

Private FastAPI service for recording historical NBA first-basket results and
calculating a smoothed historical-rate baseline. Version 1.0.0.

The probability endpoint is a research baseline, not a trained or calibrated
model. Do not treat its output as betting probabilities.

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

## Environment variables

| Name          | Required | Default            | Purpose                              |
|---------------|----------|--------------------|--------------------------------------|
| `NBA_API_KEY` | yes      | (none)             | Secret sent in the `X-API-Key` header |
| `DB_PATH`     | no       | `data/nba_api.db`  | SQLite database location             |

Without `NBA_API_KEY`, protected endpoints return 503.

## Endpoints

| Method | Path                          | Auth | Purpose                               |
|--------|-------------------------------|------|---------------------------------------|
| GET    | `/`                           | no   | Service name and version              |
| GET    | `/health`                     | no   | Database status                       |
| GET    | `/first-basket/records`       | yes  | List saved records (`?limit=1..500`)  |
| POST   | `/first-basket/records`       | yes  | Save or update a record by `game_id`  |
| POST   | `/first-basket/probabilities` | yes  | Smoothed historical-rate baseline     |

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

Free instances sleep after inactivity and their filesystem is ephemeral, so the
SQLite database can be lost on restart or redeploy. Use a persistent disk or
regular backups before relying on it for grading.

Never commit your key, a `.env` file, or the database.
