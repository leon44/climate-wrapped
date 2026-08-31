"""One-time build script: parse NOAA's ghcnd-stations.txt + ghcnd-inventory.txt
into a small bundled JSON file the app ships with, so station search/map works
with zero network calls at runtime.

Run this locally whenever the station list needs refreshing, then commit the
resulting data/stations.json:

    python scripts/build_stations.py

v1 scope: Northern Hemisphere only (see project decision log in README), so
this also drops any station with latitude <= 0.
"""

import datetime
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DATA_DIR, MAX_YEARS_SINCE_LAST_REPORT, MIN_RECORD_YEARS  # noqa: E402

STATIONS_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
INVENTORY_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt"

RAW_DIR = Path(__file__).resolve().parent / "raw"
STATIONS_RAW = RAW_DIR / "ghcnd-stations.txt"
INVENTORY_RAW = RAW_DIR / "ghcnd-inventory.txt"

# Elements a station needs at least TMAX+TMIN for to be useful for a "wrapped"
# summer story; PRCP is nice-to-have and handled as optional at query time.
REQUIRED_ELEMENTS = {"TMAX", "TMIN"}
TRACKED_ELEMENTS = {"TMAX", "TMIN", "PRCP", "AWND", "SNOW", "SNWD"}


def download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"  using cached {dest.name}")
        return
    print(f"  downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)


def parse_stations(path: Path) -> dict:
    """Fixed-width ghcnd-stations.txt -> {station_id: {...}}"""
    stations = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            station_id = line[0:11].strip()
            lat = float(line[12:20].strip())
            lon = float(line[21:30].strip())
            elev = line[31:37].strip()
            state = line[38:40].strip()
            name = line[41:71].strip()
            stations[station_id] = {
                "id": station_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "elevation": float(elev) if elev not in ("", "-999.9") else None,
                "state": state,
            }
    return stations


def parse_inventory(path: Path) -> dict:
    """Fixed-width ghcnd-inventory.txt -> {station_id: {element: (first, last)}}"""
    inventory: dict = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            station_id = line[0:11].strip()
            element = line[31:35].strip()
            first_year = int(line[36:40].strip())
            last_year = int(line[41:45].strip())
            inventory.setdefault(station_id, {})[element] = (first_year, last_year)
    return inventory


def build() -> list:
    print("Parsing stations...")
    stations = parse_stations(STATIONS_RAW)
    print(f"  {len(stations)} stations total")

    print("Parsing inventory...")
    inventory = parse_inventory(INVENTORY_RAW)

    current_year = datetime.date.today().year
    min_last_year = current_year - MAX_YEARS_SINCE_LAST_REPORT

    result = []
    for station_id, station in stations.items():
        if station["lat"] <= 0:
            continue  # Southern Hemisphere out of scope for v1

        elements = inventory.get(station_id, {})
        present = {el for el in TRACKED_ELEMENTS if el in elements}
        if not REQUIRED_ELEMENTS.issubset(present):
            continue

        core_years = [elements[el] for el in REQUIRED_ELEMENTS if el in elements]
        first_year = min(y[0] for y in core_years)
        last_year = max(y[1] for y in core_years)

        if (last_year - first_year + 1) < MIN_RECORD_YEARS:
            continue
        if last_year < min_last_year:
            continue

        station["first_year"] = first_year
        station["last_year"] = last_year
        station["elements"] = sorted(present)
        result.append(station)

    result.sort(key=lambda s: s["id"])
    return result


def main():
    download(STATIONS_URL, STATIONS_RAW)
    download(INVENTORY_URL, INVENTORY_RAW)

    stations = build()
    print(f"Kept {len(stations)} stations after filtering "
          f"(NH only, >={MIN_RECORD_YEARS}y record, reporting within "
          f"{MAX_YEARS_SINCE_LAST_REPORT}y)")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "stations.json"
    out_path.write_text(json.dumps(stations, indent=None, separators=(",", ":")))
    print(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
