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


@bp.route("/wrapped/<station_id>")
def wrapped(station_id):
    station = stations.get_station(station_id)
    if not station:
        abort(404, f"Unknown station {station_id}")

    calendar_gate = gate.gate_status()
    if not calendar_gate["is_open"]:
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
        return render_template(
            "gate.html", station=station, gate=calendar_gate,
            reason="data_incomplete",
        )

    stats = compute_stats(df, year)
    cards = get_narrator().build_cards(stats, station)

    _save_cached_wrapped(station_id, year, {
        "stats": stats, "cards": cards,
        "computed_at": datetime.datetime.utcnow().isoformat(),
    })

    return render_template("wrapped.html", station=station, cards=cards, stats=stats, year=year)
