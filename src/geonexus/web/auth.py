"""Web-layer authentication: BFF + JWT.

Designed for distributed deployments:

- **HS256** (shared secret): simplest for a fleet of Web backends behind one
  trust domain — every instance signs and verifies with the same secret.
- **RS256** (private/public key pair): for cross-domain / multi-tenant setups
  where signing stays with a central issuer and any backend can verify with
  the public key alone (no shared secret to leak).

Tokens are stateless (self-contained claims), so any instance can validate a
token without shared session storage — the property that makes the Web layer
horizontally scalable.

The Web layer also validates node credentials: the BFF holds per-node API
keys (``X-API-Key``) and forwards them on GeoMCP calls, so browser clients
only ever hold a JWT, never node keys.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

import jwt
from fastapi import HTTPException, Request, status

# Default token lifetime (seconds).
DEFAULT_TTL = 3600

# Claim keys used by the Web layer.
CLAIM_SUBJECT = "sub"
CLAIM_ROLES = "roles"
CLAIM_ISSUED_AT = "iat"
CLAIM_EXPIRES_AT = "exp"


class AuthError(Exception):
    """Raised when a token or API key fails validation."""


@dataclass
class JWTConfig:
    """JWT signing/verification configuration.

    Either ``secret`` (HS256) or ``private_key`` + ``public_key`` (RS256)
    must be set. When ``secret`` is provided it wins (HS256); otherwise the
    key pair is used.

    Args:
        secret: Shared HMAC secret (HS256). Set the same value on every
            backend instance in the trust domain.
        private_key: PEM private key used for signing (RS256, issuer side).
        public_key: PEM public key used for verification (RS256, any
            verifier side). When only this is set the instance can verify
            but not issue tokens (read-only verifier).
        algorithm: ``"HS256"`` or ``"RS256"`` (defaults derived from secret).
        issuer: Expected ``iss`` claim; verified when set.
        audience: Expected ``aud`` claim; verified when set.
        ttl_seconds: Token lifetime; ``exp`` = ``iat`` + ttl.
    """

    secret: Optional[str] = None
    private_key: Optional[str] = None
    public_key: Optional[str] = None
    algorithm: Optional[str] = None
    issuer: Optional[str] = None
    audience: Optional[str] = None
    ttl_seconds: int = DEFAULT_TTL

    def __post_init__(self) -> None:
        if self.algorithm is None:
            if self.secret:
                self.algorithm = "HS256"
            elif self.public_key or self.private_key:
                self.algorithm = "RS256"
            else:
                raise AuthError(
                    "JWTConfig requires either `secret` (HS256) or "
                    "`private_key`/`public_key` (RS256)"
                )
        if self.algorithm not in ("HS256", "RS256"):
            raise AuthError(f"Unsupported JWT algorithm: {self.algorithm!r}")
        if self.algorithm == "HS256" and not self.secret:
            raise AuthError("HS256 requires `secret`")
        if self.algorithm == "RS256" and not self.public_key:
            raise AuthError("RS256 requires `public_key` for verification")


def _signing_key(config: JWTConfig) -> str:
    if config.algorithm == "HS256":
        return config.secret or ""
    return config.private_key or ""


def create_token(
    config: JWTConfig,
    subject: str,
    roles: Optional[list[str]] = None,
    extra_claims: Optional[dict[str, Any]] = None,
    ttl_seconds: Optional[int] = None,
) -> str:
    """Issue a signed JWT for ``subject``.

    Raises:
        AuthError: when the config cannot sign (read-only RS256 verifier).
    """
    if config.algorithm == "RS256" and not config.private_key:
        raise AuthError("RS256 token issuance requires `private_key`")
    now = int(time.time())
    claims: dict[str, Any] = {
        CLAIM_SUBJECT: subject,
        CLAIM_ISSUED_AT: now,
        CLAIM_EXPIRES_AT: now + (ttl_seconds or config.ttl_seconds),
    }
    if roles:
        claims[CLAIM_ROLES] = roles
    if config.issuer:
        claims["iss"] = config.issuer
    if config.audience:
        claims["aud"] = config.audience
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, _signing_key(config), algorithm=config.algorithm)


def decode_token(config: JWTConfig, token: str) -> dict[str, Any]:
    """Verify and decode a JWT.

    Raises:
        AuthError: on invalid signature, expiry, issuer or audience.
    """
    # __post_init__ guarantees the verification key is set for the algorithm.
    key: str = config.public_key or config.secret or ""
    algorithm = config.algorithm or "HS256"
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            options={"verify_signature": True, "verify_exp": True, "verify_iat": True},
            issuer=config.issuer,
            audience=config.audience,
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc


# --------------------------------------------------------------------------- #
# FastAPI integration
# --------------------------------------------------------------------------- #

class BearerAuth:
    """FastAPI dependency factory that requires a valid JWT bearer token.

    Usage::

        app = create_web_app(config)
        app.get("/api/private", dependencies=[Depends(BearerAuth(config.jwt))])
    """

    def __init__(self, jwt_config: JWTConfig) -> None:
        self.config = jwt_config

    def __call__(self, request: Request) -> dict[str, Any]:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or malformed Authorization header",
            )
        token = header[len("Bearer "):].strip()
        try:
            return decode_token(self.config, token)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc


class APIKeyAuth:
    """FastAPI dependency factory that requires an ``X-API-Key`` header.

    Used by the BFF to gate node-facing calls, and mirroring the registry's
    ``X-API-Key`` convention so the same key material works everywhere.
    """

    def __init__(self, api_keys: Optional[set[str]] = None) -> None:
        self.api_keys = set(api_keys or ())

    def __call__(self, request: Request) -> None:
        if not self.api_keys:
            return
        supplied = request.headers.get("X-API-Key")
        if supplied not in self.api_keys:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid X-API-Key",
            )


def require_api_key(api_keys: Optional[set[str]]) -> APIKeyAuth:
    """Return an :class:`APIKeyAuth` dependency for the given key set."""
    return APIKeyAuth(api_keys)
