# Amazon NDVI Demo (synthetic data)

Goal: **analyze vegetation change in the Amazon rainforest** using the
GeoNexus Reference Stack MVP.

## IMPORTANT — synthetic data

This demo generates **synthetic** red/NIR rasters procedurally. They are
**not** real Sentinel-2 or Landsat imagery. Every output GeoTIFF carries the
tag `GEONEXUS_SYNTHETIC=TRUE`, and the GeoCard `geocard.yaml` records the
synthetic provenance explicitly. Do not present these outputs as real
satellite observations.

## What it demonstrates

1. Load GeoCard (`geocard.yaml`)
2. Validate GeoCard against the official JSON Schema
3. Register the `ndvi-analysis` GeoSkill on a Local GeoNode
4. Start the Local GeoNode (GeoMCP server over HTTP)
5. Send GeoMCP requests (`geo.execute` with geospatial context)
6. Execute NDVI analysis on the node
7. Generate `ndvi_2015.tif`, `ndvi_2025.tif`, `ndvi_change.tif`
8. Calculate and print summary statistics

It also demonstrates **contract gating**: a request whose bbox does not
intersect the GeoCard's spatial extent is refused by the node.

## Run it

```bash
cd /Users/mac/Repos/GeoNexus/mvp
.venv/bin/geonexus demo amazon-ndvi
```

or directly:

```bash
.venv/bin/python examples/amazon_ndvi/run_demo.py
```

Outputs land in `examples/amazon_ndvi/output/` (override with `--output DIR`,
choose the port with `--port N`, or run fully in-process with `--no-server`).

## Files

- `geocard.yaml` — GeoCard of the (synthetic) Sentinel-2 Amazon asset.
- `skill.py` — the `ndvi-analysis` GeoSkill: synthetic scene generation,
  NDVI computation, GeoTIFF writing, summary statistics.
- `run_demo.py` — the end-to-end demo runner.
