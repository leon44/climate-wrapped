"""'What counts as interesting' -- turns the raw stats dict from stats.py
into an ordered list of card ids, so the wrapped story leads with whatever
was actually notable at this station this summer instead of a fixed order.

Deliberately kept separate from stats.py: the numbers there are just facts;
the scoring heuristics here are editorial judgment and are the part most
likely to get tuned/replaced later without touching how anything is computed.
"""

CARD_ORDER_HINT = [
    "mean_temp", "hottest_day", "hot_day_counts", "heatwave",
    "rainfall_total", "wettest_day", "dry_spell",
    "top_hottest_summers", "decade_trend", "frost_day_trend", "diurnal_range_trend",
]


def _rank_significance(rank: int, n: int) -> float:
    if n <= 1:
        return 1.0
    return round(1 - (rank - 1) / (n - 1), 3)


def _record_ratio_significance(this_value: int, record_value: int, is_record: bool) -> float:
    if is_record:
        return 1.0
    if record_value <= 0:
        return 0.0
    return round(0.15 + 0.55 * min(1.0, this_value / record_value), 3)


def _deviation_significance(this_value: float, avg_value: float | None) -> float:
    if avg_value is None:
        return 0.0
    denom = max(abs(avg_value), 1.0)
    return round(min(1.0, abs(this_value - avg_value) / denom), 3)


def _slope_significance(slope: float, scale: float) -> float:
    return round(min(1.0, abs(slope) * scale), 3)


def score_card(card_id: str, stats: dict) -> float:
    block = stats.get(card_id)
    if not block:
        return 0.0

    if card_id == "mean_temp":
        return round(abs(block["percentile"] - 50) / 50, 3)

    if card_id == "hottest_day":
        rank = block.get("all_time_rank")
        n = block.get("all_time_n_days")
        return _rank_significance(rank, n) if rank and n else 0.0

    if card_id == "hot_day_counts":
        deviations = [
            _deviation_significance(v["this_year"], v["long_term_avg"])
            for v in block.values()
        ]
        return max(deviations) if deviations else 0.0

    if card_id in ("heatwave", "dry_spell"):
        return _record_ratio_significance(
            block["this_year_days"], block["record_days"], block["is_record"]
        )

    if card_id in ("rainfall_total",):
        return _rank_significance(block["rank"], block["n_summers"])

    if card_id == "wettest_day":
        rank = block.get("all_time_rank")
        n = block.get("all_time_n_days")
        return _rank_significance(rank, n) if rank and n else 0.0

    if card_id == "top_hottest_summers":
        return 0.75  # always a strong, easy-to-grasp anchor card

    if card_id == "decade_trend":
        return 0.5  # context card, shown with fixed medium priority

    if card_id == "frost_day_trend":
        return _slope_significance(block["slope_days_per_year"], scale=8.0)

    if card_id == "diurnal_range_trend":
        return _slope_significance(block["slope_c_per_year"], scale=40.0)

    return 0.0


def rank_cards(stats: dict) -> list:
    """Returns [{"id": card_id, "score": float}, ...] for every card the
    station's data supports, most significant first. Ties keep the order
    from CARD_ORDER_HINT so output is deterministic."""
    available = [c for c in CARD_ORDER_HINT if stats.get(c) is not None]
    scored = [{"id": c, "score": score_card(c, stats)} for c in available]
    scored.sort(key=lambda c: (-c["score"], CARD_ORDER_HINT.index(c["id"])))
    return scored
