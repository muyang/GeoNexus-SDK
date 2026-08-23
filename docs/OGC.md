# OGC API adapter (V1.0 → V1.1)

GeoNexus does not replace OGC standards — it **adapts** them. The OGC API
adapters import the read side of OGC API services into GeoCards, bridge the
write side (execution) into GeoSkills, and retrieve raster data via
Coverages — so discovery, contract validation, federation and execution
treat standards-based services like any other GeoNexus asset.

V1.1 adds three more OGC families: **Records** (catalogue discovery),
**Tiles / Maps / Styles** (the visualization plane) and the legacy
**WMS / WMTS** services.

## What it bridges

| OGC API family | GeoCard mapping | Use |
|----------------|-----------------|-----|
| OGC API - **Features** collections | `type: data` card | dataset discovery: spatial extent, temporal extent, CRS, license |
| OGC API - **Features** items (GeoJSON Feature) | `type: data` card | a single feature as an asset (bbox from geometry) |
| OGC API - **Processes** | `type: skill` card | a process as a reusable capability (inputs/outputs mapped) |
| OGC API - **Coverages** | `type: data` card (bands) + **raster retrieval** | coverage metadata as bands; ranges fetched as CoverageJSON → numpy → GeoTIFF |
| OGC API - **Records** (v1.1) | `type: data` card | catalogue / dataset discovery: themes → capabilities, keywords → tags, data links → access |
| OGC API - **Tiles / Maps / Styles** (v1.1) | `type: data` / `skill` card | renderable tile layers (`tiles` capability, `{z}/{y}/{x}` endpoint) and styles (`styling` capability) |
| **WMS / WMTS** (v1.1) | `type: data` card | legacy map/tile services via GetCapabilities → GetMap / GetTile endpoints |

Every imported card records `access.protocol = "ogcapi"` (or `wms` /
`wmts`) and the service endpoint, plus provenance (`Imported from
OGC API ...`), so execution stays on the node that owns the data — the
GeoNexus principle.

## V1.1: Records, Tiles/Styles, WMS/WMTS (Python API)

```python
from geonexus.adapters import (
    # OGC API - Records (catalogue discovery)
    list_ogc_records, fetch_ogc_record, ogc_record_to_geocard,
    list_ogc_record_collections, ogc_record_collection_to_geocard,
    # OGC API - Tiles / Maps / Styles (visualization plane)
    list_ogc_tilesets, ogc_tileset_to_geocard,
    list_ogc_styles, ogc_style_to_geocard, ogc_visualization_to_geocards,
    # WMS / WMTS (legacy services)
    list_wms_layers, wms_layer_to_geocard,
    list_wmts_layers, wmts_layer_to_geocard,
)

record_cards = [ogc_record_to_geocard(r, base) for r in list_ogc_records(base)]
tile_cards = ogc_visualization_to_geocards(base)          # tiles + styles
wms_cards = [wms_layer_to_geocard(l, wms_url) for l in list_wms_layers(wms_url)]
wmts_cards = [wmts_layer_to_geocard(l, wmts_url) for l in list_wmts_layers(wmts_url)]

# Register any of them into a GeoCard registry for discovery:
# registry.register(card, node_url="...")
```

Notes:

- **Records**: `themes` become discoverable `capabilities`, `keywords`
  become `tags`, and data-download `links` (rel `data` / `enclosure`) become
  the `access.endpoint` — so a catalogue record is immediately
  contract-checkable.
- **Tiles / Styles**: tilesets get a `tiles` capability with the `{z}/{y}/{x}`
  template endpoint for map clients (MapLibre / Leaflet); styles become
  `type: skill` cards with a `styling` capability.
- **WMS / WMTS**: GetCapabilities is parsed (xmltodict when installed, else
  a dependency-free light parser); each advertised layer becomes a card with
  the GetMap / GetTile endpoint ready to render.

## CLI

```bash
# List collections / processes (quick check)
geonexus card import-ogc https://demo.pygeoapi.io/master --collection lakes
geonexus card import-ogc https://demo.pygeoapi.io/master --collection lakes --feature 1
geonexus card import-ogc-process https://demo.pygeoapi.io/master --process hello-world

# Save the imported card and register it at a GeoNexus registry:
geonexus card import-ogc https://demo.pygeoapi.io/master --collection lakes \
    --output lakes.yaml
geonexus registry register lakes.yaml --url http://127.0.0.1:8790 --node http://127.0.0.1:8787
```

## Python API

```python
from geonexus.adapters import (
    list_ogc_collections,
    fetch_ogc_collection,
    fetch_ogc_feature,
    fetch_ogc_process,
)

collections = list_ogc_collections("https://demo.pygeoapi.io/master")
card = fetch_ogc_collection("https://demo.pygeoapi.io/master", "lakes")
feature = fetch_ogc_feature("https://demo.pygeoapi.io/master", "lakes", "1")
process = fetch_ogc_process("https://demo.pygeoapi.io/master", "hello-world")
```

All imports validate against the official GeoCard JSON Schema by default
(`validate=False` to skip).

## Reference run (live endpoints)

Verified against public OGC API services (2026-08-19):

- `https://demo.pygeoapi.io/master` — 17 collections; imported
  `lakes` (Large Lakes, EPSG:4326-equivalent CRS84, global bbox) and
  `lakes/1` (Lake Winnipeg, real bbox) and `hello-world` process (4 inputs,
  1 output).
- `https://demo.ldproxy.net/daraa` — 33 collections (endpoint reachable).

Interop notes learned during the spike:

- `extent.spatial.crs` may be a **string** (pygeoapi) or a **list**
  (ldproxy) — the adapter handles both.
- `f=json` is appended to every request for content negotiation.
- Feature ids can collide across collections, so imported feature cards use
  `collection.feature` ids (sanitized to the GeoCard id pattern).

## Design

- `src/geonexus/adapters/ogc.py` — `OgcApiClient` (read-side, `f=json`),
  pure mapping functions (`ogc_collection_to_geocard`,
  `ogc_feature_to_geocard`, `ogc_process_to_geocard`) and fetch helpers.
- Tests: `tests/test_ogc.py` (mapping, string/list CRS, errors, schema
  validation) — no network required.
- Future work: OGC API - Coverages raster retrieval and Tiles support.

## Write-side bridge (V1.0): GeoMCP execute → OGC API - Processes

An OGC process is wrapped as a **GeoSkill** whose handler bridges
`geo.execute` to the OGC execution endpoints:

```
GeoMCP geo.execute("hello-world", params={name: "GeoNexus"})
   -> GeoNode
   -> GeoSkill (bridge handler)
   -> POST /processes/{id}/execution   (Prefer: respond-async)
   -> poll job (Location header) until terminal
   -> GET job results  (job.results.href or links[rel=results])
   -> {job_status, results, ogc_process, ogc_endpoint}
```

```python
from geonexus.adapters import register_ogc_process_skill

node = GeoNode(name="bridge-node")
register_ogc_process_skill(node, "https://demo.pygeoapi.io/master", "hello-world")
node.run()
# then: geo.execute("hello-world", params={"name": "..."})
```

CLI:

```bash
# Register remote OGC processes as skills at node startup (repeatable):
geonexus node start --ogc-process "https://demo.pygeoapi.io/master::hello-world"

# Offline demo (local mock OGC Processes server):
geonexus demo ogc-process

# Same demo against the real pygeoapi service:
geonexus demo ogc-process --remote https://demo.pygeoapi.io/master --process hello-world
```

Verified against the real service (2026-08-20):

```
geo.execute('hello-world', {name: GeoNexus, message: ...})
  status: ok | job_status: successful
  results: {'id': 'echo', 'value': "Hello ...! ..."}
```

Interop notes:

- Job results can be advertised as `job.results.href` **or** as a
  `links` entry whose `rel` is `results` or the qualified OGC URI
  `.../ogc/1.0/results` (pygeoapi uses the qualified URI).
- Process failures are surfaced as GeoMCP errors carrying the full job
  document (e.g. `InvalidParameterValue`).
- Inputs conversion: scalars → `{"value": ...}`, lists → arrays of value
  objects, dicts with `value`/`href` pass through.

Code: `src/geonexus/adapters/ogc_exec.py` (`OgcProcessExecutor`,
`make_ogc_process_handler`, `make_ogc_process_skill`,
`register_ogc_process_skill`); tests in `tests/test_ogc_exec.py`.

## OGC API - Coverages (V1.0): the raster data plane

Ranges of an OGC coverage are fetched as **CoverageJSON**
(`application/prs.coverage+json`) and parsed into numpy arrays; metadata
(range names/dataTypes/units) becomes GeoCard bands.

```python
from geonexus.adapters import (
    fetch_ogc_coverage_metadata,   # -> GeoCard (bands from ranges)
    fetch_coverage_range,          # -> (np.ndarray, metadata)
    coverage_to_geotiff,           # ndarray -> GeoTIFF (rasterio)
)

card = fetch_ogc_coverage_metadata("https://cov.example", "amazon")
red, meta = fetch_coverage_range("https://cov.example", "amazon", "red")
coverage_to_geotiff(red, "red.tif", crs="EPSG:4326")
```

CLI: `geonexus card import-ogc-coverage URL --collection X`.
Demo: `geonexus demo ogc-coverage` — a `coverage-ndvi` GeoSkill pulls the
red/nir ranges from an (offline mock) OGC Coverages service, computes NDVI
and writes a GeoTIFF with statistics (reference run: NDVI mean 0.644,
vegetation fraction 0.985).

Notes:

- Public OGC API - Coverages endpoints were **not reachable from this
  machine** during the spike (rasdaman 404, eox.at timeout, ldproxy has no
  coverage endpoints) — the demo/tests use a local mock implementation; the
  adapter itself is transport-agnostic.
- `coverage_to_geotiff` requires rasterio and fails loudly without it (same
  policy as the NDVI demo).

Code: `src/geonexus/adapters/ogc_coverage.py`; tests in
`tests/test_ogc_coverage.py`.

## OGC process skills in GeoAgent pipelines (V1.0)

A remote OGC process skill is just a GeoSkill: register it on a node,
advertise the node at a registry, and use it as a **pipeline step** in a
`Goal` alongside local skills — including `${stepN.outputs.key}` templates
that flow local outputs into OGC process inputs.

```bash
geonexus demo ogc-pipeline
```

Reference run (mock OGC Processes server):

```
step 1: Local note (2015)          -> done   (output: "local-note-2015")
step 2: OGC echo (uses step1 out)  -> done   (result echoes "local-note-2015")
```

Tests: `tests/test_ogc_pipeline.py`.
