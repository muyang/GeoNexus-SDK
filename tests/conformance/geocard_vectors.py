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
        "composition": {
            "components": [
                {"id": "geocard.packed.menggu", "role": "data", "step": "s1"},
                {"id": "ndvi-change", "role": "skill", "step": "s2", "required": True},
                {"id": "report.tpl.sdg", "role": "knowledge", "step": "s3", "alias": "report"},
            ],
            "template": "tpl.sdg-report",
        },
        "params": {
            "params": [
                {
                    "name": "year",
                    "type": "integer",
                    "required": True,
                    "minimum": 2015,
                    "maximum": 2024,
                    "target": "s2.params.year",
                },
                {
                    "name": "region",
                    "type": "enum",
                    "enum": ["蒙古", "墨西哥"],
                    "default": "蒙古",
                },
                {"name": "crs", "type": "string", "scope": "fixed", "default": "EPSG:4326"},
            ]
        },
        "recipe_ref": {
            "recipe_id": "recipe://geonexus/sdg-15-3-1@1.0.0",
            "version": "1.0.0",
            "entrypoint": "s1",
            "data_snapshot": {"geokg": "2026.09.1"},
        },
    }


def _composite_case() -> dict[str, Any]:
    """A whole solution as one card: several assets plus a report.

    This is the shape a complex application case takes — the card stays a
    facade (references only), so reusing it never copies data.
    """
    return {
        "geocard_version": "1.0",
        "id": "geocard.case.sdg-15-3-1-mng-2019",
        "type": "workflow",
        "name": "SDG 15.3.1 土地退化评估（蒙古 2019）",
        "description": "2 数据 + 1 算子 + 1 报告：可复用的整体方案。",
        "spatial": {"bbox": [87.7, 41.6, 119.9, 52.1], "crs": "EPSG:4326"},
        "temporal": {"start": "2019-01-01", "end": "2019-12-31"},
        "composition": {
            "components": [
                {"id": "geocard.packed.menggu", "role": "data", "step": "s1"},
                {"id": "geocard.oge.lc08-l2", "role": "data", "step": "s1"},
                {"id": "2026.9.16 SDG15.3.1", "role": "skill", "step": "s2"},
                {"id": "report.sdg1531.mng.2019", "role": "knowledge", "step": "s3"},
            ],
            "template": "tpl.sdg-report",
        },
        "params": {
            "params": [
                {
                    "name": "year",
                    "type": "integer",
                    "required": True,
                    "minimum": 2015,
                    "maximum": 2024,
                },
                {"name": "region", "type": "enum", "enum": ["蒙古", "墨西哥"], "default": "蒙古"},
                {"name": "baseline", "type": "integer", "scope": "fixed", "default": 2015},
            ]
        },
        "recipe_ref": {
            "recipe_id": "recipe://geonexus/sdg-15-3-1@1.0.0",
            "entrypoint": "s1",
            "data_snapshot": {"geokg": "2026.09.1"},
        },
        "rendering": {"colormap": "ylgn", "opacity": 0.7},
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
    _composite_case(),
    _legacy_01(),
    _legacy_string_capabilities(),
    _extension_fields(),
    _3d_bbox(),
]

VALID_CASE_IDS: list[str] = [
    "minimal",
    "full",
    "composite-case",
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


def _composed(**sections: Any) -> dict[str, Any]:
    """A minimal card carrying whichever composition sections are given."""
    card = _minimal()
    card.update(sections)
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
    # --- composition: what a composite asset is made of ------------------- #
    (
        "component-bad-role",
        _composed(composition={"components": [{"id": "a", "role": "not-a-role"}]}),
    ),
    (
        "component-missing-id",
        _composed(composition={"components": [{"role": "data"}]}),
    ),
    (
        "component-blank-id",
        _composed(composition={"components": [{"id": "", "role": "data"}]}),
    ),
    (
        "component-unknown-key",
        _composed(composition={"components": [{"id": "a", "weight": 0.5}]}),
    ),
    (
        "composition-unknown-key",
        _composed(composition={"components": [{"id": "a"}], "dag": ["s1", "s2"]}),
    ),
    (
        "composition-template-not-string",
        _composed(composition={"components": [{"id": "a"}], "template": {"id": "tpl"}}),
    ),
    # --- params: the reusable parameter contract -------------------------- #
    (
        "param-bad-type",
        _composed(params={"params": [{"name": "year", "type": "date"}]}),
    ),
    (
        "param-bad-scope",
        _composed(params={"params": [{"name": "year", "type": "integer", "scope": "maybe"}]}),
    ),
    (
        "param-blank-name",
        _composed(params={"params": [{"name": ""}]}),
    ),
    (
        "param-non-string-name",
        _composed(params={"params": [{"name": 2019}]}),
    ),
    (
        "param-unknown-key",
        _composed(params={"params": [{"name": "year", "units": "a"}]}),
    ),
    (
        "params-unknown-key",
        _composed(params={"params": [], "optional": True}),
    ),
    # --- recipe_ref: the pointer to the executable recipe ----------------- #
    (
        "recipe-ref-missing-recipe-id",
        _composed(recipe_ref={"version": "1.0.0"}),
    ),
    (
        "recipe-ref-blank-recipe-id",
        _composed(recipe_ref={"recipe_id": ""}),
    ),
    (
        "recipe-ref-bad-snapshot-shape",
        _composed(recipe_ref={"recipe_id": "recipe://ns/name@1.0.0", "data_snapshot": "2026.09.1"}),
    ),
    (
        "recipe-ref-bad-snapshot-value",
        _composed(
            recipe_ref={"recipe_id": "recipe://ns/name@1.0.0", "data_snapshot": {"geokg": 1}}
        ),
    ),
    (
        "recipe-ref-unknown-key",
        _composed(recipe_ref={"recipe_id": "recipe://ns/name@1.0.0", "steps": []}),
    ),
]

INVALID_CASE_IDS: list[str] = [name for name, _ in INVALID_VECTORS]
