"""Raw stats computation for a station's daily history.

compute_stats(df, year, lat) returns a single plain, JSON-serializable dict
with four stat blocks: anomaly, hot_days_trend, hottest_nights, and
percentile_rank. It only computes numbers -- it has no opinion about how
they're phrased (see narrative.py) or which order they're shown in (see
ranking.py).

Summer definition (pinned here since GHCN-D itself doesn't standardize it):
  - Northern Hemisphere stations (lat >= 0): summer `year` is Jun 1 - Aug 31
    of that calendar year.
  - Southern Hemisphere stations (lat < 0): summer `year` is Dec 1 of
    `year - 1` through Feb 28/29 of `year` -- i.e. Dec of year N through Feb
    of year N+1 is labeled "summer N+1", the same convention meteorologists
    use for a Southern Hemisphere DJF season.

current_summer is the target `year` passed in; the app's gating logic
elsewhere (see gate.py) guarantees it's fully in the past before this runs.
historical_summers are all *prior* complete summers in the record.

Data-quality rules, applied uniformly across all four stats:
  - A summer (current or historical) is dropped from any calculation it
    would distort if more than ~25% of its days are missing the column(s)
    that calculation needs -- NaN/absent days are never treated as cold or
    zero.
  - Each stat requires at least MIN_HISTORICAL_SUMMERS qualifying historical
    summers (8 -- low enough that every station meeting this app's own
    20+-year record-length bar for being listed at all should clear it, high
    enough that percentile/trend numbers aren't drawn from a handful of
    years); short of that, the stat returns {"insufficient_data": True}
    instead of a number computed from too small a sample.

Every °C value in the returned dict is rounded to 1 decimal, except the two
fixed threshold values -- hot_days_trend's threshold_c ("the hot-day bar")
and hottest_nights's night_threshold_c -- which are rounded to the nearest
whole degree since they read as round-number benchmarks rather than precise
measurements. Trend/percentile math is done at full precision internally
before rounding at the edge.
"""

import calendar
import datetime
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PERCENTILE = 95
MAX_MISSING_FRACTION = 0.25
MIN_HISTORICAL_SUMMERS = 8
MAX_BASELINE_YEARS = 30


def _is_southern_hemisphere(lat: float | None) -> bool:
    return lat is not None and lat < 0


def summer_window(year: int, lat: float | None) -> tuple[datetime.date, datetime.date]:
    """The calendar window for the summer labeled `year` at this latitude.
    See module docstring for the Southern Hemisphere Dec(year-1)-Feb(year)
    convention."""
    if _is_southern_hemisphere(lat):
        start = datetime.date(year - 1, 12, 1)
        last_feb_day = 29 if calendar.isleap(year) else 28
        end = datetime.date(year, 2, last_feb_day)
    else:
        start = datetime.date(year, 6, 1)
        end = datetime.date(year, 8, 31)
    return start, end


def _season_slice(df: pd.DataFrame, year: int, lat: float | None) -> tuple[pd.DataFrame, int]:
    start, end = summer_window(year, lat)
    mask = (df["DATE"].dt.date >= start) & (df["DATE"].dt.date <= end)
    total_days = (end - start).days + 1
    return df.loc[mask], total_days


def _summer_qualifies(season: pd.DataFrame, total_days: int, columns: tuple[str, ...]) -> bool:
    if season.empty:
        return False
    for col in columns:
        if col not in season.columns:
            return False
        missing_fraction = 1 - (season[col].notna().sum() / total_days)
        if missing_fraction > MAX_MISSING_FRACTION:
            return False
    return True


def _candidate_years(df: pd.DataFrame) -> range:
    years = df["DATE"].dt.year
    return range(int(years.min()) - 1, int(years.max()) + 2)


def _qualifying_summers(df: pd.DataFrame, lat: float | None, columns: tuple[str, ...]) -> dict:
    """{year: season_df} for every year whose season has <=10% missing data
    across `columns`."""
    out = {}
    for year in _candidate_years(df):
        season, total_days = _season_slice(df, year, lat)
        if _summer_qualifies(season, total_days, columns):
            out[year] = season
    return out


def _split_current_historical(qualifying: dict, current_year: int) -> tuple[pd.DataFrame | None, dict]:
    current_season = qualifying.get(current_year)
    historical = {y: s for y, s in qualifying.items() if y < current_year}
    return current_season, historical


def _linear_trend_per_decade(series: dict) -> float:
    """Slope of a 1-degree least-squares fit, in units per decade.

    Computed by hand (closed-form OLS) rather than via np.polyfit: polyfit
    routes through LAPACK's lstsq, which spins up OpenBLAS's native thread
    pool on first use -- on cgroup-limited containers that pool has been
    observed to overflow an internal buffer and SIGSEGV the worker. A 1D fit
    over a handful of points doesn't need LAPACK at all.
    """
    years = np.array(sorted(series.keys()), dtype=float)
    if len(years) < 2:
        return 0.0
    counts = np.array([series[y] for y in sorted(series.keys())], dtype=float)
    years_centered = years - years.mean()
    slope_per_year = float((years_centered * counts).sum() / (years_centered ** 2).sum())
    return slope_per_year * 10


def compute_anomaly(df: pd.DataFrame, current_year: int, lat: float | None) -> dict:
    """Stat 1: current summer's mean TMAX vs. a rolling baseline of up to
    the most recent 30 qualifying historical summers."""
    qualifying = _qualifying_summers(df, lat, ("TMAX",))
    current_season, historical = _split_current_historical(qualifying, current_year)

    if current_season is None or len(historical) < MIN_HISTORICAL_SUMMERS:
        logger.info(
            "anomaly: insufficient data for summer %s (historical=%d)",
            current_year, len(historical),
        )
        return {"insufficient_data": True}

    baseline_years = sorted(historical.keys(), reverse=True)[:MAX_BASELINE_YEARS]
    baseline_values = pd.concat([historical[y]["TMAX"] for y in baseline_years]).dropna()
    baseline_mean = float(baseline_values.mean())

    current_values = current_season["TMAX"].dropna()
    current_mean = float(current_values.mean())

    return {
        "anomaly_c": round(current_mean - baseline_mean, 1),
        "current_mean_c": round(current_mean, 1),
        "baseline_mean_c": round(baseline_mean, 1),
        "baseline_years_used": len(baseline_years),
    }


def compute_hot_days_trend(df: pd.DataFrame, current_year: int, lat: float | None) -> dict:
    """Stat 2: days per summer above the station's fixed 95th-percentile
    TMAX threshold (computed once from the whole historical record)."""
    qualifying = _qualifying_summers(df, lat, ("TMAX",))
    current_season, historical = _split_current_historical(qualifying, current_year)

    if current_season is None or len(historical) < MIN_HISTORICAL_SUMMERS:
        logger.info(
            "hot_days_trend: insufficient data for summer %s (historical=%d)",
            current_year, len(historical),
        )
        return {"insufficient_data": True}

    all_historical_tmax = pd.concat([s["TMAX"] for s in historical.values()]).dropna()
    threshold = float(np.percentile(all_historical_tmax, PERCENTILE))

    historical_series = {y: int((s["TMAX"] > threshold).sum()) for y, s in historical.items()}
    current_count = int((current_season["TMAX"] > threshold).sum())
    full_series = {**historical_series, current_year: current_count}

    return {
        "threshold_c": round(threshold),
        "series": full_series,
        "current_summer_count": current_count,
        "historical_mean_count": round(float(np.mean(list(historical_series.values()))), 1),
        "trend_days_per_decade": round(_linear_trend_per_decade(full_series), 2),
    }


def compute_hottest_nights(df: pd.DataFrame, current_year: int, lat: float | None) -> dict:
    """Stat 3: mirror of compute_hot_days_trend on TMIN, plus a diurnal
    range narrowing bonus computed from the same TMAX/TMIN pull."""
    qualifying = _qualifying_summers(df, lat, ("TMIN",))
    current_season, historical = _split_current_historical(qualifying, current_year)

    if current_season is None or len(historical) < MIN_HISTORICAL_SUMMERS:
        logger.info(
            "hottest_nights: insufficient data for summer %s (historical=%d)",
            current_year, len(historical),
        )
        return {"insufficient_data": True}

    all_historical_tmin = pd.concat([s["TMIN"] for s in historical.values()]).dropna()
    threshold = float(np.percentile(all_historical_tmin, PERCENTILE))

    historical_series = {y: int((s["TMIN"] > threshold).sum()) for y, s in historical.items()}
    current_count = int((current_season["TMIN"] > threshold).sum())
    full_series = {**historical_series, current_year: current_count}

    diurnal_current, diurnal_baseline = _diurnal_range_bonus(df, current_year, lat)

    return {
        "night_threshold_c": round(threshold),
        "series": full_series,
        "current_summer_count": current_count,
        "historical_mean_count": round(float(np.mean(list(historical_series.values()))), 1),
        "trend_nights_per_decade": round(_linear_trend_per_decade(full_series), 2),
        "diurnal_range_current_c": diurnal_current,
        "diurnal_range_baseline_c": diurnal_baseline,
    }


def _diurnal_range_bonus(df: pd.DataFrame, current_year: int, lat: float | None):
    """mean(TMAX - TMIN) for current summer vs. the historical mean, over
    whichever summers have qualifying coverage of both columns. Returns
    (None, None) rather than raising if there isn't enough data -- this is a
    bonus field, not worth invalidating the whole hottest-nights stat over."""
    qualifying = _qualifying_summers(df, lat, ("TMAX", "TMIN"))
    current_season, historical = _split_current_historical(qualifying, current_year)
    if current_season is None or not historical:
        logger.info("diurnal_range: insufficient paired TMAX/TMIN data for summer %s", current_year)
        return None, None

    diurnal_current = round(float((current_season["TMAX"] - current_season["TMIN"]).mean()), 1)
    historical_ranges = pd.concat(
        [(s["TMAX"] - s["TMIN"]) for s in historical.values()]
    ).dropna()
    diurnal_baseline = round(float(historical_ranges.mean()), 1)
    return diurnal_current, diurnal_baseline


def compute_percentile_rank(df: pd.DataFrame, current_year: int, lat: float | None) -> dict:
    """Stat 4: current summer's mean TMAX ranked against every historical
    summer's mean TMAX, plus the top 5 hottest summers on record (by mean
    TMAX) for the card's leaderboard."""
    qualifying = _qualifying_summers(df, lat, ("TMAX",))
    current_season, historical = _split_current_historical(qualifying, current_year)

    if current_season is None or len(historical) < MIN_HISTORICAL_SUMMERS:
        logger.info(
            "percentile_rank: insufficient data for summer %s (historical=%d)",
            current_year, len(historical),
        )
        return {"insufficient_data": True}

    current_mean = float(current_season["TMAX"].mean())
    historical_means = {y: float(s["TMAX"].mean()) for y, s in historical.items()}

    lower_count = sum(1 for v in historical_means.values() if v < current_mean)
    percentile = round(100 * lower_count / len(historical_means))

    all_means = {**historical_means, current_year: current_mean}
    ordered = sorted(all_means.items(), key=lambda kv: kv[1], reverse=True)
    rank = next(i for i, (y, _) in enumerate(ordered, start=1) if y == current_year)
    top_summers = [
        {
            "rank": i,
            "year": y,
            "mean_tmax_c": round(v, 1),
            "is_current_summer": y == current_year,
        }
        for i, (y, v) in enumerate(ordered[:5], start=1)
    ]

    return {
        "percentile": int(percentile),
        "rank": rank,
        "total_summers": len(all_means),
        "top_summers": top_summers,
    }


def compute_stats(df: pd.DataFrame, year: int, lat: float | None = None) -> dict:
    """All four stat blocks for a station's daily history and target summer
    `year`. Each block is always present; a block a station's data can't
    support carries {"insufficient_data": True} instead of being omitted, so
    callers/templates never have to distinguish "not computed" from "key
    missing"."""
    if "TMAX" not in df.columns:
        logger.warning("compute_stats: station has no TMAX column, nothing computable")
        return {
            "summer_year": year,
            "anomaly": {"insufficient_data": True},
            "hot_days_trend": {"insufficient_data": True},
            "hottest_nights": {"insufficient_data": True},
            "percentile_rank": {"insufficient_data": True},
        }

    return {
        "summer_year": year,
        "anomaly": compute_anomaly(df, year, lat),
        "hot_days_trend": compute_hot_days_trend(df, year, lat),
        "hottest_nights": compute_hottest_nights(df, year, lat),
        "percentile_rank": compute_percentile_rank(df, year, lat),
    }
