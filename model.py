"""First-basket rates from stored history, and grading of predictions."""
import math

STARTER_PRIOR_RATE = 0.1  # one of ten starters, before seeing any games
MIN_PROBABILITY = 1e-6


def smoothed_rate(first_baskets, starts, prior_games):
    """Shrink a player's observed rate toward the 1-in-10 starter prior."""
    return (first_baskets + prior_games * STARTER_PRIOR_RATE) / (
        starts + prior_games
    )


def normalize(players):
    total = sum(p["smoothed_rate"] for p in players)
    for p in players:
        p["probability"] = round(p["smoothed_rate"] / total, 6)
    return players


def grade(candidates, actual_player_id):
    """Score one prediction set against the actual first scorer.

    Probability mass not assigned to any listed player is treated as an
    "anyone else" bucket, so a set that missed the scorer still gets a
    finite (but bad) log loss.
    """
    probs = {c["player_id"]: c["probability"] for c in candidates}
    other = max(0.0, 1.0 - sum(probs.values()))
    hit_listed = actual_player_id in probs
    p_actual = probs[actual_player_id] if hit_listed else other
    log_loss = -math.log(max(p_actual, MIN_PROBABILITY))
    brier = sum(
        (p - (1.0 if pid == actual_player_id else 0.0)) ** 2
        for pid, p in probs.items()
    ) + (other - (0.0 if hit_listed else 1.0)) ** 2
    top = max(candidates, key=lambda c: c["probability"])
    return {
        "top_pick_hit": top["player_id"] == actual_player_id,
        "actual_in_candidates": hit_listed,
        "probability_of_actual": round(p_actual, 6),
        "log_loss": round(log_loss, 6),
        "brier": round(brier, 6),
    }


def summarize(grades):
    n = len(grades)
    if not n:
        return {"graded_games": 0}
    return {
        "graded_games": n,
        "top_pick_hit_rate": round(
            sum(g["top_pick_hit"] for g in grades) / n, 4),
        "mean_log_loss": round(sum(g["log_loss"] for g in grades) / n, 4),
        "mean_brier": round(sum(g["brier"] for g in grades) / n, 4),
    }
