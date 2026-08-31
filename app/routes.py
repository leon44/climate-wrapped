import datetime
import json

from flask import Blueprint, abort, jsonify, render_template, request

from app import gate, ghcnd, stations
from app.config import STADIA_API_KEY, STATS_CACHE_DIR
from app.ghcnd import StationFetchError
from app.narrative import get_narrator
from app.stats import compute_stats

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template("index.html", stadia_api_key=STADIA_API_KEY)


@bp.route("/api/stations/search")
def api_search():
    q = request.args.get("q", "")
    return jsonify(stations.search_stations(q, limit=10))


@bp.route("/api/stations/all")
def api_all_stations():
    return jsonify(stations.all_stations_geo())


@bp.route("/api/stations/nearest")
def api_nearest():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        abort(400, "lat and lon query params are required")
    return jsonify(stations.nearest_stations(lat, lon, limit=8))


def _stats_cache_path(station_id: str, year: int):
    return STATS_CACHE_DIR / f"{station_id}_{year}.json"


def _load_cached_wrapped(station_id: str, year: int) -> dict | None:
    path = _stats_cache_path(station_id, year)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cached_wrapped(station_id: str, year: int, payload: dict) -> None:
    STATS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _stats_cache_path(station_id, year).write_text(json.dumps(payload), encoding="utf-8")


#: Fixed date used by the "2025" preview checkbox on the landing page --
#: lets you demo the wrapped story on a real deploy before this year's
#: summer-completion gate has opened, without a permanent FAKE_TODAY env
#: var (see app/config.py) forcing that date for every visitor.
PREVIEW_2025_DATE = datetime.date(2025, 9, 15)


@bp.route("/wrapped/<station_id>")
def wrapped(station_id):
    station = stations.get_station(station_id)
    if not station:
        abort(404, f"Unknown station {station_id}")

    preview_today = PREVIEW_2025_DATE if request.args.get("preview") == "2025" else None
    calendar_gate = gate.gate_status(preview_today)
    if not calendar_gate["is_open"]:
        print(
            f"[wrapped gate] {station_id} ({station.get('name')}) blocked: "
            f"calendar gate not open yet -- today={calendar_gate['today']}, "
            f"opens_on={calendar_gate['opens_on']}"
        )
        return render_template(
            "gate.html", station=station, gate=calendar_gate,
            reason="calendar",
        )

    year = calendar_gate["summer_year"]

    cached = _load_cached_wrapped(station_id, year)
    if cached:
        return render_template("wrapped.html", station=station, cards=cached["cards"],
                                stats=cached["stats"], year=year)

    try:
        df = ghcnd.get_station_dataframe(station_id)
    except StationFetchError:
        abort(502, f"Could not fetch data for station {station_id}")

    if not gate.station_summer_data_complete(df, year):
        season = ghcnd.summer_slice(df, year)
        start, end = gate.summer_window(year)
        days_in_summer = (end - start).days + 1
        coverage = {
            col: round(season[col].notna().sum() / days_in_summer, 3)
            for col in ("TMAX", "TMIN") if col in season.columns
        }
        print(
            f"[wrapped gate] {station_id} ({station.get('name')}) blocked: "
            f"summer {year} data incomplete -- coverage={coverage} "
            f"(need >= {gate.MIN_DAY_COVERAGE} for each of TMAX/TMIN)"
        )
        return render_template(
            "gate.html", station=station, gate=calendar_gate,
            reason="data_incomplete",
        )

    stats = compute_stats(df, year, station.get("lat"))
    cards = get_narrator().build_cards(stats, station)

    _save_cached_wrapped(station_id, year, {
        "stats": stats, "cards": cards,
        "computed_at": datetime.datetime.utcnow().isoformat(),
    })

    return render_template("wrapped.html", station=station, cards=cards, stats=stats, year=year)
