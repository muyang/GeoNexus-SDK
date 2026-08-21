"""GGIHS — GeoNexus Global Geospatial Intelligence Health Service (V1.0+).

Aggregates the **catalog and health of the whole federation** across one or
more GeoCard Registries: per-node cards/skills, live health, and deduplicated
catalog. It is the observation plane on top of the registry layer — it
stores nothing; it only reads and merges.

Endpoints (FastAPI):
  - ``GET /health``   service health + aggregated counts
  - ``GET /summary``  nodes/cards/skills counts + healthy rollup
  - ``GET /nodes``    merged per-node view (cards, skills, healthy)
  - ``GET /catalog``  deduplicated catalog (cards + skills, owner + health)

A registry that errors is recorded in ``errors``; the rest are still served.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from ..registry import RegistryClient, RegistryClientError

logger = logging.getLogger(__name__)

SERVICE_VERSION = "1.0.0"

# Single-file dashboard (vanilla HTML/JS, no external dependencies).
_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GGIHS — GeoNexus federation dashboard</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem auto;
         max-width: 1100px; color: #222; background: #fafafa; }
  h1 { border-bottom: 2px solid #2563eb; padding-bottom: .4rem; }
  .cards { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }
  .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
          padding: .8rem 1.2rem; min-width: 120px; box-shadow: 0 1px 2px #0001; }
  .card .n { font-size: 1.6rem; font-weight: 700; }
  .card .l { color: #64748b; font-size: .8rem; text-transform: uppercase; }
  table { border-collapse: collapse; width: 100%; background: #fff;
          border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
  th, td { text-align: left; padding: .5rem .8rem; border-bottom: 1px solid #eef2f7;
           font-size: .85rem; }
  th { background: #f1f5f9; }
  .ok { color: #16a34a; font-weight: 600; }
  .bad { color: #dc2626; font-weight: 600; }
  .unk { color: #94a3b8; }
  .error { background: #fef2f2; border: 1px solid #fecaca; padding: .6rem .8rem;
           border-radius: 6px; margin: .6rem 0; font-size: .8rem; }
</style>
</head>
<body>
<h1>GGIHS — GeoNexus federation dashboard</h1>
<div id="errors"></div>
<div class="cards" id="cards"></div>
<h2>Nodes</h2>
<table><thead><tr><th>node</th><th>cards</th><th>skills</th><th>health</th></tr></thead>
<tbody id="nodes"></tbody></table>
<h2>Catalog</h2>
<table><thead><tr><th>id</th><th>type</th><th>owner</th><th>health</th></tr></thead>
<tbody id="catalog"></tbody></table>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge = h => h === true ? '<span class="ok">healthy</span>'
  : h === false ? '<span class="bad">unhealthy</span>' : '<span class="unk">unknown</span>';
async function refresh() {
  try {
    const [summary, nodes, catalog] = await Promise.all([
      fetch('/summary').then(r => r.json()),
      fetch('/nodes').then(r => r.json()),
      fetch('/catalog').then(r => r.json()),
    ]);
    document.getElementById('cards').innerHTML = [
      ['nodes', summary.nodes], ['healthy', summary.nodes_healthy],
      ['unhealthy', summary.nodes_unhealthy], ['cards', summary.cards],
      ['skills', summary.skills], ['registries', summary.registries],
    ].map(([l, n]) => `<div class="card"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join('');
    document.getElementById('nodes').innerHTML = Object.entries(nodes.nodes)
      .map(([url, info]) => `<tr><td>${esc(url)}</td><td>${esc(info.cards.join(', '))}</td>` +
        `<td>${esc(info.skills.join(', '))}</td><td>${badge(info.healthy)}</td></tr>`).join('');
    document.getElementById('catalog').innerHTML = catalog.cards
      .map(c => `<tr><td>${esc(c.id)}</td><td>${esc(c.card.type)}</td>` +
        `<td>${esc(c.node_url)}</td><td>${badge(c.healthy)}</td></tr>`).join('');
    const errors = [];
    if (summary.registries_error) errors.push(`${summary.registries_error} registry/registries unreachable`);
    document.getElementById('errors').innerHTML = errors.map(e =>
      `<div class="error">${esc(e)}</div>`).join('');
  } catch (e) {
    document.getElementById('errors').innerHTML =
      `<div class="error">Failed to load GGIHS data: ${esc(e.message)}</div>`;
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class GGIHSService:
    """Aggregate the GeoNexus network's nodes and catalog from registries.

    Args:
        registries: List of GeoCard Registry URLs to aggregate.
        name: Service display name.
        live_probe: When True, probe each node's GeoMCP ``/health`` directly
            (short timeout) instead of relying on registry-reported health.
        probe_timeout: Timeout for live probes.
        api_keys: Optional registry API keys (``{registry_url: key}``) for
            registries that gate writes (reads are usually open).
    """

    def __init__(
        self,
        registries: list[str],
        name: str = "ggihs",
        live_probe: bool = False,
        probe_timeout: float = 3.0,
        api_keys: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.registries = list(registries)
        self.live_probe = live_probe
        self.probe_timeout = probe_timeout
        self.api_keys = api_keys or {}

    # ------------------------------------------------------------------ #
    # Aggregation
    # ------------------------------------------------------------------ #
    def _client(self, registry_url: str) -> RegistryClient:
        return RegistryClient(
            registry_url,
            timeout=max(self.probe_timeout, 10.0),
            api_key=self.api_keys.get(registry_url),
        )

    def collect(self) -> dict[str, Any]:
        """Pull /nodes + catalog from every registry (resilient)."""
        merged_nodes: dict[str, dict[str, Any]] = {}
        cards: dict[str, dict[str, Any]] = {}
        skills: dict[str, dict[str, Any]] = {}
        registry_status: dict[str, Any] = {}
        errors: dict[str, str] = {}

        for registry_url in self.registries:
            try:
                with self._client(registry_url) as client:
                    node_view = client.get_nodes()
                    card_entries = client.list_cards()
                    skill_entries = client.list_skills()
            except RegistryClientError as exc:
                errors[registry_url] = str(exc)
                registry_status[registry_url] = "error"
                logger.warning("GGIHS could not reach registry %s: %s", registry_url, exc)
                continue
            registry_status[registry_url] = "ok"
            for node_url, info in (node_view.get("nodes") or {}).items():
                node = merged_nodes.setdefault(
                    node_url, {"cards": [], "skills": [], "healthy": None}
                )
                node["cards"] = list(set(node["cards"]) | set(info.get("cards") or []))
                node["skills"] = list(set(node["skills"]) | set(info.get("skills") or []))
                node["healthy"] = info.get("healthy")
            for entry in card_entries:
                cards.setdefault(entry["card"]["id"], entry)
            for entry in skill_entries:
                skills.setdefault(entry["skill"]["name"], entry)

        for node in merged_nodes.values():
            node["cards"] = sorted(node["cards"])
            node["skills"] = sorted(node["skills"])

        if self.live_probe:
            for node_url in merged_nodes:
                merged_nodes[node_url]["healthy"] = self._probe_node(node_url)

        return {
            "registries": registry_status,
            "nodes": merged_nodes,
            "cards": cards,
            "skills": skills,
            "errors": errors,
        }

    def _probe_node(self, node_url: str) -> bool:
        try:
            import httpx

            response = httpx.get(f"{node_url.rstrip('/')}/health", timeout=self.probe_timeout)
            data = response.json() if response.status_code == 200 else {}
            return response.status_code == 200 and data.get("status") == "ok"
        except Exception as exc:  # noqa: BLE001 - probe is best-effort
            logger.debug("GGIHS live probe failed for %s: %s", node_url, exc)
            return False

    # ------------------------------------------------------------------ #
    # Views
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        data = self.collect()
        return {
            "status": "ok",
            "service": "ggihs",
            "name": self.name,
            "version": SERVICE_VERSION,
            "summary": self._summarize(data),
            "time": datetime.now(timezone.utc).isoformat(),
        }

    def summary(self) -> dict[str, Any]:
        return self._summarize(self.collect())

    def nodes(self) -> dict[str, Any]:
        data = self.collect()
        return {"count": len(data["nodes"]), "nodes": data["nodes"]}

    def catalog(self) -> dict[str, Any]:
        data = self.collect()
        health = {url: info["healthy"] for url, info in data["nodes"].items()}
        card_list = []
        for card_id, entry in data["cards"].items():
            card_list.append(
                {
                    "id": card_id,
                    "card": entry["card"],
                    "node_url": entry.get("node_url"),
                    "healthy": health.get(entry.get("node_url")),
                }
            )
        skill_list = []
        for skill_name, entry in data["skills"].items():
            skill_list.append(
                {
                    "name": skill_name,
                    "skill": entry["skill"],
                    "node_url": entry.get("node_url"),
                    "healthy": health.get(entry.get("node_url")),
                }
            )
        return {
            "cards": sorted(card_list, key=lambda c: c["id"]),
            "skills": sorted(skill_list, key=lambda s: s["name"]),
            "counts": {"cards": len(card_list), "skills": len(skill_list)},
        }

    def _summarize(self, data: dict[str, Any]) -> dict[str, Any]:
        nodes = data["nodes"]
        healthy = sum(1 for n in nodes.values() if n["healthy"] is True)
        unhealthy = sum(1 for n in nodes.values() if n["healthy"] is False)
        unknown = sum(1 for n in nodes.values() if n["healthy"] is None)
        return {
            "registries": len(self.registries),
            "registries_ok": sum(1 for s in data["registries"].values() if s == "ok"),
            "registries_error": sum(1 for s in data["registries"].values() if s == "error"),
            "nodes": len(nodes),
            "nodes_healthy": healthy,
            "nodes_unhealthy": unhealthy,
            "nodes_unknown": unknown,
            "cards": len(data["cards"]),
            "skills": len(data["skills"]),
        }

    # ------------------------------------------------------------------ #
    # HTTP transport
    # ------------------------------------------------------------------ #
    def create_app(self) -> FastAPI:
        app = FastAPI(title=f"GGIHS: {self.name}", version=SERVICE_VERSION)

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return self.health()

        @app.get("/summary")
        async def summary() -> dict[str, Any]:
            return self.summary()

        @app.get("/nodes")
        async def nodes() -> dict[str, Any]:
            return self.nodes()

        @app.get("/catalog")
        async def catalog() -> dict[str, Any]:
            return self.catalog()

        @app.get("/")
        async def dashboard() -> Any:
            from fastapi.responses import HTMLResponse

            return HTMLResponse(_DASHBOARD_HTML)

        return app

    def run(self, host: str = "127.0.0.1", port: int = 8800, log_level: str = "info") -> None:
        import uvicorn

        uvicorn.run(self.create_app(), host=host, port=port, log_level=log_level)
