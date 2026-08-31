"""Summer-completion gate: decides whether the wrapped feature is allowed to
show data for the current target summer yet.

Design decision (flagged inline, confirm if this reading is wrong): the
target summer is always the *current calendar year's* Jun 1 - Aug 31 window,
Spotify-Wrapped-style -- the product is framed as "your summer", so before
this year's summer has finished we show a "come back after summer ends"
state rather than silently falling back to last year's data.
"""

import datetime

import pandas as pd

from app.config import (
    FAKE_TODAY,
    GATE_REPORTING_LAG_DAYS,
    SUMMER_END_MONTH_DAY,
    SUMMER_START_MONTH_DAY,
)
from app.ghcnd import summer_slice

MIN_DAY_COVERAGE = 0.9  # fraction of the 92 summer days that must have data


def _real_or_fake_today() -> datetime.date:
    """Real today, unless FAKE_TODAY is set (dev/testing override -- see
    app/config.py)."""
    if FAKE_TODAY:
        return datetime.date.fromisoformat(FAKE_TODAY)
    return datetime.date.today()


def summer_window(year: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, *SUMMER_START_MONTH_DAY)
    end = datetime.date(year, *SUMMER_END_MONTH_DAY)
    return start, end


def target_summer_year(today: datetime.date | None = None) -> int:
    today = today or _real_or_fake_today()
    return today.year


def gate_opens_on(year: int) -> datetime.date:
    _, end = summer_window(year)
    return end + datetime.timedelta(days=GATE_REPORTING_LAG_DAYS)


def gate_status(today: datetime.date | None = None) -> dict:
    """Calendar-only gate check -- doesn't require any station data."""
    today = today or _real_or_fake_today()
    year = target_summer_year(today)
    start, end = summer_window(year)
    opens_on = gate_opens_on(year)
    return {
        "summer_year": year,
        "is_open": today >= opens_on,
        "opens_on": opens_on.isoformat(),
        "summer_start": start.isoformat(),
        "summer_end": end.isoformat(),
        "today": today.isoformat(),
    }


def station_summer_data_complete(df: pd.DataFrame, year: int) -> bool:
    """A station may still pass the calendar gate but lag on actually
    publishing this year's data -- check day coverage for the elements that
    matter most (TMAX/TMIN) before treating the summer as usable."""
    days_in_summer = (summer_window(year)[1] - summer_window(year)[0]).days + 1
    season = summer_slice(df, year)

    for col in ("TMAX", "TMIN"):
        if col not in season.columns:
            continue
        coverage = season[col].notna().sum() / days_in_summer
        if coverage < MIN_DAY_COVERAGE:
            return False
    return True
