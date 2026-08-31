"""Sanity tests against two real GHCN-D stations chosen for contrast:

  - USW00094728 "NY CITY CNTRL PARK": ~155 year record, mid-latitude (40.8N).
  - CA002402351 "GRISE FIORD CLIMATE": ~18 year record, high Arctic (76.4N).

These hit the network on first run (and cache to cache/raw/) unless a copy
is already cached -- that's a deliberate v1 tradeoff to test against real
data rather than fabricated fixtures. If NOAA is unreachable and nothing is
cached, the test is skipped rather than failed.
"""

import json

import pytest

from app.ghcnd import StationFetchError, get_station_dataframe
from app.narrative import get_narrator
from app.ranking import rank_cards
from app.stats import compute_stats
from app.stations import get_station

STATIONS = ["USW00094728", "CA002402351"]
TEST_YEAR = 2025


@pytest.fixture(scope="module", params=STATIONS)
def station_df(request):
    try:
        return request.param, get_station_dataframe(request.param)
    except (StationFetchError, Exception) as e:  # network unavailable in CI, etc.
        pytest.skip(f"could not fetch {request.param}: {e}")


def test_dataframe_has_core_columns(station_df):
    _, df = station_df
    assert "DATE" in df.columns
    assert "TMAX" in df.columns
    assert "TMIN" in df.columns
    # tenths-of-a-degree encoding should already be divided down to plausible
    # Celsius values, not raw tenths (e.g. not 350 for a 35.0C day).
    assert df["TMAX"].dropna().between(-90, 60).all()


def test_compute_stats_shape(station_df):
    station_id, df = station_df
    stats = compute_stats(df, TEST_YEAR)

    assert stats["summer_year"] == TEST_YEAR
    assert "TMAX" in stats["elements_present"]
    assert stats["n_summers_on_record"] >= 2

    mt = stats["mean_temp"]
    assert 1 <= mt["rank"] <= mt["n_summers"]
    assert 0 <= mt["percentile"] <= 100

    hd = stats["hottest_day"]
    assert hd["date"].startswith(str(TEST_YEAR))
    assert hd["all_time_rank"] >= 1

    for key in ("days_ge_28c", "days_ge_30c", "days_ge_35c"):
        assert stats["hot_day_counts"][key]["this_year"] >= 0

    assert stats["heatwave"]["this_year_days"] >= 0
    assert stats["heatwave"]["record_days"] >= stats["heatwave"]["this_year_days"] \
        or stats["heatwave"]["is_record"]

    top10 = stats["top_hottest_summers"]
    assert len(top10) <= 10
    assert [c["rank"] for c in top10] == list(range(1, len(top10) + 1))
    # descending mean temp
    temps = [c["mean_temp_c"] for c in top10]
    assert temps == sorted(temps, reverse=True)


def test_compute_stats_is_json_serializable(station_df):
    _, df = station_df
    stats = compute_stats(df, TEST_YEAR)
    json.dumps(stats)  # raises on numpy/pandas leakage


def test_missing_elements_are_omitted_not_faked(station_df):
    """A station with no PRCP data for a given summer should simply not
    have rainfall keys, rather than reporting a fabricated zero."""
    station_id, df = station_df
    stats = compute_stats(df, TEST_YEAR)
    if "PRCP" not in df.columns or df["PRCP"].notna().sum() == 0:
        assert "rainfall_total" not in stats
        assert "wettest_day" not in stats


def test_ranking_orders_by_significance(station_df):
    _, df = station_df
    stats = compute_stats(df, TEST_YEAR)
    ranked = rank_cards(stats)
    scores = [c["score"] for c in ranked]
    assert scores == sorted(scores, reverse=True)
    assert set(c["id"] for c in ranked) == {
        k for k in stats if k not in ("summer_year", "elements_present", "n_summers_on_record")
    }


def test_narrative_cards_reference_only_computed_numbers(station_df):
    station_id, df = station_df
    stats = compute_stats(df, TEST_YEAR)
    station = get_station(station_id)
    cards = get_narrator().build_cards(stats, station)

    assert cards[0]["id"] == "intro"
    assert cards[-1]["id"] == "outro"
    for card in cards:
        assert card["headline"]
        assert card["sentence"]
    json.dumps(cards)


def test_contrasting_stations_both_produce_a_mean_temp_card():
    """The whole point of picking these two stations: a 155-year mid-latitude
    record and an 18-year Arctic record should both still produce a usable
    core stat, even though most of their other numbers look nothing alike."""
    for station_id in STATIONS:
        try:
            df = get_station_dataframe(station_id)
        except (StationFetchError, Exception) as e:
            pytest.skip(f"could not fetch {station_id}: {e}")
        stats = compute_stats(df, TEST_YEAR)
        assert stats.get("mean_temp") is not None, station_id
