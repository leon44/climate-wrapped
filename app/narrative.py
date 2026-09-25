"""Turns a stats dict into the actual card copy shown in the wrapped story.

Two ways to generate this copy were considered:

  1. Plain Python string templates keyed off the stat values (implemented
     here as TemplateNarrator). Fast, free, fully deterministic, zero
     external dependency -- the numbers are always exactly the numbers
     stats.py computed, phrased with simple f-strings.
  2. Call the Claude API with the ranked stats as structured input to
     generate punchier, more varied copy, constrained to only phrase the
     numbers given. Higher quality prose, but adds a network dependency and
     per-generation API cost -- mitigated by generating once per station per
     summer and caching the copy alongside the stats (see app/routes.py,
     which caches the whole wrapped payload including narrative together).

v1 ships option 1. Both would implement the same `Narrator.build_cards`
interface, so swapping in a `ClaudeNarrator` later is a matter of adding a
new class and changing one line in routes.py -- nothing else in the app
would need to change.
"""

import abc
import datetime

CARD_ORDER = [
    "anomaly", "hottest_day", "hot_days_trend",
    "hottest_nights", "avg_temp_trend", "percentile_rank", "precip_total",
]

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}

# Below this, a station's 95th-percentile threshold reads as "warm" rather
# than "hot" -- e.g. a station whose hot-day bar is 18°C shouldn't have cards
# calling an 18°C day "hot".
HOT_LABEL_THRESHOLD_C = 20


def _hot_or_warm(threshold_c: float) -> str:
    return "hot" if threshold_c >= HOT_LABEL_THRESHOLD_C else "warm"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{_ORDINAL_SUFFIXES.get(n % 10, 'th')}"


def _temp(c_value, digits=0, unit="C") -> str:
    """Format an absolute temperature reading (a point on the scale, e.g.
    'the high was 31°C') -- converts with the C-to-F affine formula."""
    if unit == "F":
        return f"{c_value * 9 / 5 + 32:.{digits}f}°F"
    return f"{c_value:.{digits}f}°C"


def _temp_delta(c_value, digits=1, unit="C") -> str:
    """Format a temperature *difference* (an anomaly or a diurnal range,
    e.g. '2°C warmer') -- these scale by 9/5 with no +32 offset, since
    converting a gap between two points isn't converting a point."""
    if unit == "F":
        return f"{c_value * 9 / 5:.{digits}f}°F"
    return f"{c_value:.{digits}f}°C"


def _to_unit(c_value, unit="C"):
    """Convert a raw absolute-temperature chart value (not display text)."""
    if c_value is None:
        return None
    return c_value * 9 / 5 + 32 if unit == "F" else c_value


def _fmt_date(iso_date: str) -> str:
    """'2025-06-24' -> 'Jun 24, 2025'."""
    try:
        return datetime.date.fromisoformat(iso_date).strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return iso_date


def _fmt_month_day(iso_date: str) -> str:
    """'2025-06-24' -> 'Jun 24'. Used where the comparison is about timing
    within the season, not the specific calendar year."""
    try:
        return datetime.date.fromisoformat(iso_date).strftime("%b %-d")
    except (ValueError, TypeError):
        return iso_date


class Narrator(abc.ABC):
    @abc.abstractmethod
    def build_cards(self, stats: dict, station: dict, unit: str = "C") -> list:
        """Returns an ordered list of card dicts:
        {id, kind, headline, sentence, chart (optional Chart.js spec), stats (raw values for the template)}
        """


class TemplateNarrator(Narrator):
    """Option 1: deterministic string templates."""

    def build_cards(self, stats: dict, station: dict, unit: str = "C") -> list:
        year = stats["summer_year"]
        cards = [self._intro_card(stats, station)]

        builders = {
            "anomaly": self._anomaly_card,
            "hottest_day": self._hottest_day_card,
            "hot_days_trend": self._hot_days_trend_card,
            "hottest_nights": self._hottest_nights_card,
            "avg_temp_trend": self._avg_temp_trend_card,
            "percentile_rank": self._percentile_rank_card,
            "precip_total": self._precip_total_card,
        }

        for card_id in CARD_ORDER:
            block = stats.get(card_id)
            if block is None or block.get("insufficient_data"):
                continue
            builder = builders.get(card_id)
            if builder:
                cards.append(builder(block, stats, station, unit))

        cards.append(self._outro_card(stats, station, year))
        return cards

    # -- individual cards --------------------------------------------------

    def _intro_card(self, stats, station):
        return {
            "id": "intro", "kind": "intro",
            "headline": f"Summer {stats['summer_year']}",
            "sentence": f"{station['name']} has been keeping records since "
                        f"{station.get('first_year', '—')}. Here's how this summer measured up.",
        }

    def _anomaly_card(self, block, stats, station, unit="C"):
        direction = "warmer" if block["anomaly_c"] >= 0 else "cooler"
        return {
            "id": "anomaly", "kind": "stat",
            "headline": _temp(block['current_mean_c'], 1, unit),
            "sentence": (
                f"This summer's average daily high was {_temp(block['current_mean_c'], 1, unit)}. "
                f"That's {_temp_delta(abs(block['anomaly_c']), 1, unit)} {direction} than the "
                f"pre-2010 average of "
                f"{_temp(block['baseline_mean_c'], 1, unit)}."
            ),
            "stats": block,
        }

    def _hottest_day_card(self, block, stats, station, unit="C"):
        date_label = _fmt_date(block["hottest_day_date"])
        sentence = (
            f"The hottest day of summer {stats['summer_year']} was {date_label}, "
            f"reaching {_temp(block['hottest_day_tmax_c'], 1, unit)}."
        )
        if block["is_all_time_record"]:
            sentence += " That's the hottest day on record."
        else:
            sentence += f" That's the hottest day since {_fmt_date(block['record_since_date'])}."
        days = sorted(block["daily_series"].keys())
        return {
            "id": "hottest_day", "kind": "trend",
            "headline": _temp(block["hottest_day_tmax_c"], 1, unit),
            "sentence": sentence,
            "chart": {
                "type": "line",
                "labels": [_fmt_month_day(d) for d in days],
                "data": [_to_unit(block["daily_series"][d], unit) for d in days],
                "point_radius": 0,
                "span_gaps": True,
            },
            "stats": block,
        }

    def _hot_days_trend_card(self, block, stats, station, unit="C"):
        label = _hot_or_warm(block["threshold_c"])
        years = sorted(block["series"].keys())
        return {
            "id": "hot_days_trend", "kind": "trend",
            "headline": f"{block['current_summer_count']} {label} days",
            "sentence": (
                f"{block['current_summer_count']} days topped {_temp(block['threshold_c'], 0, unit)} "
                f"this summer. Versus a pre-2010 "
                f"average of {block['historical_mean_count']:.1f}."
            ),
            "chart": {
                "type": "bar",
                "labels": [str(y) for y in years],
                "data": [block["series"][y] for y in years],
            },
            "stats": block,
        }

    def _hottest_nights_card(self, block, stats, station, unit="C"):
        label = _hot_or_warm(block["night_threshold_c"])
        years = sorted(block["series"].keys())
        sentence = (
            f"{block['current_summer_count']} nights stayed above "
            f"{_temp(block['night_threshold_c'], 0, unit)} this summer, versus a pre-2010 "
            f"average of {block['historical_mean_count']:.1f}."
        )
        if block["diurnal_range_current_c"] is not None and block["diurnal_range_baseline_c"] is not None:
            narrow_direction = (
                "narrower" if block["diurnal_range_current_c"] < block["diurnal_range_baseline_c"]
                else "wider"
            )
            sentence += (
                f" The gap between daily highs and lows was {narrow_direction} too: "
                f"{_temp_delta(block['diurnal_range_current_c'], 1, unit)} this summer versus "
                f"{_temp_delta(block['diurnal_range_baseline_c'], 1, unit)} historically."
            )
        return {
            "id": "hottest_nights", "kind": "trend",
            "headline": f"{block['current_summer_count']} {label} nights",
            "sentence": sentence,
            "chart": {
                "type": "bar",
                "labels": [str(y) for y in years],
                "data": [block["series"][y] for y in years],
            },
            "stats": block,
        }

    def _avg_temp_trend_card(self, block, stats, station, unit="C"):
        years = sorted(block["series"].keys())
        diff = block["current_rolling_mean_c"] - block["series"][years[0]]
        direction = "warmer" if diff >= 0 else "colder"
        return {
            "id": "avg_temp_trend", "kind": "trend",
            "headline": _temp(block["current_rolling_mean_c"], 1, unit),
            "sentence": (
                f"The 5-year rolling average of daily high temperature is now "
                f"{_temp(block['current_rolling_mean_c'], 1, unit)}. That's {_temp_delta(abs(diff), 1, unit)} "
                f"{direction} than when this station started measuring in {block['record_start_year']}."
            ),
            "chart": {
                "type": "line",
                "labels": [str(y) for y in years],
                "data": [_to_unit(block["series"][y], unit) for y in years],
                "point_radius": 0,
            },
            "stats": block,
        }

    def _percentile_rank_card(self, block, stats, station, unit="C"):
        rank_word = _ordinal(block["rank"])
        top_list = [
            {
                "rank": item["rank"],
                "label": str(item["year"]),
                "value": _temp(item["mean_tmax_c"], 1, unit),
                "is_current_summer": item["is_current_summer"],
            }
            for item in block["top_summers"]
        ]
        return {
            "id": "percentile_rank", "kind": "stat",
            "headline": f"{rank_word} hottest",
            "sentence": (
                f"This was the {rank_word} hottest summer of {block['total_summers']} "
                f"on record here — hotter than {block['percentile']}% of them."
            ),
            "list": {
                "title": "Top 5 hottest summers on record",
                "rows": top_list,
            },
            "stats": block,
        }

    def _precip_total_card(self, block, stats, station, unit="C"):
        years = sorted(block["series"].keys())
        direction = "wetter" if block["current_total_mm"] >= block["historical_mean_mm"] else "drier"
        diff_mm = abs(block["current_total_mm"] - block["historical_mean_mm"])
        return {
            "id": "precip_total", "kind": "trend",
            "headline": f"{block['current_total_mm']:.0f}mm of rain",
            "sentence": (
                f"{block['current_total_mm']:.0f}mm fell this summer. "
                f"That's {diff_mm:.0f}mm {direction} than the pre-2010 "
                f"average of {block['historical_mean_mm']:.0f}mm."
            ),
            "chart": {
                "type": "bar",
                "labels": [str(y) for y in years],
                "data": [block["series"][y] for y in years],
            },
            "stats": block,
        }

    def _outro_card(self, stats, station, year):
        return {
            "id": "outro", "kind": "outro",
            "headline": "That's a wrap",
            "sentence": f"Thanks for spending summer {year} with {station['name']}.",
        }


def get_narrator() -> Narrator:
    """Single seam the rest of the app calls through -- swap in a
    ClaudeNarrator here later without touching routes.py beyond this line."""
    return TemplateNarrator()
