# 🛰️ Baltic Traffic Monitor

[![CI](https://github.com/KristoferFangrat/baltic-traffic-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/KristoferFangrat/baltic-traffic-monitor/actions/workflows/ci.yml)
[![Security scan](https://github.com/KristoferFangrat/baltic-traffic-monitor/actions/workflows/security.yml/badge.svg)](https://github.com/KristoferFangrat/baltic-traffic-monitor/actions/workflows/security.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)
![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-FF4B4B)

En datapipeline som bygger en **lägesbild över fartygs- och flygtrafik i Östersjöregionen** från öppna realtidskällor:

- **Fartyg (AIS)** från [Fintraffic Digitraffic](https://www.digitraffic.fi/en/marine-traffic/): positioner, fart, kurs och fartygsdata för hela Östersjön
- **Flyg (ADS-B)** från [adsb.lol](https://adsb.lol): positioner, höjd, fart och transponderkod för flygplan runt Stockholm

Datan hämtas med Python, lagras i PostgreSQL, analyseras med SQL-vyer och visas i en Streamlit-dashboard. Allt körs med ett kommando via Docker Compose.

> *A Python + SQL + Docker data pipeline that ingests live AIS (ships) and ADS-B (aircraft) data for the Baltic Sea into PostgreSQL, detects anomalies with SQL window functions and visualises everything in Streamlit.*

---

## Funktioner

| | |
|---|---|
| 🗺️ **Live-karta** | Fartyg färgade efter kategori, flygplan, militärt flyg och nödkoder |
| 🧭 **Destinationer** | Fartygens AIS-destination (t.ex. `FIHEL`, `SE STO`, `HELSINKI`, `SENYN>FIMHQ`) tolkas mot FN:s hamnkodslista och visas som *Helsingfors (Helsinki), Finland (FIHEL)*. Flygets rutt hämtas från adsb.lol och visas som *Helsinki, Finland (HEL)*. Översikt per fartygs- och flygkategori |
| ⚠️ **Avvikelsedetektion i SQL** | AIS-avbrott (fartyg som slutar sända), fartyg som ligger still trots status "under gång", nödkoder 7500/7600/7700 |
| 📈 **Trafikanalys** | Trafikvolym per timme, fördelning per fartygskategori, flyghöjd och flygplanstyp |
| 🔧 **Pipeline-övervakning** | Varje körning loggas i `ingestion_runs` (antal hämtade/nya rader, fel), och det visas i dashboarden |
| ♻️ **Idempotenta laddningar** | `COPY` till en staging-tabell följt av `INSERT … ON CONFLICT DO NOTHING`, så att omkörningar inte skapar dubbletter |
| 🔒 **Säkerhetsskanning** | Trivy skannar Docker-imagen, Python-beroenden, hemligheter och Docker-konfiguration vid varje push och varje vecka |
| 🧪 **Tester + CI** | Enhetstester för parsers och klienter, integrationstester mot riktig PostgreSQL och en Docker-smoke test i GitHub Actions |
| 🎭 **Demoläge** | Syntetisk data längs riktiga farleder, så att dashboarden går att visa helt utan internet |

## Arkitektur

```mermaid
flowchart LR
    A[Digitraffic AIS API<br/>REST · GeoJSON] --> C
    B[adsb.lol ADS-B API<br/>positioner + rutter] --> C
    L[UN/LOCODE<br/>17 500 hamnar] -.->|tolkar destination| C
    C[Collector<br/>Python · requests<br/>retry + schemaläggning] -->|COPY + upsert| D[(PostgreSQL 16<br/>tabeller + SQL-vyer)]
    D --> E[Streamlit<br/>dashboard · pydeck]
    C -.->|loggar varje körning| D
```

| Tjänst | Vad den gör |
|---|---|
| `db` | PostgreSQL 16 med schema `traffic`, nås på `localhost:5433` från din dator |
| `collector` | Hämtar AIS-positioner var 2:a minut, fartygsdata varje timme, ADS-B varje minut och flygrutter för nya anropssignaler; rensar data äldre än 7 dagar |
| `dashboard` | Streamlit på <http://localhost:8501> |
| `seed` | (valfri) laddar demodata |

## Kom igång

**Krav:** Docker Desktop.

```bash
git clone https://github.com/KristoferFangrat/baltic-traffic-monitor.git
cd baltic-traffic-monitor
cp .env.example .env          # justera område/intervall vid behov
docker compose up -d --build
```

Öppna **<http://localhost:8501>**. Efter några minuter har de första hämtningarna kommit in.

Vill du visa dashboarden direkt med data, t.ex. på en intervju utan nätverk:

```bash
docker compose --profile demo run --rm seed
```

### Utan Docker (för utveckling)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
docker compose up -d db                               # eller egen PostgreSQL
export PYTHONPATH=src DATABASE_URL=postgresql://btm:btm@localhost:5433/btm

python -m btm.collector init-db   # skapa schema och vyer
python -m btm.collector once      # en hämtning från varje källa
python -m btm.collector run       # kör kontinuerligt
streamlit run dashboard/app.py
```

## Datamodell

```
traffic.vessels             mmsi (PK), name, ship_type, call_sign, imo, destination, draught_m,
                            dest_locode, dest_name, dest_country   ← tolkad destination
traffic.vessel_positions    mmsi, observed_at, lat, lon, sog_knots, cog_deg, heading_deg, nav_status
traffic.aircraft            icao_hex (PK), registration, type_code, last_callsign, is_military
traffic.aircraft_positions  icao_hex, observed_at, lat, lon, altitude_ft, ground_speed_kt, squawk, …
traffic.flight_routes       callsign (PK), route_codes, origin_*, dest_icao, dest_iata, dest_city, dest_country, plausible
traffic.ingestion_runs      source, started_at, finished_at, fetched, inserted, status, error_message
```

Positionstabellerna har en unik nyckel på `(mmsi, observed_at)` respektive `(icao_hex, observed_at)` samt index för tidsfrågor.

### SQL-vyer ([`sql/02_views.sql`](sql/02_views.sql))

| Vy | Teknik |
|---|---|
| `v_latest_vessels` / `v_latest_aircraft` | `DISTINCT ON` för senaste position per objekt |
| `v_ais_gaps` | `LAG()`-fönsterfunktion som hittar glapp på mer än 30 min för fartyg i rörelse, plus haversine-avstånd i SQL |
| `v_loitering` | Aggregering med `HAVING`: fartyg "under gång" som hållit sig inom ca 2 km i minst 3 h |
| `v_emergency_squawks` | Flyg med transponderkod 7500/7600/7700 |
| `v_vessel_destinations` / `v_flight_destinations` | Antal fartyg/flygplan per destination och kategori. Otolkad fritext grupperas på det besättningen skrev |
| `v_hourly_traffic` | Unika objekt per timme och källa |
| `v_pipeline_health` | Senaste körning per källa |
| `ship_category()` | SQL-funktion som översätter AIS-fartygstyp (ITU-R M.1371) till kategori |

## Skärmdumpar

| Fartyg | Avvikelser |
|---|---|
| ![Fartyg](docs/vessels.png) | ![Avvikelser](docs/anomalies.png) |

| Flyg | Pipeline |
|---|---|
| ![Flyg](docs/aircraft.png) | ![Pipeline](docs/pipeline.png) |

**Destinationer per fartygs- och flygkategori**

![Destinationer](docs/destinations.png)

*(Skärmdumparna visar demodata.)*

## Tester

```bash
pytest                                                    # enhetstester
TEST_DATABASE_URL=postgresql://btm:btm@localhost:5432/btm_test pytest   # + integrationstester
ruff check . && ruff format --check .
```

GitHub Actions kör linting, alla tester mot en PostgreSQL-tjänst, bygger Docker-imagen och gör en smoke test av hela stacken.

## Hur destinationer tolkas

**Fartyg.** AIS-fältet *destination* är fritext som besättningen själv skriver in, så samma hamn kan stå på många sätt. [`btm/destinations.py`](src/btm/destinations.py) tolkar det i ordning:

1. Tomma värden och skräp (`@@@@@`, `UNKNOWN`) blir *Okänd*.
2. Rutter som `SENYN>FIMHQ` eller `STOCKHOLM - HELSINKI` delas upp, och sista ledet räknas som destination.
3. Statusar som `FOR ORDERS` och `FISHING` blir *Inväntar order* respektive *Fiske*.
4. UN/LOCODE (`FIHEL`, `SE STO`, `se-sto`) slås upp i [`data/ports.csv`](data/ports.csv).
5. Annars matchas hamnnamnet (`HELSINKI`, `Göteborg`, `GOTHENBURG`). Om namnet finns i flera länder väljs hamnen i Östersjöregionen.

Det som inte går att tolka visas precis som det skrevs, märkt *(okänd kod)*, så ingen information går förlorad. Tvåspråkiga finska hamnar visas med det svenska namnet först (*Åbo (Turku)*), och länder har svenska namn.

**Flyg.** ADS-B innehåller ingen destination. Därför slår insamlingen upp varje ny anropssignal i adsb.lols rutt-API (max 100 per anrop, cachat i `flight_routes`). Rutterna kommer från en öppen community-databas och matchas bara på anropssignal. Därför markeras en rutt som *osäker* när planets position inte ligger längs den. Militärt och privat flyg saknar ofta rutt.

## Säkerhet

Workflowet [`security.yml`](.github/workflows/security.yml) kör [Trivy](https://trivy.dev):

| Skanning | Vad som kontrolleras | Stoppar bygget? |
|---|---|---|
| **Container-image** | Kända sårbarheter (CVE) i OS-paket och Python-paket i den byggda imagen | Ja, vid **CRITICAL** som har en rättning |
| **Repository** | `requirements.txt`, inlagda hemligheter (API-nycklar, lösenord), Dockerfile/Compose-konfiguration | Ja, vid hemligheter eller **CRITICAL** som har en rättning |

- Alla fynd av nivå HIGH och CRITICAL laddas upp till fliken **Security → Code scanning** på GitHub.
- Skanningen körs även **varje måndag**, eftersom nya CVE:er publiceras även när koden inte ändras.
- Sårbarheter **utan** tillgänglig rättning rapporteras men stoppar inte bygget. Accepterade risker dokumenteras med motivering i [`.trivyignore`](.trivyignore).
- Imagen kör `apt-get upgrade` vid bygget för att få med säkerhetsrättningar som släppts efter base image, och appen körs som en icke-root-användare.
- Alla externa GitHub Actions är låsta till en exakt **commit-SHA** i stället för en tagg. Taggar kan flyttas: det hände med `trivy-action` i mars 2026 (CVE-2026-33634), när angripare skrev om nästan alla versionstaggar så att de pekade på skadlig kod.

## Projektstruktur

```
├── dashboard/app.py          Streamlit-dashboard
├── data/ports.csv            17 500 hamnar från UN/LOCODE (byggs med scripts/build_ports.py)
├── sql/
│   ├── 01_schema.sql         tabeller, constraints, index
│   └── 02_views.sql          analysvyer och avvikelsedetektion
├── src/btm/
│   ├── collector.py          CLI + schemaläggare
│   ├── db.py                 laddning, upsert, körloggning
│   ├── destinations.py       tolkning av AIS-destinationer + svenska landsnamn
│   ├── formatting.py         texter i inforutor och tabeller
│   ├── seed.py               demodata
│   ├── config.py             inställningar från miljövariabler
│   └── sources/              API-klienter och parsers (ais.py, adsb.py)
├── tests/                    pytest + JSON-fixtures
├── Dockerfile
└── docker-compose.yml
```

## Möjliga vidareutvecklingar

- PostGIS för geografiska frågor (t.ex. fartyg nära undervattenskablar eller inom ett område)
- Strömmande AIS via Digitraffics MQTT-gränssnitt i stället för polling
- OpenSky Network (OAuth2) som andra ADS-B-källa för jämförelse av datakvalitet
- Orkestrering med Airflow/Dagster och modellering med dbt
- Larm (e-post/Slack) när en avvikelse upptäcks
- SBOM (Software Bill of Materials) och signering av imagen med cosign

## Datakällor och licenser

- AIS-data: © Fintraffic / Digitraffic, licens [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- ADS-B-data: © adsb.lol, licens [ODbL](https://opendatacommons.org/licenses/odbl/). Flygrutter: [vradarserver/standing-data](https://github.com/vradarserver/standing-data) via adsb.lol
- Hamnkoder: [UN/LOCODE](https://unlocode.unece.org) från UNECE, via [datasets/un-locode](https://github.com/datasets/un-locode)
- Landsnamn på svenska: ISO 3166 via [pycountry](https://github.com/pycountry/pycountry)

Projektet använder endast öppet publicerad data och är byggt i utbildnings- och portfoliosyfte.

---

Byggt av **Kristofer Fangrat** · Data Engineer
