"""GeoNexus Web layer (BFF).

Exposes the SDK to browser clients over a JWT-authenticated REST API with
async tasks, SSE progress, and server-side node credential forwarding.
"""

from .app import WebConfig, create_web_app, run_web
from .auth import (
    APIKeyAuth,
    AuthError,
    BearerAuth,
    JWTConfig,
    create_token,
    decode_token,
)
from .datasets import DatasetSubmit, create_datasets_router
from .router import (
    ExecuteRequest,
    GoalRequest,
    LoginRequest,
    LoginResponse,
    TaskResponse,
    create_web_router,
)
from .tasks import (
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    Task,
    TaskManager,
    TaskNotCancellableError,
    TaskNotFoundError,
)

__all__ = [
    "WebConfig",
    "create_web_app",
    "run_web",
    "JWTConfig",
    "BearerAuth",
    "APIKeyAuth",
    "AuthError",
    "create_token",
    "decode_token",
    "create_web_router",
    "create_datasets_router",
    "DatasetSubmit",
    "TaskManager",
    "Task",
    "TaskNotFoundError",
    "TaskNotCancellableError",
    "LoginRequest",
    "LoginResponse",
    "ExecuteRequest",
    "GoalRequest",
    "TaskResponse",
    "QUEUED",
    "RUNNING",
    "DONE",
    "FAILED",
    "CANCELLED",
]
