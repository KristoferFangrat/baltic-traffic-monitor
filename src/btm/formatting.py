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


# Tooltip lines are returned as plain text (no HTML). The dashboard puts the
# line breaks in the tooltip *template*, because deck.gl escapes the values it
# inserts – an embedded "<br/>" would be shown literally.
TOOLTIP_FIELDS = ("tt_title", "tt_line2", "tt_line3")


def vessel_tooltip(r: Mapping[str, Any]) -> tuple[str, str, str]:
    name = text(r.get("name"), default=text(r.get("mmsi"), "okänt fartyg"))
    return (
        f"🚢 {name}",
        f"{text(r.get('category'), 'Okänd')} · {number(r.get('sog_knots'), '.1f', ' kn')}",
        f"Destination: {text(r.get('destination'), '–')}",
    )


def aircraft_tooltip(r: Mapping[str, Any]) -> tuple[str, str, str]:
    ident = text(r.get("callsign"), default=text(r.get("icao_hex"), "okänt"))
    details = " ".join(p for p in (text(r.get("type_code")), text(r.get("registration"))) if p)
    altitude = "på marken" if flag(r.get("on_ground")) else number(r.get("altitude_ft"), ",.0f", " ft")
    return (
        f"✈️ {ident}",
        details or "–",
        f"{altitude} · {number(r.get('ground_speed_kt'), '.0f', ' kt')}",
    )
