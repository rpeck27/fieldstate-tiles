#!/usr/bin/env bash
# Town-map tile build → R2 bucket fieldstate-tiles (tiles.fieldstate.org).
# See README.md. Normally run by the "Build tiles" workflow in the public
# fieldstate-tiles repo, one step per job; also runs locally. Each step is
# resumable (skips work whose output exists) and each layer is uploaded only
# after its own verify_tiles.py check passes.
#
#   build.sh basemap          extract + verify + upload basemap.pmtiles
#   build.sh parcels-fetch    pull NC OneMap parcels (owner fields never requested); SHARD=i/n
#   build.sh parcels-tiles    merge counties, tippecanoe, verify + upload parcels.pmtiles
#   build.sh buildings        Microsoft footprints → buildings.pmtiles, verify + upload
#   build.sh assets           glyphs, sprites, licenses, attribution; verify + upload
#   build.sh all              every step in order
#
# Env:
#   BASEMAP_BUILD    Protomaps daily build date YYYYMMDD (default: yesterday UTC)
#   BASEMAP_MAXZOOM  default 15 (14 roughly halves the basemap)
#   WORK, OUT        default .tiles/work, .tiles/out next to this folder's repo root
#   UPLOAD=1 with R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
#                    R2_BUCKET (default fieldstate-tiles). Without UPLOAD=1 nothing leaves the machine.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || echo "$HERE")"
WORK="${WORK:-$REPO/.tiles/work}"
OUT="${OUT:-$REPO/.tiles/out}"
BASEMAP_BUILD="${BASEMAP_BUILD:-$(date -u -d 'yesterday' +%Y%m%d 2>/dev/null || date -u -v-1d +%Y%m%d)}"
BASEMAP_MAXZOOM="${BASEMAP_MAXZOOM:-15}"
BASEMAP_URL="https://build.protomaps.com/${BASEMAP_BUILD}.pmtiles"
BUILDINGS_URL="${BUILDINGS_URL:-https://usbuildingdata.blob.core.windows.net/usbuildings-v2/NorthCarolina.geojson.zip}"
ASSETS_REPO="https://github.com/protomaps/basemaps-assets.git"
ASSETS_REF="${ASSETS_REF:-main}"
UA="FieldstateBot/1.0 (+https://fieldstate.org/bot; bots@fieldstate.org)"
CC="Cache-Control: public, max-age=86400"

mkdir -p "$WORK" "$OUT"
log() { printf '%s tiles %s\n' "$(date -u +%H:%M:%S)" "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing tool: $1 (see README)"; exit 1; }; }
robots_ok() {
  python3 "$HERE/polite_http.py" "$1" || { echo "robots.txt disallows $1 for FieldstateBot; stopping."; exit 3; }
}

r2() {  # rclone against the bucket, configured from env only (no config file)
  need rclone
  : "${R2_ACCOUNT_ID:?set R2_ACCOUNT_ID}" "${R2_ACCESS_KEY_ID:?set R2_ACCESS_KEY_ID}" \
    "${R2_SECRET_ACCESS_KEY:?set R2_SECRET_ACCESS_KEY}"
  RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
  RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true \
    rclone "$@"
}
B="r2:${R2_BUCKET:-fieldstate-tiles}"

upload_pmtiles() {  # $1 = file name under OUT
  [ "${UPLOAD:-0}" = "1" ] || { log "UPLOAD!=1: $1 built, not uploaded"; return; }
  log "upload $1"
  r2 copyto "$OUT/$1" "$B/$1" --s3-chunk-size 64M \
    --header-upload "Content-Type: application/vnd.pmtiles" --header-upload "$CC"
}

step_basemap() {
  need pmtiles
  if [ ! -s "$OUT/basemap.pmtiles" ]; then
    robots_ok "$BASEMAP_URL"
    log "basemap dry run ($BASEMAP_URL, maxzoom $BASEMAP_MAXZOOM)"
    pmtiles extract "$BASEMAP_URL" "$OUT/basemap.pmtiles" --region="$HERE/region-nc-tx-va.geojson" \
      --maxzoom="$BASEMAP_MAXZOOM" --dry-run
    log "basemap extract"
    pmtiles extract "$BASEMAP_URL" "$OUT/basemap.pmtiles" --region="$HERE/region-nc-tx-va.geojson" \
      --maxzoom="$BASEMAP_MAXZOOM" --download-threads=4
  fi
  python3 "$HERE/verify_tiles.py" --layer basemap "$OUT/basemap.pmtiles"
  upload_pmtiles basemap.pmtiles
}

step_parcels_fetch() {
  log "parcels fetch ${SHARD:-all} (owner fields never requested)"
  python3 "$HERE/nc_parcels_fetch.py" --out "$WORK/parcels" ${SHARD:+--shard "$SHARD"}
}

step_parcels_tiles() {
  need tippecanoe
  # All 100 counties or stop; tippecanoe reads the county files directly (no
  # merged copy, to keep the runner's disk free).
  mapfile -t COUNTY_FILES < <(python3 "$HERE/nc_parcels_fetch.py" --out "$WORK/parcels" --check)
  [ "${#COUNTY_FILES[@]}" -ge "${PARCEL_COUNTIES:-100}" ] \
    || { echo "parcels: ${#COUNTY_FILES[@]} finished counties, need ${PARCEL_COUNTIES:-100}; stopping."; exit 1; }
  if [ ! -s "$OUT/parcels.pmtiles" ]; then
    log "parcels tippecanoe (${#COUNTY_FILES[@]} counties)"
    tippecanoe -o "$OUT/parcels.pmtiles" --force -l parcels -Z12 -z16 \
      -y parno -y cntyname -y gisacres \
      --drop-densest-as-needed --extend-zooms-if-still-dropping \
      --attribution='Parcels: NC counties via NC OneMap' \
      "${COUNTY_FILES[@]}"
  fi
  python3 "$HERE/verify_tiles.py" --layer parcels "$OUT/parcels.pmtiles" \
    --parcel-file "$WORK/parcels/county"
  upload_pmtiles parcels.pmtiles
}

step_buildings() {
  need tippecanoe; need unzip
  if [ ! -s "$OUT/buildings.pmtiles" ]; then
    robots_ok "$BUILDINGS_URL"
    mkdir -p "$WORK/buildings"
    if [ ! -s "$WORK/buildings/NorthCarolina.geojson.zip" ]; then
      log "buildings download"
      curl -fL --retry 3 -A "$UA" -o "$WORK/buildings/NorthCarolina.geojson.zip" "$BUILDINGS_URL"
    fi
    [ -s "$WORK/buildings/NorthCarolina.geojson" ] || unzip -o -q "$WORK/buildings/NorthCarolina.geojson.zip" -d "$WORK/buildings"
    python3 "$HERE/buildings_height.py" "$WORK/buildings/NorthCarolina.geojson" > "$WORK/buildings/nc-buildings.geojsonl"
    rm -f "$WORK/buildings/NorthCarolina.geojson"  # 1.3 GB; keep the runner's disk free
    log "buildings tippecanoe"
    tippecanoe -o "$OUT/buildings.pmtiles" --force -l buildings -Z13 -z16 -y h \
      --drop-densest-as-needed \
      --attribution='Buildings: Microsoft Building Footprints (ODbL)' \
      "$WORK/buildings/nc-buildings.geojsonl"
  fi
  python3 "$HERE/verify_tiles.py" --layer buildings "$OUT/buildings.pmtiles"
  upload_pmtiles buildings.pmtiles
}

step_assets() {
  if [ ! -d "$OUT/fonts" ]; then
    log "glyphs + sprites ($ASSETS_REF)"
    rm -rf "$WORK/basemaps-assets"
    git clone --depth 1 --branch "$ASSETS_REF" "$ASSETS_REPO" "$WORK/basemaps-assets"
    git -C "$WORK/basemaps-assets" rev-parse HEAD > "$OUT/ASSETS_COMMIT.txt"
    mkdir -p "$OUT/fonts" "$OUT/sprites" "$OUT/LICENSES/fonts" "$OUT/LICENSES/sprites"
    for f in "Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic"; do
      cp -R "$WORK/basemaps-assets/fonts/$f" "$OUT/fonts/"
    done
    cp -R "$WORK/basemaps-assets/sprites/v4" "$OUT/sprites/"
    # Each asset directory carries its own license; verify refuses to publish without it.
    find "$WORK/basemaps-assets/fonts" -maxdepth 2 -type f \( -iname '*licen*' -o -iname '*ofl*' \) -exec cp {} "$OUT/LICENSES/fonts/" \;
    find "$WORK/basemaps-assets/sprites" -maxdepth 2 -type f -iname '*licen*' -exec cp {} "$OUT/LICENSES/sprites/" \;
    find "$WORK/basemaps-assets" -maxdepth 1 -type f -iname '*licen*' -exec cp {} "$OUT/LICENSES/" \;
    # sprites/ ships no license file of its own: basemaps-assets' README says
    # the sprites derive from MIT-licensed tangrams/icons. Publish that README
    # line's source and the upstream MIT license text beside the sprites.
    if [ -z "$(ls -A "$OUT/LICENSES/sprites")" ]; then
      grep -i -A8 '^## License' "$WORK/basemaps-assets/README.md" > "$OUT/LICENSES/sprites/SOURCE.txt"
      grep -qi 'tangrams/icons' "$OUT/LICENSES/sprites/SOURCE.txt" \
        || { echo "sprites: basemaps-assets README no longer names tangrams/icons; check the license by hand."; exit 1; }
      rm -rf "$WORK/tangrams-icons"
      git clone -q --depth 1 --filter=blob:none --sparse https://github.com/tangrams/icons.git "$WORK/tangrams-icons"
      git -C "$WORK/tangrams-icons" sparse-checkout set --no-cone /LICENSE.md
      cp "$WORK/tangrams-icons/LICENSE.md" "$OUT/LICENSES/sprites/tangrams-icons-LICENSE.md"
    fi
  fi
  cp "$HERE/ATTRIBUTION.txt" "$OUT/ATTRIBUTION.txt"
  printf 'basemap_build=%s\nbasemap_maxzoom=%s\nbuilt_at=%s\n' \
    "$BASEMAP_BUILD" "$BASEMAP_MAXZOOM" "$(date -u +%FT%TZ)" > "$OUT/BUILD.txt"
  python3 "$HERE/verify_tiles.py" --layer assets "$OUT"
  [ "${UPLOAD:-0}" = "1" ] || { log "UPLOAD!=1: assets built, not uploaded"; return; }
  log "upload glyphs, sprites, licenses, attribution"
  r2 copy "$OUT/fonts" "$B/fonts" --transfers 16 \
    --header-upload "Content-Type: application/x-protobuf" --header-upload "$CC"
  r2 copy "$OUT/sprites" "$B/sprites" --transfers 8 --header-upload "$CC"
  r2 copy "$OUT/LICENSES" "$B/LICENSES" --header-upload "$CC"
  for f in ATTRIBUTION.txt BUILD.txt ASSETS_COMMIT.txt; do
    r2 copyto "$OUT/$f" "$B/$f" --header-upload "Content-Type: text/plain; charset=utf-8"
  done
}

case "${1:-all}" in
  basemap) step_basemap ;;
  parcels-fetch) step_parcels_fetch ;;
  parcels-tiles) step_parcels_tiles ;;
  buildings) step_buildings ;;
  assets) step_assets ;;
  all) step_assets; step_basemap; step_parcels_fetch; step_parcels_tiles; step_buildings ;;
  *) echo "usage: build.sh basemap|parcels-fetch|parcels-tiles|buildings|assets|all"; exit 2 ;;
esac
log "done ${1:-all}"
