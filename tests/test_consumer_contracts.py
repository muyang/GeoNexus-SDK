"""Consumer contract tests: Platform v0.9.2 + GeoHarness 0.2.9 call sites (#4).

These tests fail if a future dev build drops or renames a public entry point that
the consumers import. They assert the surface, not the implementation.
"""

from __future__ import annotations

import inspect

from geonexus.geocard.contracts import (
    ContractReport,
    ContractRequest,
    ResultManifest,
    validate_contract,
)
from geonexus.mcp_adapter import create_mcp_server
from geonexus.registration import RegistrationError, make_draft, safe_url, validate_profile
from geonexus.registry.server import RegistryServer


def test_geocard_contracts():
    request = ContractRequest(
        bbox=[-73.9, -15, -44, 5], crs="EPSG:4326", start="2015-01-01", end="2025-12-31", required_bands=["red"]
    )
    assert request.bbox == [-73.9, -15, -44, 5]
    assert callable(validate_contract)
    assert hasattr(ContractReport, "model_dump") and hasattr(ResultManifest, "model_validate")


def test_mcp_adapter_guarded():
    parameters = inspect.signature(create_mcp_server).parameters
    assert parameters["http_guarded"].kind is inspect.Parameter.KEYWORD_ONLY


def test_registration_surface():
    assert issubclass(RegistrationError, Exception)
    assert set(inspect.signature(make_draft).parameters) >= {"metadata", "kind", "operation"}
    assert set(inspect.signature(validate_profile).parameters) >= {"card", "binding"}
    assert safe_url("http://127.0.0.1:8787")


def test_registry_surface():
    parameters = inspect.signature(RegistryServer.__init__).parameters
    assert {"public_submissions", "api_key_principals"} <= set(parameters)
    assert "submitted_by" in inspect.signature(RegistryServer.register).parameters
