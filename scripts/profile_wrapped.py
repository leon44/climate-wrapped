"""Deep-dive profiler for a single station's wrapped computation --
fetch/parse + compute_stats -- using cProfile.

The app itself logs a one-line-per-phase timing summary on every
non-cached /wrapped/<station_id> request (see [wrapped timing] and
[stats timing] in the runtime logs, app/routes.py + app/stats.py). Reach
for this script when that summary says *which* phase is slow and you need
the full call-graph breakdown of *why*.

Usage:
    python scripts/profile_wrapped.py UKE00105881
    python scripts/profile_wrapped.py UKE00105881 --year 2025 --sort tottime
"""

import argparse
import cProfile
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ghcnd  # noqa: E402
from app.gate import target_summer_year  # noqa: E402
from app.stats import compute_stats  # noqa: E402
from app.stations import get_station  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("station_id")
    parser.add_argument("--year", type=int, default=None, help="defaults to the current target summer year")
    parser.add_argument("--sort", default="cumulative", help="pstats sort key, e.g. cumulative, tottime, calls")
    parser.add_argument("--top", type=int, default=30, help="number of rows to print")
    parser.add_argument("--force-refresh", action="store_true", help="bypass the raw CSV cache")
    args = parser.parse_args()

    station = get_station(args.station_id)
    if not station:
        parser.error(f"unknown station {args.station_id}")

    year = args.year or target_summer_year()

    profiler = cProfile.Profile()
    profiler.enable()

    df = ghcnd.get_station_dataframe(args.station_id, force_refresh=args.force_refresh)
    compute_stats(df, year, station.get("lat"))

    profiler.disable()

    stats = pstats.Stats(profiler).sort_stats(args.sort)
    stats.print_stats(args.top)


if __name__ == "__main__":
    main()
