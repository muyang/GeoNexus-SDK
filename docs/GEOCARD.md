# GeoCard specification (v1.0)

> GeoCard is *"machine-readable identity, capability and contract description
> for a geospatial asset."*

**Normative status:** this document and
[`schemas/geocard.schema.json`](../schemas/geocard.schema.json) (JSON Schema
Draft 2020-12) are the GeoCard specification. A GeoCard **conforms** when it
validates against the schema. The conformance vector suite in
`tests/conformance/` is the executable form of this spec — a conforming
implementation must pass the same vectors.

## Versioning & extension policy

- `geocard_version` accepts **`0.1`** (legacy) and **`1.0`** (current); the
  SDK emits `1.0`. Cards written as `0.1` remain valid — a 1.0 implementation
  MUST accept them.
- The top level allows `additionalProperties` **for extension only**: an
  unknown top-level key is permitted and MUST be an object or array
  (namespace your extensions, e.g. `geonexus:*`). Known sections are
  validated strictly (`additionalProperties: false`).
- Spec changes: additive (new optional sections/fields) are allowed within
  1.x; removing or changing semantics requires a MAJOR version.
- Deprecation policy: see `docs/API_STABILITY.md`.

## Top-level (required)

| Field              | Type   | Description                                             |
|--------------------|--------|---------------------------------------------------------|
| `geocard_version`  | string | GeoCard spec version — `"0.1"` (legacy) or `"1.0"`.     |
| `id`               | string | Stable unique identifier (`^[A-Za-z0-9][A-Za-z0-9._-]*$`). |
| `type`             | string | One of `data`, `model`, `skill`, `agent`, `workflow`, `knowledge`, `compute`. |
| `name`             | string | Human-readable display name.                            |
| `description`      | string | Free-text description.                                  |
| `tags`             | [string] | Optional discovery tags.                               |

## Sections (all optional)

### `spatial`

| Field        | Type          | Description                                        |
|--------------|---------------|----------------------------------------------------|
| `bbox`       | [number] (4-6)| `[west, south, east, north]` (2D) or 3D variant.    |
| `crs`        | string        | e.g. `"EPSG:4326"`, `"EPSG:3857"`.                 |
| `resolution` | number (>=0)  | Best spatial resolution in metres.                  |
| `geometry`   | string        | Optional GeoJSON geometry footprint.                |

### `temporal`

| Field      | Type   | Description                              |
|------------|--------|------------------------------------------|
| `start`    | string | ISO 8601 start of coverage.              |
| `end`      | string | ISO 8601 end of coverage.                |
| `interval` | string | Sampling interval, e.g. `"P16D"`, `"P5D"`.|

### `bands`

Array of `{name, dtype?, units?, description?}` — spectral/thematic bands.

### `inputs` / `outputs`

Arrays of `{name, type, description?, required?}` — declared interface of a
skill, model, workflow or compute asset. `type` examples: `raster`,
`vector`, `number`, `string`, `array`, `object`.

### `capabilities`

Array of capability names (strings) or `{name, description?}` objects, e.g.
`["ndvi", "change-detection"]`.

### `access`

| Field      | Type   | Description                                      |
|------------|--------|--------------------------------------------------|
| `protocol` | string | e.g. `geomcp`, `https`, `s3`, `stac`, `ogcapi`.  |
| `endpoint` | string | URL or endpoint identifier.                      |
| `auth`     | string | e.g. `none`, `apikey`, `oauth2`.                 |
| `format`   | string | e.g. `GeoTIFF`, `COG`, `Zarr`, `GeoJSON`.        |

### `provenance`

`{provider?, source?, lineage?}` — where the asset came from.

### `license`

Either an SPDX string (`"CC-BY-4.0"`) or `{name?, url?}`.

### `compliance`

| Field          | Type     | Description                                            |
|----------------|----------|--------------------------------------------------------|
| `sovereignty`  | string   | Governing jurisdiction, e.g. `BR`, `EU`, `local-node`. |
| `restrictions` | [string] | Usage restrictions, e.g. `["demo-only"]`.              |
| `sensitivity`  | string   | `public` \| `restricted` \| `sensitive` \| `secret`.   |

### `trust`

| Field      | Type    | Description                                        |
|------------|---------|----------------------------------------------------|
| `verified` | boolean | Whether the asset has been verified.               |
| `score`    | number  | Trust score in [0,1]. **MVP: informational only** — not scientifically validated. |

### `runtime`

`{cpu?, memory?, gpu?}` — requirements for compute assets.

### `interface`

`{type?, version?}` — e.g. `{type: "geomcp-skill", version: "1.0"}`.

### `rendering`

`{min?, max?, colormap?, opacity?}` — visualisation hints.

## Contract satisfaction

A GeoCard is more than metadata: the `ContractValidator` decides whether a
card **satisfies a request**:

| Check                  | Rule                                                                 |
|------------------------|----------------------------------------------------------------------|
| CRS compatibility      | card CRS equivalent to requested CRS (pyproj, fallback string match) |
| bbox intersection      | card bbox intersects requested bbox (reprojected when CRSs differ)   |
| temporal overlap       | card `[start, end]` overlaps the requested window                    |
| band compatibility     | every requested band is provided by the card                         |
| resolution             | card resolution is at least as fine as requested                     |

Missing declarations produce **warnings** ("cannot verify") rather than
silent success. Semantic similarity / IoU thresholds are **not** implemented
in the MVP — they are documented extension points, not validated science.

## Python SDK

```python
from geonexus.geocard import GeoCardBuilder, ContractValidator, load_geocard

card = (
    GeoCardBuilder(id="sentinel-2-amazon", type="data", name="Sentinel-2 Amazon",
                   description="Sentinel-2 imagery covering Amazon rainforest")
    .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
    .temporal(start="2015-01-01", end="2025-12-31")
    .band("B04", dtype="uint16").band("B08", dtype="uint16")
    .capability("ndvi")
    .build()
)
card.validate()
card.save("geocard.yaml")

loaded = GeoCard.load("geocard.yaml")
result = ContractValidator().check(
    loaded, bbox=[...], crs="EPSG:4326",
    start="2020-01-01", end="2025-01-01", required_bands=["B04", "B08"],
)
print(result.satisfied, result.reasons, result.warnings)
```

## Asset types

| Type       | Meaning                                                     |
|------------|-------------------------------------------------------------|
| `data`     | A geospatial dataset (rasters, vectors, point clouds...).   |
| `model`    | A trained geospatial model (segmentation, classification...).|
| `skill`    | A reusable executable capability.                           |
| `agent`    | An orchestration agent (future).                            |
| `workflow` | A composed sequence of capabilities.                        |
| `knowledge`| Curated knowledge about a region/domain.                    |
| `compute`  | A compute service (CPU/GPU) offered by a node.              |
