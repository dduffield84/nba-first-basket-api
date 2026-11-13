"""Import any sport's records from a CSV in the ledger's own columns.

This is the route for the college football pipeline (and anything else)
until a dedicated importer exists: export one row per prediction or bet
with these columns. Blank cells stay blank; nothing is filled in.

Required: entry_key, kind, sport, market, event_date, event_label,
          selection, model_version, predicted_at, source
Optional: event_id, home, away, event_start_utc, selection_id, side, line,
          model_prob, market_prob, decision, odds_american, book, price_at,
          stake_units, source_ref, linked_entry_key, data_flags (use ;)
Results:  outcome (win/loss/push/void), actual_value, settled_at
Prices:   open_odds, open_line, open_at, close_odds, close_line, close_at
          (close_at falls back to event_start_utc and open_at to
          predicted_at; a price with neither time is skipped)

CFB example row (a spread lean that was not bet):
    cfb:2026:w10:UGA@BAMA:spread,prediction,cfb,cfb_spread,2026-11-07,
    UGA@BAMA,UGA,v2,2026-11-05T14:00:00Z,cfb pipeline,...,side=UGA,line=3.5

Usage:
    python scripts/ledger/from_csv.py cfb_ledger_export.csv --dry-run
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402

REQUIRED = ("entry_key", "kind", "sport", "market", "event_date",
            "event_label", "selection", "model_version", "predicted_at",
            "source")
TEXT = ("event_id", "home", "away", "selection_id", "side", "decision",
        "book", "source_ref", "linked_entry_key")
NUM = ("line", "model_prob", "market_prob", "odds_american", "stake_units")
TIME = ("event_start_utc", "price_at")


def convert(rows):
    out = C.Out()
    for r in rows:
        missing = [c for c in REQUIRED if C.blank(r.get(c))]
        if missing:
            out.skip("missing " + ", ".join(missing))
            continue
        e = {c: C.text(r[c]) for c in REQUIRED}
        e["predicted_at"] = C.iso_utc(r["predicted_at"]) or r["predicted_at"]
        for c in TEXT:
            if not C.blank(r.get(c)):
                e[c] = C.text(r[c])
        for c in NUM:
            if C.num(r.get(c)) is not None:
                e[c] = C.num(r[c])
        for c in TIME:
            if C.iso_utc(r.get(c)):
                e[c] = C.iso_utc(r[c])
        if not C.blank(r.get("data_flags")):
            e["data_flags"] = [f.strip() for f in r["data_flags"].split(";")
                               if f.strip()]
        out.entries.append(e)
        key = e["entry_key"]
        if not C.blank(r.get("outcome")):
            settled = C.iso_utc(r.get("settled_at"))
            if not settled:
                out.skip("result without settled_at")
            else:
                out.results.append({
                    "entry_key": key, "outcome": C.text(r["outcome"]).lower(),
                    "actual_value": C.num(r.get("actual_value")),
                    "settled_at": settled, "source": e["source"]})
        for kind in ("open", "close"):
            o, ln = (C.valid_odds(r.get(f"{kind}_odds")),
                     C.num(r.get(f"{kind}_line")))
            at = C.iso_utc(r.get(f"{kind}_at")) or (
                e.get("event_start_utc") if kind == "close"
                else e["predicted_at"])
            if o is None and ln is None:
                continue
            if not at:
                out.skip(f"{kind} price without a time")
                continue
            out.prices.append({
                "entry_key": key, "price_type": kind, "odds_american": o,
                "line": ln, "book": e.get("book"), "observed_at": at,
                "source": e["source"]})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("csv")
    C.add_common_args(p)
    args = p.parse_args()
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    C.finish(convert(rows), args)


if __name__ == "__main__":
    main()
