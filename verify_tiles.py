"""Gate before upload: the parcel tiles and the parcel file carry no owner
field, every layer has its license file, and each layer stays under its size
cap (the caps sum to 8.2 GB, inside R2's 10 GB free tier).

  python verify_tiles.py --layer parcels out/parcels.pmtiles --parcel-file work/parcels/nc-parcels.geojsonl
  python verify_tiles.py --layer basemap out/basemap.pmtiles
  python verify_tiles.py --layer assets out/
  python verify_tiles.py --all out/

Reads each PMTiles' metadata with ``pmtiles show --metadata`` (vector_layers
fields). Exit 1 on any failure; build.sh uploads a layer only after its check
passes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ALLOWED_FIELDS = {
    "parcels": {"parno", "cntyname", "gisacres"},
    "buildings": {"h"},
}
PARCEL_FILE_FIELDS = {"parno", "cntyfips", "cntyname", "gisacres"}
OWNERISH = ("own", "mail", "madd", "mzip", "mcity", "val", "sale", "transf", "legdec")
FREE_TIER_BYTES = 10 * 1000 ** 3
BUDGET_BYTES = 8 * 1000 ** 3  # leave headroom under the 10 GB free tier
REQUIRED = ("basemap.pmtiles", "parcels.pmtiles", "buildings.pmtiles", "ATTRIBUTION.txt")
LAYER_CAP_BYTES = {
    "basemap": 4 * 1000 ** 3,
    "parcels": 3 * 1000 ** 3,
    "buildings": 1 * 1000 ** 3,
    "assets": 200 * 1000 ** 2,
}
LICENSE_DIRS = ("LICENSES/fonts", "LICENSES/sprites")


def layer_fields(pmtiles: Path) -> dict[str, set[str]]:
    out = subprocess.run(["pmtiles", "show", "--metadata", str(pmtiles)],
                         check=True, capture_output=True, text=True).stdout
    meta = json.loads(out)
    if isinstance(meta.get("json"), str):  # older writers nest it
        meta = {**meta, **json.loads(meta["json"])}
    return {vl["id"]: set((vl.get("fields") or {}).keys())
            for vl in meta.get("vector_layers") or []}


def check_fields(name: str, fields: dict[str, set[str]]) -> list[str]:
    errs = []
    allowed = ALLOWED_FIELDS.get(name)
    if allowed is None:
        return errs
    for layer, keys in fields.items():
        extra = keys - allowed
        if extra:
            errs.append(f"{name}.pmtiles layer {layer} has fields outside the allow-list: {sorted(extra)}")
        bad = [k for k in keys if any(o in k.lower() for o in OWNERISH)]
        if bad:
            errs.append(f"{name}.pmtiles layer {layer} has owner/value fields: {bad}")
    return errs


def check_parcel_file(path: Path, sample: int | None = None) -> list[str]:
    errs = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if sample is not None and i >= sample:
                break
            keys = set((json.loads(line).get("properties") or {}).keys())
            if keys - PARCEL_FILE_FIELDS:
                errs.append(f"{path} line {i + 1} has fields {sorted(keys - PARCEL_FILE_FIELDS)}")
                break
    return errs


def verify_layer(name: str, path: str, parcel_file: str | None = None) -> list[str]:
    p = Path(path)
    errs: list[str] = []
    if name == "assets":
        for d in LICENSE_DIRS:
            if not any((p / d).glob("*")):
                errs.append(f"no license file under {d}")
        if not (p / "ATTRIBUTION.txt").exists():
            errs.append("missing ATTRIBUTION.txt")
        size = sum(f.stat().st_size for d in ("fonts", "sprites", "LICENSES")
                   for f in (p / d).rglob("*") if f.is_file())
    else:
        if not p.exists():
            return [f"missing {p}"]
        errs += check_fields(name, layer_fields(p))
        size = p.stat().st_size
    if name == "parcels" and parcel_file:
        pf = Path(parcel_file)
        for f in (sorted(pf.glob("*.geojsonl")) if pf.is_dir() else [pf]):
            errs += check_parcel_file(f)
    print(f"verify_tiles layer={name} bytes={size} ({size / 1e9:.2f} GB)", flush=True)
    if size > LAYER_CAP_BYTES[name]:
        errs.append(f"{name} is {size / 1e9:.2f} GB, over its {LAYER_CAP_BYTES[name] / 1e9:.1f} GB cap")
    return errs


def _report(errs: list[str]) -> int:
    for e in errs:
        print(f"verify_tiles FAIL {e}", flush=True)
    if not errs:
        print("verify_tiles ok", flush=True)
    return 1 if errs else 0


def main_all(out_dir: str, parcel_file: str | None = None) -> int:
    out = Path(out_dir)
    errs: list[str] = []
    for req in REQUIRED:
        if not (out / req).exists():
            errs.append(f"missing {req}")
    for d in LICENSE_DIRS:
        if not any((out / d).glob("*")):
            errs.append(f"no license file under {d}")
    for name in ("parcels", "buildings"):
        p = out / f"{name}.pmtiles"
        if p.exists():
            errs += check_fields(name, layer_fields(p))
    if parcel_file:
        errs += check_parcel_file(Path(parcel_file))
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"verify_tiles total_bytes={total} ({total / 1e9:.2f} GB)", flush=True)
    if total > BUDGET_BYTES:
        errs.append(f"total {total / 1e9:.2f} GB is over the {BUDGET_BYTES / 1e9:.0f} GB budget "
                    f"(R2 free tier is {FREE_TIER_BYTES / 1e9:.0f} GB)")
    return _report(errs)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=sorted(LAYER_CAP_BYTES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("path")
    ap.add_argument("--parcel-file")
    args = ap.parse_args(argv)
    if args.all:
        return main_all(args.path, args.parcel_file)
    if not args.layer:
        ap.error("--layer or --all")
    return _report(verify_layer(args.layer, args.path, args.parcel_file))


if __name__ == "__main__":
    sys.exit(main())
