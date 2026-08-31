"""Raw stats computation for a station's daily history.

compute_stats(df, year) returns a single plain, JSON-serializable dict. It
only computes numbers -- it has no opinion about which numbers are
interesting or how to phrase them (see ranking.py and narrative.py for that).

Every stat block adapts to whatever elements the station actually reports:
if a station has no PRCP, rainfall-related keys are simply omitted rather
than raising or faking zeros.

A couple of definitions aren't standardized by GHCN-D itself, so they're
pinned here as explicit assumptions:
  - "heatwave" = a run of consecutive summer days with TMAX >= HEATWAVE_C.
  - "dry spell" = a run of consecutive summer days with PRCP < DRY_DAY_MM.
  - a day with missing data never counts toward either streak, and breaks
    a streak in progress (we'd rather undercount a streak than fabricate one
    across a data gap).
"""

import datetime

import numpy as np
import pandas as pd

from app.config import HOT_DAY_THRESHOLDS_C
from app.ghcnd import summer_slice

HEATWAVE_C = 30.0
DRY_DAY_MM = 1.0
MIN_SUMMER_COVERAGE = 0.8  # fraction of 92 days required for a summer to count in history
SUMMER_DAYS = 92  # Jun 1 - Aug 31


def _native(x):
    """numpy/pandas scalar -> plain python type, NaN -> None."""
    if x is None:
        return None
    if isinstance(x, (np.generic,)):
        x = x.item()
    if isinstance(x, float) and np.isnan(x):
        return None
    if isinstance(x, (pd.Timestamp, datetime.date, datetime.datetime)):
        return x.date().isoformat() if hasattr(x, "date") else x.isoformat()
    return x


def _longest_run(mask: pd.Series) -> int:
    """Longest run of consecutive True values; NaN/False breaks the run."""
    if mask.empty:
        return 0
    filled = mask.fillna(False).astype(bool).to_numpy()
    best = cur = 0
    for v in filled:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return int(best)


def _summers_by_year(df: pd.DataFrame) -> dict:
    """{year: summer-slice DataFrame} for every year with a sufficiently
    complete Jun-Aug window, based on TMAX coverage."""
    if "TMAX" not in df.columns:
        return {}
    years = sorted(df["DATE"].dt.year.unique())
    out = {}
    for year in years:
        season = summer_slice(df, int(year))
        if season.empty:
            continue
        coverage = season["TMAX"].notna().sum() / SUMMER_DAYS
        if coverage >= MIN_SUMMER_COVERAGE:
            out[int(year)] = season
    return out


def _mean_temp_stats(summers: dict, this_year: int) -> dict | None:
    means = {y: (s["TMAX"] + s["TMIN"]).mean() / 2 for y, s in summers.items()
             if "TMIN" in s.columns}
    means = {y: v for y, v in means.items() if not np.isnan(v)}
    if this_year not in means or len(means) < 2:
        return None

    ordered = sorted(means.items(), key=lambda kv: kv[1], reverse=True)
    rank = next(i for i, (y, _) in enumerate(ordered, start=1) if y == this_year)
    n = len(ordered)
    warmer_than = sum(1 for y, v in means.items() if y != this_year and v < means[this_year])
    percentile = round(100 * warmer_than / (n - 1), 1) if n > 1 else 100.0

    return {
        "this_year_mean_c": round(float(means[this_year]), 2),
        "long_term_mean_c": round(float(np.mean(list(means.values()))), 2),
        "rank": rank,
        "n_summers": n,
        "percentile": percentile,
        "first_year": min(means),
        "last_year": max(means),
    }


def _hottest_day_stats(df: pd.DataFrame, summers: dict, this_year: int) -> dict | None:
    if "TMAX" not in df.columns or this_year not in summers:
        return None
    all_time = df.dropna(subset=["TMAX"]).sort_values("TMAX", ascending=False).reset_index(drop=True)
    if all_time.empty:
        return None

    this_season = summers[this_year].dropna(subset=["TMAX"])
    if this_season.empty:
        return None
    hottest_row = this_season.loc[this_season["TMAX"].idxmax()]

    all_time_max_row = all_time.iloc[0]
    rank_series = all_time.index[all_time["DATE"] == hottest_row["DATE"]]
    rank = int(rank_series[0]) + 1 if len(rank_series) else None

    return {
        "date": _native(hottest_row["DATE"]),
        "tmax_c": round(float(hottest_row["TMAX"]), 1),
        "all_time_rank": rank,
        "all_time_n_days": len(all_time),
        "all_time_hottest_c": round(float(all_time_max_row["TMAX"]), 1),
        "all_time_hottest_date": _native(all_time_max_row["DATE"]),
    }


def _hot_day_counts(summers: dict, this_year: int, thresholds=HOT_DAY_THRESHOLDS_C) -> dict | None:
    if this_year not in summers or "TMAX" not in summers[this_year].columns:
        return None
    out = {}
    for t in thresholds:
        this_count = int((summers[this_year]["TMAX"] >= t).sum())
        historical_counts = [int((s["TMAX"] >= t).sum()) for y, s in summers.items()
                              if y != this_year and "TMAX" in s.columns]
        avg = round(float(np.mean(historical_counts)), 1) if historical_counts else None
        out[f"days_ge_{t}c"] = {"this_year": this_count, "long_term_avg": avg}
    return out


def _heatwave_stats(summers: dict, this_year: int) -> dict | None:
    if this_year not in summers or "TMAX" not in summers[this_year].columns:
        return None
    this_streak = _longest_run(summers[this_year]["TMAX"] >= HEATWAVE_C)
    all_streaks = {y: _longest_run(s["TMAX"] >= HEATWAVE_C) for y, s in summers.items()
                   if "TMAX" in s.columns}
    record_year, record_streak = max(all_streaks.items(), key=lambda kv: kv[1]) if all_streaks else (None, 0)
    return {
        "this_year_days": this_streak,
        "record_days": record_streak,
        "record_year": record_year,
        "threshold_c": HEATWAVE_C,
        "is_record": this_streak >= record_streak and this_streak > 0,
    }


def _rainfall_stats(summers: dict, this_year: int) -> dict | None:
    if this_year not in summers or "PRCP" not in summers[this_year].columns:
        return None
    totals = {y: s["PRCP"].sum(min_count=1) for y, s in summers.items() if "PRCP" in s.columns}
    totals = {y: v for y, v in totals.items() if not pd.isna(v)}
    if this_year not in totals or len(totals) < 2:
        return None

    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    rank = next(i for i, (y, _) in enumerate(ordered, start=1) if y == this_year)
    n = len(ordered)

    return {
        "this_year_mm": round(float(totals[this_year]), 1),
        "long_term_avg_mm": round(float(np.mean(list(totals.values()))), 1),
        "rank": rank,
        "n_summers": n,
    }


def _wettest_day_stats(df: pd.DataFrame, summers: dict, this_year: int) -> dict | None:
    if "PRCP" not in df.columns or this_year not in summers:
        return None
    all_time = df.dropna(subset=["PRCP"]).sort_values("PRCP", ascending=False).reset_index(drop=True)
    if all_time.empty:
        return None
    this_season = summers[this_year].dropna(subset=["PRCP"])
    if this_season.empty:
        return None
    wettest_row = this_season.loc[this_season["PRCP"].idxmax()]
    rank_series = all_time.index[all_time["DATE"] == wettest_row["DATE"]]
    rank = int(rank_series[0]) + 1 if len(rank_series) else None

    return {
        "date": _native(wettest_row["DATE"]),
        "prcp_mm": round(float(wettest_row["PRCP"]), 1),
        "all_time_rank": rank,
        "all_time_n_days": len(all_time),
        "all_time_wettest_mm": round(float(all_time.iloc[0]["PRCP"]), 1),
        "all_time_wettest_date": _native(all_time.iloc[0]["DATE"]),
    }


def _dry_spell_stats(summers: dict, this_year: int) -> dict | None:
    if this_year not in summers or "PRCP" not in summers[this_year].columns:
        return None
    this_streak = _longest_run(summers[this_year]["PRCP"] < DRY_DAY_MM)
    all_streaks = {y: _longest_run(s["PRCP"] < DRY_DAY_MM) for y, s in summers.items()
                   if "PRCP" in s.columns}
    record_year, record_streak = max(all_streaks.items(), key=lambda kv: kv[1]) if all_streaks else (None, 0)
    return {
        "this_year_days": this_streak,
        "record_days": record_streak,
        "record_year": record_year,
        "is_record": this_streak >= record_streak and this_streak > 0,
    }


def _top_hottest_summers(summers: dict, top_n: int = 10, recent_years: int = 15) -> list | None:
    means = {y: (s["TMAX"] + s["TMIN"]).mean() / 2 for y, s in summers.items()
             if "TMIN" in s.columns}
    means = {y: v for y, v in means.items() if not np.isnan(v)}
    if not means:
        return None
    ordered = sorted(means.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    current_year = datetime.date.today().year
    return [
        {"rank": i, "year": y, "mean_temp_c": round(float(v), 2),
         "is_recent": (current_year - y) < recent_years}
        for i, (y, v) in enumerate(ordered, start=1)
    ]


def _decade_trend(summers: dict) -> list | None:
    means = {y: (s["TMAX"] + s["TMIN"]).mean() / 2 for y, s in summers.items()
             if "TMIN" in s.columns}
    means = {y: v for y, v in means.items() if not np.isnan(v)}
    if not means:
        return None
    buckets: dict = {}
    for y, v in means.items():
        decade = (y // 10) * 10
        buckets.setdefault(decade, []).append(v)
    return [
        {"decade": d, "mean_temp_c": round(float(np.mean(vals)), 2), "n_summers": len(vals)}
        for d, vals in sorted(buckets.items())
    ]


def _frost_day_trend(df: pd.DataFrame) -> dict | None:
    if "TMIN" not in df.columns:
        return None
    yearly = df.dropna(subset=["TMIN"]).groupby(df["DATE"].dt.year).apply(
        lambda g: int((g["TMIN"] < 0).sum())
    )
    if yearly.empty or len(yearly) < 2:
        return None
    years = yearly.index.to_numpy(dtype=float)
    counts = yearly.to_numpy(dtype=float)
    slope = float(np.polyfit(years, counts, 1)[0])

    buckets: dict = {}
    for y, c in yearly.items():
        decade = (int(y) // 10) * 10
        buckets.setdefault(decade, []).append(c)
    by_decade = [{"decade": d, "avg_frost_days": round(float(np.mean(vals)), 1)}
                 for d, vals in sorted(buckets.items())]

    return {"slope_days_per_year": round(slope, 3), "by_decade": by_decade}


def _diurnal_range_trend(df: pd.DataFrame) -> dict | None:
    if "TMAX" not in df.columns or "TMIN" not in df.columns:
        return None
    d = df.dropna(subset=["TMAX", "TMIN"]).copy()
    if d.empty:
        return None
    d["RANGE"] = d["TMAX"] - d["TMIN"]
    yearly = d.groupby(d["DATE"].dt.year)["RANGE"].mean()
    if len(yearly) < 2:
        return None
    years = yearly.index.to_numpy(dtype=float)
    ranges = yearly.to_numpy(dtype=float)
    slope = float(np.polyfit(years, ranges, 1)[0])

    buckets: dict = {}
    for y, r in yearly.items():
        decade = (int(y) // 10) * 10
        buckets.setdefault(decade, []).append(r)
    by_decade = [{"decade": d_, "avg_range_c": round(float(np.mean(vals)), 2)}
                 for d_, vals in sorted(buckets.items())]

    return {"slope_c_per_year": round(slope, 4), "by_decade": by_decade}


def compute_stats(df: pd.DataFrame, year: int) -> dict:
    """Everything computable from a station's daily history for a given
    target summer `year`. Keys for stats a station's data can't support are
    simply absent -- callers/templates should treat missing keys as "not
    available for this station", not as zero."""
    summers = _summers_by_year(df)

    stats = {
        "summer_year": year,
        "elements_present": sorted(c for c in ("TMAX", "TMIN", "PRCP", "AWND") if c in df.columns),
        "n_summers_on_record": len(summers),
        "mean_temp": _mean_temp_stats(summers, year),
        "hottest_day": _hottest_day_stats(df, summers, year),
        "hot_day_counts": _hot_day_counts(summers, year),
        "heatwave": _heatwave_stats(summers, year),
        "rainfall_total": _rainfall_stats(summers, year),
        "wettest_day": _wettest_day_stats(df, summers, year),
        "dry_spell": _dry_spell_stats(summers, year),
        "top_hottest_summers": _top_hottest_summers(summers),
        "decade_trend": _decade_trend(summers),
        "frost_day_trend": _frost_day_trend(df),
        "diurnal_range_trend": _diurnal_range_trend(df),
    }
    return {k: v for k, v in stats.items() if v is not None or k in ("summer_year", "elements_present")}
