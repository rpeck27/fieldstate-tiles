# fieldstate-tiles

Build scripts for the map tiles behind fieldstate.org's town maps, served from
`https://tiles.fieldstate.org`.

The workflow **Build tiles** (Actions tab, manual start only) builds three map
layers for North Carolina and uploads them to the `fieldstate-tiles` storage
bucket:

| File | Source | License |
|---|---|---|
| `basemap.pmtiles` | Protomaps build of OpenStreetMap | ODbL 1.0, © OpenStreetMap contributors |
| `parcels.pmtiles` | NC OneMap statewide parcels | free to use; county stewards are the authority |
| `buildings.pmtiles` | Microsoft US Building Footprints | ODbL 1.0 |

Parcel owner names, mailing addresses, sale and value fields are never
requested from NC OneMap and never published. Only the parcel number, county
and acreage are kept, and `verify_tiles.py` blocks the upload if any other
field appears.

Every source is read as FieldstateBot (`https://fieldstate.org/bot`), with
robots.txt obeyed and at most one request per second per host.

Full sources and licenses: `ATTRIBUTION.txt`. These scripts are maintained in
the Fieldstate engine and copied here as-is; edit them there, not here.
