# Quickstart — run the whole demo in under 10 minutes

This walkthrough assumes macOS/Linux with Python 3.11+ (the repo was built
and tested with Python 3.12) and `git`.

## 1. Create the environment (≈2 min)

```bash
cd /path/to/GeoNexus-SDK
python3.12 -m venv .venv                 # or: python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
```

This installs GeoNexus plus `pydantic`, `jsonschema`, `PyYAML`, `numpy`,
`rasterio`, `shapely`, `pyproj`, `fastapi`, `uvicorn`, `httpx`, `pytest`.

> If rasterio fails to install, see the note in `docs/MVP_IMPLEMENTATION.md`
> (the demo fails loudly rather than faking GeoTIFF output).

## 2. Run the tests (≈1 min)

```bash
.venv/bin/pytest
```

All tests pass (schema, builder, save/load, contract checks, GeoMCP
request/response, node registry, NDVI skill).

## 3. Validate the demo GeoCard (30 s)

```bash
.venv/bin/geonexus card validate examples/amazon_ndvi/geocard.yaml
```

Expected output: `OK: examples/amazon_ndvi/geocard.yaml` followed by a
summary line.

## 4. Run the Amazon NDVI demo (≈1-2 min)

```bash
.venv/bin/geonexus demo amazon-ndvi
```

What happens (all rasters are **SYNTHETIC** — never real satellite data):

1. loads and validates `examples/amazon_ndvi/geocard.yaml`
2. builds and registers the `ndvi-analysis` GeoSkill
3. starts a Local GeoNode (GeoMCP server) on `127.0.0.1:8787`
4. runs contract validation against the request
5. sends `geo.execute` over HTTP (GeoMCPClient)
6. computes NDVI for 2015 and 2025 synthetic scenes
7. writes `output/ndvi_2015.tif`, `output/ndvi_2025.tif`,
   `output/ndvi_change.tif`
8. prints summary statistics and a contract-gate demonstration (a request
   outside the card's bbox is refused with error 2000)

## 5. Run the federated demo (optional, ≈1 min)

```bash
.venv/bin/geonexus demo federated
```

Starts a shared **GeoCard Registry** (`127.0.0.1:8790`) and a **data node**
(`127.0.0.1:8787`), advertises the demo card, performs a contract-gated
registry search, then executes the NDVI skill **on the data node** via
pushdown. Outputs land in `examples/federated/output/`.

You can drive the pieces manually too:

```bash
.venv/bin/geonexus registry start                       # terminal A
.venv/bin/geonexus node start --registry http://127.0.0.1:8790   # terminal B
.venv/bin/geonexus registry search --url http://127.0.0.1:8790 \
    --capability ndvi --bbox=-73.9,-15,-44,5 --bands B04,B08
.venv/bin/geonexus inspector http://127.0.0.1:8787
```

## 6. Start a live node and call it with curl (optional, ≈1 min)

Terminal A:

```bash
.venv/bin/geonexus node start
```

Terminal B:

```bash
curl http://127.0.0.1:8787/health
# {"status":"ok","node":"local-node",...}

curl http://127.0.0.1:8787/capabilities

curl -X POST http://127.0.0.1:8787/geomcp -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0",
  "id": "curl-1",
  "method": "geo.execute",
  "params": {
    "skill": "ndvi-analysis",
    "geocards": ["sentinel-2-amazon"],
    "spatial": {"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
    "temporal": {"start": "2020-01-01", "end": "2025-01-01"},
    "params": {
      "red": "/absolute/path/to/examples/amazon_ndvi/output/synthetic_red_2015.tif",
      "nir": "/absolute/path/to/examples/amazon_ndvi/output/synthetic_nir_2015.tif",
      "output": "/absolute/path/to/out/ndvi_via_curl.tif"
    }
  }
}'
```

(The demo run in step 4 already generated the synthetic red/NIR pairs you can
point at.)

## 6. GeoAgent + MCP bridge (V0.5, optional, ≈1 min each)

```bash
# GeoAgent: capability-based plan + pushdown execution (registry + node + 2 NDVI steps)
.venv/bin/geonexus demo agent

# Plan/execute a goal against a live registry (with the demo node running):
.venv/bin/geonexus agent run --registry http://127.0.0.1:8790 \
    --capability ndvi --bbox=-73.9,-15,-44,5 --bands B04,B08 \
    --window 2015-01-01/2015-12-31 --window 2025-01-01/2025-12-31 \
    --param red=/abs/synthetic_red_2015.tif --param nir=/abs/synthetic_nir_2015.tif \
    --label vegetation-change-amazon

# MCP bridge (official MCP SDK over stdio — wire it into Claude Desktop etc.)
.venv/bin/geonexus mcp run --bare
```

## 7. Explore

```bash
.venv/bin/geonexus version
.venv/bin/geonexus card inspect examples/amazon_ndvi/geocard.yaml
.venv/bin/geonexus skill list --url http://127.0.0.1:8787
.venv/bin/geonexus registry skill list --url http://127.0.0.1:8790
.venv/bin/geonexus init my-project
.venv/bin/python examples/local_node.py
```

## 8. Where are the outputs?

```
examples/amazon_ndvi/output/
├── synthetic_red_2015.tif     # SYNTHETIC input
├── synthetic_nir_2015.tif     # SYNTHETIC input
├── synthetic_red_2025.tif
├── synthetic_nir_2025.tif
├── ndvi_2015.tif              # float32 NDVI, EPSG:4326
├── ndvi_2025.tif
├── ndvi_change.tif            # 2025 - 2015
└── demo_summary.json
```

All outputs are tagged `GEONEXUS_SYNTHETIC=TRUE`.

Total: well under 10 minutes including installation.
