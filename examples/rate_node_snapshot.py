"""Assess a saved geo.describe response without contacting or trusting its node."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from geonexus.admission import ResourceDeclaration, ToolBinding, rate_resource
from geonexus.admission.models import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    body = json.loads(args.snapshot.read_text())
    body = body.get("result", body)
    resources = []
    for card in body.get("geocards", []):
        tool = next(
            (s for s in body.get("skills", []) if s.get("geocard_id", s.get("name")) == card["id"]),
            None,
        )
        resource = ResourceDeclaration(
            card=card,
            version="snapshot-" + digest(card),
            binding=ToolBinding(
                tool=tool["name"],
                input_schema=tool.get("input_schema", {}),
                output_schema=tool.get("output_schema"),
            )
            if tool
            else None,
        )
        rating = rate_resource(resource)
        resources.append(
            {
                "id": card["id"],
                "type": card["type"],
                "snapshot_digest": resource.digest,
                "grade": rating.grade,
                "score": rating.score,
                "blockers": rating.blockers,
                "evidence_coverage": rating.evidence_coverage,
            }
        )
    report = {
        "input": str(args.snapshot),
        "notice": "Offline metadata snapshot, unknown publication stage and no authenticated evidence. No registration, approval, scientific validation or invocation.",
        "resources": resources,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "assessed": len(resources),
                "not_ready": [r["id"] for r in resources if r["blockers"]],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
