"""Tooltips must survive the missing values real AIS/ADS-B data contains."""

import math

import pandas as pd
import pytest

from btm.formatting import aircraft_tooltip, flag, is_missing, number, text, vessel_tooltip


@pytest.mark.parametrize("value", [None, float("nan"), math.nan, pd.NA, pd.NaT])
def test_is_missing_catches_every_null_flavour(value):
    assert is_missing(value)


@pytest.mark.parametrize("value", [0, 0.0, "", False, "SAS1", 12.5])
def test_is_missing_keeps_real_values(value):
    assert not is_missing(value)


def test_helpers_fall_back_to_defaults():
    assert number(float("nan"), ".0f", " kt") == "–"
    assert number(0, ".0f", " kt") == "0 kt"  # zero is a value, not "missing"
    assert text(pd.NA, "x") == "x"
    assert flag(float("nan")) is False and flag(True) is True


def test_aircraft_tooltip_with_missing_speed_and_altitude():
    # The exact row shape that crashed the dashboard: NaN ground speed from the DB
    row = pd.Series(
        {
            "callsign": None,
            "icao_hex": "4ac9e1",
            "type_code": float("nan"),
            "registration": "SE-RJX",
            "altitude_ft": float("nan"),
            "on_ground": None,
            "ground_speed_kt": float("nan"),
        }
    )
    title, details, motion = aircraft_tooltip(row)
    assert title == "✈️ 4ac9e1"
    assert details == "SE-RJX"
    assert motion == "– · –"


def test_aircraft_tooltip_lines():
    row = {
        "callsign": "CKK216",
        "type_code": "B77L",
        "registration": "B-228S",
        "altitude_ft": 31000,
        "ground_speed_kt": 415.0,
    }
    assert aircraft_tooltip(row) == ("✈️ CKK216", "B77L B-228S", "31,000 ft · 415 kt")


def test_aircraft_tooltip_on_ground():
    row = {"callsign": "SAS1", "on_ground": True, "altitude_ft": 0, "ground_speed_kt": 12.0}
    assert aircraft_tooltip(row)[2] == "på marken · 12 kt"


def test_vessel_tooltip_with_missing_values():
    row = pd.Series(
        {"name": None, "mmsi": 265547250, "category": None, "sog_knots": float("nan"), "destination": pd.NA}
    )
    assert vessel_tooltip(row) == ("🚢 265547250", "Okänd · –", "Destination: –")


@pytest.mark.parametrize("func", [aircraft_tooltip, vessel_tooltip])
def test_tooltips_contain_no_html(func):
    # deck.gl escapes inserted values, so any markup would be shown literally
    lines = func({"name": "X", "callsign": "Y", "altitude_ft": 1000})
    assert not any("<" in line or ">" in line for line in lines)


def test_tooltips_expand_into_dataframe_columns():
    df = pd.DataFrame(
        [
            {"callsign": "SAS1", "icao_hex": "a", "altitude_ft": 36000, "ground_speed_kt": 450.0},
            {"callsign": None, "icao_hex": "b", "altitude_ft": None, "ground_speed_kt": None},
        ]
    )
    df[["t1", "t2", "t3"]] = df.apply(aircraft_tooltip, axis=1, result_type="expand")
    assert df["t3"].tolist() == ["36,000 ft · 450 kt", "– · –"]
    assert df["t1"].tolist() == ["✈️ SAS1", "✈️ b"]
