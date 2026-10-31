"""Pure helpers for turning NBA.com responses into first-basket facts.

Kept free of network calls so they can be tested with fixture data.
"""
import re


def _made(row):
    return str(row.get("shotResult") or "").lower() == "made"


def _is_field_goal(row):
    return str(row.get("isFieldGoal")) in ("1", "True", "true")


def _is_free_throw(row):
    return str(row.get("actionType") or "").lower() == "free throw"


def _ft_made(row):
    # Free-throw rows don't always carry shotResult; a miss says MISS.
    if row.get("shotResult"):
        return _made(row)
    return "miss" not in str(row.get("description") or "").lower()


def parse_first_basket(pbp_rows):
    """Find the first made field goal of a game from PlayByPlayV3 rows.

    Returns a dict, or None when no made field goal is found. "flags"
    lists anything a human should look at before trusting the record.
    """
    rows = sorted(
        (r for r in pbp_rows if r.get("period") is not None),
        key=lambda r: (int(r["period"]), int(r.get("actionNumber") or 0)),
    )
    flags = []
    first_ft = None
    tip_to = None
    for row in rows:
        action = str(row.get("actionType") or "").lower()
        if action == "jump ball" and tip_to is None:
            match = re.search(r"tip to (.+?)\s*$",
                              str(row.get("description") or ""), re.I)
            tip_to = match.group(1).strip() if match else None
        if _is_free_throw(row) and _ft_made(row) and first_ft is None:
            first_ft = row
        if _is_field_goal(row) and _made(row):
            if not row.get("personId") or not row.get("teamTricode"):
                flags.append("first_fg_missing_player_or_team")
            if int(row["period"]) != 1:
                flags.append("first_fg_not_in_first_quarter")
            if first_ft is not None:
                flags.append("free_throw_scored_before_first_fg")
            return {
                "player_id": str(row.get("personId") or ""),
                "player_name": row.get("playerName")
                or row.get("playerNameI") or "",
                "team": str(row.get("teamTricode") or "").upper(),
                "tip_to": tip_to,
                "flags": flags,
            }
    return None


def parse_starters(player_stat_rows):
    """Starters are the players BoxScoreTraditionalV3 lists a position for."""
    starters = []
    for row in player_stat_rows:
        if not str(row.get("position") or "").strip():
            continue
        name = " ".join(
            p for p in (row.get("firstName"), row.get("familyName")) if p
        ) or row.get("nameI") or ""
        starters.append({
            "player_id": str(row["personId"]),
            "player_name": name,
            "team": str(row["teamTricode"]).upper(),
        })
    return starters


def _name_key(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


def match_tip_winner(tip_to, starters):
    """Map the 'Tip to <name>' player to a team using the starters list."""
    if not tip_to:
        return None
    key = _name_key(tip_to)
    for s in starters:
        full = _name_key(s["player_name"])
        last = _name_key(s["player_name"].split(" ")[-1])
        if key and (key == full or key == last or full.endswith(key)):
            return s["team"]
    return None


def parse_game_log(rows):
    """Group LeagueGameLog team rows into one entry per game."""
    games = {}
    for row in rows:
        game = games.setdefault(row["GAME_ID"], {
            "game_id": row["GAME_ID"],
            "game_date": str(row["GAME_DATE"])[:10],
            "season_id": str(row.get("SEASON_ID") or ""),
        })
        team = row["TEAM_ABBREVIATION"]
        if " vs. " in row["MATCHUP"]:
            game["home_team"] = team
        elif " @ " in row["MATCHUP"]:
            game["away_team"] = team
    return sorted(games.values(), key=lambda g: (g["game_date"], g["game_id"]))
