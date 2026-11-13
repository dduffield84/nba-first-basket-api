"""HTTP endpoints for the shared Edge Intelligence ledger.

Writes need the main API key. Reads accept the read-only key too.
Nothing here edits or deletes a ledger row: corrections are new rows.
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, select

import ledger_db as L
import ledger_metrics as LM

write_router = APIRouter(prefix="/ledger", tags=["ledger"])
read_router = APIRouter(prefix="/ledger", tags=["ledger"])

get_engine: Callable = lambda: None  # noqa: E731  (set by main.py)

MAX_BATCH = 500
# A prediction may not claim to be made in the future.
CLOCK_SKEW = timedelta(minutes=5)


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def as_utc(dt, flags, name):
    if dt is None:
        return None
    if dt.tzinfo is None:
        flags.append(f"{name}_timezone_assumed_utc")
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_odds(value):
    if value is not None and -100 < value < 100:
        raise ValueError("odds_american must be <= -100 or >= +100")
    return value


def check_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("event_date must use YYYY-MM-DD")
    return value


class EntryIn(BaseModel):
    entry_key: str = Field(min_length=1, max_length=200)
    kind: Literal["prediction", "paper_bet", "real_bet"]
    sport: Literal["nfl", "nba", "cfb"]
    market: str = Field(min_length=3, max_length=40)
    event_id: str | None = Field(default=None, max_length=64)
    event_date: str
    event_label: str = Field(min_length=3, max_length=40)
    home: str | None = Field(default=None, max_length=40)
    away: str | None = Field(default=None, max_length=40)
    event_start_utc: datetime | None = None
    selection: str = Field(min_length=1, max_length=120)
    selection_id: str | None = Field(default=None, max_length=64)
    side: str | None = Field(default=None, max_length=12)
    line: float | None = None
    model_prob: float | None = Field(default=None, ge=0, le=1)
    market_prob: float | None = Field(default=None, ge=0, le=1)
    model_version: str = Field(min_length=1, max_length=60)
    decision: Literal["BET", "PASS"] | None = None
    odds_american: float | None = None
    book: str | None = Field(default=None, max_length=40)
    price_at: datetime | None = None
    stake_units: float = Field(default=0.0, ge=0, le=100)
    predicted_at: datetime
    source: str = Field(min_length=1, max_length=200)
    source_ref: str | None = Field(default=None, max_length=200)
    linked_entry_key: str | None = Field(default=None, max_length=200)
    data_flags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("decision", mode="before")
    @classmethod
    def upper_decision(cls, v):
        return v.strip().upper() if isinstance(v, str) and v.strip() else None

    _odds = field_validator("odds_american")(lambda cls, v: check_odds(v))
    _date = field_validator("event_date")(lambda cls, v: check_date(v))

    @model_validator(mode="after")
    def rules(self):
        if L.market_sport(self.market) != self.sport:
            raise ValueError(f"market {self.market!r} is not a "
                             f"{self.sport} market (see GET /ledger/markets)")
        if self.kind == "prediction" and self.model_prob is None:
            raise ValueError("a prediction needs model_prob")
        if self.kind == "paper_bet" and self.decision is None:
            raise ValueError("a paper bet needs an explicit BET or PASS")
        if self.kind == "real_bet":
            if self.odds_american is None or not self.book:
                raise ValueError("a real bet needs the odds taken and book")
            if self.stake_units <= 0:
                raise ValueError("a real bet needs a positive stake")
        if self.decision == "BET" and self.odds_american is None:
            raise ValueError("a BET needs a price")
        refused, _ = L.nba_book_check(self.market, self.book)
        if refused and self.odds_american is not None:
            raise ValueError(refused)
        return self


class EntryBatch(BaseModel):
    entries: list[EntryIn] = Field(min_length=1, max_length=MAX_BATCH)


class PriceIn(BaseModel):
    entry_key: str = Field(min_length=1, max_length=200)
    price_type: Literal["open", "bet", "current", "close"]
    odds_american: float | None = None
    line: float | None = None
    book: str | None = Field(default=None, max_length=40)
    observed_at: datetime
    source: str = Field(min_length=1, max_length=200)

    _odds = field_validator("odds_american")(lambda cls, v: check_odds(v))

    @model_validator(mode="after")
    def some_price(self):
        if self.odds_american is None and self.line is None:
            raise ValueError("a price needs odds_american or line")
        return self


class PriceBatch(BaseModel):
    prices: list[PriceIn] = Field(min_length=1, max_length=MAX_BATCH)


class ResultIn(BaseModel):
    entry_key: str = Field(min_length=1, max_length=200)
    outcome: Literal["win", "loss", "push", "void"]
    actual_value: float | None = None
    settled_at: datetime
    source: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class ResultBatch(BaseModel):
    results: list[ResultIn] = Field(min_length=1, max_length=MAX_BATCH)


# Fields whose difference makes a re-sent entry a conflict, not a repeat.
SAME_FIELDS = ("kind", "sport", "market", "event_date", "event_label",
               "selection", "side", "line", "model_prob", "model_version",
               "decision", "odds_american", "book", "stake_units",
               "predicted_at")


def entry_row(e: EntryIn, recorded):
    flags = list(e.data_flags)
    predicted = as_utc(e.predicted_at, flags, "predicted_at")
    start = as_utc(e.event_start_utc, flags, "event_start")
    if predicted > recorded + CLOCK_SKEW:
        raise HTTPException(422, f"{e.entry_key}: predicted_at is in the "
                                 "future")
    pregame = None if start is None else int(predicted < start)
    if start is not None and recorded >= start:
        flags.append("backfilled_after_start")
    _, unconfirmed = L.nba_book_check(e.market, e.book)
    if unconfirmed and e.odds_american is not None:
        flags.append(unconfirmed)
    return {
        "entry_key": e.entry_key, "kind": e.kind, "sport": e.sport,
        "market": e.market, "event_id": e.event_id,
        "event_date": e.event_date, "event_label": e.event_label,
        "home": e.home, "away": e.away, "event_start_utc": iso(start),
        "selection": e.selection, "selection_id": e.selection_id,
        "side": e.side, "line": e.line, "model_prob": e.model_prob,
        "market_prob": e.market_prob, "model_version": e.model_version,
        "decision": e.decision, "odds_american": e.odds_american,
        "book": e.book, "price_at": iso(as_utc(e.price_at, flags, "price_at")),
        "stake_units": e.stake_units, "predicted_at": iso(predicted),
        "recorded_at": iso(recorded), "pregame_verified": pregame,
        "source": e.source, "source_ref": e.source_ref,
        "data_flags": ",".join(sorted(set(flags))) or None,
    }


def entries_by_key(conn, keys):
    t = L.ledger_entries
    out = {}
    keys = list(keys)
    for i in range(0, len(keys), 400):
        for r in conn.execute(select(t).where(
                t.c.entry_key.in_(keys[i:i + 400]))):
            out[r.entry_key] = r
    return out


@write_router.post("/entries")
def add_entries(batch: EntryBatch):
    keys = [e.entry_key for e in batch.entries]
    if len(keys) != len(set(keys)):
        raise HTTPException(422, "entry_key repeats within the batch")
    recorded = now_utc()
    rows = [entry_row(e, recorded) for e in batch.entries]
    with get_engine().begin() as conn:
        links = {e.linked_entry_key for e in batch.entries
                 if e.linked_entry_key}
        existing = entries_by_key(conn, set(keys) | links)
        conflicts, created, duplicates = [], [], []
        for row in rows:
            old = existing.get(row["entry_key"])
            if old is None:
                created.append(row)
            elif all(getattr(old, f) == row[f] for f in SAME_FIELDS):
                duplicates.append(row["entry_key"])
            else:
                diff = [f for f in SAME_FIELDS if getattr(old, f) != row[f]]
                conflicts.append({"entry_key": row["entry_key"],
                                  "differs": diff})
        if conflicts:
            raise HTTPException(409, {
                "detail": "these entry_keys already exist with different "
                          "values; ledger rows cannot be changed",
                "conflicts": conflicts})
        new_ids = {}
        to_create = {r["entry_key"] for r in created}
        for e, row in zip(batch.entries, rows):
            if row["entry_key"] not in to_create:
                continue
            if e.linked_entry_key:
                link = existing.get(e.linked_entry_key) or new_ids.get(
                    e.linked_entry_key)
                if link is None:
                    raise HTTPException(
                        422, f"{e.entry_key}: linked_entry_key not found")
                row["linked_entry_id"] = getattr(link, "id", link)
            new_id = conn.execute(L.ledger_entries.insert().values(
                **row)).inserted_primary_key[0]
            new_ids[row["entry_key"]] = new_id
    return {"created": len(created), "duplicates": len(duplicates),
            "duplicate_keys": duplicates[:50]}


def need_entries(conn, keys):
    found = entries_by_key(conn, set(keys))
    missing = sorted(set(keys) - set(found))
    if missing:
        raise HTTPException(422, {"detail": "unknown entry_key",
                                  "missing": missing[:50]})
    return found


@write_router.post("/prices")
def add_prices(batch: PriceBatch):
    recorded = iso(now_utc())
    created = duplicates = 0
    with get_engine().begin() as conn:
        found = need_entries(conn, [p.entry_key for p in batch.prices])
        lp = L.ledger_prices
        for p in batch.prices:
            e = found[p.entry_key]
            refused, unconfirmed = L.nba_book_check(e.market, p.book)
            if refused and p.odds_american is not None:
                raise HTTPException(422, f"{p.entry_key}: {refused}")
            flags = [unconfirmed] if unconfirmed else []
            observed = as_utc(p.observed_at, flags, "observed_at")
            if (p.price_type == "close" and e.event_start_utc
                    and observed > datetime.fromisoformat(e.event_start_utc)):
                flags.append("close_observed_after_start")
            row = {"entry_id": e.id, "price_type": p.price_type,
                   "odds_american": p.odds_american, "line": p.line,
                   "book": p.book, "observed_at": iso(observed),
                   "recorded_at": recorded, "source": p.source,
                   "data_flags": ",".join(flags) or None}
            same = conn.execute(select(lp.c.id).where(and_(
                lp.c.entry_id == e.id, lp.c.price_type == p.price_type,
                lp.c.observed_at == row["observed_at"],
                (lp.c.book == p.book) if p.book else lp.c.book.is_(None),
            ))).first()
            if same:
                duplicates += 1
                continue
            conn.execute(lp.insert().values(**row))
            created += 1
    return {"created": created, "duplicates": duplicates}


@write_router.post("/results")
def add_results(batch: ResultBatch):
    recorded = iso(now_utc())
    created = duplicates = 0
    with get_engine().begin() as conn:
        found = need_entries(conn, [r.entry_key for r in batch.results])
        lr = L.ledger_results
        for r in batch.results:
            e = found[r.entry_key]
            latest = conn.execute(select(lr).where(lr.c.entry_id == e.id)
                                  .order_by(lr.c.id.desc())).first()
            if (latest and latest.outcome == r.outcome
                    and latest.actual_value == r.actual_value):
                duplicates += 1
                continue
            note = r.note
            if latest:
                note = (f"corrects result #{latest.id} "
                        f"({latest.outcome})" + (f"; {note}" if note else ""))
            conn.execute(lr.insert().values(
                entry_id=e.id, outcome=r.outcome,
                actual_value=r.actual_value,
                settled_at=iso(as_utc(r.settled_at, [], "settled_at")),
                recorded_at=recorded, source=r.source, note=note))
            created += 1
    return {"created": created, "duplicates": duplicates}


def load_entries(conn, sport=None, market=None, kind=None,
                 model_version=None, since=None, until=None,
                 limit=None, offset=0):
    t = L.ledger_entries
    q = select(t).order_by(t.c.event_date.desc(), t.c.id.desc())
    for col, val in ((t.c.sport, sport), (t.c.market, market),
                     (t.c.kind, kind), (t.c.model_version, model_version)):
        if val:
            q = q.where(col == val)
    if since:
        q = q.where(t.c.event_date >= since)
    if until:
        q = q.where(t.c.event_date <= until)
    if limit:
        q = q.limit(limit).offset(offset)
    entries = [dict(r._mapping) for r in conn.execute(q)]
    ids = [e["id"] for e in entries]
    results, prices = {}, {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for r in conn.execute(select(L.ledger_results).where(
                L.ledger_results.c.entry_id.in_(chunk))
                .order_by(L.ledger_results.c.id)):
            results.setdefault(r.entry_id, []).append(dict(r._mapping))
        for p in conn.execute(select(L.ledger_prices).where(
                L.ledger_prices.c.entry_id.in_(chunk))
                .order_by(L.ledger_prices.c.observed_at,
                          L.ledger_prices.c.id)):
            prices.setdefault(p.entry_id, []).append(dict(p._mapping))
    for e in entries:
        e["data_flags"] = [f for f in (e["data_flags"] or "").split(",") if f]
        hist = results.get(e["id"], [])
        e["result_history"] = hist
        e["outcome"] = hist[-1]["outcome"] if hist else None
        ps = prices.get(e["id"], [])
        e["prices"] = ps

        def latest(kind, field):
            same_book = [p for p in ps if p["price_type"] == kind
                         and p[field] is not None and p["book"] == e["book"]]
            anyp = [p for p in ps if p["price_type"] == kind
                    and p[field] is not None]
            pick = (same_book or anyp or [None])[-1]
            return pick[field] if pick else None
        e["open_odds"] = latest("open", "odds_american")
        e["bet_odds"] = latest("bet", "odds_american")
        e["close_odds"] = latest("close", "odds_american")
        e["close_line"] = latest("close", "line")
        e["fair_odds"] = LM.fair_american(e["model_prob"])
        e["expected_value"] = (
            round(LM.expected_value(e["model_prob"], e["odds_american"]), 4)
            if LM.expected_value(e["model_prob"], e["odds_american"])
            is not None else None)
        e["staked"] = LM.is_staked(e)
    return entries


@read_router.get("/markets")
def markets():
    return {
        "markets": L.MARKETS,
        "kinds": L.KINDS,
        "nba_first_scorer_book_market": L.NBA_FIRST_SCORER_BOOK_MARKET,
        "nba_book_rule_note": "From the Stage 1 audit. Confirm each book's "
                              "current house rules before relying on them.",
    }


Sport = Annotated[Literal["nfl", "nba", "cfb"] | None, Query()]
Kind = Annotated[Literal["prediction", "paper_bet", "real_bet"] | None,
                 Query()]
DateQ = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]


@read_router.get("/entries")
def list_entries(sport: Sport = None, kind: Kind = None,
                 market: Annotated[str | None, Query(max_length=40)] = None,
                 model_version: Annotated[str | None,
                                          Query(max_length=60)] = None,
                 since: DateQ = None, until: DateQ = None,
                 limit: Annotated[int, Query(ge=1, le=500)] = 100,
                 offset: Annotated[int, Query(ge=0)] = 0):
    with get_engine().connect() as conn:
        rows = load_entries(conn, sport, market, kind, model_version,
                            since, until, limit, offset)
    return {"count": len(rows), "offset": offset, "entries": rows}


@read_router.get("/performance")
def performance(sport: Sport = None, kind: Kind = None,
                market: Annotated[str | None, Query(max_length=40)] = None,
                model_version: Annotated[str | None,
                                         Query(max_length=60)] = None,
                since: DateQ = None, until: DateQ = None,
                group_by: Annotated[
                    Literal["sport", "market", "model_version",
                            "odds_band"] | None, Query()] = None):
    with get_engine().connect() as conn:
        rows = load_entries(conn, sport, market, kind, model_version,
                            since, until)
    return {
        "filters": {"sport": sport, "kind": kind, "market": market,
                    "model_version": model_version, "since": since,
                    "until": until},
        "rules": [
            "Only predictions made before the event start count.",
            "Predictions, paper bets and real bets are never combined.",
            "A paper bet is staked only on an explicit BET with a price.",
            "Results are never deleted; a correction is a new row.",
            "All figures are as recorded; nothing here is a guarantee.",
        ],
        "performance": LM.performance(rows, group_by),
    }


@read_router.get("/board")
def board(event_date: Annotated[str | None, Query(
              alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
          sport: Sport = None):
    """One day's model predictions for the app's Edge Board: model vs
    market probability, fair odds, EV and BET/PASS, newest data first.

    Only predictions are listed (bets are a member's own business). A row
    is marked live only when it was made before its start and the start
    has not passed; anything else is shown as not current."""
    day = event_date or now_utc().date().isoformat()
    with get_engine().connect() as conn:
        rows = load_entries(conn, sport=sport, kind="prediction",
                            since=day, until=day)
    now = now_utc()
    items = []
    for e in rows:
        start = (datetime.fromisoformat(e["event_start_utc"])
                 if e["event_start_utc"] else None)
        if start is None:
            status = "start time not verified"
        elif e["pregame_verified"] != 1:
            status = "made after the start"
        elif start <= now:
            status = "started"
        else:
            status = "live"
        items.append({
            "entry_key": e["entry_key"], "sport": e["sport"],
            "market": e["market"], "event_label": e["event_label"],
            "event_start_utc": e["event_start_utc"],
            "selection": e["selection"], "side": e["side"],
            "line": e["line"], "model_prob": e["model_prob"],
            "market_prob": e["market_prob"],
            "price_implied_prob": LM.implied(e["odds_american"]),
            "odds_american": e["odds_american"], "book": e["book"],
            "price_at": e["price_at"], "fair_odds": e["fair_odds"],
            "expected_value": e["expected_value"],
            "decision": e["decision"] or "PASS",
            "model_version": e["model_version"],
            "predicted_at": e["predicted_at"], "status": status,
            "outcome": e["outcome"], "data_flags": e["data_flags"],
        })
    items.sort(key=lambda i: (i["status"] != "live",
                              i["decision"] != "BET",
                              -(i["expected_value"] or -9)))
    return {
        "date": day,
        "generated_at": now.isoformat(),
        "last_prediction_at": max((i["predicted_at"] for i in items),
                                  default=None),
        "counts": {s: sum(i["status"] == s for i in items) for s in
                   ("live", "started", "made after the start",
                    "start time not verified")},
        "items": items,
    }
