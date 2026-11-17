"""Offline assessments or explicit submission to a known governance service."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .models import NodeAnnouncement
from .rating import rate_node, rate_resource
from .transport import AdmissionClient


def main(argv=None):
    parser = argparse.ArgumentParser(prog="geonode-admission")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["rate", "submit"]:
        command = commands.add_parser(name)
        command.add_argument("announcement", type=Path)
        if name == "submit":
            command.add_argument("--registry", required=True)
            command.add_argument("--api-key-env", default="GEONEXUS_ADMISSION_API_KEY")
    schemas = commands.add_parser("schemas")
    schemas.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schemas":
            args.output.mkdir(parents=True, exist_ok=True)
            from .models import Evidence
            from .transport import ReviewRequest

            for model in [NodeAnnouncement, Evidence, ReviewRequest]:
                (args.output / (model.__name__ + ".schema.json")).write_text(
                    json.dumps(model.model_json_schema(), indent=2), encoding="utf-8"
                )
            return 0
        announcement = NodeAnnouncement.model_validate_json(
            args.announcement.read_text(encoding="utf-8")
        )
        if args.command == "submit":
            with AdmissionClient(args.registry, os.environ.get(args.api_key_env)) as client:
                result = client.submit(announcement)
        else:
            result = {
                "notice": "Offline declaration-only assessment; no authenticated inspection evidence or approval.",
                "manifest_digest": announcement.digest,
                "node": rate_node(announcement).model_dump(mode="json"),
                "resources": {
                    r.resource_id: rate_resource(r).model_dump(mode="json")
                    for r in announcement.resources
                },
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError):
        parser.exit(
            2,
            "Invalid declaration, unavailable input or failed submission; credentials are never printed.\n",
        )
