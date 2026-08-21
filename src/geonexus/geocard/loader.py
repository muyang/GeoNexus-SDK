"""GeoCard loading.

``load_geocard`` reads a GeoCard from a YAML or JSON file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .model import GeoCard, GeoCardError
from .validator import validate_card_schema


def _parse_file(path: Path) -> dict[str, Any]:
    """Parse a YAML or JSON file into a dictionary."""
    if not path.exists():
        raise GeoCardError(f"GeoCard file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        elif path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            # Unknown extension: try JSON first, then YAML.
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - defensive
        raise GeoCardError(f"Failed to parse GeoCard file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GeoCardError(f"GeoCard file {path} must contain a mapping")
    return data


def load_geocard(path: str | os.PathLike, validate: bool = True) -> GeoCard:
    """Load a GeoCard from a YAML or JSON file.

    Args:
        path: File path (``.yaml``, ``.yml`` or ``.json``).
        validate: When True (default), validate against the official schema
            and raise :class:`GeoCardError` on failure.
    """
    data = _parse_file(Path(path))
    if validate:
        report = validate_card_schema(data)
        if not report.valid:
            from .model import GeoCardValidationError

            raise GeoCardValidationError(report.errors)
    return GeoCard.model_validate(data)


def load_geocard_dict(path: str | os.PathLike, validate: bool = True) -> dict[str, Any]:
    """Load a raw GeoCard dictionary from a file without building the model."""
    data = _parse_file(Path(path))
    if validate:
        report = validate_card_schema(data)
        if not report.valid:
            from .model import GeoCardValidationError

            raise GeoCardValidationError(report.errors)
    return data


def load_many(paths: list[str | os.PathLike], validate: bool = True) -> list[GeoCard]:
    """Load several GeoCard files, preserving order."""
    return [load_geocard(p, validate=validate) for p in paths]
