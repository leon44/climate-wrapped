# Summer Wrapped

A "Spotify Wrapped"-style story for a GHCN-D weather station's most recent
summer, compared against its full climate record.

## Stack & decisions

- **Flask**, not FastAPI — this app is almost entirely server-rendered HTML
  plus a couple of small JSON endpoints for the map/search box; Flask's
  simpler templating story fit that better than FastAPI's async-first,
  schema-first design, which would've been overhead here (no async I/O to
  exploit, no OpenAPI consumers).
- **Pandas** for all the time-series stats (`app/stats.py`).
- **Leaflet** (station picker map) + **Chart.js** (wrapped-card charts) via
  CDN — no SPA framework, no frontend build step. The basemap tiles are
  Stamen Watercolor, hosted by Stadia Maps — needs a free `STADIA_API_KEY`
  (signup at stadiamaps.com; no card required for the free tier). Unlike a
  typical secret, this key is meant to be used client-side (`static/js/map.js`
  puts it straight in the tile URL) and is restricted by domain/referrer in
  the Stadia dashboard rather than kept server-side. (We tried Apple MapKit
  JS first — the server-side JWT signing worked and Apple's servers accepted
  it, but headless-browser testing couldn't confirm the visual render, and
  Watercolor was a better fit for the vibe anyway.)
- **No database.** The station list is a bundled JSON file built once at
  build time (`scripts/build_stations.py`); GHCN-D CSVs and computed
  wrapped stats are cached to flat files under `cache/`.
- **Deploy target: DigitalOcean App Platform**, not a Droplet+gunicorn.
  App Platform was simpler to wire up for v1 — push-to-deploy from a
  GitHub repo, no server/SSH/systemd/nginx to manage. The tradeoff worth
  knowing: App Platform's filesystem is writable but **ephemeral** — it's
  wiped on every redeploy or restart. That's fine for `cache/`, since it
  just refills itself from NOAA on a cold start, but it means the "never
  re-fetch immutable prior years" optimization only holds between
  restarts, not across them. If that starts to matter (e.g. paying for
  NOAA bandwidth/latency on every deploy), the fix is either a DO Volume
  mounted at `cache/`, or moving the stats cache to a tiny managed
  Postgres/Spaces bucket — no code changes needed beyond swapping
  `app/config.py`'s cache paths.

## Scope decisions made without asking further

These were flagged as open questions in the brief; here's what v1 does and why:

- **Northern Hemisphere only.** Station list (`scripts/build_stations.py`)
  filters to `lat > 0`, and the gate (`app/gate.py`) only knows the Jun 1 -
  Aug 31 window. Confirmed with the user before building — Southern
  Hemisphere support would mean carrying a second summer-window/gate path
  and handling a station's "year" spanning a Dec-Feb boundary, which felt
  like real added complexity better done as a deliberate follow-up.
- **Visual style: bold Spotify-Wrapped**. Full-bleed gradient cards,
  oversized stat numbers, minimal chrome — `static/css/style.css`.
- **Gate targets the current calendar year's summer, not "most recently
  completed."** i.e. during Jun-Aug (and for `GATE_REPORTING_LAG_DAYS`
  after), the app shows "come back after summer ends" rather than quietly
  falling back to last year's wrapped. This matches the explicit UI copy
  in the brief and the Wrapped framing (it's *this* year's story or
  nothing) — flagging in case the intent was actually "always show
  whatever's most recently complete."
- **"Heatwave" = consecutive days with TMAX ≥ 30°C**, and **"dry spell" =
  consecutive days with PRCP < 1.0mm** (`app/stats.py`). GHCN-D doesn't
  define either term itself; these are common-sense thresholds, pinned as
  constants (`HEATWAVE_C`, `DRY_DAY_MM`) so they're easy to tune later. A
  day with missing data breaks a streak rather than either continuing or
  resetting it silently — we'd rather undercount a streak than fabricate
  one across a data gap.
- **Narrative layer: plain Python templates (option 1)**, not the Claude
  API. Structured behind a `Narrator` interface (`app/narrative.py`) so a
  `ClaudeNarrator` can be added later as a second implementation without
  touching `stats.py`, `ranking.py`, or the routes/templates — the cache
  in `routes.py` already caches the whole `{stats, cards}` payload
  together, so swapping narrators later still only costs one API call per
  station per summer.
- **Search is substring matching** on station name/state/id
  (`app/stations.py`), not fuzzy. GHCN-D station names are often
  abbreviated ("CNTRL" not "CENTRAL") — good enough for v1, but a
  misspelled search will come up empty. Typeahead-as-you-type mitigates
  this in practice.

## Project structure

```
app/
  config.py       paths, cache TTL, gate thresholds
  stations.py     loads bundled station list, search + nearest-station lookups
  ghcnd.py        fetch + disk-cache a station's GHCN-D CSV, parse to a clean DataFrame
  gate.py         summer-completion gate (calendar check + per-station data-completeness check)
  stats.py        compute_stats(df, year) -> plain dict of raw numbers
  ranking.py      scores/orders stat cards by "how interesting is this"
  narrative.py    turns (stats, ranking) into card copy; Narrator interface
  routes.py       Flask routes + wrapped-payload caching
scripts/
  build_stations.py   one-time: ghcnd-stations.txt + ghcnd-inventory.txt -> data/stations.json
data/
  stations.json   bundled, committed station list (build output)
templates/, static/  server-rendered HTML + vanilla JS/CSS
cache/
  raw/            cached per-station GHCN-D CSVs
  stats/          cached computed {stats, cards} per station+summer
tests/
  test_stats.py   sanity tests against two real, contrasting stations
```

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# only needed once, or whenever you want to refresh the station list:
python scripts/build_stations.py

export STADIA_API_KEY=your-free-key-from-stadiamaps.com   # basemap tiles; map is blank without it
python run.py   # http://localhost:5000
```

Run tests (hits the network on first run to fetch two real stations' CSVs,
then caches them under `cache/raw/`):

```bash
pytest tests/ -v
```

The two test stations were picked for contrast: `USW00094728` (NY Central
Park, ~155-year record, 40.8°N) and `CA002402351` (Grise Fiord, ~18-year
record, 76.4°N Arctic) — one has decades of dense data and every element;
the other is short, sparse, and barely has a "summer" by mid-latitude
standards. Both still produce a usable wrapped story, which is the point.

### Trying the wrapped story before summer actually ends

The gate is real — locally, `/wrapped/<station_id>` will show the "come
back after summer ends" page until `Aug 31 + GATE_REPORTING_LAG_DAYS` of
the current year. To preview the actual card sequence sooner, either:

- set your system clock forward (not recommended), or
- temporarily monkeypatch `app.gate.gate_status` (see the pattern used in
  `tests/`) in a throwaway script, or
- lower `GATE_REPORTING_LAG_DAYS` / temporarily hardcode a past
  `summer_year` while developing UI.

## Deploying to DigitalOcean App Platform

1. Push this repo to GitHub.
2. In the DO dashboard: **Apps → Create App → GitHub**, pick the repo/branch.
   App Platform will detect it as a Python app; point it at `requirements.txt`
   and set the run command to:
   ```
   gunicorn run:app --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT
   ```
   (already in `Procfile`, which DO also reads directly).
3. Or skip the dashboard and deploy the included spec directly:
   ```bash
   doctl apps create --spec app.yaml
   ```
   (edit `app.yaml`'s `github.repo` first).
4. No database is required for v1. Set `STADIA_API_KEY` (free tier from
   stadiamaps.com) so the basemap actually renders. Optional env vars:
   `RAW_CACHE_TTL_SECONDS`, `GATE_REPORTING_LAG_DAYS` (see `app/config.py`).
5. See the ephemeral-filesystem note above re: `cache/` on App Platform.

## Data source & encoding notes

- Daily CSVs: `https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/{STATION_ID}.csv`
- Station metadata: NOAA's `ghcnd-stations.txt` + `ghcnd-inventory.txt`,
  parsed once by `scripts/build_stations.py` (fixed-width format per
  NOAA's own column spec, not re-derived).
- TMAX/TMIN are tenths of °C, PRCP is tenths of mm in the raw format;
  `app/ghcnd.py` converts to real units on load. Missing values are blank
  in this CSV export (GHCN-D's fixed-width `.dly` format uses `-9999` for
  the same thing; both are treated as missing, defensively).
- Station list is filtered at build time to ≥20 years of TMAX+TMIN record,
  still reporting within the last year, Northern Hemisphere only
  (`app/config.py: MIN_RECORD_YEARS`, `MAX_YEARS_SINCE_LAST_REPORT`).
- Not every station reports every element — `compute_stats` and the
  narrative layer both skip (never fake) stat blocks a station's data
  can't support; see `tests/test_stats.py::test_missing_elements_are_omitted_not_faked`.
