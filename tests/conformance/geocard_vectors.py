"""GeoCard conformance vectors (v1.0).

A transport-and-SDK-independent set of cards used to check the official
GeoCard JSON Schema (`schemas/geocard.schema.json`). The vectors are plain
dictionaries so any implementation of the GeoCard spec can run them.

- ``VALID_VECTORS``   — must validate.
- ``INVALID_VECTORS`` — must NOT validate (each targets one rule).
"""

from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------- #
# Valid cards
# --------------------------------------------------------------------------- #
def _minimal() -> dict[str, Any]:
    return {
        "geocard_version": "1.0",
        "id": "minimal-card",
        "type": "data",
        "name": "Minimal",
        "description": "The smallest valid card.",
    }


def _full() -> dict[str, Any]:
    return {
        "geocard_version": "1.0",
        "id": "full-card",
        "type": "skill",
        "name": "Full Card",
        "description": "Exercises every section.",
        "tags": ["a", "b"],
        "spatial": {
            "bbox": [-73.9, -15.0, -44.0, 5.0],
            "crs": "EPSG:4326",
            "resolution": 10,
        },
        "temporal": {"start": "2015-01-01", "end": "2025-12-31", "interval": "P16D"},
        "bands": [
            {"name": "B04", "dtype": "uint16", "units": "dn"},
            {"name": "B08", "dtype": "uint16", "units": "dn"},
        ],
        "inputs": [
            {"name": "nir", "type": "raster", "required": True},
            {"name": "red", "type": "raster"},
        ],
        "outputs": [{"name": "ndvi", "type": "raster"}],
        "capabilities": ["ndvi", {"name": "change-detection", "description": "diff"}],
        "access": {
            "protocol": "geomcp",
            "endpoint": "http://127.0.0.1:8787",
            "auth": "none",
            "format": "GeoTIFF",
        },
        "provenance": {"provider": "X", "source": "Y", "lineage": "Z"},
        "license": "CC-BY-4.0",
        "compliance": {
            "sovereignty": "BR",
            "restrictions": ["demo-only"],
            "sensitivity": "public",
        },
        "trust": {"verified": True, "score": 0.5},
        "runtime": {"cpu": 2, "memory": "4Gi", "gpu": "none"},
        "interface": {"type": "geomcp-skill", "version": "1.0"},
        "rendering": {"min": -1, "max": 1, "colormap": "viridis", "opacity": 0.8},
    }


def _legacy_01() -> dict[str, Any]:
    """Cards written against the 0.1 spec stay valid in 1.0."""
    card = _minimal()
    card["geocard_version"] = "0.1"
    return card


def _legacy_string_capabilities() -> dict[str, Any]:
    card = _minimal()
    card["capabilities"] = ["ndvi", "change-detection"]
    return card


def _extension_fields() -> dict[str, Any]:
    """additionalProperties is allowed at the top level."""
    card = _minimal()
    card["custom_extension"] = {"anything": [1, 2, 3]}
    return card


def _3d_bbox() -> dict[str, Any]:
    card = _minimal()
    card["spatial"] = {"bbox": [0, 0, 0, 1, 1, 1], "crs": "EPSG:4979"}
    return card


VALID_VECTORS: list[dict[str, Any]] = [
    _minimal(),
    _full(),
    _legacy_01(),
    _legacy_string_capabilities(),
    _extension_fields(),
    _3d_bbox(),
]

VALID_CASE_IDS: list[str] = [
    "minimal",
    "full",
    "legacy-0.1",
    "string-capabilities",
    "extension-fields",
    "3d-bbox",
]


# --------------------------------------------------------------------------- #
# Invalid cards
# --------------------------------------------------------------------------- #
def _missing(field: str) -> dict[str, Any]:
    card = _minimal()
    del card[field]
    return card


def _bad_enum(field: str, value: Any) -> dict[str, Any]:
    card = _minimal()
    card[field] = value
    return card


def _bad_type(field: str, value: Any) -> dict[str, Any]:
    card = _minimal()
    card[field] = value
    return card


INVALID_VECTORS: list[tuple[str, dict[str, Any]]] = [
    ("missing-geocard-version", _missing("geocard_version")),
    ("missing-id", _missing("id")),
    ("missing-type", _missing("type")),
    ("missing-name", _missing("name")),
    ("missing-description", _missing("description")),
    ("bad-geocard-version", _bad_enum("geocard_version", "9.9")),
    ("bad-type", _bad_enum("type", "not-a-real-type")),
    (
        "bad-bbox-arity",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "data",
            "name": "X",
            "description": "d",
            "spatial": {"bbox": [1, 2, 3]},
        },
    ),
    (
        "negative-resolution",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "data",
            "name": "X",
            "description": "d",
            "spatial": {"resolution": -5},
        },
    ),
    (
        "unknown-section-key",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "data",
            "name": "X",
            "description": "d",
            "spatial": {"bogus": True},
        },
    ),
    (
        "band-without-name",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "data",
            "name": "X",
            "description": "d",
            "bands": [{"dtype": "uint16"}],
        },
    ),
    (
        "bad-sensitivity",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "data",
            "name": "X",
            "description": "d",
            "compliance": {"sensitivity": "top-secret"},
        },
    ),
    (
        "trust-score-out-of-range",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "data",
            "name": "X",
            "description": "d",
            "trust": {"score": 7},
        },
    ),
    (
        "input-without-type",
        {
            "geocard_version": "1.0",
            "id": "x",
            "type": "skill",
            "name": "X",
            "description": "d",
            "inputs": [{"name": "a"}],
        },
    ),
]

INVALID_CASE_IDS: list[str] = [name for name, _ in INVALID_VECTORS]
