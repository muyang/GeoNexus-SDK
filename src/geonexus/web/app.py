"""Web layer: BFF (backend-for-frontend) REST API over the GeoNexus SDK.

Builds a FastAPI application that fronts the SDK for browser clients:

- JWT auth (HS256 shared secret or RS256 key pair) — stateless, so multiple
  backend instances behind a load balancer can verify each other's tokens
  (distributed deployments).
- Async tasks (``TaskManager``) so long-running GeoMCP executions and
  LLM-planned goals never block HTTP requests.
- Node credentials are held server-side and forwarded as ``X-API-Key``;
  browsers only ever hold a JWT.

Typical topology::

    Browser ──JWT──▶ Web backend (this app) ──X-API-Key──▶ GeoNode(s)
                                        └──▶ Registry ──▶ more nodes
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import JWTConfig
from .router import WebConfig, create_web_router
from .tasks import TaskManager

logger = logging.getLogger(__name__)

__all__ = [
    "WebConfig",
    "JWTConfig",
    "TaskManager",
    "create_web_app",
    "run_web",
]


def create_web_app(
    config: WebConfig | None = None,
    *,
    title: str = "GeoNexus Web API",
    version: str = "1.1.0",
    cors_origins: list[str] | None = None,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """Create the GeoNexus Web-layer FastAPI application.

    Args:
        config: :class:`WebConfig`; a default (dev) config is used when
            omitted so ``create_web_app()`` works out of the box.
        title: OpenAPI title.
        version: OpenAPI version string.
        cors_origins: Allowed CORS origins (default: none). Pass ``["*"]``
            for local development.
        **fastapi_kwargs: Extra kwargs forwarded to :class:`FastAPI`.
    """
    config = config or WebConfig()
    app = FastAPI(title=title, version=version, **fastapi_kwargs)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(create_web_router(config))
    app.state.web_config = config
    return app


def run_web(
    config: WebConfig | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8900,
    log_level: str = "info",
    cors_origins: list[str] | None = None,
) -> None:
    """Run the Web layer with uvicorn (blocking)."""
    import uvicorn

    app = create_web_app(config, cors_origins=cors_origins)
    uvicorn.run(app, host=host, port=port, log_level=log_level)
