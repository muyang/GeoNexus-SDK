"""Optional HTTP control-plane adapter. GeoMCP/MCP execution is unchanged."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from pydantic import Field

from .models import Envelope, Evidence, NodeAnnouncement, Principal, endpoint_url
from .service import AdmissionService


class ReviewRequest(Envelope):
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    subject: str = "node"
    resource_id: str | None = None
    decision: str
    note: str = Field(min_length=1, max_length=2000)


def create_admission_app(service: AdmissionService, authenticate: Callable):
    """Host callback must verify identity/scopes; request JSON cannot set either.

    This is an optional reference adapter, not a replacement for Platform/RuoYi.
    It does not run code, fetch announced URLs, or trust publisher evidence.
    """
    from fastapi import Depends, FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="GeoNode admission", version="1.0")

    async def actor(request: Request):
        value = authenticate(request)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, Principal):
            from fastapi import HTTPException

            raise HTTPException(401, "Verified identity required")
        return value

    # FastAPI resolves locally imported Request against function globals.
    actor.__annotations__["request"] = Request

    @app.exception_handler(PermissionError)
    async def permission_error(request, error):
        return JSONResponse({"error": str(error)}, status_code=403)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"error": str(error)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"error": "Resource or node unavailable"}, status_code=404)

    @app.post("/api/v1/node-announcements")
    async def announce(body: NodeAnnouncement, identity: Principal = Depends(actor)):
        return service.submit(body, identity)

    @app.get("/api/v1/node-announcements")
    async def submissions(identity: Principal = Depends(actor)):
        return service.submissions(identity)

    @app.get("/api/v1/nodes/{node_id}/assessment")
    async def assessment(node_id: str, identity: Principal = Depends(actor)):
        identity.require("nodes:review")
        return service.assess(node_id)

    @app.get("/api/v1/nodes/{node_id}/declaration")
    async def declaration(node_id: str, identity: Principal = Depends(actor)):
        return service.declaration(node_id, identity)

    @app.get("/api/v1/nodes/{node_id}/reviews")
    async def review_history(node_id: str, identity: Principal = Depends(actor)):
        return {"items": service.history(node_id, identity)}

    @app.post("/api/v1/nodes/{node_id}/observations")
    async def observe(node_id: str, body: Evidence, identity: Principal = Depends(actor)):
        return service.observe(node_id, body, identity)

    @app.post("/api/v1/nodes/{node_id}/reviews")
    async def review(node_id: str, body: ReviewRequest, identity: Principal = Depends(actor)):
        return service.review(node_id, actor=identity, **body.model_dump())

    @app.get("/api/v1/admitted-resources")
    async def catalog():
        return service.admitted_catalog()

    return app


class AdmissionClient:
    def __init__(self, registry_url: str, api_key: str | None = None, *, transport=None):
        import httpx

        self.base = endpoint_url(registry_url)
        self.client = httpx.Client(
            base_url=self.base,
            headers={"X-API-Key": api_key} if api_key else {},
            transport=transport,
            follow_redirects=False,
            timeout=20,
        )

    def submit(self, announcement: NodeAnnouncement):
        import httpx

        try:
            result = self.client.post(
                "/api/v1/node-announcements", json=announcement.model_dump(mode="json")
            )
        except httpx.HTTPError:
            raise ValueError("Cannot reach admission service") from None
        if result.status_code != 200:
            # Do not echo provider/server payloads that could contain credentials.
            raise ValueError(f"Admission submission returned HTTP {result.status_code}")
        return result.json()

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
