"""Streamlit dashboard for Baltic Traffic Monitor.

Run locally:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import psycopg
import pydeck as pdk
import streamlit as st

# Make `btm` importable when running `streamlit run dashboard/app.py` without PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from btm.destinations import country_name  # noqa: E402
from btm.formatting import (  # noqa: E402
    TOOLTIP_FIELDS,
    aircraft_tooltip,
    flag,
    flight_destination,
    flight_route,
    vessel_destination,
    vessel_tooltip,
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://btm:btm@localhost:5433/btm")
REFRESH_SECONDS = 30

st.set_page_config(page_title="Baltic Traffic Monitor", page_icon="🛰️", layout="wide")

CATEGORY_COLORS: dict[str, list[int]] = {
    "Last": [31, 119, 180],
    "Tanker": [140, 86, 75],
    "Passagerare": [44, 160, 44],
    "Fiske": [23, 190, 207],
    "Bogsering": [127, 127, 127],
    "Militär": [214, 39, 40],
    "Myndighet": [148, 103, 189],
    "Sjöräddning": [255, 127, 14],
    "Segelbåt": [188, 189, 34],
    "Fritidsbåt": [188, 189, 34],
}
DEFAULT_COLOR = [120, 120, 120]
AIRCRAFT_COLOR = [255, 140, 0]
MILITARY_AIRCRAFT_COLOR = [200, 0, 0]


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def query(sql: str) -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("SET search_path TO traffic, public")
        cur = conn.execute(sql)
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def safe_query(sql: str) -> pd.DataFrame | None:
    try:
        return query(sql)
    except psycopg.OperationalError as exc:
        st.error(f"Kan inte ansluta till databasen: {exc}")
        st.stop()
    except psycopg.errors.UndefinedTable:
        st.warning("Schemat saknas. Kör `python -m btm.collector init-db` först.")
        st.stop()
    return None


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🛰️ Baltic Traffic Monitor")
st.sidebar.caption("Lägesbild över fartygs- och flygtrafik i Östersjöregionen")
show_vessels = st.sidebar.checkbox("Visa fartyg", value=True)
show_aircraft = st.sidebar.checkbox("Visa flygplan", value=True)

vessels = safe_query("SELECT * FROM v_latest_vessels")
aircraft = safe_query("SELECT * FROM v_latest_aircraft")

categories = sorted(vessels["category"].dropna().unique()) if not vessels.empty else []
selected = st.sidebar.multiselect("Fartygskategorier", categories, default=categories)
min_alt = st.sidebar.slider("Min. flyghöjd (fot)", 0, 45000, 0, step=1000)

if st.sidebar.button("Uppdatera nu"):
    query.clear()
    st.rerun()

st.sidebar.markdown(
    "---\n**Datakällor**\n\n"
    "- AIS: [Fintraffic Digitraffic](https://www.digitraffic.fi/en/marine-traffic/) (CC BY 4.0)\n"
    "- ADS-B: [adsb.lol](https://adsb.lol) (ODbL)"
)

if not vessels.empty:
    vessels = vessels[vessels["category"].isin(selected)]
if not aircraft.empty:
    aircraft = aircraft[aircraft["altitude_ft"].fillna(0) >= min_alt]

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
gaps = safe_query("SELECT * FROM v_ais_gaps ORDER BY gap DESC")
loiter = safe_query("SELECT * FROM v_loitering ORDER BY last_seen DESC")
squawks = safe_query("SELECT * FROM v_emergency_squawks ORDER BY observed_at DESC")

st.title("Lägesbild – Östersjön")
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Fartyg (senaste timmen)", len(vessels))
k2.metric("Flygplan (senaste 10 min)", len(aircraft))
k3.metric("Militära flygplan", int(aircraft["is_military"].map(flag).sum()) if not aircraft.empty else 0)
k4.metric("AIS-avbrott (24 h)", len(gaps))
k5.metric("Nödkoder (24 h)", len(squawks))

tab_map, tab_vessels, tab_air, tab_dest, tab_anom, tab_pipe = st.tabs(
    ["🗺️ Karta", "🚢 Fartyg", "✈️ Flyg", "🧭 Destinationer", "⚠️ Avvikelser", "🔧 Pipeline"]
)

# Readable destination columns, shared by the map, the tables and the destinations tab
if not vessels.empty:
    vessels["destination_label"] = vessels.apply(vessel_destination, axis=1)
if not aircraft.empty:
    aircraft["destination_label"] = aircraft.apply(flight_destination, axis=1)
    aircraft["route"] = aircraft.apply(flight_route, axis=1)

# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
with tab_map:
    layers = []
    if show_vessels and not vessels.empty:
        v = vessels.copy()
        v["color"] = v["category"].map(lambda c: CATEGORY_COLORS.get(c, DEFAULT_COLOR))
        v[list(TOOLTIP_FIELDS)] = v.apply(vessel_tooltip, axis=1, result_type="expand")
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                v[["lon", "lat", "color", *TOOLTIP_FIELDS]],  # only what the map needs – no NaNs in the JSON
                get_position="[lon, lat]",
                get_fill_color="color",
                get_radius=1800,
                radius_min_pixels=3,
                radius_max_pixels=10,
                pickable=True,
                opacity=0.85,
            )
        )
    if show_aircraft and not aircraft.empty:
        a = aircraft.copy()
        a["color"] = a["is_military"].map(lambda m: MILITARY_AIRCRAFT_COLOR if flag(m) else AIRCRAFT_COLOR)
        a[list(TOOLTIP_FIELDS)] = a.apply(aircraft_tooltip, axis=1, result_type="expand")
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                a[["lon", "lat", "color", *TOOLTIP_FIELDS]],
                get_position="[lon, lat]",
                get_fill_color="color",
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                stroked=True,
                get_radius=2500,
                radius_min_pixels=4,
                radius_max_pixels=12,
                pickable=True,
            )
        )
    if not squawks.empty:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                squawks[["lon", "lat"]],
                get_position="[lon, lat]",
                get_fill_color=[255, 0, 0, 60],
                get_line_color=[255, 0, 0],
                stroked=True,
                line_width_min_pixels=2,
                get_radius=15000,
            )
        )

    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(latitude=58.6, longitude=19.5, zoom=5),
            map_style="light",
            # Line breaks live in the template; the values are inserted as escaped text
            tooltip={
                "html": "<b>{tt_title}</b><br/>{tt_line2}<br/>{tt_line3}<br/>{tt_line4}",
                "style": {"fontSize": "13px", "lineHeight": "1.4"},
            },
        ),
        height=620,
    )
    st.caption("Färg = fartygskategori · orange = flyg · röd = militärt flyg · röd ring = nödkod")

# ---------------------------------------------------------------------------
# Vessels
# ---------------------------------------------------------------------------
with tab_vessels:
    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("Per kategori")
        if not vessels.empty:
            st.bar_chart(vessels["category"].value_counts())
    with c2:
        st.subheader("Trafikvolym per timme")
        hourly = safe_query("SELECT * FROM v_hourly_traffic ORDER BY hour")
        if not hourly.empty:
            st.line_chart(hourly.pivot(index="hour", columns="source", values="unique_objects"))
    st.subheader("Senaste positioner")
    if not vessels.empty:
        st.dataframe(
            vessels.sort_values("observed_at", ascending=False)[
                [
                    "name",
                    "mmsi",
                    "category",
                    "destination_label",
                    "destination",
                    "sog_knots",
                    "observed_at",
                    "lat",
                    "lon",
                ]
            ],
            column_config={
                "name": "Namn",
                "mmsi": st.column_config.NumberColumn("MMSI", format="%d"),
                "category": "Kategori",
                "destination_label": "Destination",
                "destination": "AIS-destination (rå)",
                "sog_knots": st.column_config.NumberColumn("Fart (kn)", format="%.1f"),
                "observed_at": "Senast sedd",
            },
            use_container_width=True,
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# Aircraft
# ---------------------------------------------------------------------------
with tab_air:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Höjdfördelning")
        if not aircraft.empty:
            bins = pd.cut(
                aircraft["altitude_ft"].fillna(0),
                [-1, 0, 10000, 20000, 30000, 50000],
                labels=["Mark", "0–10k", "10–20k", "20–30k", ">30k"],
            )
            st.bar_chart(bins.value_counts().sort_index())
    with c2:
        st.subheader("Vanligaste flygplanstyper")
        if not aircraft.empty:
            st.bar_chart(aircraft["type_code"].value_counts().head(10))
    if not aircraft.empty:
        st.dataframe(
            aircraft.sort_values("altitude_ft", ascending=False)[
                [
                    "callsign",
                    "registration",
                    "type_code",
                    "route",
                    "destination_label",
                    "altitude_ft",
                    "ground_speed_kt",
                    "squawk",
                    "is_military",
                    "observed_at",
                ]
            ],
            column_config={
                "callsign": "Anropssignal",
                "registration": "Registrering",
                "type_code": "Typ",
                "route": "Rutt",
                "destination_label": "Destination",
                "altitude_ft": st.column_config.NumberColumn("Höjd (ft)", format="%d"),
                "ground_speed_kt": st.column_config.NumberColumn("Fart (kt)", format="%d"),
                "squawk": "Transponderkod",
                "is_military": "Militärt",
                "observed_at": "Senast sedd",
            },
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------
def _vessel_group_label(r: pd.Series) -> str:
    if r["resolved"]:
        return vessel_destination(r)
    return "Okänd" if r["dest_name"] == "Okänd" else f"{r['dest_name']} (okänd kod)"


def _top(df: pd.DataFrame, label: str, count: str, n: int = 15) -> pd.Series:
    return df.groupby(label)[count].sum().sort_values(ascending=False).head(n)


def _hbar(series: pd.Series, x_title: str) -> None:
    """Horizontal bar chart sorted by count, with room for long place names."""
    data = series.rename_axis("label").reset_index(name="count")
    chart = (
        alt.Chart(data)
        .mark_bar()
        .encode(
            x=alt.X("count:Q", title=x_title, axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320, labelOverlap=False)),
            tooltip=[alt.Tooltip("label:N", title="Destination"), alt.Tooltip("count:Q", title="Antal")],
        )
        .properties(height=max(120, 28 * len(data)))
    )
    st.altair_chart(chart, use_container_width=True)


with tab_dest:
    st.caption(
        "Fartygens destination är det besättningen själv skrivit in i AIS, oftast en hamnkod enligt "
        "UN/LOCODE (t.ex. FIHEL = Helsingfors). Flygets rutter kommer från adsb.lol och matchas på "
        "anropssignal; *osäker* betyder att planets position inte stämmer med rutten. "
        "Visar alla kategorier oberoende av filtren i sidomenyn."
    )
    vdest = safe_query("SELECT * FROM v_vessel_destinations")
    fdest = safe_query("SELECT * FROM v_flight_destinations")
    if not vdest.empty:
        vdest["label"] = vdest.apply(_vessel_group_label, axis=1)
        vdest["country_name"] = vdest["dest_country"].map(lambda c: country_name(c) if isinstance(c, str) else "Okänt")
    if not fdest.empty:
        fdest["label"] = fdest.apply(flight_destination, axis=1)
        fdest["country_name"] = fdest["dest_country"].map(lambda c: country_name(c) if isinstance(c, str) else "Okänt")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🚢 Fartyg – vart är de på väg?")
        if vdest.empty:
            st.info("Inga fartyg den senaste timmen.")
        else:
            cats = ["Alla kategorier", *sorted(vdest["category"].dropna().unique())]
            vcat = st.selectbox("Fartygskategori", cats, key="vdest_cat")
            vsel = vdest if vcat == "Alla kategorier" else vdest[vdest["category"] == vcat]
            total = int(vsel["vessels"].sum())
            known = int(vsel.loc[vsel["resolved"], "vessels"].sum())
            m1, m2 = st.columns(2)
            m1.metric("Fartyg", total)
            m2.metric("Med tolkad destination", f"{known} ({known / total:.0%})" if total else "0")
            st.markdown("**Vanligaste destinationer**")
            _hbar(_top(vsel, "label", "vessels"), "Antal fartyg")
            st.markdown("**Per land**")
            _hbar(_top(vsel, "country_name", "vessels", 10), "Antal fartyg")
    with c2:
        st.subheader("✈️ Flyg – vart är de på väg?")
        if fdest.empty:
            st.info("Inga flygplan de senaste 10 minuterna.")
        else:
            fcat = st.selectbox("Flygkategori", ["Alla kategorier", "Civilt", "Militärt"], key="fdest_cat")
            fsel = fdest if fcat == "Alla kategorier" else fdest[fdest["category"] == fcat]
            total = int(fsel["aircraft"].sum())
            known = int(fsel.loc[fsel["dest_iata"].notna() | fsel["dest_icao"].notna(), "aircraft"].sum())
            m1, m2 = st.columns(2)
            m1.metric("Flygplan", total)
            m2.metric("Med känd destination", f"{known} ({known / total:.0%})" if total else "0")
            st.markdown("**Vanligaste destinationer**")
            _hbar(_top(fsel, "label", "aircraft"), "Antal flygplan")
            st.markdown("**Per land**")
            _hbar(_top(fsel, "country_name", "aircraft", 10), "Antal flygplan")

    st.subheader("Destinationer per kategori – fartyg")
    if not vdest.empty:
        pivot = vdest.pivot_table(index="label", columns="category", values="vessels", aggfunc="sum", fill_value=0)
        pivot["Totalt"] = pivot.sum(axis=1)
        st.dataframe(
            pivot.sort_values("Totalt", ascending=False).rename_axis("Destination"),
            use_container_width=True,
        )
    st.subheader("Destinationer per kategori – flyg")
    if not fdest.empty:
        pivot = fdest.pivot_table(index="label", columns="category", values="aircraft", aggfunc="sum", fill_value=0)
        pivot["Totalt"] = pivot.sum(axis=1)
        st.dataframe(
            pivot.sort_values("Totalt", ascending=False).rename_axis("Destination"),
            use_container_width=True,
        )

# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------
with tab_anom:
    st.subheader("AIS-avbrott")
    st.caption("Fartyg i rörelse (> 2 knop) som slutat sända i mer än 30 minuter och sedan dykt upp igen.")
    st.dataframe(gaps, use_container_width=True, hide_index=True)

    st.subheader("Fartyg som ligger still")
    st.caption("Status 'under gång' men har hållit sig inom ca 2 km i minst 3 timmar.")
    st.dataframe(loiter, use_container_width=True, hide_index=True)

    st.subheader("Nödkoder från flyg")
    st.caption("Transponderkod 7500 (kapning), 7600 (radiobortfall) eller 7700 (nödläge).")
    st.dataframe(squawks, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Pipeline health
# ---------------------------------------------------------------------------
with tab_pipe:
    st.subheader("Senaste körning per källa")
    st.dataframe(safe_query("SELECT * FROM v_pipeline_health"), use_container_width=True, hide_index=True)
    st.subheader("Körhistorik")
    st.dataframe(
        safe_query("SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT 200"),
        use_container_width=True,
        hide_index=True,
    )
