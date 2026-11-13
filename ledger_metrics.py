"""Performance figures for the shared ledger. Pure functions, no database.

Rules (they follow the NBA V7 safeguards and the Edge Intelligence plan):

- Only rows with pregame_verified == 1 count. Others are reported as
  excluded with a reason, never silently dropped. A row whose source
  excluded it (a data flag starting with "excluded") never counts.
- Predictions, paper bets and real bets are summarised separately.
- A bet is staked only when its decision is BET, it has a price and a
  positive stake. Real bets are always explicit, so they need only the
  price and stake.
- CLV uses the raw implied probability of the closing price minus that of
  the price taken (positive means the price taken was better than the
  close). Line CLV is in points, for spreads, totals and props.
"""
import math

MIN_P = 1e-6

ODDS_BANDS = (
    ("-200 or shorter", None, -200),
    ("-199 to -101", -199, -101),
    ("-100 to +199", -100, 199),
    ("+200 to +499", 200, 499),
    ("+500 to +999", 500, 999),
    ("+1000 or longer", 1000, None),
)


def implied(odds):
    if odds is None:
        return None
    o = float(odds)
    if -100 < o < 100:
        return None
    return -o / (-o + 100) if o < 0 else 100 / (o + 100)


def decimal_odds(odds):
    o = float(odds)
    return 1 + (o / 100 if o > 0 else 100 / -o)


def fair_american(p):
    if p is None or p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def expected_value(p, odds):
    """EV per unit staked at these odds, given model probability p."""
    if p is None or implied(odds) is None:
        return None
    return p * (decimal_odds(odds) - 1) - (1 - p)


def odds_band(odds):
    if implied(odds) is None:
        return "no price"
    o = float(odds)
    for label, lo, hi in ODDS_BANDS:
        if (lo is None or o >= lo) and (hi is None or o <= hi):
            return label
    return "no price"


def is_staked(e):
    if implied(e.get("odds_american")) is None:
        return False
    if not (e.get("stake_units") or 0) > 0:
        return False
    if e["kind"] == "real_bet":
        return True
    if e["kind"] == "paper_bet":
        return str(e.get("decision") or "").strip().upper() == "BET"
    return False


def bet_units(e, outcome):
    """Profit in units for a staked entry and a settled outcome."""
    stake = float(e["stake_units"])
    if outcome == "win":
        return stake * (decimal_odds(e["odds_american"]) - 1)
    if outcome == "loss":
        return -stake
    return 0.0


def line_clv(e, close_line):
    line, side, market = e.get("line"), (e.get("side") or ""), e["market"]
    if line is None or close_line is None:
        return None
    if market.endswith("_spread"):
        return round(float(line) - float(close_line), 2)
    s = side.lower()
    if s in ("over", "yes"):
        return round(float(close_line) - float(line), 2)
    if s in ("under", "no"):
        return round(float(line) - float(close_line), 2)
    return None


def taken_price(e):
    return e.get("bet_odds") if e.get("bet_odds") is not None \
        else e.get("odds_american")


def price_clv(e):
    t, c = implied(taken_price(e)), implied(e.get("close_odds"))
    if t is None or c is None:
        return None
    return c - t


def calibration(pairs, bins=10):
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(p, y) for p, y in pairs
               if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if not sel:
            continue
        out.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": len(sel),
            "mean_model_prob": round(sum(p for p, _ in sel) / len(sel), 4),
            "observed_rate": round(sum(y for _, y in sel) / len(sel), 4),
        })
    return out


def summarize(entries, bins=10):
    """entries: dicts with entry fields plus outcome, close_odds and
    close_line (each None when unknown). All are the same kind."""
    counted, excluded = [], {}
    for e in entries:
        source_excluded = [f for f in (e.get("data_flags") or [])
                           if f.startswith("excluded")]
        if source_excluded:
            excluded["excluded by source"] = excluded.get(
                "excluded by source", 0) + 1
        elif e.get("pregame_verified") == 1:
            counted.append(e)
        else:
            why = ("predicted at or after the start"
                   if e.get("pregame_verified") == 0 else "no start time")
            excluded[why] = excluded.get(why, 0) + 1
    settled = [e for e in counted if e.get("outcome") in ("win", "loss")]
    out = {
        "entries": len(counted),
        "excluded": excluded,
        "settled": len(settled),
        "pushes_or_voids": sum(
            e.get("outcome") in ("push", "void") for e in counted),
        "awaiting_result": sum(e.get("outcome") is None for e in counted),
    }
    probs = [(float(e["model_prob"]), 1 if e["outcome"] == "win" else 0)
             for e in settled if e.get("model_prob") is not None]
    if probs:
        n = len(probs)
        out["probability"] = {
            "n": n,
            "hit_rate": round(sum(y for _, y in probs) / n, 4),
            "mean_model_prob": round(sum(p for p, _ in probs) / n, 4),
            "brier": round(sum((p - y) ** 2 for p, y in probs) / n, 6),
            "log_loss": round(sum(
                -math.log(max(p if y else 1 - p, MIN_P))
                for p, y in probs) / n, 6),
            "calibration": calibration(probs, bins),
        }
    staked = [e for e in counted if is_staked(e)]
    if staked:
        graded = [e for e in staked if e.get("outcome") is not None]
        wins = sum(e["outcome"] == "win" for e in graded)
        losses = sum(e["outcome"] == "loss" for e in graded)
        risked = sum(float(e["stake_units"]) for e in graded
                     if e["outcome"] in ("win", "loss"))
        units = sum(bet_units(e, e["outcome"]) for e in graded)
        out["betting"] = {
            "staked": len(staked),
            "graded": len(graded),
            "wins": wins,
            "losses": losses,
            "pushes_or_voids": len(graded) - wins - losses,
            "win_pct": round(wins / (wins + losses), 4)
            if wins + losses else None,
            "units_risked": round(risked, 4),
            "units": round(units, 4),
            "roi": round(units / risked, 4) if risked else None,
        }
    clv = [(e, price_clv(e)) for e in counted]
    clv = [c for _, c in clv if c is not None]
    lclv = [line_clv(e, e.get("close_line")) for e in counted]
    lclv = [c for c in lclv if c is not None]
    out["clv"] = {
        "with_close": len(clv),
        "missing_close": len(counted) - len(clv),
        "mean_price_clv": round(sum(clv) / len(clv), 4) if clv else None,
        "beat_close_rate": round(sum(c > 0 for c in clv) / len(clv), 4)
        if clv else None,
        "with_line_close": len(lclv),
        "mean_line_clv_pts": round(sum(lclv) / len(lclv), 3)
        if lclv else None,
    }
    return out


GROUP_FIELDS = ("sport", "market", "model_version", "odds_band")


def group_key(e, field):
    if field == "odds_band":
        return odds_band(taken_price(e))
    return e.get(field)


def performance(entries, group_by=None):
    """Summaries per kind, never mixing kinds, optionally broken down."""
    out = {}
    for kind in ("prediction", "paper_bet", "real_bet"):
        rows = [e for e in entries if e["kind"] == kind]
        if not rows:
            continue
        block = {"all": summarize(rows)}
        if group_by:
            groups = {}
            for e in rows:
                groups.setdefault(str(group_key(e, group_by)), []).append(e)
            block["by_" + group_by] = {
                k: summarize(v) for k, v in sorted(groups.items())}
        out[kind] = block
    return out
