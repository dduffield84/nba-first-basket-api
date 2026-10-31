"""Import historical first baskets and starters from NBA.com via nba_api.

Results are merged into data/first_baskets.csv and data/game_starters.csv,
which the API loads on startup. Games already in the CSV are skipped, so
the script can be run repeatedly to backfill a season a chunk at a time.

    python scripts/import_first_baskets.py --season 2025-26 --max-games 200

NBA.com throttles heavily and often refuses cloud IPs; the script backs
off, and stops after several consecutive failures instead of hammering.
With --source auto (the default) it falls back from stats.nba.com to the
cdn.nba.com game feeds, which are usually reachable from the cloud but only
cover the current season.
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

SOURCES = {
    "stats": "nba_api playbyplayv3 + boxscoretraditionalv3",
    "cdn": "cdn.nba.com liveData playbyplay + boxscore",
}
CDN_SCHEDULE_URL = (
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json")


def annotate(level, message):
    """Print a GitHub Actions annotation, visible on the run summary."""
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::{level}::{message}", flush=True)
    else:
        print(message, flush=True)


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
    return (rows_of(pbp.play_by_play),
            firstbasket.parse_starters(rows_of(box.player_stats)))


def fetch_cdn_games(season, season_type, timeout):
    from nba_api.live.nba.library.http import NBALiveHTTP
    req = urllib.request.Request(CDN_SCHEDULE_URL, headers=NBALiveHTTP.headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        schedule = json.load(resp)
    cdn_season, games = firstbasket.parse_cdn_schedule(schedule, season_type)
    if cdn_season and cdn_season != season:
        raise RuntimeError(
            f"cdn.nba.com only has the {cdn_season} schedule, not {season}")
    return games


def fetch_cdn_game(game_id, timeout):
    from nba_api.live.nba.endpoints import boxscore, playbyplay
    pbp = playbyplay.PlayByPlay(game_id=game_id, timeout=timeout)
    box = boxscore.BoxScore(game_id=game_id, timeout=timeout)
    return (pbp.actions.get_dict(),
            firstbasket.parse_live_starters(box.game.get_dict()))


FETCHERS = {
    "stats": (fetch_games, fetch_game),
    "cdn": (fetch_cdn_games, fetch_cdn_game),
}


def build_record(game, season, pbp_rows, starters, source="stats"):
    first = firstbasket.parse_first_basket(pbp_rows)
    if first is None:
        return None, []
    flags = list(first["flags"])
    if len(starters) != 10:
        flags.append(f"found_{len(starters)}_starters")
    tip_team = firstbasket.match_tip_winner(
        first["tip_to"], starters, first["tip_to_id"])
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
        "source": SOURCES[source],
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
    parser.add_argument("--source", default="auto",
                        choices=["auto", "stats", "cdn"],
                        help="auto tries stats.nba.com, then cdn.nba.com")
    parser.add_argument("--post", action="store_true",
                        help="also POST each record to API_URL using "
                             "NBA_API_KEY")
    args = parser.parse_args(argv)

    api_url = os.getenv("API_URL", "")
    api_key = os.getenv("NBA_API_KEY", "")
    if args.post and not (api_url and api_key):
        parser.error("--post needs API_URL and NBA_API_KEY set")

    done = {r["game_id"] for r in db.read_csv(db.FIRST_BASKETS_CSV)}
    sources = ["stats", "cdn"] if args.source == "auto" else [args.source]
    games = None
    for source in sources:
        fetch_schedule, fetch_one = FETCHERS[source]
        print(f"Fetching {args.season} {args.season_type} schedule from "
              f"{source}...", flush=True)
        try:
            games = with_retries(
                lambda: fetch_schedule(
                    args.season, args.season_type, args.timeout),
                3, args.delay)
            break
        except Exception as exc:
            annotate("warning", f"{source} schedule failed: "
                     f"{exc.__class__.__name__}: {exc}")
    if games is None:
        annotate("error", "Could not reach NBA.com for the schedule from any "
                 "source. It is probably blocking this machine.")
        return 1
    pending = [g for g in games if g["game_id"] not in done]
    todo = pending[:args.max_games]
    print(f"{len(games)} completed games, {len(todo)} to import "
          f"(source: {source})", flush=True)

    records, starters, failures, streak = [], [], [], 0
    for i, game in enumerate(todo, 1):
        try:
            pbp_rows, game_starters = with_retries(
                lambda: fetch_one(game["game_id"], args.timeout),
                3, args.delay)
            streak = 0
        except Exception as exc:
            failures.append(game["game_id"])
            streak += 1
            print(f"[{i}/{len(todo)}] {game['game_id']} failed: "
                  f"{exc.__class__.__name__}: {exc}", flush=True)
            if streak >= args.max_consecutive_failures:
                annotate("error", "Too many failures in a row; NBA.com is "
                         f"probably blocking or throttling this machine. "
                         f"Last error: {exc.__class__.__name__}: {exc}")
                break
            continue
        record, game_starters = build_record(
            game, args.season, pbp_rows, game_starters, source)
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
    flagged = sum(1 for r in records if r["data_flags"])
    annotate("notice", f"Imported {len(records)} games from {source} "
             f"({flagged} flagged for review); {len(failures)} failed; "
             f"{len(pending) - len(records)} still to import.")
    # Fail the run only if nothing at all could be fetched.
    return 1 if todo and not records else 0


if __name__ == "__main__":
    sys.exit(main())
