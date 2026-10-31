"""Tooltip formatting for the dashboard.

Real-world AIS/ADS-B data has plenty of missing values. After a round-trip
through pandas a SQL NULL can arrive as ``None``, ``NaN`` or ``pd.NA`` – and
``NaN`` is truthy, so the usual ``value or default`` idiom does not work.
Everything here goes through :func:`is_missing` instead.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from btm.destinations import country_name


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value) or (isinstance(value, float) and math.isnan(value))  # NaN != NaN
    except (TypeError, ValueError):  # pd.NA raises on bool()
        return True


def text(value: Any, default: str = "") -> str:
    if is_missing(value):
        return default
    s = str(value).strip()
    return s or default


def number(value: Any, fmt: str = ",.0f", unit: str = "", default: str = "–") -> str:
    if is_missing(value):
        return default
    try:
        return f"{float(value):{fmt}}{unit}"
    except (TypeError, ValueError):
        return default


def flag(value: Any) -> bool:
    """Missing booleans count as False."""
    return False if is_missing(value) else bool(value)


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------
def vessel_destination(r: Mapping[str, Any]) -> str:
    """'Helsingfors (Helsinki), Finland (FIHEL)'. Falls back to what the crew typed."""
    name = text(r.get("dest_name"))
    if name:
        country = country_name(text(r.get("dest_country")) or None)
        code = text(r.get("dest_locode"))
        place = f"{name}, {country}" if country else name
        return f"{place} ({code})" if code else place
    raw = text(r.get("destination"))
    return f"{raw} (okänd kod)" if raw else "Okänd"


def flight_destination(r: Mapping[str, Any]) -> str:
    """'Helsinki, Finland (HEL)', marked when adsb.lol thinks the route doesn't fit."""
    city = text(r.get("dest_city")) or text(r.get("dest_name"))
    if not city:
        return "Okänd"
    country = country_name(text(r.get("dest_country")) or None)
    code = text(r.get("dest_iata")) or text(r.get("dest_icao"))
    label = f"{city}, {country}" if country else city
    label = f"{label} ({code})" if code else label
    if r.get("route_plausible") is False:  # explicit False only – None means "not checked"
        label += " – osäker"
    return label


def flight_route(r: Mapping[str, Any]) -> str:
    """'ARN → HEL' (IATA codes, ICAO when IATA is missing)."""
    origin = text(r.get("origin_iata")) or text(r.get("origin_icao"))
    dest = text(r.get("dest_iata")) or text(r.get("dest_icao"))
    return f"{origin} → {dest}" if origin and dest else "–"


# Tooltip lines are returned as plain text (no HTML). The dashboard puts the
# line breaks in the tooltip *template*, because deck.gl escapes the values it
# inserts – an embedded "<br/>" would be shown literally.
TOOLTIP_FIELDS = ("tt_title", "tt_line2", "tt_line3", "tt_line4")


def vessel_tooltip(r: Mapping[str, Any]) -> tuple[str, str, str, str]:
    name = text(r.get("name"), default=text(r.get("mmsi"), "okänt fartyg"))
    return (
        f"🚢 {name}",
        f"{text(r.get('category'), 'Okänd')} · {number(r.get('sog_knots'), '.1f', ' kn')}",
        f"→ {vessel_destination(r)}",
        f"AIS-destination: {text(r.get('destination'), '–')}",  # exactly what the crew typed
    )


def aircraft_tooltip(r: Mapping[str, Any]) -> tuple[str, str, str, str]:
    ident = text(r.get("callsign"), default=text(r.get("icao_hex"), "okänt"))
    details = " ".join(p for p in (text(r.get("type_code")), text(r.get("registration"))) if p)
    altitude = "på marken" if flag(r.get("on_ground")) else number(r.get("altitude_ft"), ",.0f", " ft")
    origin = text(r.get("origin_iata")) or text(r.get("origin_icao"))
    destination = f"→ {flight_destination(r)}" + (f" · från {origin}" if origin else "")
    return (
        f"✈️ {ident}",
        details or "–",
        f"{altitude} · {number(r.get('ground_speed_kt'), '.0f', ' kt')}",
        destination,
    )
