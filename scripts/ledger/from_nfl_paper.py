"""Import NFL paper bets (nfl_builder.py v38+) into the shared ledger.

Reads the paper-bet JSON files the builder writes
(NFL_Week<N>_paper_bets_<run>.json, a list) or the graded copies the Bet
Tracker keeps (tracker/**/paper_bets/*.json, one bet per file). Read-only.

Every recorded bet becomes a paper_bet entry. A bet with a positive stake
is a BET; a zero-stake lean (for example "totals lean") is recorded as
PASS, so it is tracked but never staked.

The builder's records carry no kickoff time, so pass --kickoffs with a
JSON map {"AWAY@HOME": "2026-11-09T18:00:00Z", ...}. Without a kickoff an
entry is still stored but does not count, because it cannot be shown to
be pregame.

Usage:
    python scripts/ledger/from_nfl_paper.py out/NFL_Week9_paper_bets_*.json \
        --kickoffs kickoffs_w9.json --dry-run
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

import common as C  # noqa: E402

MARKET = {
    "Anytime TD": "nfl_anytime_td",
    "First TD": "nfl_first_td",
    "2+ TDs": "nfl_2plus_td",
    "3+ TDs": "nfl_3plus_td",
    "Player Prop": "nfl_player_prop",
    "Spread": "nfl_spread",
    "Total": "nfl_total",
    "Moneyline": "nfl_moneyline",
}
RESULT = {"W": "win", "L": "loss", "P": "push"}
SOURCE = "nfl_builder.py paper bets"


def load(paths):
    rows = []
    for pattern in paths:
        for path in sorted(glob.glob(pattern, recursive=True)):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rows.extend(data if isinstance(data, list) else [data])
    # graded copies and the original file can both be given: graded wins
    best = {}
    for b in rows:
        if not isinstance(b, dict) or not b.get("id"):
            continue
        old = best.get(b["id"])
        if old is None or (old.get("res") in (None, "Pending")
                           and b.get("res") not in (None, "Pending")):
            best[b["id"]] = b
    return list(best.values())


def convert(bets, kickoffs=None):
    out = C.Out()
    kickoffs = kickoffs or {}
    for b in bets:
        market = MARKET.get(b.get("type"))
        if not market:
            out.skip(f"unknown bet type {b.get('type')!r}")
            continue
        game = C.text(b.get("g"))
        predicted = C.iso_utc(b.get("ts_utc")) or C.iso_utc(b.get("ts"))
        date = C.text(b.get("date"))
        if not (game and predicted and date):
            out.skip("missing game, time or date")
            continue
        flags = []
        if not C.iso_utc(b.get("ts_utc")):
            flags.append("predicted_at_from_local_capture_time")
        kickoff = C.iso_utc(kickoffs.get(game))
        if not kickoff:
            flags.append("no_kickoff_time")
        away, home = game.split("@", 1) if "@" in game else (None, None)
        odds = C.valid_odds(b.get("odds"))
        stake = C.num(b.get("stake")) or 0.0
        decision = "BET" if stake > 0 and odds is not None else "PASS"
        if stake > 0 and odds is None:
            flags.append("bet_without_price_recorded_as_pass")
        mp = C.num(b.get("mp"))
        mp = mp / 100 if mp is not None and 1 < mp <= 100 else mp
        if mp is not None and not 0 <= mp <= 1:
            mp = None
        if b.get("note"):
            flags.append("note_" + C.text(b["note"]).split()[0].lower()[:20])
        key = f"nfl:{b['id']}"
        out.entries.append({
            "entry_key": key, "kind": "paper_bet", "sport": "nfl",
            "market": market, "event_date": date, "event_label": game,
            "home": home, "away": away, "event_start_utc": kickoff,
            "selection": C.text(b.get("sel")) or C.text(b.get("n")) or game,
            "selection_id": C.text(b.get("pid")),
            "side": C.text(b.get("side")), "line": C.num(b.get("line")),
            "model_prob": mp, "model_version":
                C.text(b.get("model_version")) or "unknown",
            "decision": decision, "odds_american": odds,
            "book": C.text(b.get("book")) if odds is not None else None,
            "price_at": C.iso_utc(b.get("price_ts")),
            "stake_units": stake if decision == "BET" else 0.0,
            "predicted_at": predicted, "source": SOURCE,
            "source_ref": C.text(b.get("run")), "data_flags": flags,
        })
        res = b.get("res")
        if res in RESULT:
            outcome = RESULT[res]
            if res == "P" and "did not play" in str(b.get("note", "")):
                outcome = "void"
            out.results.append({
                "entry_key": key, "outcome": outcome,
                "actual_value": C.num(b.get("actual")),
                "settled_at": kickoff or predicted,
                "source": "nfl_builder.py weekly grading (nflverse)"})
        co, cl = C.valid_odds(b.get("codds")), C.num(b.get("cline"))
        if (co is not None or cl is not None) and kickoff:
            out.prices.append({
                "entry_key": key, "price_type": "close", "odds_american": co,
                "line": cl, "book": C.text(b.get("book")),
                "observed_at": kickoff,
                "source": "nfl_builder.py last pre-kickoff snapshot"})
        elif co is not None or cl is not None:
            out.skip("closing price dropped: no kickoff time")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("paths", nargs="+", help="paper-bet JSON files or globs")
    p.add_argument("--kickoffs", help="JSON map of AWAY@HOME to kickoff")
    C.add_common_args(p)
    args = p.parse_args()
    kick = {}
    if args.kickoffs:
        with open(args.kickoffs, encoding="utf-8") as f:
            kick = json.load(f)
    bets = load(args.paths)
    if not bets:
        sys.exit("no paper bets found")
    C.finish(convert(bets, kick), args)


if __name__ == "__main__":
    main()
