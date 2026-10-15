"""Pull NC OneMap statewide parcel polygons WITHOUT owner fields, for the
town-map tiles (README.md).

Asks the service for four attributes only: parno, cntyfips, cntyname,
gisacres (OUT_FIELDS). Owner, mailing, sale and value fields are never
requested, and any field the service returns beyond the allow-list is dropped
before a line is written (``keep_props``), so nothing else can reach disk.

HTTP goes through polite_http (FieldstateBot, robots.txt obeyed, one request
per host gap). Per county, paged by objectid in pages of 2,000, written to
<out>/county/<cntyname>.geojsonl (one GeoJSON Feature per line) with a
``.done`` marker; a county with a marker is skipped (resumable).

  python nc_parcels_fetch.py --out work/parcels                 # all counties
  python nc_parcels_fetch.py --out work/parcels --shard 1/4     # every 4th county
  python nc_parcels_fetch.py --out work/parcels --county Cleveland
  python nc_parcels_fetch.py --out work/parcels --check         # list county files, all done?
  python nc_parcels_fetch.py --out work/parcels --merge         # → nc-parcels.geojsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

LAYER_URL = ("https://services.nconemap.gov/secure/rest/services/"
             "NC1Map_Parcels/FeatureServer/1")
QUERY_URL = LAYER_URL + "/query"
# The ONLY attributes ever requested or written. Never add an owner, mailing,
# sale or value field here.
OUT_FIELDS = ("parno", "cntyfips", "cntyname", "gisacres")
FORBIDDEN = ("own", "mail", "madd", "mcity", "mstate", "mzip", "munit", "val",
             "sale", "transf", "legdec", "subsurf")
PAGE = 2000
NC_COUNTIES = 100


def keep_props(props: dict | None) -> dict:
    """Allow-list, case-insensitively; everything else is dropped."""
    out = {}
    for k, v in (props or {}).items():
        lk = str(k).lower()
        if lk in OUT_FIELDS:
            out[lk] = v
    return out


def assert_allowlist_is_owner_free() -> None:
    for f in OUT_FIELDS:
        if any(bad in f for bad in FORBIDDEN):
            raise SystemExit(f"refusing to run: OUT_FIELDS contains {f!r}")


def shard(names: list[str], spec: str | None) -> list[str]:
    """``i/n`` → every n-th county starting at i (1-based), in sorted order."""
    if not spec:
        return names
    i, n = (int(x) for x in spec.split("/"))
    if not 1 <= i <= n:
        raise SystemExit(f"bad --shard {spec}")
    return sorted(names)[i - 1::n]


def counties(http) -> list[str]:
    r = http.get(QUERY_URL, timeout=120, params={
        "where": "1=1", "outFields": "cntyname", "returnDistinctValues": "true",
        "returnGeometry": "false", "orderByFields": "cntyname", "f": "json"})
    r.raise_for_status()
    names = sorted({f["attributes"]["cntyname"] for f in r.json().get("features", [])
                    if (f.get("attributes") or {}).get("cntyname")})
    if len(names) < NC_COUNTIES:
        print(f"warning: service lists {len(names)} counties (expected {NC_COUNTIES})", flush=True)
    return names


def fetch_county(http, name: str, out_dir: Path) -> int:
    path = out_dir / "county" / f"{name}.geojsonl"
    done = path.with_suffix(".done")
    if done.exists():
        return int(done.read_text() or 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    n, offset = 0, 0
    safe = name.replace("'", "''")
    with path.open("w", encoding="utf-8") as fh:
        while True:
            r = http.get(QUERY_URL, timeout=180, params={
                "where": f"cntyname='{safe}'", "outFields": ",".join(OUT_FIELDS),
                "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "6",
                "orderByFields": "objectid", "resultOffset": offset,
                "resultRecordCount": PAGE, "f": "geojson"})
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                raise RuntimeError(f"{name} offset={offset}: {body['error']}")
            feats = body.get("features") or []
            for f in feats:
                if not f.get("geometry"):
                    continue
                fh.write(json.dumps({"type": "Feature", "geometry": f["geometry"],
                                     "properties": keep_props(f.get("properties"))},
                                    separators=(",", ":")) + "\n")
                n += 1
            exceeded = body.get("exceededTransferLimit") or (
                (body.get("properties") or {}).get("exceededTransferLimit"))
            if not feats or (len(feats) < PAGE and not exceeded):
                break
            offset += len(feats)
    done.write_text(str(n))
    return n


def check_complete(out: Path, expected: int = NC_COUNTIES) -> list[Path]:
    """Finished county files, or exit when fewer than ``expected`` are done (a
    partial state map would publish silently missing counties)."""
    done = sorted((out / "county").glob("*.done"))
    if len(done) < expected:
        raise SystemExit(f"only {len(done)} of {expected} counties finished")
    return [m.with_suffix(".geojsonl") for m in done]


def merge(out: Path, expected: int = NC_COUNTIES) -> int:
    """Concatenate every finished county into nc-parcels.geojsonl. Fails when
    fewer than ``expected`` counties are finished (a partial state map would
    publish silently missing counties)."""
    done = sorted((out / "county").glob("*.done"))
    if len(done) < expected:
        raise SystemExit(f"only {len(done)} of {expected} counties finished; not merging")
    total = 0
    with (out / "nc-parcels.geojsonl").open("w", encoding="utf-8") as fh:
        for marker in done:
            with marker.with_suffix(".geojsonl").open(encoding="utf-8") as src:
                for line in src:
                    fh.write(line)
                    total += 1
    print(f"nc_parcels merged counties={len(done)} parcels={total}", flush=True)
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="work/parcels")
    ap.add_argument("--county", action="append", help="one county (repeatable)")
    ap.add_argument("--shard", help="i/n: every n-th county starting at i")
    ap.add_argument("--merge", action="store_true", help="only merge finished counties")
    ap.add_argument("--check", action="store_true",
                    help="only list finished county files; exit 1 unless all are done")
    ap.add_argument("--expected", type=int, default=NC_COUNTIES)
    args = ap.parse_args(argv)
    out = Path(args.out)
    if args.merge:
        merge(out, args.expected)
        return 0
    if args.check:
        for path in check_complete(out, args.expected):
            print(path)
        return 0
    assert_allowlist_is_owner_free()
    import polite_http as http

    if not http.robots_allowed(QUERY_URL):
        print(f"robots.txt disallows {QUERY_URL} for FieldstateBot; stopping.", flush=True)
        return 2
    names = args.county or shard(counties(http), args.shard)
    total = 0
    for name in names:
        n = fetch_county(http, name, out)
        total += n
        print(f"nc_parcels county={name} parcels={n} total={total}", flush=True)
    print(f"nc_parcels done counties={len(names)} parcels={total}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
