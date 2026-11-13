"""Import the NBA V7 results ledger into the shared ledger.

Reads output/results_ledger.csv (and output/ledger_exclusions.csv when
present) from the NBA V7 folder. Read-only: the V7 files are not changed.

Per ledger row (one player, one market, one game):
- a prediction entry with the model probability, the price shown, the
  BET/PASS decision and the times;
- a paper_bet entry, linked to it, only when V7 staked it (decision BET
  with a price), matching V7's own rule;
- the result (won 1/0) and, when V7 captured one, the closing price.

Games in the V7 exclusion list are imported with the flag excluded_by_v7
so they stay visible but never count.

A first-basket price from a book whose settlement rule conflicts with the
market (the Stage 1 FanDuel/first-FG routing bug) is dropped from the row,
which keeps the prediction, and counted in the summary.

Usage:
    python scripts/ledger/from_nba_v7.py C:\\NBA_MODEL\\...\\NBA_Complete_V7 --dry-run
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

import common as C  # noqa: E402
import ledger_db as L  # noqa: E402

MARKET = {"fg": "nba_first_fg", "pt": "nba_first_point"}
SOURCE = "NBA V7 fb_engine results_ledger.csv"


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def exclusions(folder):
    rows = read_csv(os.path.join(folder, "output", "ledger_exclusions.csv"))
    return {(r["date"], r["game"]): r.get("reason", "") for r in rows}


def convert(rows, excluded=None, stake_unit=None):
    """rows: dicts from results_ledger.csv. stake_unit: V7 flat_stake, so a
    staked bet is 1 unit; when None the V7 stake value is used as units."""
    out = C.Out()
    excluded = excluded or {}
    for r in rows:
        market = MARKET.get(C.text(r.get("market")))
        date, game = C.text(r.get("date")), C.text(r.get("game"))
        pid = C.text(r.get("personId"))
        player = C.text(r.get("player"))
        if not (market and date and game and player):
            out.skip("row missing market, date, game or player")
            continue
        predicted = C.iso_utc(r.get("predicted_at"))
        if not predicted:
            out.skip("no usable predicted_at")
            continue
        version = C.text(r.get("model_version")) or "unknown"
        key = f"nba:{date}:{game}:{market}:{pid or player}:{version}"
        home, away = C.text(r.get("home")), C.text(r.get("away"))
        if not (home and away) and "@" in game:
            away, home = game.split("@", 1)
        odds, book = C.valid_odds(r.get("open_odds")), C.text(r.get("book"))
        flags = []
        refused, _ = L.nba_book_check(market, book)
        if refused and odds is not None:
            out.skip("price dropped: book settles on the other market")
            flags.append("price_dropped_settlement_mismatch")
            odds = None
        decision = (C.text(r.get("decision")) or "").upper() or None
        if decision not in ("BET", "PASS"):
            decision = None
        if decision == "BET" and odds is None:
            decision = "PASS"
            flags.append("bet_without_price_recorded_as_pass")
        if (date, game) in excluded:
            flags.append("excluded_by_v7")
        if C.text(r.get("lineup_status")):
            flags.append("lineup_" + C.text(r.get("lineup_status"))
                         .lower().replace(" ", "_")[:30])
        base = {
            "sport": "nba", "market": market, "event_date": date,
            "event_label": game, "home": home, "away": away,
            "event_start_utc": C.iso_utc(r.get("tip_utc")),
            "selection": player, "selection_id": pid,
            "model_version": version, "predicted_at": predicted,
            "odds_american": odds, "book": book if odds else None,
            "price_at": C.iso_utc(r.get("odds_time")) if odds else None,
            "source": SOURCE,
            "source_ref": (f"excluded: {excluded[(date, game)]}"[:200]
                           if (date, game) in excluded else None),
            "data_flags": flags,
        }
        mp = C.num(r.get("model_p"))
        if mp is None or not 0 <= mp <= 1:
            out.skip("no usable model_p")
            continue
        out.entries.append({**base, "entry_key": key, "kind": "prediction",
                            "model_prob": mp, "decision": decision})
        keys = [key]
        bet = C.num(r.get("bet")) == 1 and decision == "BET"
        if bet:
            stake = C.num(r.get("stake")) or 0
            units = 1.0 if stake_unit else stake
            if units > 0:
                out.entries.append({
                    **base, "entry_key": key + ":paper", "kind": "paper_bet",
                    "model_prob": mp, "decision": "BET",
                    "stake_units": units, "linked_entry_key": key})
                keys.append(key + ":paper")
        won = C.num(r.get("won"))
        settled = C.iso_utc(r.get("graded_at"))
        for k in keys:
            if won in (0, 1) and settled:
                out.results.append({
                    "entry_key": k, "outcome": "win" if won else "loss",
                    "settled_at": settled,
                    "source": C.text(r.get("source")) or "fb_engine grade"})
            close = C.valid_odds(r.get("close_odds"))
            tip = base["event_start_utc"]
            if close is not None and tip and odds is not None:
                out.prices.append({
                    "entry_key": k, "price_type": "close",
                    "odds_american": close, "book": book,
                    "observed_at": tip,
                    "source": "NBA V7 closing lines (capture time not "
                              "recorded; tip time used)"})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", help="the NBA_Complete_V7 folder")
    p.add_argument("--ledger", help="results ledger CSV (default: "
                   "<folder>/output/results_ledger.csv)")
    C.add_common_args(p)
    args = p.parse_args()
    path = args.ledger or os.path.join(args.folder, "output",
                                       "results_ledger.csv")
    rows = read_csv(path)
    if not rows:
        sys.exit(f"no rows in {path}")
    C.finish(convert(rows, exclusions(args.folder), stake_unit=True), args)


if __name__ == "__main__":
    main()
