# GeoMCP specification (v1.0)

GeoMCP is the **geospatial interaction protocol** of GeoNexus — an
*MCP-compatible geospatial extension layer*: a lightweight **JSON-RPC 2.0**
protocol with geospatial context. The protocol is transport-independent;
the canonical transport for the SDK is HTTP (FastAPI), with stdio via the
official MCP bridge.

**Normative status:** this document, together with the conformance vector
suite in `tests/conformance/` (transport-independent request/response
cases), is the GeoMCP specification. The method set, JSON-RPC envelope and
error code registry are **frozen for the 1.x line** — additions are
negotiated via `geo.capabilities` → `methods`, not by overloading existing
methods. `PROTOCOL_VERSION` is `1.0.0`.

## 0. Version negotiation

- The server advertises `protocol`/`version` in `geo.capabilities` and
  `geo.health`.
- Clients SHOULD check the advertised version and MAY refuse servers with an
  incompatible major version; within the same major version, additive
  changes are tolerated.

## 1. Transport

- `POST /geomcp` — JSON-RPC dispatch (the only endpoint a client needs).
- `GET /health` — plain health payload (`{"status": "ok", ...}`).
- `GET /capabilities` — plain capability payload (same content as
  `geo.capabilities`).

Default address: `http://127.0.0.1:8787`.

Transport binding: requests are HTTP `POST`s with `Content-Type:
application/json`; responses are `application/json`. HTTP status is `200`
for valid JSON-RPC (including error responses); malformed request bodies
return `400` with a JSON-RPC parse-error response. The protocol layer
(`GeoMCPDispatcher`) is transport-independent — HTTP is one binding, and the
MCP bridge is another.

## 2. JSON-RPC envelope

Requests:

```json
{
  "jsonrpc": "2.0",
  "id": "task-001",
  "method": "geo.execute",
  "params": { "...": "..." }
}
```

Responses (success):

```json
{ "jsonrpc": "2.0", "id": "task-001", "result": { "...": "..." } }
```

Responses (error):

```json
{
  "jsonrpc": "2.0",
  "id": "task-001",
  "error": { "code": 2001, "message": "Skill not found: x", "data": { "available": [] } }
}
```

Error codes:

| Code   | Meaning                          |
|--------|----------------------------------|
| -32700 | Parse error                      |
| -32600 | Invalid request                  |
| -32601 | Method not found                 |
| -32602 | Invalid params                   |
| -32603 | Internal error                   |
| 2000   | Contract not satisfied           |
| 2001   | Skill not found                  |
| 2002   | GeoCard not found                |
| 2003   | Execution failed                 |
| 2004   | Invalid argument (e.g. missing required skill input) |

## 3. Methods

### `geo.capabilities`

Params: `{}`. Result: protocol surface — `protocol`, `version`, `node`,
`methods`, `tools`, `resources`, `skills`, `geocards`.

Result schema:

```json
{
  "protocol": "geomcp",
  "version": "1.0.0",
  "node": "local-node",
  "methods": ["geo.capabilities", "geo.describe", "geo.execute", "geo.health"],
  "tools": [{"name": "…", "description": "…", "input_schema": {…}, "output_schema": {…}}],
  "resources": ["…"],
  "skills": [{"name": "…", "description": "…", "input_schema": {…}, "output_schema": {…}, "geocard_id": "…"}],
  "geocards": ["…"]
}
```

### `geo.health`

Params: `{}`. Result: `{"status": "ok", "node": ..., "version": ..., "time": ...}`.

### `geo.describe`

Params:

```json
{ "geocards": ["sentinel-2-amazon"], "skills": ["ndvi-analysis"] }
```

Params schema: `{"geocards": {"type": ["array", "null"], "items": {"type": "string"}}, "skills": {"type": ["array", "null"], "items": {"type": "string"}}}`.
Either key optional; omitted means "all". Result:
`{"geocards": [...card dicts...], "skills": [...skill dicts...]}`.
Missing ids produce error 2002 (cards) or 2001 (skills).

### `geo.execute`

Params:

```json
{
  "skill": "ndvi-analysis",
  "geocards": ["sentinel-2-amazon"],
  "spatial":  { "bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326", "resolution": 10 },
  "temporal": { "start": "2020-01-01", "end": "2025-01-01", "interval": "P5D" },
  "params":   { "red": "/path/red.tif", "nir": "/path/nir.tif", "output": "/out/ndvi.tif" },
  "request_id": "task-001"
}
```

Result (shape produced by the runtime):

```json
{
  "status": "ok",
  "skill": "ndvi-analysis",
  "outputs": { "ndvi_raster": "/out/ndvi.tif", "stats": { "...": "..." } },
  "geocards": ["sentinel-2-amazon"],
  "executed_by": "local-runtime",
  "request_id": "task-001"
}
```

`geo.execute` params schema (normative):

```json
{
  "type": "object",
  "required": ["skill"],
  "properties": {
    "skill": {"type": "string"},
    "geocards": {"type": "array", "items": {"type": "string"}},
    "spatial": {
      "type": "object",
      "required": ["bbox"],
      "properties": {
        "bbox": {"type": "array", "minItems": 4, "items": {"type": "number"}},
        "crs": {"type": "string"},
        "resolution": {"type": "number"}
      }
    },
    "temporal": {
      "type": "object",
      "properties": {
        "start": {"type": "string"},
        "end": {"type": "string"},
        "interval": {"type": "string"}
      }
    },
    "params": {"type": "object"},
    "request_id": {"type": "string"}
  }
}
```

Execution result: `status: "ok"` plus `skill`, `outputs` (object), `geocards`,
`executed_by`, `request_id`. Contract failures and other refusals are
delivered as JSON-RPC errors (never as a `status` field).

## 4. Geospatial context

`spatial` and `temporal` are **first-class** in the protocol:

- `spatial.bbox` — `[west, south, east, north]` in `spatial.crs`.
- `spatial.crs` — default `EPSG:4326`.
- `spatial.resolution` — optional requested resolution (metres).
- `temporal.start` / `temporal.end` — ISO 8601 window.
- `temporal.interval` — optional sampling interval.

Before execution the node runs **contract validation** against every
referenced GeoCard: CRS compatibility, bbox intersection, temporal overlap,
band and resolution compatibility. A request outside the contract is refused
with error **2000** (`CONTRACT_NOT_SATISFIED`), including the failing
`reasons` in `error.data`.

## 5. Execution model

1. The client sends `geo.execute` to the node that owns the assets.
2. The node resolves the referenced GeoCards (registry/discovery).
3. The node validates the request against the cards' contracts.
4. The node's `LocalRuntime` looks up the skill, validates its required
   inputs and calls the skill handler with a `SkillContext` (request id,
   spatial/temporal context, resolved cards, workdir).
5. The handler computes (e.g. NDVI) and writes results locally — *the
   computation moved to the data, not the other way round*.
6. The node returns the result (paths + statistics) to the caller.

## 6. Python usage

```python
from geonexus.geomcp import GeoMCPClient

with GeoMCPClient("http://127.0.0.1:8787") as client:
    caps = client.capabilities()
    result = client.execute(
        skill="ndvi-analysis",
        geocards=["sentinel-2-amazon"],
        spatial={"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
        temporal={"start": "2020-01-01", "end": "2025-01-01"},
        params={"red": "red.tif", "nir": "nir.tif"},
    )
```

Errors surface as `GeoMCPClientError` with `.code` and `.message`; the client
handles request ids, timeouts and response validation internally.

## 7. Future: MCP adapter

The planned official MCP adapter will:

- expose GeoMCP methods as MCP tools (`geo_capabilities`, `geo_describe`,
  `geo_execute`, `geo_health`);
- translate MCP tool calls into GeoMCP JSON-RPC requests;
- map MCP resources onto GeoCards.

Because GeoMCP requests are plain JSON with explicit geospatial context, no
protocol redesign is needed.

## 8. Conformance

A GeoMCP implementation conforms to this specification when it passes the
transport-independent vector suite in `tests/conformance/`:

- success shapes for all four methods,
- the application error codes (2000–2004) and protocol error codes
  (−32700…−32603),
- id echo and `jsonrpc: "2.0"` enforcement,
- invalid-envelope rejection (−32600).

The vectors run against `GeoMCPDispatcher` directly (no HTTP), so the same
suite validates any transport binding.
