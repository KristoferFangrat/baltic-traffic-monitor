"""Build data/ports.csv from the UN/LOCODE code list.

Source: UNECE UN/LOCODE (https://unlocode.unece.org), via the
https://github.com/datasets/un-locode mirror.

    git clone --depth 1 https://github.com/datasets/un-locode /tmp/un-locode
    python scripts/build_ports.py /tmp/un-locode/data/code-list.csv

Keeps only locations with the "port" function and one row per LOCODE.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "ports.csv"


def parse_coord(value: str) -> tuple[str, str]:
    """'6010N 02458E' -> ('60.1667', '24.9667')"""
    try:
        la, lo = value.split()
        lat = int(la[:2]) + int(la[2:4]) / 60
        lon = int(lo[:3]) + int(lo[3:5]) / 60
        lat = -lat if la[-1] == "S" else lat
        lon = -lon if lo[-1] == "W" else lon
        return f"{lat:.4f}", f"{lon:.4f}"
    except (ValueError, IndexError):
        return "", ""


def clean_name(name: str) -> str:
    # "Saint Petersburg (ex Leningrad)" -> "Saint Petersburg"
    return re.sub(r"\s*\(ex [^)]*\)", "", name).strip()


def main(src: str) -> None:
    ports: dict[str, dict[str, str]] = {}
    with open(src, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["Location"] or r["Change"] == "X" or not r["Function"].startswith("1"):
                continue
            code = r["Country"] + r["Location"]
            lat, lon = parse_coord(r["Coordinates"])
            row = {
                "locode": code,
                "name": clean_name(r["Name"]),
                "name_ascii": clean_name(r["NameWoDiacritics"]),
                "country": r["Country"],
                "lat": lat,
                "lon": lon,
            }
            prev = ports.get(code)
            if prev is None:
                ports[code] = row
                continue
            # Same LOCODE listed twice (bilingual towns): prefer the "Helsingfors (Helsinki)" form,
            # and keep coordinates from whichever row has them.
            if "(" in row["name"] and "(" not in prev["name"]:
                row["lat"], row["lon"] = row["lat"] or prev["lat"], row["lon"] or prev["lon"]
                ports[code] = row
            elif not prev["lat"] and lat:
                prev["lat"], prev["lon"] = lat, lon

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["locode", "name", "name_ascii", "country", "lat", "lon"])
        w.writeheader()
        w.writerows(sorted(ports.values(), key=lambda r: r["locode"]))
    print(f"Wrote {len(ports)} ports to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/un-locode/data/code-list.csv")
