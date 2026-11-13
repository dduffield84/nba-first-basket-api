import os
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

import db
import ledger_api
import ledger_db  # noqa: F401  (registers the ledger tables)
import model
import relay

APP_VERSION = "1.3.0"
API_KEY = os.getenv("NBA_API_KEY", "")
# Optional second key for apps that only read the board (it cannot write).
READ_KEY = os.getenv("NBA_READ_KEY", "")

engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = db.make_engine()
    db.metadata.create_all(engine)
    db.add_missing_columns(engine)
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
app.add_middleware(GZipMiddleware, minimum_size=2000)
ledger_api.get_engine = lambda: engine


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


def key_matches(given, secret):
    return bool(secret and given) and hmac.compare_digest(
        given.encode(), secret.encode())


def require_read_key(
    x_api_key: Annotated[str | None, Header()] = None
):
    """The main key, or the read-only NBA_READ_KEY."""
    if not API_KEY and not READ_KEY:
        raise HTTPException(503, "Server API key is not configured.")
    if not (key_matches(x_api_key, API_KEY)
            or key_matches(x_api_key, READ_KEY)):
        raise HTTPException(401, "Invalid or missing API key.")


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


def latest_starters(conn, team, before=None):
    gs = db.game_starters
    query = select(gs.c.game_id).where(gs.c.team == team)
    if before:
        query = query.where(gs.c.game_date < before)
    last_game = conn.execute(
        query
        .order_by(gs.c.game_date.desc(), gs.c.game_id.desc()).limit(1)
    ).scalar()
    if last_game is None:
        return []
    return [r.player_id for r in conn.execute(
        select(gs.c.player_id).where(
            gs.c.game_id == last_game, gs.c.team == team))]


def player_history(conn, player_id, season, before=None):
    gs, fb = db.game_starters, db.first_baskets
    started = select(gs.c.game_id).where(gs.c.player_id == player_id)
    if before:
        # never count the game being predicted, or anything after it
        started = started.where(gs.c.game_date < before)
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
            starters = given or latest_starters(
                conn, team, request.game_date)
            if not given:
                flags.append(f"{side}_starters_from_last_stored_game")
            if len(starters) != 5:
                flags.append(f"{side}_has_{len(starters)}_starters")
            for pid in starters:
                starts, first, name = player_history(
                    conn, pid, request.season, request.game_date)
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
            request.tip_time, players,
            request.home_team.upper(), request.away_team.upper())

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
    home_team: str | None = Field(
        default=None, min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    away_team: str | None = Field(
        default=None, min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
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


def store_prediction(game_id, game_date, model_name, tip_time, candidates,
                     home_team=None, away_team=None):
    with engine.begin() as conn:
        set_id = conn.execute(db.prediction_sets.insert().values(
            game_id=game_id,
            game_date=game_date,
            model=model_name,
            home_team=upper(home_team),
            away_team=upper(away_team),
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
        request.tip_time, [c.model_dump() for c in request.candidates],
        request.home_team, request.away_team)
    return {"saved": True, "prediction_id": set_id}


def made_before_tip(row):
    if not row.tip_time:
        return None
    created = datetime.fromisoformat(row.created_at)
    tip = datetime.fromisoformat(row.tip_time)
    if tip.tzinfo is None:
        tip = tip.replace(tzinfo=timezone.utc)
    return created < tip


def result_for(conn, s, cache):
    """The actual first scorer: by game id, else by date and matchup."""
    if s.actual_player_id:
        return s.actual_player_id, s.actual_player_name
    if not (s.home_team and s.away_team):
        return None, None
    key = (s.game_date, s.home_team, s.away_team)
    if key not in cache:
        fb = db.first_baskets
        row = conn.execute(select(fb.c.player_id, fb.c.player_name).where(
            fb.c.game_date == s.game_date, fb.c.home_team == s.home_team,
            fb.c.away_team == s.away_team)).first()
        cache[key] = (row.player_id, row.player_name) if row else (None, None)
    return cache[key]


def load_predictions(conn, model_name=None, limit=None, offset=0):
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
    if offset:
        query = query.offset(offset)
    sets = conn.execute(query).fetchall()
    out = []
    cache = {}
    for s in sets:
        actual_id, actual_name = result_for(conn, s, cache)
        items = [dict(r._mapping) for r in conn.execute(
            select(pi.c.player_id, pi.c.player_name, pi.c.team,
                   pi.c.probability).where(pi.c.set_id == s.id))]
        entry = {
            "prediction_id": s.id,
            "game_id": s.game_id,
            "game_date": s.game_date,
            "model": s.model,
            "home_team": s.home_team,
            "away_team": s.away_team,
            "created_at": s.created_at,
            "tip_time": s.tip_time,
            "made_before_tip": made_before_tip(s),
            "candidates": items,
            "actual": None,
            "grade": None,
        }
        if actual_id:
            entry["actual"] = {
                "player_id": actual_id,
                "player_name": actual_name,
            }
            entry["grade"] = model.grade(items, actual_id)
        out.append(entry)
    return out


@app.get(
    "/predictions",
    dependencies=[Depends(require_api_key)]
)
def list_predictions(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    model_name: Annotated[str | None, Query(alias="model")] = None,
):
    with engine.connect() as conn:
        preds = load_predictions(conn, model_name, limit, offset)
    return {"count": len(preds), "offset": offset, "predictions": preds}


@app.get(
    "/predictions/grades",
    dependencies=[Depends(require_read_key)]
)
def prediction_grades(
    model_name: Annotated[str | None, Query(alias="model")] = None,
):
    with engine.connect() as conn:
        preds = load_predictions(conn, model_name)
    chosen, excluded_late = counted_predictions(preds)
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


def counted_predictions(preds):
    """Only predictions known to be made before tip-off count; one per game
    and model, the latest such prediction. preds must be newest first."""
    chosen = {}
    excluded_late = 0
    for p in preds:
        if p["made_before_tip"] is False:
            excluded_late += 1
            continue
        chosen.setdefault((p["game_id"], p["model"]), p)
    return chosen, excluded_late


@app.get(
    "/predictions/compare",
    dependencies=[Depends(require_read_key)]
)
def compare_models(
    model_a: Annotated[str, Query(min_length=1, max_length=100)] = "champion",
    model_b: Annotated[str, Query(min_length=1, max_length=100)] =
        MATCHUP_MODEL,
):
    """Head-to-head on the games both models predicted before tip-off."""
    with engine.connect() as conn:
        preds = [p for p in load_predictions(conn)
                 if p["model"] in (model_a, model_b)]
    chosen, _ = counted_predictions(preds)
    graded = {}
    for (game_id, m), p in chosen.items():
        if p["grade"] is not None:
            graded.setdefault(game_id, {})[m] = p["grade"]
    pairs = [(g[model_a], g[model_b]) for g in graded.values()
             if model_a in g and model_b in g]
    return {
        "model_a": model_a,
        "model_b": model_b,
        "rule": "latest prediction made before tip-off, per game and model",
        **model.compare(pairs),
    }


# ---------------------------------------------------------------------------
# NBA.com relay
# ---------------------------------------------------------------------------

@app.get(
    "/nba/feed",
    dependencies=[Depends(require_api_key)]
)
def nba_feed(url: Annotated[str, Query(min_length=10, max_length=300)]):
    """Fetch one of a fixed set of NBA.com JSON feeds (schedule, today's
    scoreboard, daily lineups, live box score, play-by-play) for callers
    that cannot reach NBA.com. The JSON comes back unchanged."""
    try:
        data, cached = relay.fetch(url)
    except relay.RelayError as exc:
        raise HTTPException(exc.status, exc.detail)
    return {"url": url, "cached": cached, "data": data}


def fair_american(p):
    if not p or p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


@app.get(
    "/board",
    dependencies=[Depends(require_read_key)]
)
def board(
    game_date: Annotated[str | None, Query(alias="date")] = None,
    champion: Annotated[str, Query(min_length=1, max_length=100)] =
        "champion",
):
    """Tonight's champion picks for an app: per game, the latest pick made
    before tip-off (or the latest pick if none has a tip time yet), the
    baseline's pick for comparison, and the result once it is imported."""
    if game_date:
        try:
            validate_iso_date(game_date)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    ps = db.prediction_sets
    with engine.connect() as conn:
        if not game_date:
            game_date = conn.execute(select(func.max(ps.c.game_date)).where(
                ps.c.model == champion)).scalar()
        if not game_date:
            return {"date": None, "champion": champion, "games": []}
        preds = [p for p in load_predictions(conn)
                 if p["game_date"] == game_date
                 and p["model"] in (champion, MATCHUP_MODEL)]
    chosen, _ = counted_predictions(preds)
    games = {}
    for (game_id, m), p in chosen.items():
        g = games.setdefault(game_id, {
            "game_id": game_id, "home_team": p["home_team"],
            "away_team": p["away_team"], "tip_time": p["tip_time"],
            "actual": p["actual"], "champion": None, "baseline": None})
        g["home_team"] = g["home_team"] or p["home_team"]
        g["away_team"] = g["away_team"] or p["away_team"]
        g["actual"] = g["actual"] or p["actual"]
        picks = sorted(p["candidates"], key=lambda c: c["probability"],
                       reverse=True)
        for c in picks:
            c["fair_odds"] = fair_american(c["probability"])
        entry = {"model": m, "prediction_id": p["prediction_id"],
                 "made_at": p["created_at"], "picks": picks,
                 "grade": p["grade"]}
        g["champion" if m == champion else "baseline"] = entry
    out = sorted((g for g in games.values() if g["champion"]),
                 key=lambda g: (g["tip_time"] or "", g["game_id"]))
    return {
        "date": game_date,
        "champion": champion,
        "first_basket_rule": "first made field goal",
        "games": out,
    }


# ---------------------------------------------------------------------------
# Edge Intelligence shared ledger (NFL, NBA, CFB)
# ---------------------------------------------------------------------------

app.include_router(ledger_api.write_router,
                   dependencies=[Depends(require_api_key)])
app.include_router(ledger_api.read_router,
                   dependencies=[Depends(require_read_key)])
