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
            "mean_temp": self._mean_temp_card,
            "hottest_day": self._hottest_day_card,
            "hot_day_counts": self._hot_day_counts_card,
            "heatwave": self._heatwave_card,
            "rainfall_total": self._rainfall_card,
            "wettest_day": self._wettest_day_card,
            "dry_spell": self._dry_spell_card,
            "top_hottest_summers": self._top_summers_card,
            "decade_trend": self._decade_trend_card,
            "frost_day_trend": self._frost_trend_card,
            "diurnal_range_trend": self._diurnal_trend_card,
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

    def _mean_temp_card(self, block, stats, station):
        rank_word = _ordinal(block["rank"])
        return {
            "id": "mean_temp", "kind": "stat",
            "headline": _c(block["this_year_mean_c"], 1),
            "sentence": (
                f"This was the {rank_word} hottest summer here since "
                f"{block['first_year']} out of {block['n_summers']} on record — "
                f"hotter than {block['percentile']:.0f}% of them. The long-term "
                f"average is {_c(block['long_term_mean_c'], 1)}."
            ),
            "stats": block,
        }

    def _hottest_day_card(self, block, stats, station):
        rank_word = _ordinal(block["all_time_rank"]) if block["all_time_rank"] else "—"
        return {
            "id": "hottest_day", "kind": "stat",
            "headline": _c(block["tmax_c"], 1),
            "sentence": (
                f"The hottest day this summer hit {_c(block['tmax_c'], 1)} on "
                f"{_fmt_date(block['date'])} — the {rank_word} hottest day out of "
                f"{block['all_time_n_days']} in the full record. The all-time "
                f"record is {_c(block['all_time_hottest_c'], 1)}."
            ),
            "stats": block,
        }

    def _hot_day_counts_card(self, block, stats, station):
        parts = []
        for key, v in block.items():
            t = key.replace("days_ge_", "").replace("c", "")
            avg = f"{v['long_term_avg']:.1f}" if v["long_term_avg"] is not None else "n/a"
            parts.append(f"{v['this_year']} days ≥{t}°C (avg {avg})")
        return {
            "id": "hot_day_counts", "kind": "stat",
            "headline": " / ".join(str(v["this_year"]) for v in block.values()),
            "sentence": "Hot days this summer: " + "; ".join(parts) + ".",
            "stats": block,
        }

    def _heatwave_card(self, block, stats, station):
        record_note = (
            "That ties/breaks the station's record." if block["is_record"]
            else f"The record is {block['record_days']} days, set in {block['record_year']}."
        )
        return {
            "id": "heatwave", "kind": "stat",
            "headline": f"{block['this_year_days']} days",
            "sentence": (
                f"The longest heatwave this summer (consecutive days ≥"
                f"{block['threshold_c']:.0f}°C) ran {block['this_year_days']} days. "
                f"{record_note}"
            ),
            "stats": block,
        }

    def _rainfall_card(self, block, stats, station):
        rank_word = _ordinal(block["rank"])
        return {
            "id": "rainfall_total", "kind": "stat",
            "headline": f"{block['this_year_mm']:.0f} mm",
            "sentence": (
                f"{block['this_year_mm']:.0f} mm of rain fell this summer — the "
                f"{rank_word} wettest of {block['n_summers']} summers on record "
                f"(average is {block['long_term_avg_mm']:.0f} mm)."
            ),
            "stats": block,
        }

    def _wettest_day_card(self, block, stats, station):
        rank_word = _ordinal(block["all_time_rank"]) if block["all_time_rank"] else "—"
        return {
            "id": "wettest_day", "kind": "stat",
            "headline": f"{block['prcp_mm']:.0f} mm",
            "sentence": (
                f"The wettest day this summer brought {block['prcp_mm']:.0f} mm on "
                f"{_fmt_date(block['date'])} — the {rank_word} wettest day out of "
                f"{block['all_time_n_days']} on record."
            ),
            "stats": block,
        }

    def _dry_spell_card(self, block, stats, station):
        record_note = (
            "That ties/breaks the station's record." if block["is_record"]
            else f"The record is {block['record_days']} days, set in {block['record_year']}."
        )
        return {
            "id": "dry_spell", "kind": "stat",
            "headline": f"{block['this_year_days']} days",
            "sentence": (
                f"The longest dry spell this summer ran {block['this_year_days']} "
                f"consecutive days without meaningful rain. {record_note}"
            ),
            "stats": block,
        }

    def _top_summers_card(self, block, stats, station):
        recent = sum(1 for s in block if s["is_recent"])
        top = block[0]
        return {
            "id": "top_hottest_summers", "kind": "list",
            "headline": "Top 10 hottest summers",
            "sentence": (
                f"{recent} of the top 10 hottest summers on record here have "
                f"happened in the last 15 years. The hottest was {top['year']} "
                f"at {_c(top['mean_temp_c'], 1)}."
            ),
            "chart": {
                "type": "bar",
                "labels": [str(s["year"]) for s in block],
                "data": [s["mean_temp_c"] for s in block],
            },
            "stats": block,
        }

    def _decade_trend_card(self, block, stats, station):
        first, last = block[0], block[-1]
        delta = last["mean_temp_c"] - first["mean_temp_c"]
        direction = "up" if delta > 0 else "down"
        return {
            "id": "decade_trend", "kind": "trend",
            "headline": f"{delta:+.1f}°C",
            "sentence": (
                f"Decade by decade, mean summer temperature has moved {direction} "
                f"{abs(delta):.1f}°C, from {_c(first['mean_temp_c'], 1)} in the "
                f"{first['decade']}s to {_c(last['mean_temp_c'], 1)} in the {last['decade']}s."
            ),
            "chart": {
                "type": "line",
                "labels": [f"{b['decade']}s" for b in block],
                "data": [b["mean_temp_c"] for b in block],
            },
            "stats": block,
        }

    def _frost_trend_card(self, block, stats, station):
        direction = "fewer" if block["slope_days_per_year"] < 0 else "more"
        return {
            "id": "frost_day_trend", "kind": "trend",
            "headline": f"{block['slope_days_per_year']:+.2f} days/yr",
            "sentence": (
                f"Frost days (TMIN below 0°C) have trended toward {direction} "
                f"per year across the full record, roughly "
                f"{abs(block['slope_days_per_year']):.2f} days/year."
            ),
            "chart": {
                "type": "line",
                "labels": [f"{b['decade']}s" for b in block["by_decade"]],
                "data": [b["avg_frost_days"] for b in block["by_decade"]],
            },
            "stats": block,
        }

    def _diurnal_trend_card(self, block, stats, station):
        direction = "narrowed" if block["slope_c_per_year"] < 0 else "widened"
        return {
            "id": "diurnal_range_trend", "kind": "trend",
            "headline": f"{block['slope_c_per_year']:+.3f}°C/yr",
            "sentence": (
                f"The gap between daily highs and lows has {direction} over the "
                f"full record, a trend of about {abs(block['slope_c_per_year']):.3f}°C/year."
            ),
            "chart": {
                "type": "line",
                "labels": [f"{b['decade']}s" for b in block["by_decade"]],
                "data": [b["avg_range_c"] for b in block["by_decade"]],
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
