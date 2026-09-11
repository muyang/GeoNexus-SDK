# STAC adapter (V0.4 read → V1.0+ write)

GeoNexus bridges the [STAC](https://stacspec.org/) (SpatioTemporal Asset
Catalog) ecosystem in both directions:

- **Read** (V0.4): import STAC Items as GeoCards (`geonexus card import-stac`).
- **Write** (V1.0+): publish GeoCards as STAC Items / Catalogs
  (`geonexus card export-stac`).

## Read side

```bash
geonexus card import-stac https://.../items/S2B_...   # URL or local file
```

A STAC Item (GeoJSON Feature) maps onto a GeoCard: id, bbox, CRS
(`proj:epsg`), resolution (`gsd`), temporal, bands (`eo:bands`), provenance
and license. The real-data demo (`geonexus demo stac-real`) imports live
Sentinel-2 items from Microsoft Planetary Computer.

## Write side (V1.0+)

```python
from geonexus.adapters import geocard_to_stac_item, geocard_to_stac_catalog, save_stac_item

item = geocard_to_stac_item(card, collection="geonexus-demo")
save_stac_item(item, "sentinel-2-amazon.stac-item.json")

result = geocard_to_stac_catalog([card], catalog_id="geonexus-demo")
save_stac_item(result["catalog"], "catalog.json")
```

CLI:

```bash
geonexus card export-stac examples/amazon_ndvi/geocard.yaml \
    --output item.json --collection demo

# Round-trip: the exported item imports back as a valid GeoCard:
geonexus card import-stac item.json
```

Mapping (GeoCard → STAC Item):

| GeoCard | STAC Item |
|---------|-----------|
| `id` (sanitized to `[a-zA-Z0-9._~-]`) | `id` |
| `spatial.bbox` | `bbox` (+ polygon `geometry` when no explicit geometry) |
| `temporal.start/end` | `properties.datetime/start_datetime/end_datetime` |
| `name` / `description` | `properties.title` / `description` |
| `license` | `properties.license` |
| `spatial.crs` (EPSG:xxxx) | `properties["proj:epsg"]` |
| `spatial.resolution` | `properties.gsd` |
| `bands[]` | one asset per band with `eo:bands` (else a `geocard` metadata asset) |
| `access.endpoint` / `format` | asset `href` / `type` |
| `type`, `capabilities`, version | `properties["geonexus:..."]` extension fields |

The GeoNexus extension namespace (`geonexus:type`, `geonexus:capabilities`,
`geonexus:geocard_version`) lets a STAC consumer recover the original
GeoCard semantics from the exported item.

## Demo

```bash
geonexus demo stac-write   # card -> STAC Item + Catalog -> re-import round trip
```

## Tests

`tests/test_stac_write.py` (mapping, id sanitization, catalog, export→import
round trip, JSON shape) — no network required.
