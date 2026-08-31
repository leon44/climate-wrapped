"""Fetches a station's daily history from GHCN-D and caches it to disk.

GHCN-D encoding notes (see NOAA's readme.txt):
  - TMAX / TMIN are tenths of degrees C.
  - PRCP is tenths of mm.
  - AWND is tenths of m/s.
  - Missing values are blank in the "access" CSV export we use here (the
    fixed-width .dly format uses -9999 for the same thing, so we treat that
    as missing too, defensively).
"""

import datetime
import io
import time
from pathlib import Path

import pandas as pd
import requests

from app.config import GHCND_CSV_URL, RAW_CACHE_DIR, RAW_CACHE_TTL_SECONDS

# Columns we actually need out of the ~120-column access CSV.
_WANTED_RAW_COLUMNS = ["DATE", "TMAX", "TMIN", "PRCP", "AWND", "SNOW", "SNWD"]

# Raw units -> real units. Columns not listed here are left as-is.
_TENTHS_COLUMNS = {"TMAX": 10.0, "TMIN": 10.0, "PRCP": 10.0, "AWND": 10.0}

MISSING_SENTINELS = (-9999,)


class StationFetchError(Exception):
    pass


def _raw_cache_path(station_id: str) -> Path:
    return RAW_CACHE_DIR / f"{station_id}.csv"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < RAW_CACHE_TTL_SECONDS


def _download_csv(station_id: str) -> str:
    url = GHCND_CSV_URL.format(station_id=station_id)
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        raise StationFetchError(f"No GHCN-D record found for station {station_id}")
    resp.raise_for_status()
    return resp.text


def fetch_raw_csv_text(station_id: str, force_refresh: bool = False) -> str:
    """Returns the raw CSV text for a station, using the on-disk cache when
    it's fresh enough (or when a fetch fails and a stale copy exists, so a
    transient NOAA outage doesn't break the app)."""
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _raw_cache_path(station_id)

    if not force_refresh and _is_cache_fresh(cache_path):
        return cache_path.read_text(encoding="utf-8")

    try:
        text = _download_csv(station_id)
    except (requests.RequestException, StationFetchError):
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        raise

    cache_path.write_text(text, encoding="utf-8")
    return text


def _parse_csv(csv_text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(csv_text), low_memory=False)
    available = [c for c in _WANTED_RAW_COLUMNS if c in df.columns]
    df = df[available].copy()

    df["DATE"] = pd.to_datetime(df["DATE"])

    for col, divisor in _TENTHS_COLUMNS.items():
        if col not in df.columns:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col].isin(MISSING_SENTINELS), col] = pd.NA
        df[col] = df[col] / divisor

    df = df.sort_values("DATE").reset_index(drop=True)
    return df


def get_station_dataframe(station_id: str, force_refresh: bool = False) -> pd.DataFrame:
    """Full daily history for a station as a clean DataFrame with a DATE
    column plus whichever of TMAX/TMIN/PRCP/AWND/SNOW/SNWD the station
    reports, in real units (deg C, mm, m/s), NaN for missing days."""
    csv_text = fetch_raw_csv_text(station_id, force_refresh=force_refresh)
    return _parse_csv(csv_text)


def elements_present(df: pd.DataFrame) -> set:
    core = {"TMAX", "TMIN", "PRCP", "AWND", "SNOW", "SNWD"}
    return {c for c in core if c in df.columns and df[c].notna().any()}


def summer_slice(df: pd.DataFrame, year: int, start_md=(6, 1), end_md=(8, 31)) -> pd.DataFrame:
    start = datetime.date(year, *start_md)
    end = datetime.date(year, *end_md)
    mask = (df["DATE"].dt.date >= start) & (df["DATE"].dt.date <= end)
    return df.loc[mask]
