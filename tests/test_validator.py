"""Tests for the GeoCard ContractValidator (contract satisfaction)."""

from __future__ import annotations

from geonexus.geocard import ContractValidator, GeoCardBuilder

AMAZON_BBOX = [-73.9, -15.0, -44.0, 5.0]


def _make_card() -> GeoCardBuilder:
    return (
        GeoCardBuilder(
            id="sentinel-2-amazon",
            type="data",
            name="Sentinel-2 Amazon",
            description="Sentinel-2 imagery covering Amazon rainforest",
        )
        .spatial(bbox=AMAZON_BBOX, crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .band("B04", dtype="uint16")
        .band("B08", dtype="uint16")
    )


def test_contract_crs() -> None:
    validator = ContractValidator()
    card = _make_card().build()

    ok = validator.check(card, crs="EPSG:4326")
    assert ok.satisfied
    assert any("CRS compatible" in r for r in ok.reasons)

    bad = validator.check(card, crs="EPSG:3857")
    assert not bad.satisfied
    assert any("CRS incompatible" in r for r in bad.reasons)


def test_contract_bbox() -> None:
    validator = ContractValidator()
    card = _make_card().build()

    # Requested bbox fully inside the card bbox.
    ok = validator.check(card, bbox=[-70.0, -10.0, -50.0, 0.0])
    assert ok.satisfied
    assert any("Spatial overlap" in r for r in ok.reasons)

    # Disjoint bbox.
    bad = validator.check(card, bbox=[100.0, 100.0, 120.0, 120.0])
    assert not bad.satisfied
    assert any("does not intersect" in r for r in bad.reasons)


def test_contract_temporal() -> None:
    validator = ContractValidator()
    card = _make_card().build()

    ok = validator.check(card, start="2020-01-01", end="2025-01-01")
    assert ok.satisfied
    assert any("Temporal overlap" in r for r in ok.reasons)

    bad = validator.check(card, start="2030-01-01", end="2035-01-01")
    assert not bad.satisfied
    assert any("Temporal mismatch" in r for r in bad.reasons)

    # Requested window that starts before the card and ends inside it.
    ok = validator.check(card, start="2010-01-01", end="2016-01-01")
    assert ok.satisfied


def test_contract_bands() -> None:
    validator = ContractValidator()
    card = _make_card().build()

    ok = validator.check(card, required_bands=["B04", "B08"])
    assert ok.satisfied
    assert any("Band compatible" in r for r in ok.reasons)

    bad = validator.check(card, required_bands=["B04", "B11"])
    assert not bad.satisfied
    assert any("Band mismatch" in r for r in bad.reasons)


def test_contract_resolution() -> None:
    validator = ContractValidator()
    card = _make_card().build()

    # Card is 10m; requesting 10m or coarser is fine.
    ok = validator.check(card, required_resolution=10)
    assert ok.satisfied
    ok = validator.check(card, required_resolution=20)
    assert ok.satisfied

    # Requesting finer resolution than the card provides fails.
    bad = validator.check(card, required_resolution=5)
    assert not bad.satisfied
    assert any("Resolution mismatch" in r for r in bad.reasons)


def test_contract_combined() -> None:
    validator = ContractValidator()
    card = _make_card().build()

    ok = validator.check(
        card,
        bbox=AMAZON_BBOX,
        crs="EPSG:4326",
        start="2020-01-01",
        end="2025-01-01",
        required_bands=["B04", "B08"],
        required_resolution=10,
    )
    assert ok.satisfied

    bad = validator.check(
        card,
        bbox=AMAZON_BBOX,
        crs="EPSG:4326",
        start="2020-01-01",
        end="2025-01-01",
        required_bands=["B04", "B11"],
        required_resolution=10,
    )
    assert not bad.satisfied
