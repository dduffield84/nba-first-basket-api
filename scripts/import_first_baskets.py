"""Import historical first baskets and starters from NBA.com via nba_api.

Results are merged into data/first_baskets.csv and data/game_starters.csv,
which the API loads on startup. Games already in the CSV are skipped, so
the script can be run repeatedly to backfill a season a chunk at a time.

    python scripts/import_first_baskets.py --season 2025-26 --max-games 200

NBA.com throttles heavily and often refuses cloud IPs; the script backs
off, and stops after several consecutive failures instead of hammering.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import firstbasket  # noqa: E402

SOURCE = "nba_api playbyplayv3 + boxscoretraditionalv3"


def current_season(today=None):
    today = today or date.today()
    start = today.year if today.month >= 10 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def rows_of(data_set):
    data = data_set.get_dict()
    return [dict(zip(data["headers"], row)) for row in data["data"]]


def with_retries(fn, attempts, delay):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # network, JSON or throttling errors
            if attempt == attempts:
                raise
            wait = delay * 2 ** attempt
            print(f"  retry {attempt} after {exc.__class__.__name__}; "
                  f"waiting {wait:.0f}s", flush=True)
            time.sleep(wait)


def fetch_games(season, season_type, timeout):
    from nba_api.stats.endpoints import leaguegamelog
    log = leaguegamelog.LeagueGameLog(
        season=season, season_type_all_star=season_type,
        player_or_team_abbreviation="T", timeout=timeout)
    return firstbasket.parse_game_log(rows_of(log.league_game_log))


def fetch_game(game_id, timeout):
    from nba_api.stats.endpoints import boxscoretraditionalv3, playbyplayv3
    pbp = playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=timeout)
    box = boxscoretraditionalv3.BoxScoreTraditionalV3(
        game_id=game_id, timeout=timeout)
    return rows_of(pbp.play_by_play), rows_of(box.player_stats)


def build_record(game, season, pbp_rows, player_rows):
    first = firstbasket.parse_first_basket(pbp_rows)
    if first is None:
        return None, []
    starters = firstbasket.parse_starters(player_rows)
    flags = list(first["flags"])
    if len(starters) != 10:
        flags.append(f"found_{len(starters)}_starters")
    tip_team = firstbasket.match_tip_winner(first["tip_to"], starters)
    if first["tip_to"] and not tip_team:
        flags.append("tip_winner_unmatched")
    if starters and first["player_id"] not in {
            s["player_id"] for s in starters}:
        flags.append("first_scorer_not_a_starter")
    record = {
        "game_id": game["game_id"],
        "game_date": game["game_date"],
        "player_id": first["player_id"],
        "player_name": first["player_name"],
        "team": first["team"],
        "source": SOURCE,
        # Feed data is not independently verified; see README.
        "verified": 0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "tip_winner_team": tip_team,
        "data_flags": ",".join(flags) or None,
    }
    starter_rows = [
        {**s, "game_id": game["game_id"], "game_date": game["game_date"]}
        for s in starters
    ]
    return record, starter_rows


def write_csv(path, fields, rows, key):
    existing = {key(r): r for r in db.read_csv(path)}
    for r in rows:
        existing[key(r)] = r
    ordered = sorted(existing.values(),
                     key=lambda r: (r["game_date"], r["game_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in ordered:
            writer.writerow({k: "" if r.get(k) is None else r[k]
                             for k in fields})


def post_record(api_url, api_key, record, starters):
    body = {k: v for k, v in record.items()
            if k not in ("recorded_at", "data_flags") and v is not None}
    body["verified"] = bool(record["verified"])
    body["data_flags"] = (record["data_flags"] or "").split(",") \
        if record["data_flags"] else []
    body["starters"] = [
        {k: s[k] for k in ("player_id", "player_name", "team")}
        for s in starters
    ]
    req = urllib.request.Request(
        api_url.rstrip("/") + "/first-basket/records",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", default=current_season(),
                        help="e.g. 2025-26 (default: current season)")
    parser.add_argument("--season-type", default="Regular Season",
                        choices=["Regular Season", "Playoffs"])
    parser.add_argument("--max-games", type=int, default=200)
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds to wait between games")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-consecutive-failures", type=int, default=5)
    parser.add_argument("--post", action="store_true",
                        help="also POST each record to API_URL using "
                             "NBA_API_KEY")
    args = parser.parse_args(argv)

    api_url = os.getenv("API_URL", "")
    api_key = os.getenv("NBA_API_KEY", "")
    if args.post and not (api_url and api_key):
        parser.error("--post needs API_URL and NBA_API_KEY set")

    done = {r["game_id"] for r in db.read_csv(db.FIRST_BASKETS_CSV)}
    print(f"Fetching {args.season} {args.season_type} schedule...",
          flush=True)
    games = with_retries(
        lambda: fetch_games(args.season, args.season_type, args.timeout),
        3, args.delay)
    todo = [g for g in games if g["game_id"] not in done][:args.max_games]
    print(f"{len(games)} completed games, {len(todo)} to import", flush=True)

    records, starters, failures, streak = [], [], [], 0
    for i, game in enumerate(todo, 1):
        try:
            pbp_rows, player_rows = with_retries(
                lambda: fetch_game(game["game_id"], args.timeout),
                3, args.delay)
            streak = 0
        except Exception as exc:
            failures.append(game["game_id"])
            streak += 1
            print(f"[{i}/{len(todo)}] {game['game_id']} failed: {exc}",
                  flush=True)
            if streak >= args.max_consecutive_failures:
                print("Too many failures in a row; NBA.com is probably "
                      "blocking or throttling this machine. Stopping.")
                break
            continue
        record, game_starters = build_record(
            game, args.season, pbp_rows, player_rows)
        if record is None:
            print(f"[{i}/{len(todo)}] {game['game_id']}: no made field "
                  "goal found, skipped", flush=True)
            continue
        records.append(record)
        starters.extend(game_starters)
        print(f"[{i}/{len(todo)}] {game['game_date']} {game['game_id']}: "
              f"{record['player_name']} ({record['team']})", flush=True)
        if args.post:
            post_record(api_url, api_key, record, game_starters)
        time.sleep(args.delay)

    write_csv(db.FIRST_BASKETS_CSV, db.FIRST_BASKET_FIELDS, records,
              key=lambda r: r["game_id"])
    write_csv(db.STARTERS_CSV, db.STARTER_FIELDS, starters,
              key=lambda r: (r["game_id"], r["player_id"]))
    print(f"Imported {len(records)} games; {len(failures)} failed.")
    # Fail the run only if nothing at all could be fetched.
    return 1 if todo and not records else 0


if __name__ == "__main__":
    sys.exit(main())
