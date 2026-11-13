"""Shared Edge Intelligence ledger: storage rules, safeguards and metrics."""
import importlib
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_api import KEY, H, use_test_database

READ = "read-key"
RH = {"X-API-Key": READ}
START = datetime(2026, 11, 6, 0, 10, tzinfo=timezone.utc)
BEFORE = (START - timedelta(minutes=3)).isoformat()
AFTER = (START + timedelta(minutes=3)).isoformat()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_API_KEY", KEY)
    monkeypatch.setenv("NBA_READ_KEY", READ)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger.db"))
    import db
    use_test_database(monkeypatch, db)
    monkeypatch.setattr(db, "FIRST_BASKETS_CSV", tmp_path / "fb.csv")
    monkeypatch.setattr(db, "STARTERS_CSV", tmp_path / "st.csv")
    import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        c.main = main
        yield c


def entry(key, **kw):
    e = {
        "entry_key": key, "kind": "prediction", "sport": "nba",
        "market": "nba_first_fg", "event_date": "2026-11-05",
        "event_label": "ORL@PHX", "home": "PHX", "away": "ORL",
        "event_start_utc": START.isoformat(), "selection": "Player A",
        "selection_id": "1", "model_prob": 0.2, "model_version": "v7",
        "predicted_at": BEFORE, "source": "test fixture",
    }
    e.update(kw)
    return e


def post(client, path, body, headers=H):
    return client.post(path, headers=headers, json=body)


def add(client, *entries):
    r = post(client, "/ledger/entries", {"entries": list(entries)})
    assert r.status_code == 200, r.text
    return r.json()


def perf(client, **params):
    r = client.get("/ledger/performance", headers=RH, params=params)
    assert r.status_code == 200, r.text
    return r.json()["performance"]


# ---- auth -----------------------------------------------------------------

def test_auth(client):
    assert client.get("/ledger/performance").status_code == 401
    assert client.get("/ledger/performance", headers=RH).status_code == 200
    # the read-only key cannot write
    r = post(client, "/ledger/entries", {"entries": [entry("k1")]}, RH)
    assert r.status_code == 401
    assert client.get("/ledger/markets", headers=RH).json()["markets"]["cfb"]


# ---- validation -----------------------------------------------------------

@pytest.mark.parametrize("change,needle", [
    ({"market": "cfb_spread"}, "not a nba market"),
    ({"market": "made_up"}, "not a nba market"),
    ({"model_prob": None}, "needs model_prob"),
    ({"kind": "paper_bet"}, "explicit BET or PASS"),
    ({"kind": "real_bet", "stake_units": 1}, "odds taken and book"),
    ({"kind": "real_bet", "odds_american": 500, "book": "DK"},
     "positive stake"),
    ({"decision": "BET"}, "a BET needs a price"),
    ({"odds_american": 50}, "<= -100 or >= +100"),
    ({"event_date": "11/05/2026"}, "YYYY-MM-DD"),
    ({"model_prob": 1.5}, "less than or equal to 1"),
])
def test_entry_validation(client, change, needle):
    r = post(client, "/ledger/entries", {"entries": [entry("k", **change)]})
    assert r.status_code == 422
    assert needle in r.text


def test_future_prediction_refused(client):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = post(client, "/ledger/entries",
             {"entries": [entry("k", predicted_at=future)]})
    assert r.status_code == 422 and "future" in r.text


# ---- NBA settlement routing ----------------------------------------------

def test_nba_book_routing(client):
    # FanDuel settles on first point: never in the first-FG market
    r = post(client, "/ledger/entries", {"entries": [entry(
        "fd-fg", odds_american=900, book="FanDuel")]})
    assert r.status_code == 422 and "nba_first_point" in r.text
    # DraftKings and BetMGM settle on first FG: never in first point
    for book in ("DraftKings", "BetMGM"):
        r = post(client, "/ledger/entries", {"entries": [entry(
            "x-pt", market="nba_first_point", odds_american=900,
            book=book)]})
        assert r.status_code == 422 and "nba_first_fg" in r.text
    add(client,
        entry("fd-pt", market="nba_first_point", odds_american=850,
              book="FanDuel"),
        entry("dk-fg", odds_american=900, book="DraftKings"),
        entry("other", odds_american=900, book="Caesars"))
    rows = client.get("/ledger/entries", headers=RH).json()["entries"]
    flags = {e["entry_key"]: e["data_flags"] for e in rows}
    assert "settlement_rule_unconfirmed" in flags["other"]
    assert "settlement_rule_unconfirmed" not in flags["dk-fg"]
    assert "settlement_rule_unconfirmed" not in flags["fd-pt"]
    # prices are routed by the same rule
    r = post(client, "/ledger/prices", {"prices": [{
        "entry_key": "dk-fg", "price_type": "close", "odds_american": 800,
        "book": "FanDuel", "observed_at": BEFORE, "source": "t"}]})
    assert r.status_code == 422


# ---- append-only and idempotency ------------------------------------------

def test_resend_is_duplicate_and_change_is_conflict(client):
    assert add(client, entry("k1"))["created"] == 1
    again = add(client, entry("k1"))
    assert again == {"created": 0, "duplicates": 1, "duplicate_keys": ["k1"]}
    r = post(client, "/ledger/entries",
             {"entries": [entry("k2"), entry("k1", model_prob=0.3)]})
    assert r.status_code == 409 and "model_prob" in r.text
    # nothing from the refused batch was written
    keys = [e["entry_key"] for e in
            client.get("/ledger/entries", headers=RH).json()["entries"]]
    assert keys == ["k1"]


def test_database_refuses_update_and_delete(client):
    add(client, entry("k1"))
    post(client, "/ledger/results", {"results": [{
        "entry_key": "k1", "outcome": "loss", "settled_at": AFTER,
        "source": "t"}]})
    post(client, "/ledger/prices", {"prices": [{
        "entry_key": "k1", "price_type": "close", "odds_american": 800,
        "book": "DraftKings", "observed_at": BEFORE, "source": "t"}]})
    engine = client.main.engine
    for sql in ("UPDATE ledger_entries SET model_prob = 0.9",
                "DELETE FROM ledger_entries",
                "UPDATE ledger_results SET outcome = 'win'",
                "DELETE FROM ledger_results",
                "UPDATE ledger_prices SET odds_american = 100",
                "DELETE FROM ledger_prices"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(sql))
    e = client.get("/ledger/entries", headers=RH).json()["entries"][0]
    assert e["model_prob"] == 0.2 and e["outcome"] == "loss"


def test_result_correction_keeps_history(client):
    add(client, entry("k1"))
    res = {"entry_key": "k1", "outcome": "loss", "settled_at": AFTER,
           "source": "espn"}
    assert post(client, "/ledger/results",
                {"results": [res]}).json()["created"] == 1
    assert post(client, "/ledger/results",
                {"results": [res]}).json()["duplicates"] == 1
    post(client, "/ledger/results",
         {"results": [{**res, "outcome": "win", "source": "pbp"}]})
    e = client.get("/ledger/entries", headers=RH).json()["entries"][0]
    assert e["outcome"] == "win"
    assert [h["outcome"] for h in e["result_history"]] == ["loss", "win"]
    assert "corrects result" in e["result_history"][1]["note"]


def test_unknown_entry_refused(client):
    r = post(client, "/ledger/results", {"results": [{
        "entry_key": "nope", "outcome": "win", "settled_at": AFTER,
        "source": "t"}]})
    assert r.status_code == 422 and "nope" in r.text


# ---- pregame rule ---------------------------------------------------------

def test_only_pregame_rows_count(client):
    add(client,
        entry("pre"),
        entry("post", predicted_at=AFTER, selection="Player B"),
        entry("nostart", event_start_utc=None, selection="Player C"))
    for k in ("pre", "post", "nostart"):
        post(client, "/ledger/results", {"results": [{
            "entry_key": k, "outcome": "win", "settled_at": AFTER,
            "source": "t"}]})
    rows = {e["entry_key"]: e for e in
            client.get("/ledger/entries", headers=RH).json()["entries"]}
    assert rows["pre"]["pregame_verified"] == 1
    assert rows["post"]["pregame_verified"] == 0
    assert rows["nostart"]["pregame_verified"] is None
    # history imported after the start is labelled as backfilled
    assert "backfilled_after_start" in rows["pre"]["data_flags"]
    p = perf(client)["prediction"]["all"]
    assert p["entries"] == 1 and p["settled"] == 1
    assert p["excluded"] == {"predicted at or after the start": 1,
                             "no start time": 1}


def test_zoneless_times_read_as_utc_and_flagged(client):
    add(client, entry("k", predicted_at="2026-11-06T00:07:00",
                      event_start_utc="2026-11-06T00:10:00"))
    e = client.get("/ledger/entries", headers=RH).json()["entries"][0]
    assert e["pregame_verified"] == 1
    assert "predicted_at_timezone_assumed_utc" in e["data_flags"]


# ---- kinds are never mixed; staking rules ---------------------------------

def test_kinds_reported_separately_and_bet_rules(client):
    add(client,
        entry("pred", model_prob=0.25),
        entry("paper-bet", kind="paper_bet", decision="bet ",
              odds_american=900, book="DraftKings", stake_units=1,
              selection="P1"),
        entry("paper-pass", kind="paper_bet", decision="PASS",
              odds_american=900, book="DraftKings", stake_units=1,
              selection="P2"),
        entry("paper-noprice", kind="paper_bet", decision="PASS",
              stake_units=1, selection="P3"),
        entry("real", kind="real_bet", sport="cfb", market="cfb_spread",
              event_label="UGA@BAMA", selection="UGA", side="UGA",
              line=3.5, odds_american=-110, book="DraftKings",
              stake_units=2, model_prob=None))
    for k, o in (("pred", "win"), ("paper-bet", "win"),
                 ("paper-pass", "win"), ("paper-noprice", "win"),
                 ("real", "loss")):
        post(client, "/ledger/results", {"results": [{
            "entry_key": k, "outcome": o, "settled_at": AFTER,
            "source": "t"}]})
    p = perf(client)
    assert set(p) == {"prediction", "paper_bet", "real_bet"}
    assert p["prediction"]["all"]["entries"] == 1
    paper = p["paper_bet"]["all"]["betting"]
    # only the explicit BET is staked: +900 win on 1 unit is +9
    assert paper["staked"] == 1 and paper["units"] == 9.0
    assert paper["roi"] == 9.0
    real = p["real_bet"]["all"]["betting"]
    assert real["units"] == -2.0 and real["roi"] == -1.0
    rows = {e["entry_key"]: e for e in
            client.get("/ledger/entries", headers=RH).json()["entries"]}
    assert rows["paper-bet"]["decision"] == "BET"
    assert rows["paper-pass"]["staked"] is False
    assert rows["paper-bet"]["fair_odds"] == 400   # p = 0.2
    assert rows["paper-bet"]["expected_value"] == 1.0  # 0.2 * 10 - 1


def test_bet_payouts():
    import ledger_metrics as LM
    e = {"kind": "real_bet", "odds_american": -150, "stake_units": 1}
    assert round(LM.bet_units(e, "win"), 4) == 0.6667
    assert LM.bet_units(e, "loss") == -1
    assert LM.bet_units(e, "push") == 0


# ---- probability metrics and CLV ------------------------------------------

def test_brier_logloss_calibration(client):
    probs = [(0.8, "win"), (0.6, "loss"), (0.3, "win"), (0.1, "loss")]
    for i, (p, o) in enumerate(probs):
        add(client, entry(f"k{i}", model_prob=p, selection=f"P{i}"))
        post(client, "/ledger/results", {"results": [{
            "entry_key": f"k{i}", "outcome": o, "settled_at": AFTER,
            "source": "t"}]})
    m = perf(client)["prediction"]["all"]["probability"]
    ys = [1, 0, 1, 0]
    ps = [p for p, _ in probs]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / 4
    ll = -sum(math.log(p if y else 1 - p) for p, y in zip(ps, ys)) / 4
    assert m["brier"] == round(brier, 6)
    assert m["log_loss"] == round(ll, 6)
    assert m["hit_rate"] == 0.5
    assert {b["bin"]: b["n"] for b in m["calibration"]} == {
        "0.1-0.2": 1, "0.3-0.4": 1, "0.6-0.7": 1, "0.8-0.9": 1}


def test_price_and_line_clv(client):
    add(client,
        entry("td", sport="nfl", market="nfl_anytime_td",
              event_label="KC@BUF", selection="Player T", side="Yes",
              kind="paper_bet", decision="BET", odds_american=200,
              book="DraftKings", stake_units=1, model_prob=0.4),
        entry("sp", sport="cfb", market="cfb_spread", event_label="A@B",
              selection="A", side="A", line=3.5, kind="paper_bet",
              decision="BET", odds_american=-110, book="DraftKings",
              stake_units=1, model_prob=0.55),
        entry("ov", sport="cfb", market="cfb_total", event_label="A@B",
              selection="Over", side="Over", line=50.5, kind="paper_bet",
              decision="PASS", odds_american=-110, book="DraftKings",
              model_prob=0.52))
    post(client, "/ledger/prices", {"prices": [
        {"entry_key": "td", "price_type": "close", "odds_american": 150,
         "book": "DraftKings", "observed_at": BEFORE, "source": "t"},
        # another book's close is ignored when the same book has one
        {"entry_key": "td", "price_type": "close", "odds_american": 300,
         "book": "FanDuel", "observed_at": BEFORE, "source": "t"},
        {"entry_key": "sp", "price_type": "close", "odds_american": -110,
         "line": 2.5, "book": "DraftKings", "observed_at": BEFORE,
         "source": "t"},
        {"entry_key": "ov", "price_type": "close", "line": 52.5,
         "book": "DraftKings", "observed_at": BEFORE, "source": "t"},
    ]})
    c = perf(client)["paper_bet"]["all"]["clv"]
    # +200 taken (33.3%) closing +150 (40%): +6.67 points of probability
    td = 0.4 - 1 / 3
    assert c["with_close"] == 2
    assert c["mean_price_clv"] == round((td + 0.0) / 2, 4)
    assert c["beat_close_rate"] == 0.5
    # spread +3.5 closing +2.5 is +1; Over 50.5 closing 52.5 is +2
    assert c["mean_line_clv_pts"] == 1.5
    assert c["missing_close"] == 1


def test_group_by(client):
    add(client,
        entry("a", odds_american=900, book="DraftKings"),
        entry("b", odds_american=-150, book="DraftKings", selection="B",
              model_version="v8"),
        entry("c", sport="cfb", market="cfb_total", event_label="A@B",
              selection="Over", side="Over", line=50.5))
    by = perf(client, group_by="odds_band")["prediction"]["by_odds_band"]
    assert set(by) == {"+500 to +999", "-199 to -101", "no price"}
    by = perf(client, group_by="sport")["prediction"]["by_sport"]
    assert by["nba"]["entries"] == 2 and by["cfb"]["entries"] == 1
    by = perf(client, group_by="model_version")["prediction"]
    assert set(by["by_model_version"]) == {"v7", "v8"}
    only = perf(client, sport="cfb")["prediction"]["all"]
    assert only["entries"] == 1


def test_linked_bet(client):
    add(client, entry("pred"),
        entry("bet", kind="real_bet", odds_american=900, book="DraftKings",
              stake_units=1, linked_entry_key="pred"))
    rows = {e["entry_key"]: e for e in
            client.get("/ledger/entries", headers=RH).json()["entries"]}
    assert rows["bet"]["linked_entry_id"] == rows["pred"]["id"]
    r = post(client, "/ledger/entries", {"entries": [entry(
        "bet2", kind="real_bet", odds_american=900, book="DraftKings",
        stake_units=1, linked_entry_key="missing")]})
    assert r.status_code == 422


def test_existing_nba_tables_untouched(client):
    # the ledger lives beside the first-basket tables, not inside them
    from sqlalchemy import inspect
    names = set(inspect(client.main.engine).get_table_names())
    assert {"first_baskets", "prediction_sets", "ledger_entries",
            "ledger_prices", "ledger_results"} <= names


def test_board(client):
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    day = future.date().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    add(client,
        entry("live-bet", event_date=day, event_start_utc=future.isoformat(),
              predicted_at=now, decision="BET", odds_american=900,
              book="DraftKings", model_prob=0.2),
        entry("live-pass", event_date=day, selection="B",
              event_start_utc=future.isoformat(), predicted_at=now,
              model_prob=0.05),
        entry("nostart", event_date=day, selection="C",
              event_start_utc=None, predicted_at=now),
        entry("paper", event_date=day, selection="D", kind="paper_bet",
              decision="PASS", event_start_utc=future.isoformat(),
              predicted_at=now))
    b = client.get("/ledger/board", headers=RH,
                   params={"date": day}).json()
    assert [i["entry_key"] for i in b["items"]] == [
        "live-bet", "live-pass", "nostart"]
    top = b["items"][0]
    assert top["status"] == "live" and top["decision"] == "BET"
    assert top["fair_odds"] == 400 and top["price_implied_prob"] == 0.1
    assert b["items"][1]["decision"] == "PASS"
    assert b["counts"] == {"live": 2, "started": 0,
                           "made after the start": 0,
                           "start time not verified": 1}
