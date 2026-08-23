"""WMS / WMTS adapter (v1.1) — legacy OGC web services bridge.

Bridges the traditional OGC services **Web Map Service (WMS)** and **Web Map
Tile Service (WMTS)** into the GeoNexus discovery ecosystem. Each advertised
layer becomes a GeoCard (type ``data``, ``map`` capability) with the
get-map / get-tile URL captured as the access endpoint, so Web frontends can
render legacy layers without changing their map stack.

- WMS:  ``GetCapabilities`` (``SERVICE=WMS&REQUEST=GetCapabilities``)
- WMTS: ``GetCapabilities`` (``SERVICE=WMTS&REQUEST=GetCapabilities``)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..geocard import GeoCard, GeoCardBuilder

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class OgcLegacyError(Exception):
    """Raised for WMS/WMTS capability or layer failures."""


def _get_capabilities(
    service_url: str,
    service: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch an XML GetCapabilities response and parse it to a dict."""
    try:
        from xmltodict import parse as xml_parse  # type: ignore[import-untyped]
    except ImportError:
        # Small, dependency-free fallback: extract the key XML blocks we need.
        return _parse_capabilities_light(service_url, service, timeout, client)

    owns = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = client.get(
            service_url,
            params={"SERVICE": service, "REQUEST": "GetCapabilities", "VERSION": "1.3.0"},
        )
        response.raise_for_status()
        try:
            return xml_parse(response.text)
        except Exception as exc:  # noqa: BLE001 - fall back to light parser
            logger.warning("xmltodict parse failed; using light parser: %s", exc)
            return _parse_capabilities_light(service_url, service, timeout, client)
    except httpx.HTTPError as exc:
        raise OgcLegacyError(f"WMS/WMTS GetCapabilities failed ({service_url}): {exc}") from exc
    finally:
        if owns:
            client.close()


def _parse_capabilities_light(
    service_url: str,
    service: str,
    timeout: float,
    client: httpx.Client | None,
) -> dict[str, Any]:
    """Minimal GetCapabilities parser (no xmltodict dependency).

    Extracts layer names/titles/bbox via regex on the XML. Good enough for
    discovery GeoCards; use ``xmltodict`` for full fidelity.
    """
    import re

    owns = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = client.get(
            service_url,
            params={"SERVICE": service, "REQUEST": "GetCapabilities", "VERSION": "1.3.0"},
        )
        response.raise_for_status()
        text = response.text
    except httpx.HTTPError as exc:
        raise OgcLegacyError(f"WMS/WMTS GetCapabilities failed ({service_url}): {exc}") from exc
    finally:
        if owns:
            client.close()

    layers: list[dict[str, Any]] = []
    # Match each <Layer> block that directly contains a <Name> (leaf layers),
    # handling nesting by scanning for Name-first blocks.
    for block in re.findall(r"<Layer>(?:(?!</Layer>).)*?<Name>.*?</Name>(?:(?!</Layer>).)*?</Layer>", text, re.S):
        name = re.search(r"<Name>(.*?)</Name>", block, re.S)
        title = re.search(r"<Title>(.*?)</Title>", block, re.S)
        bbox_m = re.search(r"<BoundingBox[^>]*CRS=\"([^\"]+)\"[^>]*>(.*?)</BoundingBox>", block, re.S)
        if name:
            layers.append(
                {
                    "name": name.group(1).strip(),
                    "title": title.group(1).strip() if title else None,
                    "bbox_raw": bbox_m.group(2) if bbox_m else None,
                    "bbox_crs": bbox_m.group(1) if bbox_m else None,
                }
            )
    # WMTS uses <Identifier> instead of <Name>; scan separately.
    if not layers:
        for block in re.findall(r"<Layer>(?:(?!</Layer>).)*?<Identifier>.*?</Identifier>(?:(?!</Layer>).)*?</Layer>", text, re.S):
            ident = re.search(r"<Identifier>(.*?)</Identifier>", block, re.S)
            title = re.search(r"<Title>(.*?)</Title>", block, re.S)
            if ident:
                layers.append(
                    {
                        "name": ident.group(1).strip(),
                        "title": title.group(1).strip() if title else None,
                    }
                )
    return {"capabilities": {"layers": layers}}


def _extract_layers(caps: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise different GetCapabilities shapes into a layer list."""
    layers: list[dict[str, Any]] = []
    cap_maps = caps.get("Capabilities") or caps.get("capabilities") or caps
    if not isinstance(cap_maps, dict):
        return layers
    # Light parser output: {"layers": [{"name": ..., "title": ...}]}.
    if isinstance(cap_maps.get("layers"), list):
        out: list[dict[str, Any]] = []
        for layer in cap_maps["layers"]:
            if not isinstance(layer, dict):
                continue
            name = layer.get("name") or layer.get("Name")
            if not name:
                continue
            standard: dict[str, Any] = {
                "Name": str(name),
                "Title": str(layer.get("title") or layer.get("Title") or name),
            }
            if layer.get("bbox_raw") or layer.get("bbox_crs"):
                standard["BoundingBox"] = {
                    "lowerCorner": " ".join(str(x) for x in (layer.get("bbox_raw") or "").split()[:2]),
                    "upperCorner": " ".join(str(x) for x in (layer.get("bbox_raw") or "").split()[2:]),
                }
            out.append(standard)
        return out
    # Nested Layer trees (WMS): Layer / Layer / Layer …
    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("Layer"):
            children = node["Layer"]
            if isinstance(children, dict):
                children = [children]
            for child in children:
                if isinstance(child, dict) and child.get("Name"):
                    layers.append(child)
                walk(child)
        elif node.get("Contents"):
            walk(node["Contents"])
        elif node.get("Layer"):
            walk(node["Layer"])

    walk(cap_maps)
    return layers


def fetch_wms_capabilities(
    wms_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch WMS GetCapabilities and return the parsed document."""
    return _get_capabilities(wms_url, "WMS", timeout=timeout, client=client)


def list_wms_layers(
    wms_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """List WMS layers from GetCapabilities (name + title + bbox when given)."""
    caps = _get_capabilities(wms_url, "WMS", timeout=timeout, client=client)
    return _extract_layers(caps)


def wms_layer_to_geocard(
    layer: dict[str, Any],
    wms_url: str,
    geocard_version: str = "1.0",
) -> GeoCard:
    """Convert a WMS layer into a renderable GeoCard."""
    name = str(layer.get("Name") or layer.get("name") or "wms-layer")
    title = str(layer.get("Title") or layer.get("title") or name)
    # GetMap endpoint (WMS 1.3.0).
    get_map = f"{wms_url}?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS={name}"

    builder = (
        GeoCardBuilder(
            id=name,
            type="data",
            name=title,
            description=str(
                layer.get("Abstract") or layer.get("abstract") or f"WMS layer '{name}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogc", "wms", "map")
        .capability("map", "WMS renderable layer (GetMap)")
        .access(protocol="wms", endpoint=get_map, format="png")
        .provenance(
            provider=str(wms_url),
            source=name,
            lineage=f"Imported from WMS GetCapabilities (layer '{name}')",
        )
    )
    bbox = _layer_bbox(layer)
    if bbox:
        builder.spatial(bbox=bbox)
    return builder.build()


def fetch_wmts_capabilities(
    wmts_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch WMTS GetCapabilities and return the parsed document."""
    return _get_capabilities(wmts_url, "WMTS", timeout=timeout, client=client)


def list_wmts_layers(
    wmts_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """List WMTS layers from GetCapabilities."""
    caps = _get_capabilities(wmts_url, "WMTS", timeout=timeout, client=client)
    return _extract_layers(caps)


def wmts_layer_to_geocard(
    layer: dict[str, Any],
    wmts_url: str,
    geocard_version: str = "1.0",
) -> GeoCard:
    """Convert a WMTS layer into a tileable GeoCard."""
    name = str(layer.get("Identifier") or layer.get("Name") or layer.get("name") or "wmts-layer")
    title = str(layer.get("Title") or layer.get("title") or name)
    get_tile = (
        f"{wmts_url}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        f"&LAYER={name}&TILEMATRIXSET={{tilematrixset}}"
        f"&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}"
    )

    builder = (
        GeoCardBuilder(
            id=name,
            type="data",
            name=title,
            description=str(
                layer.get("Abstract") or layer.get("abstract") or f"WMTS layer '{name}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogc", "wmts", "tiles")
        .capability("tiles", "WMTS tile layer ({z}/{y}/{x} template)")
        .access(protocol="wmts", endpoint=get_tile, format="png")
        .provenance(
            provider=str(wmts_url),
            source=name,
            lineage=f"Imported from WMTS GetCapabilities (layer '{name}')",
        )
    )
    bbox = _layer_bbox(layer)
    if bbox:
        builder.spatial(bbox=bbox)
    return builder.build()


def _layer_bbox(layer: dict[str, Any]) -> list[float] | None:
    """Extract a WMS/WMTS layer bbox (various shapes) as [w, s, e, n]."""
    raw = layer.get("BoundingBox") or layer.get("boundingBox") or layer.get("bbox_raw")
    crs = layer.get("CRS") or layer.get("bbox_crs")
    if isinstance(raw, dict) and raw.get("lowerCorner") and raw.get("upperCorner"):
        try:
            lower = [float(x) for x in str(raw["lowerCorner"]).split()]
            upper = [float(x) for x in str(raw["upperCorner"]).split()]
            return [min(lower[0], upper[0]), min(lower[1], upper[1]),
                    max(lower[0], upper[0]), max(lower[1], upper[1])]
        except (TypeError, ValueError, IndexError):
            pass
    if isinstance(raw, str) and crs:
        parts = raw.split()
        if len(parts) == 4:
            try:
                return [float(x) for x in parts]
            except ValueError:
                pass
    return None
