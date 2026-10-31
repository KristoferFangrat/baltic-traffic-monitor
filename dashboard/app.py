"""Streamlit dashboard for Baltic Traffic Monitor.

Run locally:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg
import pydeck as pdk
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://btm:btm@localhost:5432/btm")
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
k3.metric("Militära flygplan", int(aircraft["is_military"].fillna(False).sum()) if not aircraft.empty else 0)
k4.metric("AIS-avbrott (24 h)", len(gaps))
k5.metric("Nödkoder (24 h)", len(squawks))

tab_map, tab_vessels, tab_air, tab_anom, tab_pipe = st.tabs(
    ["🗺️ Karta", "🚢 Fartyg", "✈️ Flyg", "⚠️ Avvikelser", "🔧 Pipeline"]
)

# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
with tab_map:
    layers = []
    if show_vessels and not vessels.empty:
        v = vessels.copy()
        v["color"] = v["category"].map(lambda c: CATEGORY_COLORS.get(c, DEFAULT_COLOR))
        v["label"] = v.apply(
            lambda r: (
                f"🚢 {r['name'] or r['mmsi']}<br/>{r['category']} · {r['sog_knots'] or 0:.1f} kn"
                f"<br/>Destination: {r['destination'] or '–'}"
            ),
            axis=1,
        )
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                v,
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
        a["color"] = a["is_military"].map(lambda m: MILITARY_AIRCRAFT_COLOR if m else AIRCRAFT_COLOR)
        a["label"] = a.apply(
            lambda r: (
                f"✈️ {r['callsign'] or r['icao_hex']}<br/>{r['type_code'] or ''} {r['registration'] or ''}"
                f"<br/>{int(r['altitude_ft'] or 0):,} ft · {int(r['ground_speed_kt'] or 0)} kt"
            ),
            axis=1,
        )
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                a,
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
                squawks,
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
            tooltip={"html": "{label}"},
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
    st.dataframe(
        vessels.drop(columns=["nav_status"], errors="ignore").sort_values("observed_at", ascending=False),
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
    st.dataframe(aircraft.sort_values("altitude_ft", ascending=False), use_container_width=True, hide_index=True)

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
