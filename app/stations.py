"""Loads the bundled station list and answers search / nearest-station
queries. Pure in-memory lookups -- no network calls, so this module never
touches ghcnd.py.
"""

import json
import math
from functools import lru_cache

from app.config import STATIONS_FILE


@lru_cache(maxsize=1)
def _load_stations() -> list:
    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


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


def nearest_stations(lat: float, lon: float, limit: int = 8) -> list:
    """Nearest stations to (lat, lon), each with a `distance_km` and
    `record_years` field added, sorted by distance."""
    results = []
    for s in _load_stations():
        dist = _haversine_km(lat, lon, s["lat"], s["lon"])
        results.append({**s, "distance_km": round(dist, 1),
                         "record_years": s["last_year"] - s["first_year"] + 1})
    results.sort(key=lambda s: s["distance_km"])
    return results[:limit]


def search_stations(query: str, limit: int = 8) -> list:
    """Simple case-insensitive substring match on name/state/id, longer
    record length breaking ties. Good enough for an autocomplete box without
    pulling in a search dependency."""
    q = query.strip().lower()
    if not q:
        return []

    scored = []
    for s in _load_stations():
        haystack = f"{s['name']} {s['state']} {s['id']}".lower()
        if q in haystack:
            record_years = s["last_year"] - s["first_year"] + 1
            starts_with = haystack.startswith(q) or s["name"].lower().startswith(q)
            scored.append((starts_with, record_years, {**s, "record_years": record_years}))

    scored.sort(key=lambda t: (not t[0], -t[1]))
    return [s for _, _, s in scored[:limit]]
