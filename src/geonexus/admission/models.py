"""Versioned declarations and governance envelopes, separate from static GeoCards."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from ..geocard import validate_card_schema


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def endpoint_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Endpoint must be HTTP(S), without credentials, query or fragment")
    if parsed.hostname in {"0.0.0.0", "::"}:
        raise ValueError("Advertise a reachable address, not a bind-all address")
    return value.rstrip("/")


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Capacity(Envelope):
    cpu_cores: float | None = Field(default=None, gt=0)
    memory_gib: float | None = Field(default=None, gt=0)
    gpu_count: StrictInt | None = Field(default=None, ge=0)

    @field_validator("cpu_cores", "memory_gib", mode="before")
    @classmethod
    def no_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Capacity is numeric, not boolean")
        return value


class NodeDeclaration(Envelope):
    node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    name: str = Field(min_length=1, max_length=200)
    operator: str = Field(min_length=1, max_length=200)
    endpoint: str
    protocol: Literal["geomcp-http", "mcp-http"] = "geomcp-http"
    jurisdictions: list[str] = Field(default_factory=list, max_length=20)
    capacity: Capacity = Field(default_factory=Capacity)
    provider_class: Literal["third_party", "official"] = "third_party"

    _endpoint = field_validator("endpoint")(endpoint_url)


class Publication(Envelope):
    stage: Literal["unknown", "draft", "experimental", "stable", "deprecated"] = "unknown"
    documentation: str | None = None
    source_code: str | None = None
    reproducible_example: str | None = None


class ToolBinding(Envelope):
    tool: str = Field(min_length=1, max_length=200)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class ResourceDeclaration(Envelope):
    card: dict[str, Any]
    version: str = Field(min_length=1, max_length=100)
    visibility: Literal["public", "restricted", "sensitive", "secret"] = "restricted"
    publication: Publication = Field(default_factory=Publication)
    binding: ToolBinding | None = None

    @field_validator("card")
    @classmethod
    def valid_card(cls, value):
        report = validate_card_schema(value)
        if not report.valid:
            raise ValueError("; ".join(report.errors))
        # Reject non-JSON / non-finite metadata and detach callers' mutable objects.
        return json.loads(json.dumps(value, allow_nan=False))

    @property
    def resource_id(self) -> str:
        return self.card["id"]

    @property
    def digest(self) -> str:
        return digest(self)


class NodeAnnouncement(Envelope):
    schema_version: Literal["node-admission/1.0"] = "node-admission/1.0"
    revision: StrictInt = Field(ge=1)
    declared_at: AwareDatetime = Field(default_factory=utcnow)
    expires_at: AwareDatetime
    node: NodeDeclaration
    resources: list[ResourceDeclaration] = Field(max_length=1000)

    @model_validator(mode="after")
    def coherent(self):
        if self.expires_at <= self.declared_at:
            raise ValueError("expires_at must follow declared_at")
        ids = [r.resource_id for r in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("Resource IDs must be unique within a node")
        return self

    @property
    def digest(self) -> str:
        # Heartbeat renewal cannot silently replace reviewed content. Changing a
        # node, card, binding, release stage, visibility or revision changes this.
        return digest(
            {
                "schema_version": self.schema_version,
                "revision": self.revision,
                "node": self.node.model_dump(mode="json"),
                "resources": [r.model_dump(mode="json") for r in self.resources],
            }
        )


class Principal(Envelope):
    """Must be constructed from verified host authentication, never request JSON."""

    subject: str = Field(min_length=1, max_length=200)
    scopes: frozenset[str] = frozenset()

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise PermissionError("Missing scope: " + scope)


class Evidence(Envelope):
    subject_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    check: Literal[
        "endpoint_reachable",
        "compute_verified",
        "source_verified",
        "data_access",
        "reproducibility",
        "scientific_validation",
    ]
    outcome: Literal["pass", "fail", "unknown"]
    issuer: str
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    reference: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.expires_at <= self.observed_at:
            raise ValueError("Evidence expiry must follow observation")
        return self


class AccessContext(Envelope):
    # Unknown is not permission. The host supplies task-specific decisions.
    access_allowed: bool | None = None
    user_authorized: bool | None = None
    budget_available: bool | None = None
    sovereignty_allowed: bool | None = None
    contract_satisfied: bool | None = None

    @field_validator(
        "access_allowed",
        "user_authorized",
        "budget_available",
        "sovereignty_allowed",
        "contract_satisfied",
        mode="before",
    )
    @classmethod
    def explicit_bool(cls, value):
        if value is not None and type(value) is not bool:
            raise ValueError("Policy decisions must be explicit booleans or unknown")
        return value
