"""Central configuration for paths, caching, and gate thresholds."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
STATIONS_FILE = DATA_DIR / "stations.json"

CACHE_DIR = BASE_DIR / "cache"
RAW_CACHE_DIR = CACHE_DIR / "raw"
STATS_CACHE_DIR = CACHE_DIR / "stats"

GHCND_CSV_URL = (
    "https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily"
    "/access/{station_id}.csv"
)

# Re-fetch a station's raw CSV if the cached copy is older than this, but only
# ever matters while a summer is still in progress or the current summer's
# stats haven't been computed yet -- see app/ghcnd.py. Once a summer's wrapped
# stats are cached, that year is treated as immutable and never re-fetched.
RAW_CACHE_TTL_SECONDS = int(os.environ.get("RAW_CACHE_TTL_SECONDS", 24 * 60 * 60))

# Northern-hemisphere meteorological summer.
SUMMER_START_MONTH_DAY = (6, 1)
SUMMER_END_MONTH_DAY = (8, 31)

# Station inclusion filters (used at build time by scripts/build_stations.py).
MIN_RECORD_YEARS = 40
MAX_YEARS_SINCE_LAST_REPORT = 1

# A few days of grace after Aug 31 to let NOAA's pipeline catch up before we
# trust a station's summer data as "fully present".
GATE_REPORTING_LAG_DAYS = int(os.environ.get("GATE_REPORTING_LAG_DAYS", 5))

# Testing/dev-only override: set FAKE_TODAY=YYYY-MM-DD to make the gate
# (app/gate.py) believe it's that date instead of the real one, so you can
# preview the wrapped story before the actual summer-completion gate opens.
# Unset (the default) in any real deployment -- this only affects the gate's
# calendar check, not the station data itself.
FAKE_TODAY = os.environ.get("FAKE_TODAY")

# Stadia Maps API key for the Stamen Watercolor basemap tiles (free tier,
# signup at stadiamaps.com). Unlike a typical secret key, Stadia's key is
# designed to be used directly in client-side tile requests -- it's exposed
# in the page source, restricted by domain/referrer in the Stadia dashboard
# rather than kept server-side. See templates/index.html + static/js/map.js.
STADIA_API_KEY = os.environ.get("STADIA_API_KEY", "")
