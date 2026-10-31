import sys
from datetime import date
from pathlib import Path

import firstbasket
import model

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_first_baskets as importer  # noqa: E402


def pbp(n, action, period=1, fg=0, result="", pid=0, name="", team="",
        desc=""):
    return {"actionNumber": n, "period": period, "actionType": action,
            "isFieldGoal": fg, "shotResult": result, "personId": pid,
            "playerName": name, "teamTricode": team, "description": desc}


PBP = [
    pbp(4, "Missed Shot", fg=1, result="Missed", pid=2, name="Brunson",
        team="NYK"),
    pbp(2, "Jump Ball", desc="Jump Ball Horford vs. Robinson: Tip to Tatum"),
    pbp(7, "Made Shot", fg=1, result="Made", pid=1, name="Tatum",
        team="BOS"),
    pbp(9, "Made Shot", fg=1, result="Made", pid=2, name="Brunson",
        team="NYK"),
    pbp(1, "period", desc="Period Start"),
]

BOX = [
    {"personId": 1, "firstName": "Jayson", "familyName": "Tatum",
     "teamTricode": "BOS", "position": "F"},
    {"personId": 3, "firstName": "Al", "familyName": "Horford",
     "teamTricode": "bos", "position": "C"},
    {"personId": 2, "firstName": "Jalen", "familyName": "Brunson",
     "teamTricode": "NYK", "position": "G"},
    {"personId": 9, "firstName": "Bench", "familyName": "Guy",
     "teamTricode": "NYK", "position": ""},
]


def test_first_basket_is_first_made_field_goal_in_order():
    first = firstbasket.parse_first_basket(PBP)
    assert first["player_id"] == "1"
    assert first["team"] == "BOS"
    assert first["tip_to"] == "Tatum"
    assert first["flags"] == []


def test_free_throw_before_field_goal_is_flagged():
    rows = PBP + [pbp(5, "Free Throw", pid=2, name="Brunson", team="NYK",
                      desc="Brunson Free Throw 1 of 2 (1 PTS)")]
    first = firstbasket.parse_first_basket(rows)
    assert first["player_id"] == "1"
    assert "free_throw_scored_before_first_fg" in first["flags"]
    missed = PBP + [pbp(5, "Free Throw", pid=2, team="NYK",
                        desc="MISS Brunson Free Throw 1 of 2")]
    assert firstbasket.parse_first_basket(missed)["flags"] == []


def test_no_made_field_goal():
    assert firstbasket.parse_first_basket(PBP[:2]) is None


def test_starters_and_tip_winner():
    starters = firstbasket.parse_starters(BOX)
    assert [s["player_id"] for s in starters] == ["1", "3", "2"]
    assert starters[1]["team"] == "BOS"
    assert firstbasket.match_tip_winner("Tatum", starters) == "BOS"
    assert firstbasket.match_tip_winner("Jalen Brunson", starters) == "NYK"
    assert firstbasket.match_tip_winner("Nobody", starters) is None
    assert firstbasket.match_tip_winner(None, starters) is None


def test_game_log_grouping():
    games = firstbasket.parse_game_log([
        {"GAME_ID": "002", "GAME_DATE": "2025-10-24", "SEASON_ID": "22025",
         "TEAM_ABBREVIATION": "NYK", "MATCHUP": "NYK @ BOS"},
        {"GAME_ID": "002", "GAME_DATE": "2025-10-24", "SEASON_ID": "22025",
         "TEAM_ABBREVIATION": "BOS", "MATCHUP": "BOS vs. NYK"},
        {"GAME_ID": "001", "GAME_DATE": "2025-10-22", "SEASON_ID": "22025",
         "TEAM_ABBREVIATION": "LAL", "MATCHUP": "LAL vs. GSW"},
    ])
    assert [g["game_id"] for g in games] == ["001", "002"]
    assert games[1]["home_team"] == "BOS"
    assert games[1]["away_team"] == "NYK"


def test_build_record_flags():
    game = {"game_id": "002", "game_date": "2025-10-24",
            "home_team": "BOS", "away_team": "NYK"}
    record, starters = importer.build_record(
        game, "2025-26", PBP, firstbasket.parse_starters(BOX))
    assert record["player_name"] == "Tatum"
    assert record["tip_winner_team"] == "BOS"
    assert record["verified"] == 0
    assert record["data_flags"] == "found_3_starters"
    assert len(starters) == 3 and starters[0]["game_id"] == "002"


def test_current_season():
    assert importer.current_season(date(2025, 11, 1)) == "2025-26"
    assert importer.current_season(date(2026, 3, 1)) == "2025-26"
    assert importer.current_season(date(2026, 10, 30)) == "2026-27"


def test_grading():
    cands = [{"player_id": "A", "probability": 0.5},
             {"player_id": "B", "probability": 0.3}]
    hit = model.grade(cands, "A")
    assert hit["top_pick_hit"] and hit["actual_in_candidates"]
    assert abs(hit["log_loss"] - 0.693147) < 1e-5
    # 0.25 + 0.09 + "other" bucket 0.2 -> 0.04
    assert abs(hit["brier"] - 0.38) < 1e-6
    miss = model.grade(cands, "Z")
    assert not miss["actual_in_candidates"]
    assert abs(miss["probability_of_actual"] - 0.2) < 1e-6
    summary = model.summarize([hit, miss])
    assert summary["graded_games"] == 2
    assert summary["top_pick_hit_rate"] == 0.5
    assert model.summarize([]) == {"graded_games": 0}


def test_importer_end_to_end(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "FIRST_BASKETS_CSV", tmp_path / "fb.csv")
    monkeypatch.setattr(db, "STARTERS_CSV", tmp_path / "st.csv")
    games = [{"game_id": "001", "game_date": "2025-10-22",
              "home_team": "BOS", "away_team": "NYK"},
             {"game_id": "002", "game_date": "2025-10-24",
              "home_team": "NYK", "away_team": "BOS"}]
    calls = []

    def fake_fetch(game_id, timeout):
        calls.append(game_id)
        return PBP, firstbasket.parse_starters(BOX)
    monkeypatch.setitem(importer.FETCHERS, "stats",
                        (lambda *a: games, fake_fetch))

    assert importer.main(["--season", "2025-26", "--delay", "0"]) == 0
    rows = db.read_csv(db.FIRST_BASKETS_CSV)
    assert [r["game_id"] for r in rows] == ["001", "002"]
    assert len(db.read_csv(db.STARTERS_CSV)) == 6

    # A second run skips games already in the CSV.
    calls.clear()
    assert importer.main(["--season", "2025-26", "--delay", "0"]) == 0
    assert calls == []
    assert len(db.read_csv(db.FIRST_BASKETS_CSV)) == 2


def test_importer_stops_when_blocked(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "FIRST_BASKETS_CSV", tmp_path / "fb.csv")
    monkeypatch.setattr(db, "STARTERS_CSV", tmp_path / "st.csv")
    games = [{"game_id": f"00{i}", "game_date": "2025-10-22"}
             for i in range(9)]
    monkeypatch.setitem(importer.FETCHERS, "stats",
                        (lambda *a: games, None))
    calls = []

    def blocked(game_id, timeout):
        calls.append(game_id)
        raise ConnectionError("blocked")
    monkeypatch.setitem(importer.FETCHERS, "stats",
                        (lambda *a: games, blocked))
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)
    assert importer.main(["--delay", "0", "--source", "stats",
                          "--max-consecutive-failures", "2"]) == 1
    assert len(calls) == 2 * 3  # two games, three attempts each


CDN_PBP = [
    {"actionNumber": 4, "period": 1, "actionType": "jumpball",
     "isFieldGoal": 0, "personId": 1629684, "teamTricode": "BOS",
     "jumpBallRecoverdPersonId": 1629684,
     "description": "Jump Ball T. Thompson vs. N. Vucevic: Tip to G. Williams"},
    {"actionNumber": 7, "period": 1, "actionType": "freethrow",
     "isFieldGoal": 0, "shotResult": "Made", "personId": 202696,
     "teamTricode": "ORL", "description": "N. Vucevic Free Throw 1 of 1"},
    {"actionNumber": 9, "period": 1, "actionType": "3pt", "isFieldGoal": 1,
     "shotResult": "Made", "personId": 1627759, "playerName": "Brown",
     "playerNameI": "J. Brown", "teamTricode": "BOS"},
]

CDN_BOX = {
    "homeTeam": {"teamTricode": "BOS", "players": [
        {"personId": 1627759, "name": "Jaylen Brown", "starter": "1"},
        {"personId": 1629684, "name": "Grant Williams", "starter": "1"},
        {"personId": 1, "name": "Bench Guy", "starter": "0"}]},
    "awayTeam": {"teamTricode": "ORL", "players": [
        {"personId": 202696, "name": "Nikola Vucevic", "starter": "1"}]},
}


def test_cdn_feed_parsing():
    first = firstbasket.parse_first_basket(CDN_PBP)
    assert first["player_id"] == "1627759" and first["team"] == "BOS"
    assert first["tip_to_id"] == "1629684"
    assert first["flags"] == ["free_throw_scored_before_first_fg"]
    starters = firstbasket.parse_live_starters(CDN_BOX)
    assert [s["player_id"] for s in starters] == [
        "1627759", "1629684", "202696"]
    assert starters[2]["team"] == "ORL"
    assert firstbasket.match_tip_winner(
        first["tip_to"], starters, first["tip_to_id"]) == "BOS"
    # Without the id, "G. Williams" still matches by surname.
    assert firstbasket.match_tip_winner("G. Williams", starters) == "BOS"


def test_surname_match_must_be_unambiguous():
    starters = [
        {"player_id": "1", "player_name": "Jalen Williams", "team": "OKC"},
        {"player_id": "2", "player_name": "Grant Williams", "team": "CHA"},
    ]
    assert firstbasket.match_tip_winner("G. Williams", starters) is None


def test_cdn_schedule():
    season, games = firstbasket.parse_cdn_schedule({"leagueSchedule": {
        "seasonYear": "2026-27", "gameDates": [{"games": [
            {"gameId": "0022600002", "gameStatus": 3,
             "gameDateEst": "2026-10-21T00:00:00Z",
             "homeTeam": {"teamTricode": "LAL"},
             "awayTeam": {"teamTricode": "GSW"}},
            {"gameId": "0022600003", "gameStatus": 1,
             "gameDateEst": "2026-11-01T00:00:00Z"},
            {"gameId": "0012600001", "gameStatus": 3,
             "gameDateEst": "2026-10-05T00:00:00Z"},
        ]}]}})
    assert season == "2026-27"
    assert games == [{"game_id": "0022600002", "game_date": "2026-10-21",
                      "home_team": "LAL", "away_team": "GSW"}]


def test_auto_falls_back_to_cdn(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "FIRST_BASKETS_CSV", tmp_path / "fb.csv")
    monkeypatch.setattr(db, "STARTERS_CSV", tmp_path / "st.csv")
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)

    def blocked(*a):
        raise TimeoutError("stats.nba.com timed out")
    games = [{"game_id": "0022600002", "game_date": "2026-10-21",
              "home_team": "BOS", "away_team": "ORL"}]
    monkeypatch.setitem(importer.FETCHERS, "stats", (blocked, blocked))
    monkeypatch.setitem(importer.FETCHERS, "cdn", (
        lambda *a: games,
        lambda gid, t: (CDN_PBP, firstbasket.parse_live_starters(CDN_BOX))))
    assert importer.main(["--season", "2026-27", "--delay", "0"]) == 0
    rows = db.read_csv(db.FIRST_BASKETS_CSV)
    assert rows[0]["player_id"] == "1627759"
    assert rows[0]["source"].startswith("cdn.nba.com")
    assert rows[0]["tip_winner_team"] == "BOS"
