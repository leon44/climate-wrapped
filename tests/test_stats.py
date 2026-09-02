"""Unit tests for each of the four stats.py functions against small,
hand-computable synthetic DataFrames, plus smoke tests against two real
GHCN-D stations chosen for contrast:

  - USW00094728 "NY CITY CNTRL PARK": ~155 year record, mid-latitude (40.8N).
  - CA002402351 "GRISE FIORD CLIMATE": ~18 year record, high Arctic (76.4N).

The real-station tests hit the network on first run (and cache to
cache/raw/) unless a copy is already cached -- that's a deliberate v1
tradeoff to test against real data rather than fabricated fixtures. If NOAA
is unreachable and nothing is cached, the test is skipped rather than failed.
"""

import datetime
import json

import pandas as pd
import pytest

import app.stats as stats_mod
from app.ghcnd import StationFetchError, get_station_dataframe
from app.narrative import get_narrator
from app.stats import (
    compute_anomaly,
    compute_avg_temp_trend,
    compute_first_last_hot_day,
    compute_hot_days_trend,
    compute_hottest_nights,
    compute_percentile_rank,
    compute_stats,
    summer_window,
)
from app.stations import get_station

STATIONS = ["USW00094728", "CA002402351"]
TEST_YEAR = 2025  # fully completed given today's date in this environment
NORTH_LAT = 40.8


# -- synthetic fixture helpers -----------------------------------------------

def _summer_df(specs: dict) -> pd.DataFrame:
    """specs: {year: {"TMAX": scalar-or-92-list, "TMIN": scalar-or-92-list}}.
    Builds one row per Jun 1 - Aug 31 day for each year given."""
    frames = []
    for year, cols in specs.items():
        dates = pd.date_range(f"{year}-06-01", f"{year}-08-31", freq="D")
        assert len(dates) == 92
        data = {"DATE": dates}
        for col, vals in cols.items():
            if not isinstance(vals, (list, tuple)):
                vals = [vals] * 92
            assert len(vals) == 92
            data[col] = vals
        frames.append(pd.DataFrame(data))
    df = pd.concat(frames, ignore_index=True)
    df["DATE"] = pd.to_datetime(df["DATE"])
    return df.sort_values("DATE").reset_index(drop=True)


def _tail(n_total, low, n_hot, hot):
    return [low] * (n_total - n_hot) + [hot] * n_hot


@pytest.fixture
def lower_min_historical(monkeypatch):
    """Small synthetic fixtures use 3 historical years, well under the
    production MIN_HISTORICAL_SUMMERS=8 gate -- lower it so the unit tests
    exercise the actual math instead of the insufficient_data path."""
    monkeypatch.setattr(stats_mod, "MIN_HISTORICAL_SUMMERS", 3)


# -- summer_window -------------------------------------------------------

def test_summer_window_northern_hemisphere():
    start, end = summer_window(2024, lat=40.8)
    assert start == datetime.date(2024, 6, 1)
    assert end == datetime.date(2024, 8, 31)


def test_summer_window_southern_hemisphere():
    start, end = summer_window(2024, lat=-33.9)
    assert start == datetime.date(2023, 12, 1)
    assert end == datetime.date(2024, 2, 29)  # 2024 is a leap year


def test_summer_window_southern_hemisphere_non_leap_year():
    start, end = summer_window(2023, lat=-33.9)
    assert end == datetime.date(2023, 2, 28)


# -- stat 1: compute_anomaly ----------------------------------------------

def test_compute_anomaly_known_values(lower_min_historical):
    df = _summer_df({
        2020: {"TMAX": 20.0},
        2021: {"TMAX": 20.0},
        2022: {"TMAX": 20.0},
        2023: {"TMAX": 25.0},
    })
    result = compute_anomaly(df, 2023, NORTH_LAT)
    assert result == {
        "anomaly_c": 5.0,
        "current_mean_c": 25.0,
        "baseline_mean_c": 20.0,
        "baseline_years_used": 3,
    }


def test_compute_anomaly_insufficient_data_below_min_historical():
    # only 2 historical summers, well under the default 8-summer minimum
    df = _summer_df({
        2022: {"TMAX": 20.0},
        2023: {"TMAX": 20.0},
        2024: {"TMAX": 25.0},
    })
    assert compute_anomaly(df, 2024, NORTH_LAT) == {"insufficient_data": True}


def test_compute_anomaly_drops_summer_with_too_much_missing_data(lower_min_historical):
    # 2021 has only 62/92 days of TMAX (~33% missing) -> excluded from history
    sparse = [20.0] * 62 + [None] * 30
    df = _summer_df({
        2020: {"TMAX": 20.0},
        2021: {"TMAX": sparse},
        2022: {"TMAX": 20.0},
        2023: {"TMAX": 30.0},
    })
    result = compute_anomaly(df, 2023, NORTH_LAT)
    assert result == {"insufficient_data": True}  # only 2 qualifying historical summers left


# -- stat 2: compute_hot_days_trend ----------------------------------------

def test_compute_hot_days_trend_known_values(lower_min_historical):
    df = _summer_df({
        2020: {"TMAX": _tail(92, 20.0, 0, 35.0)},
        2021: {"TMAX": _tail(92, 20.0, 2, 35.0)},
        2022: {"TMAX": _tail(92, 20.0, 4, 35.0)},
        2023: {"TMAX": _tail(92, 20.0, 7, 35.0)},
    })
    result = compute_hot_days_trend(df, 2023, NORTH_LAT)
    assert result["threshold_c"] == 20.0
    assert result["series"] == {2020: 0, 2021: 2, 2022: 4, 2023: 7}
    assert result["current_summer_count"] == 7
    assert result["historical_mean_count"] == 2.0
    assert result["trend_days_per_decade"] == 23.0


def test_compute_hot_days_trend_insufficient_data():
    df = _summer_df({
        2022: {"TMAX": 20.0},
        2023: {"TMAX": 20.0},
        2024: {"TMAX": 25.0},
    })
    assert compute_hot_days_trend(df, 2024, NORTH_LAT) == {"insufficient_data": True}


# -- stat: compute_first_last_hot_day ---------------------------------------

def test_compute_first_last_hot_day_known_values(lower_min_historical):
    def build_year(first_idx, last_idx):
        vals = [20.0] * 92
        vals[first_idx] = 35.0
        vals[last_idx] = 35.0
        return vals

    df = _summer_df({
        2005: {"TMAX": build_year(8, 80)},
        2006: {"TMAX": build_year(10, 82)},
        2007: {"TMAX": build_year(12, 84)},
        2008: {"TMAX": build_year(5, 90)},
    })
    result = compute_first_last_hot_day(df, 2008, NORTH_LAT)
    assert result["threshold_c"] == 20

    season_start = datetime.date(2008, 6, 1)
    assert result["current_first_hot_day"] == (season_start + datetime.timedelta(days=5)).isoformat()
    assert result["current_last_hot_day"] == (season_start + datetime.timedelta(days=90)).isoformat()
    assert result["historical_avg_first_hot_day"] == (season_start + datetime.timedelta(days=10)).isoformat()
    assert result["historical_avg_last_hot_day"] == (season_start + datetime.timedelta(days=82)).isoformat()
    assert result["first_hot_day_days_diff"] == -5
    assert result["last_hot_day_days_diff"] == 8
    assert result["historical_summers_used"] == 3


def test_compute_first_last_hot_day_insufficient_data():
    df = _summer_df({
        2022: {"TMAX": 20.0},
        2023: {"TMAX": 20.0},
        2024: {"TMAX": 25.0},
    })
    assert compute_first_last_hot_day(df, 2024, NORTH_LAT) == {"insufficient_data": True}


# -- stat 3: compute_hottest_nights ----------------------------------------

def test_compute_hottest_nights_known_values(lower_min_historical):
    def build_year(n_hot, tmax_offset):
        tmin = _tail(92, 10.0, n_hot, 22.0)
        tmax = [t + tmax_offset for t in tmin]
        return {"TMIN": tmin, "TMAX": tmax}

    df = _summer_df({
        2020: build_year(0, 10.0),
        2021: build_year(2, 10.0),
        2022: build_year(4, 10.0),
        2023: build_year(7, 6.0),  # current summer: narrower diurnal range
    })
    result = compute_hottest_nights(df, 2023, NORTH_LAT)
    assert result["night_threshold_c"] == 10.0
    assert result["series"] == {2020: 0, 2021: 2, 2022: 4, 2023: 7}
    assert result["current_summer_count"] == 7
    assert result["historical_mean_count"] == 2.0
    assert result["trend_nights_per_decade"] == 23.0
    assert result["diurnal_range_current_c"] == 6.0
    assert result["diurnal_range_baseline_c"] == 10.0


def test_compute_hottest_nights_insufficient_data():
    df = _summer_df({
        2022: {"TMIN": 10.0, "TMAX": 20.0},
        2023: {"TMIN": 10.0, "TMAX": 20.0},
        2024: {"TMIN": 12.0, "TMAX": 22.0},
    })
    assert compute_hottest_nights(df, 2024, NORTH_LAT) == {"insufficient_data": True}


# -- stat: compute_avg_temp_trend --------------------------------------------

def test_compute_avg_temp_trend_known_values(lower_min_historical):
    # avg daily temp (TMAX+TMIN)/2 rises by 1.0C/year -> 10C/decade, and the
    # rolling window is 5 summers, so the first two rolling points land on
    # 2024 and 2025 (17.0, 18.0).
    df = _summer_df({
        2020 + i: {"TMAX": 20.0 + i, "TMIN": 10.0 + i} for i in range(6)
    })
    result = compute_avg_temp_trend(df, 2025, NORTH_LAT)
    assert result["series"] == {2024: 17.0, 2025: 18.0}
    assert result["current_rolling_mean_c"] == 18.0
    assert result["trend_c_per_decade_since_2010"] == 10.0


def test_compute_avg_temp_trend_insufficient_data():
    df = _summer_df({
        2022: {"TMAX": 20.0, "TMIN": 10.0},
        2023: {"TMAX": 20.0, "TMIN": 10.0},
        2024: {"TMAX": 25.0, "TMIN": 15.0},
    })
    assert compute_avg_temp_trend(df, 2024, NORTH_LAT) == {"insufficient_data": True}


# -- stat 4: compute_percentile_rank ---------------------------------------

def test_compute_percentile_rank_known_values(lower_min_historical):
    df = _summer_df({
        2020: {"TMAX": 18.0},
        2021: {"TMAX": 20.0},
        2022: {"TMAX": 22.0},
        2023: {"TMAX": 25.0},
    })
    result = compute_percentile_rank(df, 2023, NORTH_LAT)
    assert result["percentile"] == 100
    assert result["rank"] == 1
    assert result["total_summers"] == 4
    assert result["top_summers"] == [
        {"rank": 1, "year": 2023, "mean_tmax_c": 25.0, "is_current_summer": True},
        {"rank": 2, "year": 2022, "mean_tmax_c": 22.0, "is_current_summer": False},
        {"rank": 3, "year": 2021, "mean_tmax_c": 20.0, "is_current_summer": False},
        {"rank": 4, "year": 2020, "mean_tmax_c": 18.0, "is_current_summer": False},
    ]


def test_compute_percentile_rank_middle_of_pack(lower_min_historical):
    df = _summer_df({
        2020: {"TMAX": 18.0},
        2021: {"TMAX": 20.0},
        2022: {"TMAX": 24.0},
        2023: {"TMAX": 22.0},  # warmer than 2 of 3 historical summers
    })
    result = compute_percentile_rank(df, 2023, NORTH_LAT)
    assert result["percentile"] == round(100 * 2 / 3)
    assert result["rank"] == 2  # 24.0 (2022) > 22.0 (2023) > 20.0 > 18.0
    assert result["total_summers"] == 4
    assert [item["year"] for item in result["top_summers"]] == [2022, 2023, 2021, 2020]
    assert result["top_summers"][1]["is_current_summer"] is True


def test_compute_percentile_rank_insufficient_data():
    df = _summer_df({
        2022: {"TMAX": 20.0},
        2023: {"TMAX": 20.0},
        2024: {"TMAX": 25.0},
    })
    assert compute_percentile_rank(df, 2024, NORTH_LAT) == {"insufficient_data": True}


def test_compute_percentile_rank_top_summers_capped_at_5(lower_min_historical):
    df = _summer_df({y: {"TMAX": float(y - 2000)} for y in range(2010, 2018)})  # 2010..2017, current=2017
    result = compute_percentile_rank(df, 2017, NORTH_LAT)
    assert len(result["top_summers"]) == 5
    assert [item["year"] for item in result["top_summers"]] == [2017, 2016, 2015, 2014, 2013]
    assert [item["rank"] for item in result["top_summers"]] == [1, 2, 3, 4, 5]


# -- compute_stats orchestrator ---------------------------------------------

def test_compute_stats_all_insufficient_below_min_historical():
    """Fewer than the default 8 qualifying historical summers -> every
    stat block flags insufficient_data rather than returning a number
    computed from a tiny sample."""
    df = _summer_df({y: {"TMAX": 20.0, "TMIN": 10.0} for y in range(2020, 2025)})
    result = compute_stats(df, 2024, NORTH_LAT)
    for key in ("anomaly", "hot_days_trend", "hottest_nights", "percentile_rank"):
        assert result[key] == {"insufficient_data": True}


def test_compute_stats_no_tmax_column():
    df = pd.DataFrame({"DATE": pd.date_range("2024-06-01", "2024-08-31")})
    result = compute_stats(df, 2024, NORTH_LAT)
    for key in ("anomaly", "hot_days_trend", "hottest_nights", "percentile_rank"):
        assert result[key] == {"insufficient_data": True}


def test_compute_stats_is_json_serializable_with_full_synthetic_history(lower_min_historical):
    def build_year(n_hot):
        tmin = _tail(92, 10.0, n_hot, 22.0)
        tmax = [t + 10.0 for t in tmin]
        return {"TMIN": tmin, "TMAX": tmax}

    specs = {y: build_year(y - 2020) for y in range(2020, 2024)}  # 2020..2023
    df = _summer_df(specs)
    stats = compute_stats(df, 2023, NORTH_LAT)
    assert stats["summer_year"] == 2023
    json.dumps(stats)  # raises on numpy/pandas leakage or non-str-coercible keys


def test_compute_stats_southern_hemisphere(lower_min_historical):
    """Southern Hemisphere station: summer N spans Dec (N-1) - Feb (N)."""
    frames = []
    for label_year, tmax in ((2021, 15.0), (2022, 15.0), (2023, 15.0), (2024, 20.0)):
        start = datetime.date(label_year - 1, 12, 1)
        end = datetime.date(label_year, 2, 28 if not _is_leap(label_year) else 29)
        dates = pd.date_range(start, end, freq="D")
        frames.append(pd.DataFrame({"DATE": dates, "TMAX": tmax}))
    df = pd.concat(frames, ignore_index=True)
    df["DATE"] = pd.to_datetime(df["DATE"])

    result = compute_anomaly(df, 2024, lat=-33.9)
    assert result["current_mean_c"] == 20.0
    assert result["baseline_mean_c"] == 15.0
    assert result["anomaly_c"] == 5.0


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


# -- narrative integration ---------------------------------------------------

def test_narrative_shows_all_available_cards_in_fixed_order(lower_min_historical):
    def build_year(n_hot):
        tmin = _tail(92, 10.0, n_hot, 22.0)
        tmax = [t + 10.0 for t in tmin]
        return {"TMIN": tmin, "TMAX": tmax}

    specs = {y: build_year(max(0, y - 2020)) for y in range(2020, 2024)}
    df = _summer_df(specs)
    stats = compute_stats(df, 2023, NORTH_LAT)

    station = {"name": "Test Station", "first_year": 2020}
    cards = get_narrator().build_cards(stats, station)
    # cards appear in fixed CARD_ORDER, skipping whatever the station's data
    # can't support -- no ranking/significance involved.
    from app.narrative import CARD_ORDER
    expected_ids = ["intro"] + [
        cid for cid in CARD_ORDER if not stats[cid].get("insufficient_data")
    ] + ["outro"]
    assert [c["id"] for c in cards] == expected_ids
    assert "precip_total" not in expected_ids  # this synthetic df has no PRCP column
    for card in cards:
        assert card["headline"]
        assert card["sentence"]
        assert "significance" not in card
    json.dumps(cards)

    percentile_card = next(c for c in cards if c["id"] == "percentile_rank")
    assert percentile_card["list"]["title"]
    rows = percentile_card["list"]["rows"]
    assert 1 <= len(rows) <= 5
    assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    assert sum(row["is_current_summer"] for row in rows) == 1


def test_narrative_skips_insufficient_data_stats():
    df = _summer_df({y: {"TMAX": 20.0, "TMIN": 10.0} for y in range(2020, 2025)})
    stats = compute_stats(df, 2024, NORTH_LAT)
    station = {"name": "Test Station", "first_year": 2020}
    cards = get_narrator().build_cards(stats, station)
    # nothing but intro/outro should be built when every stat is insufficient
    assert [c["id"] for c in cards] == ["intro", "outro"]


def test_narrative_calls_it_warm_below_20c(lower_min_historical):
    """A cool station's 95th-percentile threshold can land under 20°C --
    cards should say "warm", not "hot", for both the day and night stats."""
    def build_year(n_hot):
        tmin = _tail(92, 5.0, n_hot, 12.0)
        tmax = [t + 8.0 for t in tmin]  # base TMAX 13.0, hot-tail TMAX 20.0 -> threshold < 20
        return {"TMIN": tmin, "TMAX": tmax}

    specs = {y: build_year(max(0, y - 2020)) for y in range(2020, 2024)}
    df = _summer_df(specs)
    stats = compute_stats(df, 2023, NORTH_LAT)

    assert stats["hot_days_trend"]["threshold_c"] < 20
    assert stats["hottest_nights"]["night_threshold_c"] < 20

    station = {"name": "Test Station", "first_year": 2020}
    cards = get_narrator().build_cards(stats, station)
    by_id = {c["id"]: c for c in cards}

    assert "warm days" in by_id["hot_days_trend"]["headline"]
    assert "warm-day bar" in by_id["hot_days_trend"]["sentence"]
    assert "warm nights" in by_id["hottest_nights"]["headline"]


def test_narrative_calls_it_hot_at_or_above_20c(lower_min_historical):
    def build_year(n_hot):
        tmin = _tail(92, 21.0, n_hot, 30.0)  # base TMIN 21.0 -> night threshold >= 20
        tmax = [t + 10.0 for t in tmin]  # base TMAX 31.0 -> day threshold >= 20
        return {"TMIN": tmin, "TMAX": tmax}

    specs = {y: build_year(max(0, y - 2020)) for y in range(2020, 2024)}
    df = _summer_df(specs)
    stats = compute_stats(df, 2023, NORTH_LAT)

    assert stats["hot_days_trend"]["threshold_c"] >= 20
    assert stats["hottest_nights"]["night_threshold_c"] >= 20

    station = {"name": "Test Station", "first_year": 2020}
    cards = get_narrator().build_cards(stats, station)
    by_id = {c["id"]: c for c in cards}

    assert "hot days" in by_id["hot_days_trend"]["headline"]
    assert "hot-day bar" in by_id["hot_days_trend"]["sentence"]
    assert "hot nights" in by_id["hottest_nights"]["headline"]


# -- real-station smoke tests -----------------------------------------------

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


def test_compute_stats_smoke_against_real_station(station_df):
    station_id, df = station_df
    station = get_station(station_id)
    stats = compute_stats(df, TEST_YEAR, station["lat"])

    assert stats["summer_year"] == TEST_YEAR
    json.dumps(stats)  # raises on numpy/pandas leakage

    for key in ("anomaly", "hot_days_trend", "hottest_nights", "percentile_rank"):
        block = stats[key]
        assert isinstance(block, dict)
        if block.get("insufficient_data"):
            continue  # e.g. a short or gappy record may fall short of the 8-summer minimum
        if key == "anomaly":
            assert isinstance(block["anomaly_c"], float)
            assert 1 <= block["baseline_years_used"] <= 30
        elif key in ("hot_days_trend", "hottest_nights"):
            assert block["current_summer_count"] >= 0
            assert len(block["series"]) >= 1
        elif key == "percentile_rank":
            assert 0 <= block["percentile"] <= 100
            assert 1 <= block["rank"] <= block["total_summers"]


def test_narrative_cards_from_real_station(station_df):
    station_id, df = station_df
    station = get_station(station_id)
    stats = compute_stats(df, TEST_YEAR, station["lat"])
    cards = get_narrator().build_cards(stats, station)

    assert cards[0]["id"] == "intro"
    assert cards[-1]["id"] == "outro"
    for card in cards:
        assert card["headline"]
        assert card["sentence"]
    json.dumps(cards)
