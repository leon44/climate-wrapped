"""Loads the bundled station list and answers search / nearest-station
queries. Pure in-memory lookups -- no network calls, so this module never
touches ghcnd.py.
"""

import json
import math
from functools import lru_cache

from app.config import STATIONS_FILE
from app.gate import target_summer_year


def _titlecase(name: str) -> str:
    """First letter of each space-separated word capitalized, rest
    lowercase -- station names in the source data are all caps."""
    return " ".join(word.capitalize() for word in name.split(" "))


@lru_cache(maxsize=1)
def _load_stations() -> list:
    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        stations = json.load(f)
    for s in stations:
        s["name"] = _titlecase(s["name"])
    return stations


@lru_cache(maxsize=1)
def _by_id() -> dict:
    return {s["id"]: s for s in _load_stations()}


def get_station(station_id: str) -> dict | None:
    return _by_id().get(station_id)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _likely_reporting_for(station: dict, year: int) -> bool:
    """Cheap, network-free heuristic: a station whose inventory record
    already ended before the target summer can't possibly have that
    summer's data, so drop it. This is a metadata-only filter -- it does
    NOT catch stations that are still nominally active but reporting too
    sparsely within the target summer (see gate.station_summer_data_complete,
    which needs the actual daily data to catch those)."""
    return station["last_year"] >= year


@lru_cache(maxsize=4)
def all_stations_geo(year: int | None = None) -> tuple:
    """Minimal station geometry for the overview map (id/name/state/lat/lon
    plus record length) -- no distance or search scoring, so this is cheap
    enough to hand every station to the client for clustering. Stations
    whose inventory record ends before `year` (default: this gate cycle's
    target summer) are excluded -- see `_likely_reporting_for`."""
    year = year or target_summer_year()
    return tuple(
        {
            "id": s["id"],
            "name": s["name"],
            "state": s["state"],
            "lat": s["lat"],
            "lon": s["lon"],
            "first_year": s["first_year"],
            "last_year": s["last_year"],
            "record_years": s["last_year"] - s["first_year"] + 1,
        }
        for s in _load_stations()
        if _likely_reporting_for(s, year)
    )


def nearest_stations(lat: float, lon: float, limit: int = 8) -> list:
    """Nearest stations to (lat, lon), each with a `distance_km` and
    `record_years` field added, sorted by distance. Excludes stations
    unlikely to have this summer's data -- see `_likely_reporting_for`."""
    year = target_summer_year()
    results = []
    for s in _load_stations():
        if not _likely_reporting_for(s, year):
            continue
        dist = _haversine_km(lat, lon, s["lat"], s["lon"])
        results.append({**s, "distance_km": round(dist, 1),
                         "record_years": s["last_year"] - s["first_year"] + 1})
    results.sort(key=lambda s: s["distance_km"])
    return results[:limit]


def search_stations(query: str, limit: int = 8) -> list:
    """Simple case-insensitive substring match on name/state/id, longer
    record length breaking ties. Good enough for an autocomplete box without
    pulling in a search dependency. Excludes stations unlikely to have this
    summer's data -- see `_likely_reporting_for`."""
    q = query.strip().lower()
    if not q:
        return []

    year = target_summer_year()
    scored = []
    for s in _load_stations():
        if not _likely_reporting_for(s, year):
            continue
        haystack = f"{s['name']} {s['state']} {s['id']}".lower()
        if q in haystack:
            record_years = s["last_year"] - s["first_year"] + 1
            starts_with = haystack.startswith(q) or s["name"].lower().startswith(q)
            scored.append((starts_with, record_years, {**s, "record_years": record_years}))

    scored.sort(key=lambda t: (not t[0], -t[1]))
    return [s for _, _, s in scored[:limit]]
