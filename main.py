import os
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

import db
import model

APP_VERSION = "1.1.0"
API_KEY = os.getenv("NBA_API_KEY", "")

engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = db.make_engine()
    db.metadata.create_all(engine)
    with engine.begin() as conn:
        db.seed_from_csv(conn)
    yield
    engine.dispose()


app = FastAPI(
    title="NBA First Basket API",
    version=APP_VERSION,
    description="Private NBA data and first-basket research API.",
    lifespan=lifespan,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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


def validate_iso_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("game_date must use YYYY-MM-DD")
    return value


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
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "database_backend": engine.dialect.name if engine else None,
        "version": APP_VERSION
    }


# ---------------------------------------------------------------------------
# Historical first-basket records
# ---------------------------------------------------------------------------

class Starter(BaseModel):
    player_id: str = Field(min_length=1, max_length=32)
    player_name: str = Field(min_length=1, max_length=100)
    team: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")


class FirstBasketRecord(BaseModel):
    game_id: str = Field(min_length=1, max_length=32)
    game_date: str
    player_id: str = Field(min_length=1, max_length=32)
    player_name: str = Field(min_length=1, max_length=100)
    team: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    source: str = Field(min_length=2, max_length=200)
    verified: bool = False
    season: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    home_team: str | None = Field(default=None, max_length=3)
    away_team: str | None = Field(default=None, max_length=3)
    tip_winner_team: str | None = Field(default=None, max_length=3)
    data_flags: list[str] = Field(default_factory=list, max_length=20)
    starters: list[Starter] | None = Field(default=None, max_length=10)

    @field_validator("game_date")
    @classmethod
    def check_date(cls, value: str) -> str:
        return validate_iso_date(value)


def upper(value):
    return value.upper() if value else value


@app.post(
    "/first-basket/records",
    dependencies=[Depends(require_api_key)]
)
def save_record(record: FirstBasketRecord):
    row = {
        "game_id": record.game_id,
        "game_date": record.game_date,
        "player_id": record.player_id,
        "player_name": record.player_name,
        "team": record.team.upper(),
        "source": record.source,
        "verified": int(record.verified),
        "recorded_at": utc_now(),
        "season": record.season,
        "home_team": upper(record.home_team),
        "away_team": upper(record.away_team),
        "tip_winner_team": upper(record.tip_winner_team),
        "data_flags": ",".join(record.data_flags) or None,
    }
    try:
        with engine.begin() as conn:
            db.upsert(conn, db.first_baskets, [row])
            if record.starters is not None:
                conn.execute(db.game_starters.delete().where(
                    db.game_starters.c.game_id == record.game_id))
                db.upsert(conn, db.game_starters, [{
                    "game_id": record.game_id,
                    "player_id": s.player_id,
                    "player_name": s.player_name,
                    "team": s.team.upper(),
                    "game_date": record.game_date,
                } for s in record.starters])
    except SQLAlchemyError:
        raise HTTPException(500, "Database write failed")
    return {
        "saved": True,
        "game_id": record.game_id,
        "verified": record.verified
    }


def record_out(row):
    out = dict(row._mapping)
    out["verified"] = bool(out["verified"])
    out["data_flags"] = [
        f for f in (out.get("data_flags") or "").split(",") if f
    ]
    return out


@app.get(
    "/first-basket/records",
    dependencies=[Depends(require_api_key)]
)
def list_records(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    team: Annotated[str | None, Query(max_length=3)] = None,
    season: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
):
    t = db.first_baskets
    query = select(t).order_by(t.c.game_date.desc(), t.c.game_id.desc())
    if team:
        query = query.where(t.c.team == team.upper())
    if season:
        query = query.where(t.c.season == season)
    with engine.connect() as conn:
        rows = conn.execute(query.limit(limit)).fetchall()
    records = [record_out(r) for r in rows]
    return {
        "count": len(records),
        "records": records
    }


@app.get(
    "/first-basket/stats",
    dependencies=[Depends(require_api_key)]
)
def stats():
    fb, gs = db.first_baskets, db.game_starters
    with engine.connect() as conn:
        games = conn.execute(select(func.count()).select_from(fb)).scalar()
        flagged = conn.execute(select(func.count()).select_from(fb).where(
            fb.c.data_flags.is_not(None))).scalar()
        with_starters = conn.execute(select(
            func.count(func.distinct(gs.c.game_id)))).scalar()
        first, last = conn.execute(select(
            func.min(fb.c.game_date), func.max(fb.c.game_date))).one()
    return {
        "games": games,
        "games_with_starters": with_starters,
        "games_with_data_flags": flagged,
        "first_game_date": first,
        "last_game_date": last,
    }


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------

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


MATCHUP_MODEL = "starter_history_rate_v1"


class MatchupRequest(BaseModel):
    home_team: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    away_team: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    home_starters: list[str] | None = Field(default=None, max_length=5)
    away_starters: list[str] | None = Field(default=None, max_length=5)
    season: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    prior_games: float = Field(default=10.0, ge=0, le=200)
    save_prediction: bool = False
    game_id: str | None = Field(default=None, min_length=1, max_length=32)
    game_date: str | None = None
    tip_time: datetime | None = None

    @model_validator(mode="after")
    def check(self):
        if self.home_team.upper() == self.away_team.upper():
            raise ValueError("home_team and away_team must differ")
        if self.save_prediction and not (self.game_id and self.game_date):
            raise ValueError(
                "game_id and game_date are required to save a prediction")
        if self.game_date:
            validate_iso_date(self.game_date)
        return self


def latest_starters(conn, team):
    gs = db.game_starters
    last_game = conn.execute(
        select(gs.c.game_id).where(gs.c.team == team)
        .order_by(gs.c.game_date.desc(), gs.c.game_id.desc()).limit(1)
    ).scalar()
    if last_game is None:
        return []
    return [r.player_id for r in conn.execute(
        select(gs.c.player_id).where(
            gs.c.game_id == last_game, gs.c.team == team))]


def player_history(conn, player_id, season):
    gs, fb = db.game_starters, db.first_baskets
    started = select(gs.c.game_id).where(gs.c.player_id == player_id)
    if season:
        started = started.join(fb, fb.c.game_id == gs.c.game_id).where(
            fb.c.season == season)
    started = started.subquery()
    starts = conn.execute(
        select(func.count()).select_from(started)).scalar()
    first = conn.execute(select(func.count()).select_from(fb).where(
        fb.c.player_id == player_id,
        fb.c.game_id.in_(select(started.c.game_id)))).scalar()
    name = conn.execute(
        select(gs.c.player_name).where(gs.c.player_id == player_id)
        .order_by(gs.c.game_date.desc()).limit(1)).scalar()
    return starts, first, name


@app.post(
    "/first-basket/matchup",
    dependencies=[Depends(require_api_key)]
)
def matchup(request: MatchupRequest):
    flags = []
    players = []
    with engine.connect() as conn:
        for side, team, given in (
            ("home", request.home_team.upper(), request.home_starters),
            ("away", request.away_team.upper(), request.away_starters),
        ):
            starters = given or latest_starters(conn, team)
            if not given:
                flags.append(f"{side}_starters_from_last_stored_game")
            if len(starters) != 5:
                flags.append(f"{side}_has_{len(starters)}_starters")
            for pid in starters:
                starts, first, name = player_history(
                    conn, pid, request.season)
                if starts == 0:
                    flags.append(f"no_history_for_{pid}")
                players.append({
                    "player_id": pid,
                    "player_name": name or pid,
                    "team": team,
                    "starts": starts,
                    "first_baskets": first,
                    "smoothed_rate": round(model.smoothed_rate(
                        first, starts, request.prior_games), 6),
                })
    if not players:
        raise HTTPException(
            404, "No starters found for either team. Import history or "
                 "pass home_starters and away_starters.")
    model.normalize(players)
    players.sort(key=lambda p: p["probability"], reverse=True)
    team_share = {}
    for p in players:
        team_share[p["team"]] = round(
            team_share.get(p["team"], 0) + p["probability"], 6)

    saved_id = None
    if request.save_prediction:
        saved_id = store_prediction(
            request.game_id, request.game_date, MATCHUP_MODEL,
            request.tip_time, players)

    return {
        "model": MATCHUP_MODEL,
        "is_trained_model": False,
        "is_game_calibrated": False,
        "probabilities_are_betting_probabilities": False,
        "note": "Shares are normalized across the listed starters only; "
                "bench players and tip-off are not modelled.",
        "data_flags": flags,
        "team_share": team_share,
        "prediction_id": saved_id,
        "results": players,
    }


# ---------------------------------------------------------------------------
# Prediction ledger and grading
# ---------------------------------------------------------------------------

class PredictionCandidate(BaseModel):
    player_id: str = Field(min_length=1, max_length=32)
    player_name: str = Field(min_length=1, max_length=100)
    team: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    probability: float = Field(ge=0, le=1)


class PredictionRequest(BaseModel):
    game_id: str = Field(min_length=1, max_length=32)
    game_date: str
    model: str = Field(min_length=1, max_length=100)
    tip_time: datetime | None = None
    candidates: list[PredictionCandidate] = Field(
        min_length=1, max_length=30)

    @field_validator("game_date")
    @classmethod
    def check_date(cls, value: str) -> str:
        return validate_iso_date(value)

    @model_validator(mode="after")
    def check(self):
        ids = [c.player_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidates must not repeat a player_id")
        if sum(c.probability for c in self.candidates) > 1.0001:
            raise ValueError("candidate probabilities must sum to <= 1")
        return self


def store_prediction(game_id, game_date, model_name, tip_time, candidates):
    with engine.begin() as conn:
        set_id = conn.execute(db.prediction_sets.insert().values(
            game_id=game_id,
            game_date=game_date,
            model=model_name,
            created_at=utc_now(),
            tip_time=tip_time.astimezone(timezone.utc).isoformat()
            if tip_time and tip_time.tzinfo else
            (tip_time.isoformat() if tip_time else None),
        )).inserted_primary_key[0]
        conn.execute(db.prediction_items.insert(), [{
            "set_id": set_id,
            "player_id": c["player_id"],
            "player_name": c["player_name"],
            "team": c["team"].upper(),
            "probability": c["probability"],
        } for c in candidates])
    return set_id


@app.post(
    "/predictions",
    dependencies=[Depends(require_api_key)]
)
def save_prediction(request: PredictionRequest):
    set_id = store_prediction(
        request.game_id, request.game_date, request.model,
        request.tip_time, [c.model_dump() for c in request.candidates])
    return {"saved": True, "prediction_id": set_id}


def made_before_tip(row):
    if not row.tip_time:
        return None
    created = datetime.fromisoformat(row.created_at)
    tip = datetime.fromisoformat(row.tip_time)
    if tip.tzinfo is None:
        tip = tip.replace(tzinfo=timezone.utc)
    return created < tip


def load_predictions(conn, model_name=None, limit=None):
    ps, pi, fb = db.prediction_sets, db.prediction_items, db.first_baskets
    query = (
        select(ps, fb.c.player_id.label("actual_player_id"),
               fb.c.player_name.label("actual_player_name"))
        .select_from(ps.outerjoin(fb, fb.c.game_id == ps.c.game_id))
        .order_by(ps.c.id.desc())
    )
    if model_name:
        query = query.where(ps.c.model == model_name)
    if limit:
        query = query.limit(limit)
    sets = conn.execute(query).fetchall()
    out = []
    for s in sets:
        items = [dict(r._mapping) for r in conn.execute(
            select(pi.c.player_id, pi.c.player_name, pi.c.team,
                   pi.c.probability).where(pi.c.set_id == s.id))]
        entry = {
            "prediction_id": s.id,
            "game_id": s.game_id,
            "game_date": s.game_date,
            "model": s.model,
            "created_at": s.created_at,
            "tip_time": s.tip_time,
            "made_before_tip": made_before_tip(s),
            "candidates": items,
            "actual": None,
            "grade": None,
        }
        if s.actual_player_id:
            entry["actual"] = {
                "player_id": s.actual_player_id,
                "player_name": s.actual_player_name,
            }
            entry["grade"] = model.grade(items, s.actual_player_id)
        out.append(entry)
    return out


@app.get(
    "/predictions",
    dependencies=[Depends(require_api_key)]
)
def list_predictions(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    model_name: Annotated[str | None, Query(alias="model")] = None,
):
    with engine.connect() as conn:
        preds = load_predictions(conn, model_name, limit)
    return {"count": len(preds), "predictions": preds}


@app.get(
    "/predictions/grades",
    dependencies=[Depends(require_api_key)]
)
def prediction_grades(
    model_name: Annotated[str | None, Query(alias="model")] = None,
):
    with engine.connect() as conn:
        preds = load_predictions(conn, model_name)
    # Only predictions known to be made before tip-off count; one per
    # game and model, the latest such prediction.
    chosen = {}
    excluded_late = 0
    for p in preds:
        if p["made_before_tip"] is False:
            excluded_late += 1
            continue
        chosen.setdefault((p["game_id"], p["model"]), p)
    unknown_timing = sum(
        p["made_before_tip"] is None for p in chosen.values())
    by_model = {}
    ungraded = 0
    for (_, model_key), p in chosen.items():
        if p["grade"] is None:
            ungraded += 1
            continue
        by_model.setdefault(model_key, []).append(p["grade"])
    return {
        "models": {m: model.summarize(g) for m, g in by_model.items()},
        "awaiting_result": ungraded,
        "excluded_made_after_tip": excluded_late,
        "included_without_tip_time": unknown_timing,
    }
