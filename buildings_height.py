"""Microsoft US Building Footprints (ODbL) → GeoJSONSeq with a height ``h``
for the extruded low-poly buildings (TASK-BRIEF-2026-09-29 §3).

h = clamp(3.5 + sqrt(area_m2) / 5, 4, 15) metres: a stylised height from the
footprint size, not a measured one (the attribution says so). Only ``h`` is
kept; the source carries no other attributes we use.

The state file is a FeatureCollection written one Feature per line; this
reads it line by line (no 1.3 GB json.load) and fails if no feature parses.

USAGE
  python scripts/tiles/buildings_height.py NorthCarolina.geojson > nc-buildings.geojsonl
"""
from __future__ import annotations

import json
import math
import sys

R = 6371008.8  # mean Earth radius, m


def ring_area_m2(ring: list) -> float:
    """Planar area of a small lng/lat ring, equirectangular at its latitude."""
    if len(ring) < 4:
        return 0.0
    lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
    kx = math.radians(1) * R * math.cos(lat0)
    ky = math.radians(1) * R
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(s) / 2.0


def height(geom: dict) -> float | None:
    if geom.get("type") == "Polygon":
        polys = [geom["coordinates"]]
    elif geom.get("type") == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return None
    area = sum(ring_area_m2(p[0]) - sum(ring_area_m2(h) for h in p[1:]) for p in polys if p)
    if area <= 0:
        return None
    return round(min(15.0, max(4.0, 3.5 + math.sqrt(area) / 5)), 1)


def feature_from_line(line: str) -> dict | None:
    line = line.strip().rstrip(",")
    if not line.startswith('{"type":') and not line.startswith('{ "type"'):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if obj.get("type") == "Feature" and obj.get("geometry") else None


def main(path: str) -> int:
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = feature_from_line(line)
            if f is None:
                continue
            h = height(f["geometry"])
            if h is None:
                continue
            sys.stdout.write(json.dumps({"type": "Feature", "geometry": f["geometry"],
                                         "properties": {"h": h}},
                                        separators=(",", ":")) + "\n")
            n += 1
    print(f"buildings_height features={n}", file=sys.stderr, flush=True)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
