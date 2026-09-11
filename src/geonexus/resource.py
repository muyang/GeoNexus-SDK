"""GeoCard resource matching (v1.1).

Coordinates the **compute resources** of the GeoNexus ecosystem: a
``type: model`` GeoCard declares its runtime requirements
(``runtime.cpu`` / ``runtime.memory`` / ``runtime.gpu``) and a
``type: compute`` GeoCard declares what a node can actually provide.
:func:`match_resource` decides whether a model can run on a compute node and
explains why (or why not), so the planner can route execution to the node
that satisfies the model's requirements — the "coordinate data, model and
compute" step of the GeoCard workflow.

Typical flow::

    from geonexus.resource import resolve_compute_capabilities, match_resource

    caps = resolve_compute_capabilities(registry_url)   # type: compute cards
    match = match_resource(model_card, caps)            # model.runtime vs nodes
    if match.matched:
        node_url = match.compute_node                   # route here
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .geocard import GeoCard
from .registry import RegistryClient, RegistryClientError

logger = logging.getLogger(__name__)

# GPU capability strings understood by the matcher (case-insensitive).
_GPU_NONE = {"none", "no", "cpu", ""}


class ResourceError(Exception):
    """Raised when resource discovery or matching cannot proceed."""


@dataclass
class ComputeCapability:
    """What one compute node can provide.

    Args:
        node_url: The node's GeoMCP endpoint (where the model would run).
        cpu: Available cores (``None`` = unknown / unlimited).
        memory_gb: Available memory in GiB (``None`` = unknown).
        gpu: GPU capability — ``"none"``, ``"cuda"``, ``"mps"`` or a concrete
            device name. ``None`` = unknown.
    """

    node_url: str
    cpu: int | None = None
    memory_gb: float | None = None
    gpu: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_url": self.node_url,
            "cpu": self.cpu,
            "memory_gb": self.memory_gb,
            "gpu": self.gpu,
        }


@dataclass
class ResourceMatch:
    """The outcome of matching a model card against compute capabilities.

    Attributes:
        matched: True when some compute node satisfies the model's runtime.
        model_id: The matched model's GeoCard id.
        compute_node: The node that satisfies the requirements (matched) or
            ``None`` (unmatched).
        reasons: Human-readable lines explaining the decision — for a match,
            which node satisfies what; for a failure, every unsatisfied
            requirement (so callers can surface *why* a model was rejected).
    """

    matched: bool
    model_id: str
    compute_node: str | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "model_id": self.model_id,
            "compute_node": self.compute_node,
            "reasons": self.reasons,
        }


_MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(GiB?|GB|MiB?|MB)?\s*$", re.IGNORECASE)


def parse_memory(value: Any) -> float | None:
    """Parse a memory string (``"4Gi"``, ``"4GiB"``, ``"4096Mi"``, ``"8 GB"``)
    into GiB. Returns ``None`` when the value is not parseable.

    - GiB / Gi / GB → GiB as-is
    - MiB / Mi / MB → GiB / 1024
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _MEMORY_RE.match(str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "Gi").lower()
    if unit.startswith("mi"):
        return number / 1024.0
    return number


def _gpu_satisfies(required: Any, provided: Any) -> bool:
    """True when ``provided`` can run something requiring ``required``."""
    required_norm = str(required or "").strip().lower()
    provided_norm = str(provided or "").strip().lower()
    if required_norm in _GPU_NONE:
        return True  # no GPU needed -> any node works
    if provided_norm in _GPU_NONE:
        return False  # model needs a GPU, node has none
    # Same family (cuda/cuda, mps/mps) or a concrete device name matches.
    return required_norm == provided_norm or required_norm.startswith(provided_norm) or provided_norm.startswith(required_norm)


def match_resource(
    model_card: GeoCard,
    capabilities: list[ComputeCapability],
) -> ResourceMatch:
    """Decide whether ``model_card`` can run on any of the compute nodes.

    Rules (all must hold for a match):

    - ``cpu``: model requires ≤ node provides (when both known).
    - ``memory``: model requires ≤ node provides (when both known).
    - ``gpu``: ``none`` requirement → any node; a real requirement (e.g.
      ``cuda``) → node must offer a compatible GPU.

    Unknown values on the model side are treated as satisfied; unknown
    values on the node side are treated as "assume enough" but recorded as a
    warning-level reason. The first satisfying node wins.
    """
    runtime = model_card.runtime
    reasons: list[str] = []
    model_id = model_card.id

    if runtime is None:
        # No declared runtime: any node can host it.
        node = capabilities[0].node_url if capabilities else None
        reasons.append("model declares no runtime requirements -> any node")
        return ResourceMatch(matched=node is not None, model_id=model_id, compute_node=node, reasons=reasons)

    req_cpu = _to_int(runtime.cpu)
    req_mem = parse_memory(runtime.memory)
    req_gpu = str(runtime.gpu or "").strip().lower()

    for cap in capabilities:
        node_reasons: list[str] = []
        ok = True

        if req_cpu is not None:
            if cap.cpu is None:
                node_reasons.append(f"cpu: model needs {req_cpu}, node capacity unknown (assumed ok)")
            elif req_cpu > cap.cpu:
                node_reasons.append(f"cpu: model needs {req_cpu}, node has {cap.cpu}")
                ok = False
        if req_mem is not None:
            if cap.memory_gb is None:
                node_reasons.append(f"memory: model needs {req_mem:.1f}Gi, node capacity unknown (assumed ok)")
            elif req_mem > cap.memory_gb:
                node_reasons.append(f"memory: model needs {req_mem:.1f}Gi, node has {cap.memory_gb:.1f}Gi")
                ok = False
        if req_gpu and req_gpu not in _GPU_NONE:
            if not _gpu_satisfies(req_gpu, cap.gpu):
                node_reasons.append(f"gpu: model needs '{req_gpu}', node has '{cap.gpu or 'none'}'")
                ok = False
            else:
                node_reasons.append(f"gpu: model needs '{req_gpu}', node provides '{cap.gpu or 'none'}'")
        else:
            node_reasons.append(f"gpu: model needs none, node provides '{cap.gpu or 'none'}'")

        if ok:
            reasons.extend(node_reasons)
            reasons.append(f"selected compute node {cap.node_url}")
            return ResourceMatch(matched=True, model_id=model_id, compute_node=cap.node_url, reasons=reasons)
        reasons.append(f"{cap.node_url}: " + "; ".join(r for r in node_reasons if "unknown" not in r) or "insufficient")

    reasons.append(f"no compute node satisfies model '{model_id}' runtime requirements")
    return ResourceMatch(matched=False, model_id=model_id, compute_node=None, reasons=reasons)


def _to_int(value: Any) -> int | None:
    """Coerce a cpu value (int or '2') to int, or None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_compute_capabilities(
    registry_url: str,
    timeout: float = 30.0,
    client: RegistryClient | None = None,
) -> list[ComputeCapability]:
    """Discover ``type: compute`` GeoCards at a registry and read their
    advertised capabilities.

    Returns a :class:`ComputeCapability` per compute card (node_url from the
    card's ``access.endpoint`` or the registry entry's ``node_url``).
    """
    owns = client is None
    client = client or RegistryClient(registry_url, timeout=timeout)
    try:
        results = client.search(type="compute")
    except RegistryClientError as exc:
        raise ResourceError(f"Compute discovery failed at {registry_url}: {exc}") from exc
    finally:
        if owns:
            client.close()

    caps: list[ComputeCapability] = []
    for result in results:
        entry = result.get("entry") or {}
        card = entry.get("card") or entry
        card_id = str(card.get("id", "compute"))
        runtime = card.get("runtime") or {}
        node_url = (
            entry.get("node_url")
            or (card.get("access") or {}).get("endpoint")
            or f"unknown-node:{card_id}"
        )
        caps.append(
            ComputeCapability(
                node_url=str(node_url),
                cpu=_to_int(runtime.get("cpu")),
                memory_gb=parse_memory(runtime.get("memory")),
                gpu=runtime.get("gpu"),
            )
        )
    return caps
