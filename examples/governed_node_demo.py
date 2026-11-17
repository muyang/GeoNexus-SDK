"""Real local HTTP + GeoMCP demo with synthetic GeoJSON; no scientific claim."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from geonexus.admission import (
    AccessContext,
    AdmissionService,
    Capacity,
    Evidence,
    NodeDeclaration,
    Principal,
    Publication,
    RatingPolicy,
    create_admission_app,
)
from geonexus.admission.models import utcnow
from geonexus.geocard import GeoCard
from geonexus.geonode import GeoNode


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)

    def card(identity, kind, name, **extra):
        return GeoCard.from_dict(
            {
                "geocard_version": "1.0",
                "id": identity,
                "type": kind,
                "name": name,
                "description": "Synthetic local admission demo; not a verified scientific product.",
                "license": "MIT",
                "provenance": {"provider": "SDK example", "source": "urn:demo:synthetic"},
                "compliance": {"sensitivity": "public"},
                **extra,
            }
        )

    # Isolate reproducible demo assets from arbitrary installed entry-point plugins.
    with patch("geonexus.geonode.node.discover_plugin_entry_points", return_value=[]):
        node = GeoNode(name="governed-demo", workdir=output, port=0)
    node.register_geocard(
        card(
            "demo.data.points",
            "data",
            "Two synthetic points",
            spatial={"crs": "EPSG:4326", "bbox": [120, 30, 121, 31]},
        )
    )
    node.register_geocard(
        card("demo.compute.cpu", "compute", "Declared CPU", runtime={"cpu": 2, "memory": "4Gi"})
    )
    node.register_geocard(card("demo.model.count-method", "model", "Feature counting method"))
    skill_card = card("demo.skill.count", "skill", "Count GeoJSON features")
    node.register_geocard(skill_card)
    node.register_skill(
        "count.features",
        lambda params, context: {"count": len(params["features"])},
        geocard=skill_card,
        input_schema={
            "type": "object",
            "properties": {"features": {"type": "array", "items": {"type": "object"}}},
            "required": ["features"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )
    server = node.start_in_thread(port=0).wait_until_ready()
    try:
        url = f"http://127.0.0.1:{server.port}"
        announcement = node.declaration(
            NodeDeclaration(
                node_id="demo-peer",
                name=node.name,
                operator="Example provider",
                endpoint=url,
                jurisdictions=["local-demo"],
                capacity=Capacity(cpu_cores=2, memory_gib=4),
            ),
            version="1.0.0",
            visibility="public",
            publication=Publication(stage="experimental", documentation="urn:demo:documentation"),
        )
        (output / "announcement.json").write_text(announcement.model_dump_json(indent=2))
        publisher = Principal(subject="demo-publisher", scopes={"nodes:submit"})
        reviewer = Principal(subject="demo-reviewer", scopes={"nodes:review"})
        inspector = Principal(subject="demo-inspector", scopes={"observations:write"})
        # Demo-only identity handles; production adapter must verify RuoYi/JWT.
        identities = {"publisher": publisher, "reviewer": reviewer, "inspector": inspector}
        service = AdmissionService(
            output / ("admission-" + uuid.uuid4().hex + ".sqlite3"),
            RatingPolicy(trusted_issuers={inspector.subject}),
        )
        app = create_admission_app(
            service, lambda request: identities.get(request.headers.get("X-Demo-Identity"))
        )
        with (
            TestClient(app) as control,
            httpx.Client(base_url=url, trust_env=False, timeout=15) as execution,
        ):
            submitted = control.post(
                "/api/v1/node-announcements",
                json=announcement.model_dump(mode="json"),
                headers={"X-Demo-Identity": "publisher"},
            )
            assert submitted.status_code == 200
            assert control.get("/api/v1/admitted-resources").json()["total"] == 0
            review = {
                "manifest_digest": announcement.digest,
                "decision": "approved",
                "note": "Inspected demo snapshot; synthetic data only",
            }
            assert (
                control.post(
                    "/api/v1/nodes/demo-peer/reviews",
                    json=review,
                    headers={"X-Demo-Identity": "reviewer"},
                ).status_code
                == 200
            )
            for resource in announcement.resources:
                response = control.post(
                    "/api/v1/nodes/demo-peer/reviews",
                    json={**review, "subject": "resource", "resource_id": resource.resource_id},
                    headers={"X-Demo-Identity": "reviewer"},
                )
                assert response.status_code == 200
            # A real loopback health request, not a publisher's "online" flag.
            health = execution.get("/health")
            assert health.status_code == 200 and health.json()["status"] == "ok"
            now = utcnow()
            observation = Evidence(
                subject_digest=announcement.digest,
                check="endpoint_reachable",
                outcome="pass",
                issuer=inspector.subject,
                observed_at=now,
                expires_at=now + timedelta(minutes=5),
                reference="urn:demo:loopback-health",
            )
            assert (
                control.post(
                    "/api/v1/nodes/demo-peer/observations",
                    json=observation.model_dump(mode="json"),
                    headers={"X-Demo-Identity": "inspector"},
                ).status_code
                == 200
            )
            context = AccessContext(
                access_allowed=True,
                user_authorized=True,
                budget_available=True,
                sovereignty_allowed=True,
                contract_satisfied=True,
            )
            gate = service.eligible("demo-peer", "demo.skill.count", announcement.digest, context)
            assert gate.allowed
            response = execution.post(
                "/geomcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "demo-count",
                    "method": "geo.execute",
                    "params": {
                        "skill": "count.features",
                        "geocards": ["demo.skill.count"],
                        "params": {
                            "features": [
                                {
                                    "type": "Feature",
                                    "geometry": {"type": "Point", "coordinates": [120, 30]},
                                    "properties": {},
                                },
                                {
                                    "type": "Feature",
                                    "geometry": {"type": "Point", "coordinates": [121, 31]},
                                    "properties": {},
                                },
                            ]
                        },
                    },
                },
            )
            value = response.json()
            assert "result" in value, value
            assert value["result"]["outputs"]["count"] == 2
            catalogue = control.get("/api/v1/admitted-resources").json()
            assert catalogue["total"] == 4
            before_revoke = gate.allowed
            service.review(
                "demo-peer",
                announcement.digest,
                reviewer,
                subject="resource",
                resource_id="demo.skill.count",
                decision="revoked",
                note="Demo revocation",
            )
            denied = service.eligible("demo-peer", "demo.skill.count", announcement.digest, context)
            assert not denied.allowed
            report = {
                "synthetic": True,
                "public_before_review": 0,
                "public_after_review": catalogue["total"],
                "eligible_after_review_and_probe": before_revoke,
                "actual_geomcp_outputs": value["result"]["outputs"],
                "eligible_after_revocation": denied.allowed,
                "denial_reasons": denied.reasons,
                "assessment": service.assess("demo-peer"),
                "review_history": service.history("demo-peer", reviewer),
                "notice": "Local feature count validates engineering only. Compute capacity, data completeness and scientific correctness were not independently verified.",
            }
            (output / "demo-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k not in {"assessment", "review_history"}},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return report
    finally:
        server.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
