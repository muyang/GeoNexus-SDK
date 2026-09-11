# GeoNexus Architecture

## 1. Why GeoNexus is not Google Earth Engine

Google Earth Engine (GEE) is a **centralized** platform: petabytes of imagery
are copied into Google's infrastructure, and computation runs next to that
central archive. That model delivers enormous convenience but concentrates
data, capability and control in one operator.

GeoNexus is the opposite:

- **No central data warehouse.** Data stays at sovereign nodes.
- **No global archive.** Each node owns the assets it is responsible for.
- **No monolithic GIS.** GeoNexus orchestrates *descriptions* of assets and
  *capabilities*, then lets the computation run where the data lives.
- **Federated by design.** Nodes are peers; a future registry layer will
  discover nodes, not absorb them.

The MVP deliberately implements only the local slice of this vision so that
the contracts and protocols are proven before any federation machinery is
built.

## 2. Why federated, and why data should remain at sovereign nodes

Sovereignty is a first-class concern:

- **Jurisdiction.** Institutions, regions and nations have rules about where
  their geospatial data may be stored, processed and served. A centralized
  archive forces every participant onto one jurisdiction's terms.
- **Bandwidth and latency.** Imagery is huge. Moving terabytes to a central
  engine to run a per-pixel statistic is wasteful; computing on-site and
  returning small results is efficient.
- **Access control.** Data owners keep direct control of their assets —
  who may read them, under which license, with which restrictions
  (`compliance` and `license` in GeoCard).
- **Resilience and independence.** A federated network does not fail when a
  single operator fails, and no single operator can be compelled to disable
  the whole network.

GeoNexus therefore follows the CAFE principle: **"move computation to the
data"**. The *request* travels; the *data* stays.

## 3. The core loop

```
GeoCard                  describes an asset and its executable contract
   ↓
Registry / Discovery     the node knows which cards it owns
   ↓
Contract validation      does the card satisfy the request? (CRS, bbox,
   ↓                     temporal, bands, resolution)
GeoMCP                   the JSON-RPC protocol carries the request
   ↓
GeoNode                  the sovereign execution boundary
   ↓
GeoSkill                 the reusable capability that runs on the node
   ↓
Result                   rasters, statistics, files — returned to the caller
```

## 4. Why GeoCard is required

An AI agent cannot reason about geospatial assets without a
**machine-readable identity, capability and contract description**. GeoCard
provides:

- **Identity** — stable `id`, `type` (data / model / skill / agent /
  workflow / knowledge / compute), `name`, `description`.
- **Capability** — what the asset can do (`capabilities`, `inputs`,
  `outputs`, `interface`).
- **Contract** — the executable constraints a request must satisfy
  (`spatial`, `temporal`, `bands`, `resolution`, `access`, `compliance`,
  `license`, `trust`).

Without a card, discovery is guesswork and execution is unverifiable. The
card is validated against an official JSON Schema
(`schemas/geocard.schema.json`) and checked by a `ContractValidator` before
any computation starts.

## 5. Why GeoMCP is required, and its relationship with MCP

The Model Context Protocol (MCP) is a generic tool/resource protocol for AI
assistants. GeoMCP is the **geospatial extension layer** on top of that idea:

- It does **not** recreate the MCP ecosystem; the MVP implements a
  lightweight JSON-RPC 2.0 protocol with geospatial context
  (`geo.capabilities`, `geo.describe`, `geo.execute`, `geo.health`).
- The protocol layer is **transport-independent**; HTTP (FastAPI) is one
  transport among possible future ones.
- An **official MCP adapter** is a planned V0.5 extension: a small bridge
  that exposes GeoMCP methods as MCP tools and translates MCP requests into
  GeoMCP executions. Because GeoMCP requests are plain JSON with geospatial
  context, the adapter is mechanical, not architectural.

## 6. Relationship with OGC API and STAC

GeoNexus does not replace existing standards; it **adapts** them:

- **STAC** (SpatioTemporal Asset Catalog) describes catalogs of geospatial
  assets. A GeoCard can be derived from, or reference, STAC items; a future
  adapter (V0.4) will import STAC catalogs into a GeoCard registry.
- **OGC API — Features / Processes / Coverages** provide interoperable
  access and processing. Future adapters will let a GeoNode front OGC API
  services with GeoCards, so the federated network can reach standards-based
  backends.
- **COG / Zarr / PostGIS** are storage formats GeoNexus must be able to
  point at through `access.format` and future adapters.

The MVP keeps these as **extension points** — adapters plug in; the core
protocol does not hard-code them.

## 7. Components

| Component  | Role                                             | MVP scope                          |
|------------|--------------------------------------------------|------------------------------------|
| GeoCard    | Contract & description of assets                 | Schema + SDK + ContractValidator   |
| GeoMCP     | Interaction protocol                             | JSON-RPC 2.0 + FastAPI + client    |
| GeoNode    | Sovereign execution boundary                     | Local node (registry/runtime/server) |
| GeoSkill   | Reusable capability                              | Minimal skill abstraction          |
| GeoAgent   | Orchestration (future)                           | not implemented in the MVP         |

## 8. What the MVP deliberately does not build

- global data warehouse / satellite archive
- GPU clusters or heavy compute management
- production identity / auth systems
- full multi-agent orchestration (GeoAgent)
- a marketplace
- full ChatMap

These belong to later phases (see `docs/MVP_IMPLEMENTATION.md` for the
roadmap). The MVP's job is to prove the **contract → protocol → execution**
loop end to end.
