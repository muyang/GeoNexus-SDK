"""GeoCard validation.

Two distinct kinds of validation live here:

1. :func:`validate_card_schema` — structural validation of a raw card
   dictionary against the official GeoCard JSON Schema.
2. :class:`ContractValidator` — *contract satisfaction*: given a request
   (CRS, bbox, temporal window, bands, resolution), decide whether a GeoCard
   satisfies it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import jsonschema

from .model import GeoCard, GeoCardError

logger = logging.getLogger(__name__)

_REPO_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "schemas" / "geocard.schema.json"
)


# --------------------------------------------------------------------------- #
# 1. Structural validation against the official JSON Schema
# --------------------------------------------------------------------------- #
@dataclass
class SchemaReport:
    """Result of schema validation against the official GeoCard schema."""

    valid: bool
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


def _resolve_schema_path(explicit: str | os.PathLike | None) -> Path:
    """Resolve the GeoCard schema path.

    Priority: explicit argument > ``GEONEXUS_SCHEMA_PATH`` env var > the
    repo-layout ``schemas/geocard.schema.json`` (editable installs) > the
    packaged copy inside the installed distribution (wheel installs).
    """
    if explicit is not None:
        candidate = Path(explicit)
    else:
        env = os.environ.get("GEONEXUS_SCHEMA_PATH")
        if env:
            candidate = Path(env)
        elif _REPO_SCHEMA_PATH.exists():
            candidate = _REPO_SCHEMA_PATH
        else:
            # Wheel install: schema ships as package data.
            import importlib.resources

            packaged = importlib.resources.files("geonexus") / "data" / "geocard.schema.json"
            with importlib.resources.as_file(packaged) as path:
                candidate = path
    if not candidate.exists():
        raise GeoCardError(f"GeoCard schema not found at {candidate}")
    return candidate


def load_schema(schema_path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load and parse the official GeoCard JSON Schema."""
    path = _resolve_schema_path(schema_path)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def validate_card_schema(
    data: dict[str, Any],
    schema_path: str | os.PathLike | None = None,
) -> SchemaReport:
    """Validate a raw GeoCard dictionary against the official JSON Schema.

    Returns a :class:`SchemaReport`; never raises for invalid cards.
    """
    schema = load_schema(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
    return SchemaReport(valid=not errors, errors=errors)


# --------------------------------------------------------------------------- #
# 2. Contract satisfaction
# --------------------------------------------------------------------------- #
@dataclass
class ContractResult:
    """Outcome of a contract check against a GeoCard."""

    satisfied: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 date or datetime into a datetime object."""
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(text), time.min)
    except ValueError as exc:
        raise ValueError(f"Invalid date/time value {value!r}") from exc


def _normalise_crs(value: str) -> str:
    """Normalise a CRS identifier for string comparison."""
    return value.strip().upper().replace(" ", "")


def _crs_equivalent(card_crs: str, request_crs: str) -> bool | None:
    """Compare two CRS identifiers.

    Uses pyproj when both identifiers parse; otherwise falls back to a
    normalised string comparison. Returns None when comparison is impossible.
    """
    try:
        import pyproj  # lazy import: optional capability

        card = pyproj.CRS.from_user_input(card_crs)
        request = pyproj.CRS.from_user_input(request_crs)
        return card.equals(request)
    except Exception:  # pragma: no cover - depends on pyproj availability
        logger.debug("pyproj CRS comparison unavailable, using string compare")
        return _normalise_crs(card_crs) == _normalise_crs(request_crs)


def _reproject_bbox(
    bbox: list[float],
    src_crs: str,
    dst_crs: str,
) -> list[float]:
    """Reproject a 2D bbox [w, s, e, n] into another CRS (best effort)."""
    import pyproj

    transformer = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in (
        (bbox[0], bbox[1]),
        (bbox[2], bbox[1]),
        (bbox[0], bbox[3]),
        (bbox[2], bbox[3]),
    ):
        tx, ty = transformer.transform(x, y)
        xs.append(tx)
        ys.append(ty)
    return [min(xs), min(ys), max(xs), max(ys)]


def _bbox_overlap(a: list[float], b: list[float]) -> bool:
    """Whether two 2D bboxes [w, s, e, n] intersect (closed intervals)."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


class ContractValidator:
    """Decide whether a GeoCard satisfies a geospatial request.

    The MVP implements five checks:

    - **CRS compatibility** — the card's CRS is equivalent to the requested one.
    - **bbox intersection** — the card's spatial footprint intersects the
      requested area (after reprojection when both CRSs are declared).
    - **temporal overlap** — the card's temporal coverage overlaps the
      requested window.
    - **band compatibility** — every requested band is provided by the card.
    - **resolution compatibility** — the card is at least as fine as the
      requested resolution.

    Semantic similarity / IoU thresholds are intentionally *not* implemented:
    they are not scientifically validated at this stage and remain extension
    points.
    """

    def check(
        self,
        card: GeoCard,
        bbox: list[float] | None = None,
        crs: str | None = None,
        start: str | None = None,
        end: str | None = None,
        required_bands: list[str] | None = None,
        required_resolution: float | None = None,
    ) -> ContractResult:
        """Evaluate a request against the card.

        All request parameters are optional; only the checks for which the
        request provides a value are performed.
        """
        reasons: list[str] = []
        warnings: list[str] = []

        # -- CRS -----------------------------------------------------------------
        if crs is not None:
            card_crs = card.spatial.crs if card.spatial else None
            if card_crs is None:
                warnings.append(
                    "Card does not declare a CRS; CRS compatibility cannot be verified."
                )
            else:
                equivalent = _crs_equivalent(card_crs, crs)
                if equivalent:
                    reasons.append(f"CRS compatible: card {card_crs} matches request {crs}.")
                else:
                    reasons.append(f"CRS incompatible: card declares {card_crs}, request is {crs}.")

        # -- bbox ----------------------------------------------------------------
        card_bbox = card.spatial.bbox if card.spatial else None
        if bbox is not None:
            if card_bbox is None:
                warnings.append("Card does not declare a bbox; spatial overlap cannot be verified.")
            else:
                if len(bbox) < 4 or len(card_bbox) < 4:
                    reasons.append("Cannot compare bboxes of invalid dimension.")
                else:
                    if len(card_bbox) >= 6:
                        card_bbox = card_bbox[:4]
                    if len(bbox) >= 6:
                        bbox = bbox[:4]
                    overlap_box = card_bbox
                    used_crs: str | None = card.spatial.crs if card.spatial else None
                    if (
                        used_crs
                        and crs
                        and used_crs != crs
                        and _crs_equivalent(used_crs, crs) is False
                    ):
                        try:
                            overlap_box = _reproject_bbox(card_bbox, used_crs, crs)
                            warnings.append(
                                f"Card bbox reprojected from {used_crs} to {crs} for comparison."
                            )
                        except Exception as exc:  # pragma: no cover - defensive
                            warnings.append(f"bbox reprojection failed: {exc}")
                    if _bbox_overlap(overlap_box, bbox):
                        reasons.append("Spatial overlap: card bbox intersects requested bbox.")
                    else:
                        reasons.append(
                            "Spatial mismatch: card bbox does not intersect requested bbox."
                        )

        # -- temporal ------------------------------------------------------------
        card_temporal = card.temporal
        if start is not None or end is not None:
            if card_temporal is None or not card_temporal.start or not card_temporal.end:
                warnings.append(
                    "Card does not declare full temporal coverage; overlap cannot be verified."
                )
            else:
                try:
                    req_start = _parse_datetime(start) if start else datetime.min
                    req_end = _parse_datetime(end) if end else datetime.max
                    card_start = _parse_datetime(card_temporal.start)
                    card_end = _parse_datetime(card_temporal.end)
                except ValueError as exc:
                    warnings.append(f"Temporal check skipped: {exc}")
                else:
                    if card_start <= req_end and req_start <= card_end:
                        reasons.append(
                            f"Temporal overlap: card [{card_temporal.start} .. {card_temporal.end}] "
                            f"overlaps request [{start or '*'} .. {end or '*'}]."
                        )
                    else:
                        reasons.append(
                            "Temporal mismatch: coverage does not overlap the requested window."
                        )

        # -- bands ---------------------------------------------------------------
        if required_bands:
            card_bands = [b.name for b in card.bands]
            if not card_bands:
                warnings.append(
                    "Card does not declare bands; band compatibility cannot be verified."
                )
            else:
                card_lower = {name.lower(): name for name in card_bands}
                missing = [b for b in required_bands if b.lower() not in card_lower]
                if missing:
                    reasons.append(f"Band mismatch: card lacks required band(s) {missing}.")
                else:
                    reasons.append(
                        f"Band compatible: all requested bands {required_bands} available."
                    )

        # -- resolution ----------------------------------------------------------
        if required_resolution is not None:
            card_res = card.spatial.resolution if card.spatial else None
            if card_res is None:
                warnings.append(
                    "Card does not declare resolution; resolution compatibility cannot be verified."
                )
            else:
                if card_res <= required_resolution + 1e-9:
                    reasons.append(
                        f"Resolution compatible: card {card_res}m is at least as fine as requested {required_resolution}m."
                    )
                else:
                    reasons.append(
                        f"Resolution mismatch: card {card_res}m is coarser than requested {required_resolution}m."
                    )

        satisfied = not any(
            "mismatch" in r or "incompatible" in r or "cannot compare" in r for r in reasons
        )
        return ContractResult(satisfied=satisfied, reasons=reasons, warnings=warnings)
