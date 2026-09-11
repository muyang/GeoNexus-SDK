"""Tests for OGC API - Tiles/Styles and WMS/WMTS adapters."""

from __future__ import annotations

import httpx

from geonexus.adapters import (
    OgcApiClient,
    list_ogc_styles,
    list_ogc_tilesets,
    ogc_style_to_geocard,
    ogc_tileset_to_geocard,
    wms_layer_to_geocard,
    wmts_layer_to_geocard,
)
from geonexus.adapters.ogc_wms import list_wms_layers, list_wmts_layers


def _json_client(routes: dict[str, dict]) -> OgcApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "not found"}, request=request)
        return httpx.Response(200, json=body, request=request)

    return OgcApiClient(
        "https://ogc.test",
        client=httpx.Client(base_url="https://ogc.test", transport=httpx.MockTransport(handler)),
    )


TILESET = {
    "id": "amazon-ndvi",
    "title": "Amazon NDVI tiles",
    "description": "NDVI raster tiles",
    "crs": "http://www.opengis.net/def/crs/EPSG/0/3857",
    "links": [
        {"rel": "self", "href": "https://ogc.test/tiles/amazon-ndvi"},
        {"rel": "item", "href": "https://ogc.test/tiles/amazon-ndvi/{z}/{y}/{x}"},
    ],
}

STYLE = {
    "id": "ndvi-colormap",
    "title": "NDVI colormap",
    "description": "Green-to-red NDVI style",
    "links": [
        {"rel": "self", "href": "https://ogc.test/styles/ndvi-colormap"},
        {"rel": "style", "href": "https://ogc.test/styles/ndvi-colormap?f=mapbox"},
    ],
}


class TestTiles:
    def test_list_tilesets(self) -> None:
        client = _json_client({"/tiles": {"tilesets": [TILESET]}})
        with client:
            sets = list_ogc_tilesets("https://ogc.test", client=client)
        assert len(sets) == 1
        assert sets[0]["id"] == "amazon-ndvi"

    def test_tileset_to_geocard(self) -> None:
        card = ogc_tileset_to_geocard(TILESET, "https://ogc.test")
        assert card.id == "amazon-ndvi"
        assert card.type == "data"
        assert "tiles" in [c.name for c in card.capabilities]
        # Access endpoint = tile template link.
        assert card.access is not None
        assert "{z}/{y}/{x}" in (card.access.endpoint or "")
        # No bbox, but CRS is declared -> spatial carries the CRS only.
        assert card.spatial is not None
        assert card.spatial.bbox is None
        assert card.spatial.crs == "http://www.opengis.net/def/crs/EPSG/0/3857"

    def test_tileset_with_bbox(self) -> None:
        ts = dict(TILESET, boundingBox=[-73.9, -15.0, -44.0, 5.0])
        card = ogc_tileset_to_geocard(ts, "https://ogc.test")
        assert card.spatial is not None
        assert card.spatial.bbox == [-73.9, -15.0, -44.0, 5.0]
        assert card.spatial.crs is not None


class TestStyles:
    def test_list_styles(self) -> None:
        client = _json_client({"/styles": {"styles": [STYLE]}})
        with client:
            styles = list_ogc_styles("https://ogc.test", client=client)
        assert len(styles) == 1

    def test_style_to_geocard(self) -> None:
        card = ogc_style_to_geocard(STYLE, "https://ogc.test")
        assert card.id == "ndvi-colormap"
        assert card.type == "skill"
        assert "styling" in [c.name for c in card.capabilities]
        assert card.inputs and card.inputs[0].name == "layer"
        assert card.access is not None
        assert "mapbox" in (card.access.endpoint or "")


class TestVisualizationBundle:
    def test_visualization_to_geocards(self) -> None:
        from geonexus.adapters.ogc_tiles import (
            list_ogc_styles,
            list_ogc_tilesets,
        )

        client = _json_client(
            {
                "/tiles": {"tilesets": [TILESET]},
                "/styles": {"styles": [STYLE]},
            }
        )
        with client:
            tilesets = list_ogc_tilesets("https://ogc.test", client=client)
            styles = list_ogc_styles("https://ogc.test", client=client)
        cards = [
            ogc_tileset_to_geocard(t, "https://ogc.test") for t in tilesets
        ] + [ogc_style_to_geocard(st, "https://ogc.test") for st in styles]
        assert len(cards) == 2
        assert {c.id for c in cards} == {"amazon-ndvi", "ndvi-colormap"}


# --------------------------------------------------------------------------- #
# WMS / WMTS
# --------------------------------------------------------------------------- #

WMS_XML = """<?xml version="1.0"?>
<WMS_Capabilities version="1.3.0">
  <Capability>
    <Layer>
      <Title>root</Title>
      <Layer>
        <Name>amazon:ndvi</Name>
        <Title>Amazon NDVI</Title>
        <Abstract>NDVI layer</Abstract>
        <BoundingBox CRS="EPSG:4326" minx="-73.9" miny="-15.0" maxx="-44.0" maxy="5.0"/>
      </Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>
"""

WMTS_XML = """<?xml version="1.0"?>
<Capabilities version="1.0.0">
  <Contents>
    <Layer>
      <Identifier>global:basemap</Identifier>
      <Title>Global Basemap</Title>
    </Layer>
  </Contents>
</Capabilities>
"""


def _xml_client(xml: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml, request=request)

    return httpx.Client(
        base_url="https://legacy.test", transport=httpx.MockTransport(handler)
    )


class TestWMS:
    def test_list_wms_layers(self) -> None:
        client = _xml_client(WMS_XML)
        with client:
            layers = list_wms_layers("https://legacy.test/wms", client=client)
        assert any(layer.get("Name") == "amazon:ndvi" for layer in layers)

    def test_wms_layer_to_geocard(self) -> None:
        layer = {
            "Name": "amazon:ndvi",
            "Title": "Amazon NDVI",
            "Abstract": "NDVI layer",
            "BoundingBox": {"lowerCorner": "-73.9 -15.0", "upperCorner": "-44.0 5.0"},
        }
        card = wms_layer_to_geocard(layer, "https://legacy.test/wms")
        assert card.id == "amazon:ndvi"
        assert "wms" in card.tags
        assert "map" in [c.name for c in card.capabilities]
        assert card.access is not None
        assert "REQUEST=GetMap" in (card.access.endpoint or "")
        assert card.spatial is not None
        assert card.spatial.bbox == [-73.9, -15.0, -44.0, 5.0]


class TestWMTS:
    def test_list_wmts_layers(self) -> None:
        client = _xml_client(WMTS_XML)
        with client:
            layers = list_wmts_layers("https://legacy.test/wmts", client=client)
        assert any(layer.get("Name") == "global:basemap" for layer in layers)

    def test_wmts_layer_to_geocard(self) -> None:
        layer = {"Identifier": "global:basemap", "Title": "Global Basemap"}
        card = wmts_layer_to_geocard(layer, "https://legacy.test/wmts")
        assert card.id == "global:basemap"
        assert "wmts" in card.tags
        assert "tiles" in [c.name for c in card.capabilities]
        assert card.access is not None
        assert "REQUEST=GetTile" in (card.access.endpoint or "")
        assert "{z}" in (card.access.endpoint or "")
