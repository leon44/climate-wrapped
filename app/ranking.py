"""'What counts as interesting' -- turns the raw stats dict from stats.py
into an ordered list of card ids, so the wrapped story leads with whatever
was actually notable at this station this summer instead of a fixed order.

Deliberately kept separate from stats.py: the numbers there are just facts;
the scoring heuristics here are editorial judgment and are the part most
likely to get tuned/replaced later without touching how anything is computed.
"""

CARD_ORDER_HINT = ["anomaly", "hot_days_trend", "hottest_nights", "percentile_rank"]


def _deviation_significance(this_value: float, avg_value: float | None) -> float:
    if avg_value is None:
        return 0.0
    denom = max(abs(avg_value), 1.0)
    return round(min(1.0, abs(this_value - avg_value) / denom), 3)


def score_card(card_id: str, stats: dict) -> float:
    block = stats.get(card_id)
    if not block or block.get("insufficient_data"):
        return 0.0

    if card_id == "anomaly":
        return 1.0  # always leads -- it's the headline number

    if card_id in ("hot_days_trend", "hottest_nights"):
        return _deviation_significance(block["current_summer_count"], block["historical_mean_count"])

    if card_id == "percentile_rank":
        return round(abs(block["percentile"] - 50) / 50, 3)

    return 0.0


def rank_cards(stats: dict) -> list:
    """Returns [{"id": card_id, "score": float}, ...] for every card the
    station's data supports (i.e. not flagged insufficient_data), most
    significant first. Ties keep the order from CARD_ORDER_HINT so output is
    deterministic."""
    available = [
        c for c in CARD_ORDER_HINT
        if stats.get(c) is not None and not stats[c].get("insufficient_data")
    ]
    scored = [{"id": c, "score": score_card(c, stats)} for c in available]
    scored.sort(key=lambda c: (-c["score"], CARD_ORDER_HINT.index(c["id"])))
    return scored
