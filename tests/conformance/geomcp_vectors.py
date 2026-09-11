"""GeoMCP conformance vectors (v1.0).

Transport-independent request/response cases for the GeoMCP protocol
(JSON-RPC 2.0). Each case is a ``(method, params, expectation)`` tuple where
expectation is:

- ``{"result": {"must_contain": [...]}}`` — success; result must have keys
- ``{"error_code": N}`` — response must carry that JSON-RPC error code
- ``{"invalid_envelope": true}`` — the payload must be rejected as an
  invalid request (-32600) without a handler call

An implementation of the protocol must produce the same behaviour.
"""

from __future__ import annotations

from typing import Any

# (method, params, id, expectation)
REQUESTS: list[tuple[str, dict[str, Any], Any, dict[str, Any]]] = [
    # -- success cases -------------------------------------------------- #
    ("geo.capabilities", {}, "c1", {"result": {"must_contain": ["methods", "protocol"]}}),
    ("geo.health", {}, 42, {"result": {"must_contain": ["status", "node"]}}),
    (
        "geo.describe",
        {"geocards": ["asset-x"]},
        "d1",
        {"result": {"must_contain": ["geocards", "skills"]}},
    ),
    (
        "geo.execute",
        {"skill": "echo", "geocards": [], "params": {"v": 1}},
        "e1",
        {"result": {"must_contain": ["status", "outputs"]}},
    ),
    # -- application error codes ----------------------------------------- #
    (
        "geo.execute",
        {"skill": "echo", "geocards": ["ghost-card"]},
        "e2",
        {"error_code": 2002},  # GEOCARD_NOT_FOUND
    ),
    (
        "geo.execute",
        {"skill": "missing-skill", "geocards": []},
        "e3",
        {"error_code": 2001},  # SKILL_NOT_FOUND
    ),
    (
        "geo.execute",
        {"skill": "boom", "geocards": []},
        "e4",
        {"error_code": 2003},  # EXECUTION_FAILED
    ),
    (
        "geo.execute",
        {
            "skill": "echo",
            "geocards": ["asset-x"],
            "spatial": {"bbox": [100, 100, 120, 120], "crs": "EPSG:4326"},
        },
        "e5",
        {"error_code": 2000},  # CONTRACT_NOT_SATISFIED
    ),
    (
        "geo.execute",
        {"skill": "echo", "geocards": "not-a-list"},
        "e6",
        {"error_code": -32602},  # INVALID_PARAMS
    ),
    # -- protocol-level errors -------------------------------------------- #
    ("geo.not-a-method", {}, "x1", {"error_code": -32601}),  # METHOD_NOT_FOUND
    ("geo.describe", {"geocards": ["missing"]}, "x2", {"error_code": 2002}),
]

# Raw envelopes that must be rejected as INVALID_REQUEST (-32600).
INVALID_ENVELOPES: list[tuple[str, dict[str, Any]]] = [
    ("missing-method", {"jsonrpc": "2.0", "id": 1}),
    ("wrong-jsonrpc", {"jsonrpc": "1.0", "id": 1, "method": "geo.health"}),
    ("params-not-object", {"jsonrpc": "2.0", "id": 1, "method": "geo.health", "params": [1, 2]}),
]

CASE_IDS: list[str] = [
    "capabilities",
    "health",
    "describe",
    "execute-success",
    "execute-geocard-not-found",
    "execute-skill-not-found",
    "execute-execution-failed",
    "execute-contract-not-satisfied",
    "execute-invalid-params",
    "method-not-found",
    "describe-geocard-not-found",
]

ENVELOPE_IDS: list[str] = [name for name, _ in INVALID_ENVELOPES]
