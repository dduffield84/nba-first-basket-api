import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

KEY = "test-key"
# Set TEST_DATABASE_URL to run the suite against Postgres instead of SQLite.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
H = {"X-API-Key": KEY}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_API_KEY", KEY)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import db
    use_test_database(monkeypatch, db)
    monkeypatch.setattr(db, "FIRST_BASKETS_CSV", tmp_path / "fb.csv")
    monkeypatch.setattr(db, "STARTERS_CSV", tmp_path / "st.csv")
    import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def use_test_database(monkeypatch, db):
    if TEST_DATABASE_URL:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        engine = db.make_engine()
        db.metadata.drop_all(engine)
        engine.dispose()
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)


def starters(team, ids):
    return [{"player_id": i, "player_name": f"Player {i}", "team": team}
            for i in ids]


def save_game(client, game_id, date, scorer, team, home, away,
              home_ids, away_ids):
    r = client.post("/first-basket/records", headers=H, json={
        "game_id": game_id, "game_date": date, "player_id": scorer,
        "player_name": f"Player {scorer}", "team": team,
        "source": "test fixture", "season": "2025-26",
        "home_team": home, "away_team": away,
        "starters": starters(home, home_ids) + starters(away, away_ids),
    })
    assert r.status_code == 200, r.text


BOS = ["B1", "B2", "B3", "B4", "B5"]
NYK = ["N1", "N2", "N3", "N4", "N5"]


def test_public_endpoints(client):
    assert client.get("/").json()["version"]
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["database_backend"] == (
        "postgresql" if TEST_DATABASE_URL else "sqlite")


def test_auth(client):
    assert client.get("/first-basket/records").status_code == 401
    assert client.get("/first-basket/records",
                      headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/first-basket/records",
                      headers={b"X-API-Key": b"\xe9"}).status_code == 401
    assert client.get("/predictions/grades").status_code == 401


def test_record_round_trip_and_validation(client):
    save_game(client, "G1", "2025-10-22", "B1", "bos", "BOS", "NYK",
              BOS, NYK)
    body = client.get("/first-basket/records", headers=H).json()
    assert body["count"] == 1
    rec = body["records"][0]
    assert rec["team"] == "BOS" and rec["verified"] is False
    assert rec["data_flags"] == []
    assert client.get("/first-basket/records?team=nyk",
                      headers=H).json()["count"] == 0
    bad = client.post("/first-basket/records", headers=H, json={
        "game_id": "G2", "game_date": "10/22/2025", "player_id": "B1",
        "player_name": "x", "team": "BOS", "source": "test"})
    assert bad.status_code == 422
    assert client.get("/first-basket/records?limit=0",
                      headers=H).status_code == 422
    stats = client.get("/first-basket/stats", headers=H).json()
    assert stats["games"] == 1 and stats["games_with_starters"] == 1


def test_original_probability_endpoint(client):
    r = client.post("/first-basket/probabilities", headers=H, json={
        "candidates": [
            {"player_id": "P1", "player_name": "A",
             "first_basket_count": 12, "eligible_game_count": 60},
            {"player_id": "P2", "player_name": "B",
             "first_basket_count": 8, "eligible_game_count": 40}]})
    assert r.status_code == 200
    shares = [x["normalized_candidate_share"] for x in r.json()["results"]]
    assert abs(sum(shares) - 1) < 1e-5
    bad = client.post("/first-basket/probabilities", headers=H, json={
        "candidates": [{"player_id": "P1", "player_name": "A",
                        "first_basket_count": 70,
                        "eligible_game_count": 60}]})
    assert bad.status_code == 422


def test_matchup_uses_history(client):
    save_game(client, "G1", "2025-10-22", "B1", "BOS", "BOS", "NYK",
              BOS, NYK)
    save_game(client, "G2", "2025-10-24", "B1", "BOS", "NYK", "BOS",
              NYK, BOS)
    save_game(client, "G3", "2025-10-26", "N2", "NYK", "BOS", "NYK",
              BOS, NYK)
    r = client.post("/first-basket/matchup", headers=H, json={
        "home_team": "BOS", "away_team": "NYK", "prior_games": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    results = body["results"]
    assert len(results) == 10
    assert results[0]["player_id"] == "B1"
    assert results[0]["first_baskets"] == 2 and results[0]["starts"] == 3
    assert abs(sum(p["probability"] for p in results) - 1) < 1e-5
    assert body["prediction_id"] is None
    assert "home_starters_from_last_stored_game" in body["data_flags"]


def test_matchup_without_history(client):
    r = client.post("/first-basket/matchup", headers=H, json={
        "home_team": "BOS", "away_team": "NYK"})
    assert r.status_code == 404
    r = client.post("/first-basket/matchup", headers=H, json={
        "home_team": "BOS", "away_team": "NYK",
        "home_starters": BOS, "away_starters": NYK})
    assert r.status_code == 200
    probs = {p["probability"] for p in r.json()["results"]}
    assert probs == {0.1}
    assert client.post("/first-basket/matchup", headers=H, json={
        "home_team": "BOS", "away_team": "bos"}).status_code == 422
    assert client.post("/first-basket/matchup", headers=H, json={
        "home_team": "BOS", "away_team": "NYK",
        "save_prediction": True}).status_code == 422


def test_prediction_ledger_and_grades(client):
    tip = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    save_game(client, "G0", "2025-10-20", "B1", "BOS", "BOS", "NYK",
              BOS, NYK)
    r = client.post("/first-basket/matchup", headers=H, json={
        "home_team": "BOS", "away_team": "NYK", "save_prediction": True,
        "game_id": "G9", "game_date": "2025-11-01", "tip_time": tip})
    assert r.status_code == 200
    pid = r.json()["prediction_id"]
    assert pid

    # A manual prediction made after tip-off is excluded from grading.
    late = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post("/predictions", headers=H, json={
        "game_id": "G9", "game_date": "2025-11-01", "model": "manual",
        "tip_time": late,
        "candidates": [{"player_id": "N1", "player_name": "x",
                        "team": "NYK", "probability": 0.5}]})
    assert r.status_code == 200

    grades = client.get("/predictions/grades", headers=H).json()
    assert grades["awaiting_result"] == 1
    assert grades["excluded_made_after_tip"] == 1

    save_game(client, "G9", "2025-11-01", "B1", "BOS", "BOS", "NYK",
              BOS, NYK)
    preds = client.get("/predictions", headers=H).json()["predictions"]
    graded = next(p for p in preds if p["prediction_id"] == pid)
    assert graded["made_before_tip"] is True
    assert graded["grade"]["top_pick_hit"] is True
    grades = client.get("/predictions/grades", headers=H).json()
    summary = grades["models"]["starter_history_rate_v1"]
    assert summary["graded_games"] == 1
    assert summary["top_pick_hit_rate"] == 1.0
    assert "manual" not in grades["models"]


def test_prediction_validation(client):
    base = {"game_id": "G1", "game_date": "2025-11-01", "model": "m"}
    over = client.post("/predictions", headers=H, json={**base, "candidates": [
        {"player_id": "A", "player_name": "A", "team": "BOS",
         "probability": 0.7},
        {"player_id": "B", "player_name": "B", "team": "BOS",
         "probability": 0.7}]})
    assert over.status_code == 422
    dup = client.post("/predictions", headers=H, json={**base, "candidates": [
        {"player_id": "A", "player_name": "A", "team": "BOS",
         "probability": 0.2},
        {"player_id": "A", "player_name": "A", "team": "BOS",
         "probability": 0.2}]})
    assert dup.status_code == 422


def test_csv_seed(tmp_path, monkeypatch):
    import db
    fb = tmp_path / "fb.csv"
    st = tmp_path / "st.csv"
    fb.write_text(
        ",".join(db.FIRST_BASKET_FIELDS) + "\n"
        "G1,2025-10-22,B1,Player B1,BOS,csv,0,2025-10-23T00:00:00,"
        "2025-26,BOS,NYK,BOS,\n")
    st.write_text(",".join(db.STARTER_FIELDS) + "\n"
                  "G1,B1,Player B1,BOS,2025-10-22\n")
    monkeypatch.setenv("NBA_API_KEY", KEY)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "seed.db"))
    use_test_database(monkeypatch, db)
    monkeypatch.setattr(db, "FIRST_BASKETS_CSV", fb)
    monkeypatch.setattr(db, "STARTERS_CSV", st)
    import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        recs = c.get("/first-basket/records", headers=H).json()["records"]
        assert [r["game_id"] for r in recs] == ["G1"]
        assert recs[0]["tip_winner_team"] == "BOS"
    # Restarting must not duplicate or overwrite.
    with TestClient(main.app) as c:
        assert c.get("/first-basket/stats", headers=H).json()["games"] == 1


def test_unset_key_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_API_KEY", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        assert c.get("/first-basket/records",
                     headers=H).status_code == 503
