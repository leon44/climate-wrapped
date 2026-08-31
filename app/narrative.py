"""Turns a stats dict + card ranking into the actual card copy shown in the
wrapped story.

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

from app.ranking import rank_cards


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


def _c(x, digits=0) -> str:
    return f"{x:.{digits}f}°C"


def _fmt_date(iso_date: str) -> str:
    """'2025-06-24' -> 'Jun 24, 2025'."""
    try:
        return datetime.date.fromisoformat(iso_date).strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return iso_date


class Narrator(abc.ABC):
    @abc.abstractmethod
    def build_cards(self, stats: dict, station: dict) -> list:
        """Returns an ordered list of card dicts:
        {id, kind, headline, sentence, chart (optional Chart.js spec), stats (raw values for the template)}
        """


class TemplateNarrator(Narrator):
    """Option 1: deterministic string templates."""

    def build_cards(self, stats: dict, station: dict) -> list:
        year = stats["summer_year"]
        cards = [self._intro_card(stats, station)]

        builders = {
            "anomaly": self._anomaly_card,
            "hot_days_trend": self._hot_days_trend_card,
            "hottest_nights": self._hottest_nights_card,
            "percentile_rank": self._percentile_rank_card,
        }

        for entry in rank_cards(stats):
            builder = builders.get(entry["id"])
            if builder:
                card = builder(stats[entry["id"]], stats, station)
                card["significance"] = entry["score"]
                cards.append(card)

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

    def _anomaly_card(self, block, stats, station):
        direction = "warmer" if block["anomaly_c"] >= 0 else "cooler"
        return {
            "id": "anomaly", "kind": "stat",
            "headline": f"{block['anomaly_c']:+.1f}°C",
            "sentence": (
                f"This summer averaged {_c(block['current_mean_c'], 1)} — "
                f"{abs(block['anomaly_c']):.1f}°C {direction} than the "
                f"{block['baseline_years_used']}-year average of "
                f"{_c(block['baseline_mean_c'], 1)}."
            ),
            "stats": block,
        }

    def _hot_days_trend_card(self, block, stats, station):
        direction = "more" if block["trend_days_per_decade"] > 0 else "fewer"
        label = _hot_or_warm(block["threshold_c"])
        years = sorted(block["series"].keys())
        return {
            "id": "hot_days_trend", "kind": "trend",
            "headline": f"{block['current_summer_count']} {label} days",
            "sentence": (
                f"{block['current_summer_count']} days topped {_c(block['threshold_c'])} "
                f"this summer — your station's {label}-day bar — versus a historical "
                f"average of {block['historical_mean_count']:.1f}. That's trending "
                f"toward {abs(block['trend_days_per_decade']):.1f} {direction} days "
                f"per decade."
            ),
            "chart": {
                "type": "bar",
                "labels": [str(y) for y in years],
                "data": [block["series"][y] for y in years],
            },
            "stats": block,
        }

    def _hottest_nights_card(self, block, stats, station):
        direction = "more" if block["trend_nights_per_decade"] > 0 else "fewer"
        label = _hot_or_warm(block["night_threshold_c"])
        years = sorted(block["series"].keys())
        sentence = (
            f"{block['current_summer_count']} nights stayed above "
            f"{_c(block['night_threshold_c'])} this summer, versus a historical "
            f"average of {block['historical_mean_count']:.1f} — trending toward "
            f"{abs(block['trend_nights_per_decade']):.1f} {direction} nights per decade."
        )
        if block["diurnal_range_current_c"] is not None and block["diurnal_range_baseline_c"] is not None:
            narrow_direction = (
                "narrower" if block["diurnal_range_current_c"] < block["diurnal_range_baseline_c"]
                else "wider"
            )
            sentence += (
                f" The gap between daily highs and lows was {narrow_direction} too: "
                f"{_c(block['diurnal_range_current_c'], 1)} this summer versus "
                f"{_c(block['diurnal_range_baseline_c'], 1)} historically."
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

    def _percentile_rank_card(self, block, stats, station):
        rank_word = _ordinal(block["rank"])
        top_list = [
            {
                "rank": item["rank"],
                "label": str(item["year"]),
                "value": _c(item["mean_tmax_c"], 1),
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
