"""Governed federation must not turn publication claims into approval or authority."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from geonexus.admission import (
    AccessContext,
    AdmissionService,
    Capacity,
    Evidence,
    NodeAnnouncement,
    NodeDeclaration,
    Principal,
    Publication,
    RatingPolicy,
    ResourceDeclaration,
    ToolBinding,
    create_admission_app,
    rate_node,
    rate_resource,
)

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
PUBLISHER = Principal(subject="publisher", scopes={"nodes:submit", "nodes:review"})
REVIEWER = Principal(subject="reviewer", scopes={"nodes:review"})
INSPECTOR = Principal(subject="inspector", scopes={"observations:write"})
POLICY = RatingPolicy(trusted_issuers={"inspector"})
ALLOWED = AccessContext(
    access_allowed=True,
    user_authorized=True,
    budget_available=True,
    sovereignty_allowed=True,
    contract_satisfied=True,
)


def resource(kind="skill", identity="example.skill"):
    return ResourceDeclaration(
        card={
            "geocard_version": "1.0",
            "id": identity,
            "type": kind,
            "name": "Example",
            "description": "Synthetic example, not a scientific result",
            "license": "MIT",
            "compliance": {"sensitivity": "public"},
            "provenance": {"provider": "Example", "source": "https://example.org/source"},
            "access": {"protocol": "https", "endpoint": "https://example.org/data.geojson"},
        },
        version="1.0.0",
        visibility="public",
        publication=Publication(stage="stable", documentation="https://example.org/docs"),
        binding=ToolBinding(
            tool="example.run",
            input_schema={"type": "object", "properties": {"value": {"type": "number"}}},
        ),
    )


def announcement(
    *, resources=None, node_id="peer", provider_class="third_party", revision=1, now=NOW
):
    return NodeAnnouncement(
        node=NodeDeclaration(
            node_id=node_id,
            name="Example node",
            operator="Example org",
            endpoint="http://127.0.0.1:8787",
            provider_class=provider_class,
            capacity=Capacity(cpu_cores=2, memory_gib=4),
        ),
        resources=resources if resources is not None else [resource()],
        revision=revision,
        declared_at=now,
        expires_at=now + timedelta(hours=1),
    )


def approve(service, declaration):
    service.review(
        declaration.node.node_id,
        declaration.digest,
        REVIEWER,
        decision="approved",
        note="Inspected node",
        now=NOW,
    )
    for r in declaration.resources:
        service.review(
            declaration.node.node_id,
            declaration.digest,
            REVIEWER,
            subject="resource",
            resource_id=r.resource_id,
            decision="approved",
            note="Inspected resource contract",
            now=NOW,
        )


def evidence(subject, check="endpoint_reachable", outcome="pass", issuer="inspector", at=NOW):
    return Evidence(
        subject_digest=subject,
        check=check,
        outcome=outcome,
        issuer=issuer,
        observed_at=at,
        expires_at=at + timedelta(minutes=10),
        reference="urn:test:probe",
    )


def test_rating_and_publisher_trust_claim_cannot_approve_or_verify(tmp_path):
    value = resource()
    claimed = value.model_dump()
    claimed["card"]["trust"] = {"verified": True, "score": 1}
    forged = ResourceDeclaration.model_validate(claimed)
    assert rate_resource(forged, now=NOW).score == rate_resource(value, now=NOW).score
    service = AdmissionService(tmp_path / "registry.db", POLICY)
    a = announcement(resources=[forged])
    result = service.submit(a, PUBLISHER, now=NOW)
    assert result["status"]["node"] == "pending" and service.admitted_catalog(now=NOW)["total"] == 0
    with pytest.raises(PermissionError):
        service.review("peer", a.digest, PUBLISHER, decision="approved", note="self", now=NOW)
    assert not service.eligible("peer", forged.resource_id, a.digest, ALLOWED, now=NOW).allowed


def test_approved_peer_has_same_orchestration_gate_as_official_and_persists(tmp_path):
    outcomes = []
    for origin in ["third_party", "official"]:
        service = AdmissionService(tmp_path / (origin + ".db"), POLICY)
        a = announcement(provider_class=origin)
        service.submit(a, PUBLISHER, now=NOW)
        approve(service, a)
        assert service.admitted_catalog(now=NOW)["total"] == 1
        assert not service.eligible("peer", "example.skill", a.digest, ALLOWED, now=NOW).allowed
        service.observe("peer", evidence(a.digest), INSPECTOR, now=NOW)
        reopened = AdmissionService(tmp_path / (origin + ".db"), POLICY)
        outcomes.append(
            reopened.eligible("peer", "example.skill", a.digest, ALLOWED, now=NOW).allowed
        )
        assert len(reopened.history("peer", REVIEWER)) == 2
    assert outcomes == [True, True]
    assert (
        rate_node(announcement(provider_class="official"), now=NOW).score
        == rate_node(announcement(), now=NOW).score
    )


@pytest.mark.parametrize("field", list(AccessContext.model_fields))
@pytest.mark.parametrize("value", [None, False])
def test_review_does_not_bypass_task_policy(tmp_path, field, value):
    service = AdmissionService(tmp_path / "registry.db", POLICY)
    a = announcement()
    service.submit(a, PUBLISHER, now=NOW)
    approve(service, a)
    service.observe("peer", evidence(a.digest), INSPECTOR, now=NOW)
    context = ALLOWED.model_copy(update={field: value})
    assert not service.eligible("peer", "example.skill", a.digest, context, now=NOW).allowed


def test_evidence_identity_expiry_and_revision_cannot_be_replayed(tmp_path):
    service = AdmissionService(tmp_path / "registry.db", POLICY)
    a = announcement()
    service.submit(a, PUBLISHER, now=NOW)
    approve(service, a)
    with pytest.raises(PermissionError):
        service.observe("peer", evidence(a.digest), PUBLISHER, now=NOW)
    with pytest.raises(PermissionError):
        service.observe("peer", evidence(a.digest, issuer="publisher"), INSPECTOR, now=NOW)
    service.observe("peer", evidence(a.digest), INSPECTOR, now=NOW)
    assert not service.eligible(
        "peer", "example.skill", a.digest, ALLOWED, now=NOW + timedelta(minutes=11)
    ).allowed
    assert service.admitted_catalog(now=NOW + timedelta(hours=2))["total"] == 0
    b = announcement(revision=2)
    service.submit(b, PUBLISHER, now=NOW)
    assert (
        service.status("peer")["node"] == "pending"
        and service.admitted_catalog(now=NOW)["total"] == 0
    )
    with pytest.raises(ValueError):
        service.review("peer", a.digest, REVIEWER, decision="approved", note="stale", now=NOW)
    with pytest.raises(ValueError):
        service.observe("peer", evidence(a.digest), INSPECTOR, now=NOW)
    with pytest.raises(ValueError):
        service.submit(a, PUBLISHER, now=NOW)
    with pytest.raises(PermissionError):
        service.submit(b, Principal(subject="other", scopes={"nodes:submit"}), now=NOW)


def test_heartbeat_renews_same_snapshot_but_content_change_and_revocation_gate(tmp_path):
    service = AdmissionService(tmp_path / "registry.db", POLICY)
    a = announcement()
    service.submit(a, PUBLISHER, now=NOW)
    approve(service, a)
    renewed = announcement(now=NOW + timedelta(minutes=1))
    assert renewed.digest == a.digest
    service.submit(renewed, PUBLISHER, now=NOW + timedelta(minutes=1))
    assert service.status("peer")["node"] == "approved"
    altered = a.model_dump(mode="json")
    altered["resources"][0]["binding"]["tool"] = "different"
    with pytest.raises(ValueError):
        service.submit(NodeAnnouncement.model_validate(altered), PUBLISHER, now=NOW)
    service.review(
        "peer",
        a.digest,
        REVIEWER,
        subject="resource",
        resource_id="example.skill",
        decision="revoked",
        note="Contract withdrawn",
        now=NOW,
    )
    assert service.admitted_catalog(now=NOW)["total"] == 0
    assert len(service.history("peer", REVIEWER)) == 3


def test_bad_tool_contract_is_rated_and_cannot_receive_execution_approval(tmp_path):
    r = resource().model_dump()
    r["binding"]["input_schema"]["properties"]["upstream"] = {"type": "table"}
    bad = ResourceDeclaration.model_validate(r)
    rating = rate_resource(bad, now=NOW)
    assert rating.grade == "not_ready" and "table" in rating.blockers[0]
    assert (
        bad.card == r["card"]
        and bad.binding.input_schema["properties"]["upstream"]["type"] == "table"
    )
    service = AdmissionService(tmp_path / "registry.db")
    a = announcement(resources=[bad])
    service.submit(a, PUBLISHER, now=NOW)
    with pytest.raises(ValueError):
        service.review(
            "peer",
            a.digest,
            REVIEWER,
            subject="resource",
            resource_id=bad.resource_id,
            decision="approved",
            note="should fail",
            now=NOW,
        )


def test_unknown_evidence_never_inflates_rating_and_failure_wins_ties():
    r = resource()
    before = rate_resource(r, now=NOW)
    assert (
        before.evidence_coverage == 0
        and before.observed_checks["scientific_validation"] == "unknown"
    )
    untrusted = rate_resource(
        r, [evidence(r.digest, "source_verified", issuer="publisher")], policy=POLICY, now=NOW
    )
    assert untrusted.score == before.score
    passed = rate_resource(r, [evidence(r.digest, "source_verified")], policy=POLICY, now=NOW)
    assert passed.score > before.score and 0 < passed.evidence_coverage < 1
    stale = rate_resource(
        r,
        [evidence(r.digest, "source_verified", at=NOW - timedelta(hours=1))],
        policy=POLICY,
        now=NOW,
    )
    assert stale.score == before.score
    failed = rate_resource(
        r,
        [evidence(r.digest, "source_verified"), evidence(r.digest, "source_verified", "fail")],
        policy=POLICY,
        now=NOW,
    )
    assert failed.dimensions["provenance"].score == 0


@pytest.mark.parametrize("sensitivity", ["restricted", "secret"])
def test_public_envelope_cannot_publish_a_sensitive_card(tmp_path, sensitivity):
    r = resource().model_dump()
    r["card"]["compliance"]["sensitivity"] = sensitivity
    service = AdmissionService(tmp_path / "registry.db")
    a = announcement(resources=[ResourceDeclaration.model_validate(r)])
    service.submit(a, PUBLISHER, now=NOW)
    approve(service, a)
    assert service.admitted_catalog(now=NOW)["total"] == 0


def test_manifest_validation_rejects_duplicate_ids_credentials_and_nonfinite_capacity():
    with pytest.raises(ValueError):
        announcement(resources=[resource(), resource()])
    for url in [
        "https://user:secret@node.test",
        "https://node.test?token=secret",
        "http://0.0.0.0:8787",
    ]:
        with pytest.raises(ValueError):
            NodeDeclaration(node_id="n", name="n", operator="o", endpoint=url)
    for cpu in [float("nan"), float("inf"), True, -1]:
        with pytest.raises(ValueError):
            Capacity(cpu_cores=cpu)
    raw = announcement().model_dump()
    raw["status"] = "approved"
    with pytest.raises(ValueError):
        NodeAnnouncement.model_validate(raw)


def test_http_publisher_cannot_supply_reviewer_or_observation_authority(tmp_path):
    from geonexus.admission.models import utcnow

    a = announcement(now=utcnow())
    service = AdmissionService(tmp_path / "http.db", POLICY)
    users = {
        "publisher": Principal(subject="publisher", scopes={"nodes:submit"}),
        "reviewer": REVIEWER,
        "inspector": INSPECTOR,
    }
    app = create_admission_app(service, lambda request: users.get(request.headers.get("X-API-Key")))
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/node-announcements", json=a.model_dump(mode="json")).status_code
            == 401
        )
        res = client.post(
            "/api/v1/node-announcements",
            json=a.model_dump(mode="json"),
            headers={"X-API-Key": "publisher"},
        )
        assert res.status_code == 200 and res.json()["status"]["node"] == "pending"
        snapshot = client.get("/api/v1/nodes/peer/declaration", headers={"X-API-Key": "reviewer"})
        assert snapshot.status_code == 200 and snapshot.json()["manifest_digest"] == a.digest
        assert snapshot.json()["announcement"]["resources"][0]["card"]["id"] == "example.skill"
        assert (
            client.get(
                "/api/v1/nodes/peer/declaration", headers={"X-API-Key": "publisher"}
            ).status_code
            == 403
        )
        body = {"manifest_digest": a.digest, "decision": "approved", "note": "reviewed"}
        assert (
            client.post(
                "/api/v1/nodes/peer/reviews", json=body, headers={"X-API-Key": "publisher"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/v1/node-announcements", headers={"X-API-Key": "publisher"}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/nodes/peer/reviews",
                json={**body, "reviewer": "reviewer"},
                headers={"X-API-Key": "publisher"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/nodes/peer/reviews", json=body, headers={"X-API-Key": "reviewer"}
            ).status_code
            == 200
        )
        assert client.get("/api/v1/admitted-resources").json()["total"] == 0
        assert (
            client.post(
                "/api/v1/nodes/peer/reviews",
                json={**body, "subject": "resource", "resource_id": "example.skill"},
                headers={"X-API-Key": "reviewer"},
            ).status_code
            == 200
        )
        assert client.get("/api/v1/admitted-resources").json()["total"] == 1


def test_independent_failed_check_blocks_use_without_silently_rewriting_human_approval(tmp_path):
    service = AdmissionService(tmp_path / "failure.db", POLICY)
    a = announcement()
    service.submit(a, PUBLISHER, now=NOW)
    approve(service, a)
    service.observe("peer", evidence(a.digest), INSPECTOR, now=NOW)
    service.observe(
        "peer", evidence(a.resources[0].digest, "scientific_validation", "fail"), INSPECTOR, now=NOW
    )
    assert service.status("peer")["resources"]["example.skill"] == "approved"
    report = service.assess("peer", now=NOW)
    assert report["resources"]["example.skill"]["grade"] == "not_ready"
    assert not service.eligible("peer", "example.skill", a.digest, ALLOWED, now=NOW).allowed
    assert not service.admitted_catalog(now=NOW)["items"][0]["state"]["executable"]


def test_capacity_class_is_separate_from_trust_and_unknown_free_capacity():
    small = rate_node(announcement(), now=NOW)
    raw = announcement().model_dump(mode="json")
    raw["node"]["capacity"] = {"cpu_cores": 32, "memory_gib": 64, "gpu_count": 0}
    large = rate_node(NodeAnnouncement.model_validate(raw), now=NOW)
    assert small.capacity_profile["class"] == "small_cpu"
    assert large.capacity_profile["class"] == "large_cpu"
    assert large.capacity_profile["basis"] == "unverified_declaration"
    assert large.capacity_profile["live_free_capacity"] == "unknown"
    assert small.evidence_coverage == large.evidence_coverage == 0


def test_required_private_component_cannot_be_published_through_public_parent(tmp_path):
    child = resource(identity="hidden.data").model_dump()
    child["card"]["type"] = "data"
    child["visibility"] = "restricted"
    parent = resource(identity="parent.workflow").model_dump()
    parent["card"]["type"] = "workflow"
    parent["card"]["composition"] = {
        "components": [{"id": "hidden.data", "role": "data", "required": True}]
    }
    a = announcement(
        resources=[
            ResourceDeclaration.model_validate(child),
            ResourceDeclaration.model_validate(parent),
        ]
    )
    service = AdmissionService(tmp_path / "composition.db", POLICY)
    service.submit(a, PUBLISHER, now=NOW)
    approve(service, a)
    assert service.admitted_catalog(now=NOW)["total"] == 0
    service.review(
        "peer",
        a.digest,
        REVIEWER,
        subject="resource",
        resource_id="hidden.data",
        decision="revoked",
        note="withdrawn",
        now=NOW,
    )
    service.observe("peer", evidence(a.digest), INSPECTOR, now=NOW)
    assert not service.eligible("peer", "parent.workflow", a.digest, ALLOWED, now=NOW).allowed


def test_node_declaration_requires_resolvable_geocard_and_cli_exports_schemas(tmp_path):
    from types import SimpleNamespace

    from geonexus.admission import from_node
    from geonexus.admission.cli import main
    from geonexus.geocard import GeoCard
    from geonexus.geonode.registry import GeoCardRegistry, SkillRegistry
    from geonexus.geonode.skill import Skill

    cards, skills = GeoCardRegistry(), SkillRegistry()
    c = GeoCard.from_dict(resource().card)
    skills.register(Skill(name="example.run", geocard=c, input_schema={"type": "object"}))
    node = SimpleNamespace(geocard_registry=cards, skill_registry=skills)
    with pytest.raises(ValueError, match="node registry"):
        from_node(node, announcement().node, version="1")
    cards.register(c)
    result = from_node(node, announcement().node, version="1")
    assert result.resources[0].binding.tool == "example.run"
    path = tmp_path / "announcement.json"
    path.write_text(result.model_dump_json())
    assert main(["rate", str(path)]) == 0
    assert main(["schemas", "--output", str(tmp_path / "schemas")]) == 0
    assert (tmp_path / "schemas/NodeAnnouncement.schema.json").exists()


def test_host_allowlist_cannot_make_publisher_self_attestation_independent(tmp_path):
    publisher_inspector = Principal(
        subject="publisher", scopes={"nodes:submit", "observations:write"}
    )
    service = AdmissionService(tmp_path / "self.db", RatingPolicy(trusted_issuers={"publisher"}))
    a = announcement()
    service.submit(a, publisher_inspector, now=NOW)
    with pytest.raises(PermissionError, match="self-attest"):
        service.observe(
            "peer", evidence(a.digest, issuer="publisher"), publisher_inspector, now=NOW
        )
