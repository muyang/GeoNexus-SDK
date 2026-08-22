"""Web-layer REST router (BFF surface for browser clients).

Exposes the SDK to a Web frontend over plain JSON REST + SSE:

- ``POST /api/auth/login``        — exchange credentials for a JWT
- ``GET  /api/health``            — service health
- ``GET  /api/cards``             — registry card search / list
- ``GET  /api/skills``            — registry skill list
- ``GET  /api/nodes``             — registry node view
- ``POST /api/execute``           — run a skill on a node (async task)
- ``POST /api/goals``             — LLM-plan a natural-language goal (async)
- ``GET  /api/tasks``             — list tasks
- ``GET  /api/tasks/{id}``        — task state (+ result when done)
- ``GET  /api/tasks/{id}/stream`` — SSE progress stream
- ``POST /api/tasks/{id}/cancel`` — request cancellation

Auth model (BFF): every endpoint except ``/api/auth/login`` and
``/api/health`` requires a Bearer JWT. Node credentials never reach the
browser: the router forwards ``X-API-Key`` from :attr:`WebConfig.node_api_keys`
on GeoMCP calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent.llm_planner import (
    LLMConfig,
    plan_from_text_with_registry,
)
from ..agent.planner import GeoAgentPlanner
from ..agent.reflective import PlanReflector, ReflectiveExecutor, evaluate_plan
from ..geomcp.client import GeoMCPClient
from ..registry.client import RegistryClient
from .auth import BearerAuth, JWTConfig, create_token
from .tasks import (
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    TaskManager,
    TaskNotCancellableError,
    TaskNotFoundError,
)


@dataclass
class WebConfig:
    """Configuration for the Web layer (BFF).

    Args:
        registry_url: Shared GeoCard Registry base URL (discovery).
        jwt: :class:`JWTConfig` for token issuance/verification.
        node_api_keys: Mapping ``node_url -> api_key`` the BFF forwards as
            ``X-API-Key`` on GeoMCP calls (browser never sees these).
        users: Static credential store ``username -> password`` for the
            demo login endpoint (swap for OIDC/SSO in production).
        default_node_url: Optional node used when ``/api/execute`` omits one.
        llm: Optional :class:`LLMConfig` for ``/api/goals``; when unset the
            endpoint falls back to ``LLMConfig.from_env()``.
        task_manager: Optional pre-built :class:`TaskManager` (shared across
            app instances); a fresh one is created otherwise.
    """

    registry_url: str = "http://127.0.0.1:8790"
    jwt: JWTConfig = field(
        default_factory=lambda: JWTConfig(
            secret="geonexus-dev-secret-change-me-0123456789abcdef"
        )
    )
    node_api_keys: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)
    default_node_url: str | None = None
    llm: LLMConfig | None = None
    task_manager: TaskManager | None = None

    @property
    def tasks(self) -> TaskManager:
        if self.task_manager is None:
            self.task_manager = TaskManager()
        return self.task_manager


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    subject: str
    roles: list[str] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    skill: str
    geocards: list[str] = Field(default_factory=list)
    spatial: dict[str, Any] | None = None
    temporal: dict[str, Any] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    node_url: str | None = None


class GoalRequest(BaseModel):
    text: str
    registry_url: str | None = None
    reflective: bool = True
    """Enable reflective execution (v1.1): on step failure the LLM proposes a
    repair (retry/replace/skip/abort) and the executor retries, then the
    finished plan is self-assessed. Requires the same LLM config as planning.
    Set ``false`` for plain deterministic execution."""

    max_reflections: int | None = None
    """Override the reflection budget (default 3)."""


class TaskResponse(BaseModel):
    task_id: str
    status: str


def create_web_router(config: WebConfig) -> APIRouter:
    """Build the Web-layer REST router for the given configuration."""
    router = APIRouter(prefix="/api")
    auth = BearerAuth(config.jwt)

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    @router.post("/auth/login", response_model=LoginResponse)
    def login(body: LoginRequest) -> LoginResponse:
        expected = config.users.get(body.username)
        if expected is None or expected != body.password:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_token(config.jwt, subject=body.username, roles=["user"])
        return LoginResponse(
            token=token,
            expires_in=config.jwt.ttl_seconds,
            subject=body.username,
            roles=["user"],
        )

    # ------------------------------------------------------------------ #
    # Health / discovery
    # ------------------------------------------------------------------ #
    @router.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "geonexus-web", "registry": config.registry_url}

    @router.get("/cards")
    def cards(
        request: Request,
        q: str | None = None,
        bbox: str | None = None,
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        with RegistryClient(config.registry_url) as rc:
            if q or bbox:
                bbox_list = None
                if bbox:
                    try:
                        bbox_list = [float(x) for x in bbox.split(",")]
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=400, detail=f"Invalid bbox: {bbox!r}"
                        ) from exc
                return {"count": 0, "results": rc.search(capability=q, bbox=bbox_list)}
            data = rc.list_cards()
            return {"count": len(data), "cards": data}

    @router.get("/skills")
    def skills(
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        with RegistryClient(config.registry_url) as rc:
            data = rc.list_skills()
            return {"count": len(data), "skills": data}

    @router.get("/nodes")
    def nodes(
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        with RegistryClient(config.registry_url) as rc:
            return rc.get_nodes()

    # ------------------------------------------------------------------ #
    # Async execution (TaskManager-backed)
    # ------------------------------------------------------------------ #
    @router.post("/execute", response_model=TaskResponse, status_code=202)
    def execute(
        body: ExecuteRequest,
        _claims: dict[str, Any] = Depends(auth),
    ) -> TaskResponse:
        node_url = body.node_url or config.default_node_url
        if not node_url:
            raise HTTPException(status_code=400, detail="No node_url and no default configured")

        def _job() -> dict[str, Any]:
            api_key = config.node_api_keys.get(node_url)
            with GeoMCPClient(node_url, api_key=api_key) as node:
                return node.execute(
                    skill=body.skill,
                    geocards=body.geocards,
                    spatial=body.spatial,
                    temporal=body.temporal,
                    params=body.params,
                )

        tid = config.tasks.submit(_job, message=f"execute {body.skill}")
        return TaskResponse(task_id=tid, status=QUEUED)

    @router.post("/goals", response_model=TaskResponse, status_code=202)
    def goals(
        body: GoalRequest,
        _claims: dict[str, Any] = Depends(auth),
    ) -> TaskResponse:
        registry_url = body.registry_url or config.registry_url

        def _job() -> dict[str, Any]:
            llm_config = config.llm or LLMConfig.from_env()
            if not llm_config.is_configured():
                raise RuntimeError(
                    "LLM planner not configured: set GEONEXUS_LLM_API_KEY / "
                    "GEONEXUS_LLM_BASE_URL / GEONEXUS_LLM_MODEL"
                )
            # v1.1: ground the translation in the skills that actually exist
            # at the registry (reduces hallucinated skill names).
            goal = plan_from_text_with_registry(body.text, registry_url, config=llm_config)
            plan = GeoAgentPlanner(registry_url).plan(goal)
            evaluation: dict[str, Any] | None = None
            if body.reflective:
                with PlanReflector(config=llm_config) as reflector:
                    plan = ReflectiveExecutor(
                        registry_url,
                        reflector,
                        max_reflections=body.max_reflections or 3,
                    ).run(plan)
                    # Self-assessment of the finished plan.
                    evaluation = evaluate_plan(plan, reflector=reflector)
            else:
                from ..agent.planner import PlanExecutor

                with PlanExecutor(registry_url) as executor:
                    plan = executor.run(plan)
            return {
                "goal": goal.model_dump(mode="json"),
                "plan": plan.to_dict(),
                "reflective": body.reflective,
                "evaluation": evaluation,
            }

        tid = config.tasks.submit(_job, message=f"goal: {body.text[:60]}")
        return TaskResponse(task_id=tid, status=QUEUED)

    # ------------------------------------------------------------------ #
    # Task queries
    # ------------------------------------------------------------------ #
    @router.get("/tasks")
    def tasks_list(
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        return {"count": len(config.tasks.list()), "tasks": config.tasks.list()}

    @router.get("/tasks/{task_id}")
    def task_get(
        task_id: str,
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        try:
            return config.tasks.get(task_id, include_result=True)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/tasks/{task_id}/cancel")
    def task_cancel(
        task_id: str,
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        try:
            return config.tasks.cancel(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskNotCancellableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/tasks/{task_id}/stream")
    def task_stream(
        task_id: str,
        _claims: dict[str, Any] = Depends(auth),
    ) -> StreamingResponse:
        try:
            config.tasks.get(task_id, include_result=False)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def _events() -> Any:
            while True:
                try:
                    state = config.tasks.get(task_id, include_result=True)
                except TaskNotFoundError:
                    break
                yield f"data: {json.dumps(state)}\n\n"
                if state["status"] in (DONE, FAILED, CANCELLED):
                    break
                import time

                time.sleep(0.5)

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
