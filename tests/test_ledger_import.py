"""Importers: NBA V7 results ledger, NFL paper bets and the generic CSV."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"
                       / "ledger"))

import common as C  # noqa: E402
import from_csv  # noqa: E402
import from_nba_v7  # noqa: E402
import from_nfl_paper  # noqa: E402
from test_ledger import RH, client  # noqa: E402,F401
from test_api import KEY  # noqa: E402

TIP = "2026-11-07T00:10:00Z"


def v7_row(**kw):
    r = {"date": "2026-11-06", "game": "ORL@PHX", "home": "PHX",
         "away": "ORL", "team": "PHX", "player": "Devin Booker",
         "personId": "1626164", "market": "fg", "model_p": "0.142",
         "open_odds": "650", "book": "DraftKings",
         "odds_time": "2026-11-06 23:50", "ev": "0.06", "stake": "1.0",
         "decision": "BET", "model_version": "e188ce00fd",
         "predicted_at": "2026-11-07 00:07:00", "won": "1", "bet": "1",
         "pnl": "6.5", "close_odds": "600", "clv": "0.01",
         "graded_at": "2026-11-07 03:00", "tip_utc": TIP,
         "pregame_verified": "1", "lineup_status": "confirmed"}
    r.update(kw)
    return r


def test_nba_v7_conversion():
    rows = [
        v7_row(),
        v7_row(personId="1", player="P Two", decision="PASS", bet="0",
               won="0", close_odds=""),
        # the routing bug: a FanDuel price on first FG is dropped
        v7_row(personId="2", player="P Three", book="FanDuel",
               decision="BET", bet="0", won="0"),
        # excluded game stays visible with a flag
        v7_row(date="2026-11-05", game="MIA@GSW", home="GSW", away="MIA",
               personId="3", player="P Four", bet="0", decision="PASS"),
        v7_row(market="xx"),
        v7_row(predicted_at=""),
    ]
    ex = {("2026-11-05", "MIA@GSW"): "postgame predictions"}
    out = from_nba_v7.convert(rows, ex, stake_unit=True)
    kinds = [(e["entry_key"].split(":")[4], e["kind"]) for e in out.entries]
    assert kinds == [("1626164", "prediction"), ("1626164", "paper_bet"),
                     ("1", "prediction"), ("2", "prediction"),
                     ("3", "prediction")]
    first = out.entries[0]
    assert first["predicted_at"] == "2026-11-07T00:07:00+00:00"
    assert first["event_start_utc"] == "2026-11-07T00:10:00+00:00"
    assert first["model_prob"] == 0.142 and first["decision"] == "BET"
    assert out.entries[1]["stake_units"] == 1.0
    fd = out.entries[3]
    assert fd["odds_american"] is None and fd["decision"] == "PASS"
    assert "price_dropped_settlement_mismatch" in fd["data_flags"]
    assert "excluded_by_v7" in out.entries[4]["data_flags"]
    assert out.skipped == {
        "price dropped: book settles on the other market": 1,
        "row missing market, date, game or player": 1,
        "no usable predicted_at": 1}
    # results for every graded row, closing price only where captured
    assert len(out.results) == 5
    assert [p["entry_key"].split(":")[4] for p in out.prices] == [
        "1626164", "1626164", "3"]


def test_nba_v7_end_to_end(client):  # noqa: F811
    rows = [v7_row(), v7_row(personId="1", player="P Two", decision="PASS",
                             bet="0", won="0"),
            v7_row(date="2026-11-05", game="MIA@GSW", personId="3",
                   player="P Four", bet="0", decision="PASS")]
    out = from_nba_v7.convert(
        rows, {("2026-11-05", "MIA@GSW"): "postgame"}, stake_unit=True)
    totals = C.post_all(out, "http://testserver", KEY, session=client)
    assert totals["entries"]["created"] == 4
    again = C.post_all(out, "http://testserver", KEY, session=client)
    assert again["entries"] == {"created": 0, "duplicates": 4}
    assert again["results"] == {"created": 0, "duplicates": 4}
    p = client.get("/ledger/performance", headers=RH).json()["performance"]
    pred = p["prediction"]["all"]
    assert pred["entries"] == 2
    assert pred["excluded"] == {"excluded by source": 1}
    paper = p["paper_bet"]["all"]["betting"]
    assert paper["staked"] == 1 and paper["units"] == 6.5
    clv = p["paper_bet"]["all"]["clv"]
    assert clv["mean_price_clv"] == round(100 / 700 - 100 / 750, 4)


def nfl_bet(**kw):
    b = {"id": "2026_w08_td_00-0036", "type": "Anytime TD",
         "sel": "J. Player anytime TD", "g": "KC@BUF", "tm": "BUF",
         "pid": "00-0036", "n": "J. Player", "side": "Yes", "line": None,
         "odds": 180.0, "book": "DraftKings", "bk_code": "DK",
         "price_ts": "2026-11-01 10:00", "stake": 1.0, "mp": 38.5,
         "tier": "GOOD", "season": 2026, "week": 8, "run": "r1",
         "ts": "2026-11-01 10:00", "ts_utc": "2026-11-01T15:00:00+00:00",
         "date": "2026-11-01", "paper": True, "res": "W", "codds": 150,
         "cline": None, "model_version": "v39"}
    b.update(kw)
    return b


def test_nfl_conversion(client):  # noqa: F811
    bets = [
        nfl_bet(),
        nfl_bet(id="2026_w08_tot_KC@BUF_Over", type="Total", sel="Over 47.5",
                side="Over", line=47.5, odds=-110, stake=0.0, mp=53.0,
                res="L", codds=-110, cline=49.5, tier="totals lean"),
        nfl_bet(id="2026_w08_pr_x", type="Player Prop", res="P",
                note="did not play – void", line=60.5, side="Over"),
        nfl_bet(id="2026_w08_odd", type="Parlay"),
    ]
    out = from_nfl_paper.convert(bets, {"KC@BUF": "2026-11-01T18:00:00Z"})
    assert out.skipped == {"unknown bet type 'Parlay'": 1}
    td, tot, prop = out.entries
    assert td["decision"] == "BET" and td["model_prob"] == 0.385
    assert tot["decision"] == "PASS" and tot["stake_units"] == 0
    assert [r["outcome"] for r in out.results] == ["win", "loss", "void"]
    C.post_all(out, "http://testserver", KEY, session=client)
    p = client.get("/ledger/performance",
                   headers=RH).json()["performance"]["paper_bet"]["all"]
    assert p["betting"]["staked"] == 2
    assert p["betting"]["units"] == 1.8   # +180 win; the void returns 0
    assert p["clv"]["mean_line_clv_pts"] == 2.0  # Over 47.5 closed 49.5


def test_nfl_without_kickoff_is_stored_not_counted(client):  # noqa: F811
    out = from_nfl_paper.convert([nfl_bet()])
    assert "no_kickoff_time" in out.entries[0]["data_flags"]
    assert out.skipped == {"closing price dropped: no kickoff time": 1}
    C.post_all(out, "http://testserver", KEY, session=client)
    p = client.get("/ledger/performance",
                   headers=RH).json()["performance"]["paper_bet"]["all"]
    assert p["entries"] == 0 and p["excluded"] == {"no start time": 1}


def test_csv_import_for_cfb(client):  # noqa: F811
    rows = [{
        "entry_key": "cfb:2026:w10:UGA@BAMA:spread", "kind": "paper_bet",
        "sport": "cfb", "market": "cfb_spread", "event_date": "2026-11-07",
        "event_label": "UGA@BAMA", "selection": "UGA", "side": "UGA",
        "line": "3.5", "model_prob": "0.55", "model_version": "scanner-v2",
        "decision": "BET", "odds_american": "-110", "book": "DraftKings",
        "stake_units": "1", "predicted_at": "2026-11-05T14:00:00Z",
        "event_start_utc": "2026-11-07T20:30:00Z", "source": "cfb pipeline",
        "outcome": "win", "settled_at": "2026-11-08T00:00:00Z",
        "open_line": "4.5", "close_line": "2.5", "close_odds": "-115",
    }, {"entry_key": "bad", "kind": "prediction"}]
    out = from_csv.convert(rows)
    assert len(out.entries) == 1 and len(out.prices) == 2
    assert list(out.skipped) == [
        "missing sport, market, event_date, event_label, selection, "
        "model_version, predicted_at, source"]
    C.post_all(out, "http://testserver", KEY, session=client)
    p = client.get("/ledger/performance", headers=RH,
                   params={"sport": "cfb"}).json()["performance"]
    b = p["paper_bet"]["all"]
    assert b["betting"]["units"] == round(100 / 110, 4)
    assert b["clv"]["mean_line_clv_pts"] == 1.0
