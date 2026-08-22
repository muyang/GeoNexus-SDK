# MCP Bridge (V0.5 → V1.1)

The **Model Context Protocol (MCP)** bridge makes GeoNexus both an MCP
**host** and an MCP **client**:

- **Server side (V0.5/V1.0)** — expose GeoMCP as standard MCP tools, so any
  MCP host (Claude Desktop, MCP-enabled assistants, custom clients) can drive
  GeoNexus.
- **Client side (V1.1)** — connect to *external* MCP servers and import
  their tools as GeoSkills, so the GeoAgent planner/executor can use them
  like any local skill.

## Why

The MVP protocol (GeoMCP, JSON-RPC 2.0) is GeoNexus-native. MCP is the
generic tool/resource protocol of the AI ecosystem. The V0.5 bridge is the
official adapter between them, built on the official
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk).

## Tools exposed

| MCP tool           | Maps to GeoMCP      | Purpose |
|--------------------|---------------------|---------|
| `geo_capabilities` | `geo.capabilities`  | Protocol surface: methods, skills, tools, geocards |
| `geo_describe`     | `geo.describe`      | Describe GeoCards and skills (by id or all) |
| `geo_execute`      | `geo.execute`       | Execute a skill on the node that owns the data (geospatial context included) |
| `geo_health`       | `geo.health`        | Node status, version, time |

`geo_execute` accepts the full geospatial context: `skill`, `geocards`,
`spatial` (`{bbox, crs}`), `temporal` (`{start, end}`), `params`,
`request_id`. Contract validation still runs at the node before execution.

## Transport

The MVP bridge runs over **stdio** — the standard MCP transport for local
tools (Claude Desktop launches `geonexus mcp run` as a subprocess):

```bash
geonexus mcp run                    # with bundled demo assets (synthetic)
geonexus mcp run --bare             # empty node (register your own assets)
```

Wire format: newline-delimited JSON-RPC 2.0 (initialize → tools/list →
tools/call). A stdio smoke test in `tests/test_mcp_adapter.py` exercises the
exact client flow.

## Client configuration (example)

Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "geonexus": {
      "command": "/Users/mac/Repos/GeoNexus/mvp/.venv/bin/geonexus",
      "args": ["mcp", "run"]
    }
  }
}
```

Then ask the assistant to "run NDVI analysis on sentinel-2-amazon" — it will
call `geo_execute` with the geospatial context.

## Transports (V0.5 stdio + V1.0 Streamable HTTP)

- **stdio** (local tools): `geonexus mcp run` — newline-delimited JSON-RPC
  on stdin/stdout; the standard transport for Claude Desktop-style local
  tool subprocesses.
- **Streamable HTTP** (remote clients, V1.0): `geonexus mcp serve
  --host 127.0.0.1 --port 9000` serves the MCP endpoint at `http://host:port/mcp`
  using the official SDK's Streamable HTTP transport (JSON-RPC over HTTP,
  SSE responses). Any MCP host that supports HTTP can connect remotely.

Remote MCP client config (e.g. Claude Desktop):

```json
{
  "mcpServers": {
    "geonexus": {
      "url": "http://127.0.0.1:9000/mcp",
      "transport": "http"
    }
  }
}
```

Both transports expose the same four tools; the protocol surface is
identical.

## Code

- `src/geonexus/mcp_adapter.py` — `create_mcp_server(geomcp_server)` builds
  the MCP server; `run_stdio(server)` / `run_http(server, host, port, path)`
  run the stdio and Streamable HTTP transports.
- CLI: `geonexus mcp run` / `geonexus mcp serve` (in `src/geonexus/cli.py`).
- Smoke tests: `tests/test_mcp_adapter.py` covers the stdio handshake and
  the full Streamable HTTP session (initialize → tools/list → tools/call).

## Client side (V1.1): import external MCP tools as GeoSkills

`geonexus.mcp_client` connects to an external MCP server (stdio or
Streamable HTTP), enumerates its tools, and wraps each as a GeoSkill whose
handler forwards calls to the MCP server:

```python
from geonexus.mcp_client import MCPToolClient
from geonexus.geonode import GeoNode

# stdio transport: launch the MCP server as a subprocess
with MCPToolClient.stdio("npx", "-y", "some-mcp-server") as mcp:
    tools = mcp.list_tools()                  # [MCPTool(name, description, input_schema)]
    skills = mcp.to_skills(prefix="ext-")     # [Skill] — handlers call the MCP tool

node = GeoNode(name="imported-node")
node.register_skill_objects(skills)           # GeoAgent can now plan/execute them

# Streamable HTTP transport
with MCPToolClient.http("http://host:9000/mcp", headers={"Authorization": "Bearer x"}) as mcp:
    mcp.register_into(node, prefix="ext-")
```

CLI:

```bash
geonexus mcp import --http http://host:9000/mcp --prefix ext-
geonexus mcp import npx -y some-mcp-server --prefix ext-
```

Notes:

- MCP is async; skill handlers are sync. Each tool call opens its own
  `ClientSession` lifecycle bridged with `asyncio.run` — safe for the
  concurrent plan worker threads (a connection per call).
- `structuredContent` results are returned losslessly; text content blocks
  are flattened to `{"text": ...}`.
- Imported skills carry `mcp_source` (e.g. `mcp:tool-name`) and register
  with the planner like any other skill — combine with the reflective
  executor for cross-system self-healing pipelines.

## Limitations

- The bridge exposes GeoMCP verbatim — it does not auto-decompose goals;
  pair it with the GeoAgent planner (`geonexus agent run` / `agent ask`) for
  orchestration.
