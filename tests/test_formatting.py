"""Tooltips must survive the missing values real AIS/ADS-B data contains."""

import math

import pandas as pd
import pytest

from btm.formatting import aircraft_label, flag, is_missing, number, text, vessel_label


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


def test_aircraft_label_with_missing_speed_and_altitude():
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
    label = aircraft_label(row)
    assert "4ac9e1" in label and "SE-RJX" in label
    assert "nan" not in label.lower()


def test_aircraft_label_on_ground():
    row = {"callsign": "SAS1", "on_ground": True, "altitude_ft": 0, "ground_speed_kt": 12.0}
    assert "på marken" in aircraft_label(row) and "12 kt" in aircraft_label(row)


def test_vessel_label_with_missing_values():
    row = pd.Series(
        {"name": None, "mmsi": 265547250, "category": None, "sog_knots": float("nan"), "destination": pd.NA}
    )
    label = vessel_label(row)
    assert "265547250" in label and "Okänd" in label
    assert "nan" not in label.lower()


def test_labels_work_with_dataframe_apply():
    df = pd.DataFrame(
        [
            {"callsign": "SAS1", "icao_hex": "a", "altitude_ft": 36000, "ground_speed_kt": 450.0},
            {"callsign": None, "icao_hex": "b", "altitude_ft": None, "ground_speed_kt": None},
        ]
    )
    labels = df.apply(aircraft_label, axis=1)
    assert labels.iloc[0].endswith("36,000 ft · 450 kt")
    assert "– ft" not in labels.iloc[1] and labels.iloc[1].endswith("– · –")
