"""Shared helpers for the ledger importers.

Each importer turns one sport's existing records into three lists that
match the ledger API: entries, prices and results. Nothing is invented:
a value the source does not have stays empty, and anything the importer
had to leave out is counted in `skipped` with the reason.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

BATCH = 500


def blank(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip().lower() in ("", "nan", "none",
                                                        "null")


def num(v):
    if blank(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def text(v):
    return None if blank(v) else str(v).strip()


def iso_utc(v):
    """ISO time in UTC, or None. A time without a zone is read as UTC,
    the same rule NBA V7 uses."""
    if blank(v):
        return None
    s = str(v).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def valid_odds(v):
    o = num(v)
    return o if o is not None and abs(o) >= 100 else None


class Out:
    def __init__(self):
        self.entries, self.prices, self.results = [], [], []
        self.skipped = {}

    def skip(self, why, n=1):
        self.skipped[why] = self.skipped.get(why, 0) + n

    def summary(self):
        kinds = {}
        for e in self.entries:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return {"entries": len(self.entries), "by_kind": kinds,
                "prices": len(self.prices), "results": len(self.results),
                "skipped": self.skipped}


def post_all(out, url, key, session=None):
    """Send to the API in batches: entries first, then prices and results.
    Re-running is safe; repeats come back as duplicates."""
    import requests
    s = session or requests.Session()
    totals = {}
    for path, field, rows in (("entries", "entries", out.entries),
                              ("prices", "prices", out.prices),
                              ("results", "results", out.results)):
        for i in range(0, len(rows), BATCH):
            r = s.post(f"{url.rstrip('/')}/ledger/{path}",
                       headers={"X-API-Key": key},
                       json={field: rows[i:i + BATCH]}, timeout=60)
            if r.status_code != 200:
                raise SystemExit(f"{path} batch {i // BATCH + 1} refused "
                                 f"({r.status_code}): {r.text[:500]}")
            for k, v in r.json().items():
                if isinstance(v, int):
                    t = totals.setdefault(path, {})
                    t[k] = t.get(k, 0) + v
    return totals


def finish(out, args):
    """Print the summary, write JSON if asked, and post unless dry run."""
    print(json.dumps(out.summary(), indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"entries": out.entries, "prices": out.prices,
                       "results": out.results}, f, indent=1)
        print("wrote", args.out)
    if args.dry_run:
        print("dry run: nothing sent")
        return
    url = os.getenv("EDGE_LEDGER_URL")
    key = os.getenv("NBA_API_KEY")
    if not url or not key:
        sys.exit("Set EDGE_LEDGER_URL and NBA_API_KEY (never paste the key "
                 "into a file), or use --dry-run.")
    print(json.dumps(post_all(out, url, key), indent=1))


def add_common_args(p):
    p.add_argument("--dry-run", action="store_true",
                   help="convert and summarise only; send nothing")
    p.add_argument("--out", help="also write the converted rows as JSON")
