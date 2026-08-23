# Data registration & review workflow (v1.1)

How an uploaded dataset becomes a discoverable, executable GeoNexus asset:
**upload → generate GeoCard → submit for review → approve → discoverable
via GeoMCP**.

```
① upload    file (GeoTIFF…) ──raster_to_geocard──▶ draft GeoCard
② submit    register(status="pending") ──▶ registry holds it, hidden
③ review    GET /cards?status=pending → POST /cards/{id}/approve|reject
④ publish   approved → visible in search, executable via GeoMCP
```

## 1. Generate a GeoCard from a file (`geonexus.metadata`)

```python
from geonexus.metadata import inspect_raster, raster_stats, raster_to_geocard

meta = inspect_raster("scene.tif")
# {driver, width, height, count, dtype, crs, bounds, resolution, bands}

stats = raster_stats("scene.tif")            # nodata-aware min/max/mean/…

card = raster_to_geocard(
    "scene.tif",
    name="Amazon NDVI 2025",
    capabilities=["ndvi"],                    # what it can be used for
    tags=["amazon", "uploaded"],
    start="2025-01-01", end="2025-12-31",     # optional temporal coverage
)
# spatial (bbox/CRS/resolution) and bands are read from the actual file;
# access.endpoint points at the file.
```

## 2. Submit for review (registry state machine)

```python
from geonexus.registry import RegistryClient, STATUS_PENDING, STATUS_APPROVED

with RegistryClient("http://registry:8790", api_key="...") as rc:
    # register as pending → NOT discoverable yet
    rc.register(card, node_url="http://node:8787", status=STATUS_PENDING)

    # review queue (admin)
    pending = rc.list_cards(status=STATUS_PENDING)

    # approve → becomes discoverable; or reject with a note
    rc.approve(card.id, note="metadata OK")
    # rc.reject(card.id, note="missing license")
```

- `RegistryEntry.status ∈ {pending, approved, rejected}`; default is
  `approved` (existing registrations keep working unchanged).
- `search()` and `GET /cards` return only **approved** cards — pending and
  rejected are invisible to discovery.
- `GET /cards?status=pending|rejected|all` (admin view), and
  `POST /cards/{id}/approve|reject` (write endpoints, gated by `X-API-Key`
  when configured).
- The review state survives persistence (`RegistryStore(persist_path=...)`).

## 3. Discover & use (via GeoMCP)

Once approved, the card appears in registry search:

```python
hits = rc.search(capability="ndvi", bbox=[...])       # only approved cards
# → the card is found, contract-checked, and executable:
# geo.execute → the node owning the card runs a skill on the file
```

## Web API (BFF)

The reference app exposes the workflow over REST:

- `POST /api/datasets/upload` — multipart file upload → draft card
- `POST /api/datasets/{id}/submit` — draft → pending
- `GET  /api/datasets/pending` — review queue
- `POST /api/datasets/{id}/approve` / `reject` — review decision

See the `geonexus-web` reference repository for a working UI (upload →
pending list → approve → search).
