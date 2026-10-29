import os
import hmac
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

APP_VERSION = "1.0.0"
DB_PATH = Path(os.getenv("DB_PATH", "data/nba_api.db"))
API_KEY = os.getenv("NBA_API_KEY", "")


@contextmanager
def connect_db():
    # sqlite3's own context manager only commits/rolls back; it never
    # closes the connection, so close it explicitly here.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS first_baskets (
                game_id TEXT PRIMARY KEY,
                game_date TEXT NOT NULL,
                player_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                team TEXT NOT NULL,
                source TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL
            )
        """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="NBA First Basket API",
    version=APP_VERSION,
    description="Private NBA data and first-basket research API.",
    lifespan=lifespan,
)


def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None
):
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Server API key is not configured."
        )
    if not x_api_key or not hmac.compare_digest(
        x_api_key.encode(), API_KEY.encode()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key."
        )


@app.get("/")
def root():
    return {
        "service": "NBA First Basket API",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health")
def health():
    try:
        with connect_db() as conn:
            conn.execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "version": APP_VERSION
    }


class FirstBasketRecord(BaseModel):
    game_id: str = Field(min_length=1, max_length=32)
    game_date: str
    player_id: str = Field(min_length=1, max_length=32)
    player_name: str = Field(min_length=1, max_length=100)
    team: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    source: str = Field(min_length=2, max_length=200)
    verified: bool = False

    @field_validator("game_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("game_date must use YYYY-MM-DD")
        return value


@app.post(
    "/first-basket/records",
    dependencies=[Depends(require_api_key)]
)
def save_record(record: FirstBasketRecord):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with connect_db() as conn:
            conn.execute("""
                INSERT INTO first_baskets
                (game_id, game_date, player_id, player_name,
                 team, source, verified, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    game_date=excluded.game_date,
                    player_id=excluded.player_id,
                    player_name=excluded.player_name,
                    team=excluded.team,
                    source=excluded.source,
                    verified=excluded.verified,
                    recorded_at=excluded.recorded_at
            """, (
                record.game_id, record.game_date,
                record.player_id, record.player_name,
                record.team.upper(), record.source,
                int(record.verified), now
            ))
    except sqlite3.Error:
        raise HTTPException(500, "Database write failed")
    return {
        "saved": True,
        "game_id": record.game_id,
        "verified": record.verified
    }


@app.get(
    "/first-basket/records",
    dependencies=[Depends(require_api_key)]
)
def list_records(limit: Annotated[int, Query(ge=1, le=500)] = 100):
    with connect_db() as conn:
        rows = conn.execute("""
            SELECT game_id, game_date, player_id, player_name,
                   team, source, verified, recorded_at
            FROM first_baskets
            ORDER BY game_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
    records = [dict(row) for row in rows]
    for r in records:
        r["verified"] = bool(r["verified"])
    return {
        "count": len(records),
        "records": records
    }


class Candidate(BaseModel):
    player_id: str = Field(min_length=1, max_length=32)
    player_name: str = Field(min_length=1, max_length=100)
    first_basket_count: int = Field(ge=0)
    eligible_game_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.first_basket_count > self.eligible_game_count:
            raise ValueError(
                "First-basket count cannot exceed eligible games"
            )
        return self


class ProbabilityRequest(BaseModel):
    candidates: list[Candidate] = Field(min_length=1, max_length=20)
    smoothing: float = Field(default=1.0, gt=0, le=10)


@app.post(
    "/first-basket/probabilities",
    dependencies=[Depends(require_api_key)]
)
def calculate_probabilities(request: ProbabilityRequest):
    # This is a smoothed historical-rate baseline, NOT a
    # fully trained prediction model.
    raw = []
    for p in request.candidates:
        rate = (
            p.first_basket_count + request.smoothing
        ) / (
            p.eligible_game_count + 2 * request.smoothing
        )
        raw.append(rate)
    total = sum(raw)
    results = []
    for p, score in zip(request.candidates, raw):
        results.append({
            "player_id": p.player_id,
            "player_name": p.player_name,
            "historical_first_basket_count": p.first_basket_count,
            "eligible_game_count": p.eligible_game_count,
            "smoothed_rate": round(score, 6),
            "normalized_candidate_share": round(score / total, 6)
        })
    results.sort(
        key=lambda x: x["normalized_candidate_share"],
        reverse=True
    )
    return {
        "model": "smoothed_historical_rate_baseline",
        "is_trained_model": False,
        "is_game_calibrated": False,
        "data_source": "caller_supplied_counts",
        "probabilities_are_betting_probabilities": False,
        "results": results
    }
