"""Tests for the Web-layer JWT auth (geonexus.web.auth)."""

from __future__ import annotations

import pytest

from geonexus.web.auth import (
    AuthError,
    JWTConfig,
    create_token,
    decode_token,
)

SECRET = "test-secret-that-is-long-enough-0123456789abcdef"


def _hs256() -> JWTConfig:
    return JWTConfig(secret=SECRET, issuer="geonexus-test", audience="web")


def _rs256() -> JWTConfig:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return JWTConfig(
        private_key=private_pem,
        public_key=public_pem,
        issuer="geonexus-test",
        audience="web",
    )


class TestJWTConfig:
    def test_requires_signing_material(self) -> None:
        with pytest.raises(AuthError):
            JWTConfig()

    def test_hs256_secret_required(self) -> None:
        with pytest.raises(AuthError, match="HS256 requires `secret`"):
            JWTConfig(secret=None, algorithm="HS256")

    def test_rs256_public_key_required(self) -> None:
        with pytest.raises(AuthError, match="RS256 requires `public_key`"):
            JWTConfig(private_key="x", algorithm="RS256")

    def test_unknown_algorithm_rejected(self) -> None:
        with pytest.raises(AuthError, match="Unsupported JWT algorithm"):
            JWTConfig(secret=SECRET, algorithm="HS512")


class TestHS256:
    def test_roundtrip(self) -> None:
        cfg = _hs256()
        token = create_token(cfg, "alice", roles=["user"])
        claims = decode_token(cfg, token)
        assert claims["sub"] == "alice"
        assert claims["roles"] == ["user"]
        assert claims["iss"] == "geonexus-test"
        assert claims["aud"] == "web"
        assert claims["exp"] > claims["iat"]

    def test_extra_claims_roundtrip(self) -> None:
        cfg = _hs256()
        token = create_token(cfg, "bob", extra_claims={"scope": "read"})
        assert decode_token(cfg, token)["scope"] == "read"

    def test_tampered_token_rejected(self) -> None:
        cfg = _hs256()
        token = create_token(cfg, "alice")
        forged = token[:-2] + ("ab" if not token.endswith("ab") else "cd")
        with pytest.raises(AuthError, match="Invalid token"):
            decode_token(cfg, forged)

    def test_wrong_secret_rejected(self) -> None:
        token = create_token(_hs256(), "alice")
        other = JWTConfig(secret="another-secret-long-enough-0123456789")
        with pytest.raises(AuthError):
            decode_token(other, token)

    def test_expired_token_rejected(self) -> None:
        cfg = JWTConfig(secret=SECRET, ttl_seconds=-10)
        token = create_token(cfg, "alice")
        with pytest.raises(AuthError, match="Invalid token"):
            decode_token(_hs256(), token)

    def test_wrong_issuer_rejected(self) -> None:
        token = create_token(_hs256(), "alice")
        other = JWTConfig(secret=SECRET, issuer="someone-else")
        with pytest.raises(AuthError):
            decode_token(other, token)


class TestRS256:
    def test_roundtrip(self) -> None:
        cfg = _rs256()
        token = create_token(cfg, "alice")
        assert decode_token(cfg, token)["sub"] == "alice"

    def test_verifier_with_public_key_only(self) -> None:
        cfg = _rs256()
        token = create_token(cfg, "alice")
        # A verifier that only holds the public key can validate…
        verifier = JWTConfig(
            public_key=cfg.public_key,
            algorithm="RS256",
            issuer="geonexus-test",
            audience="web",
        )
        assert decode_token(verifier, token)["sub"] == "alice"
        # …but cannot issue tokens.
        with pytest.raises(AuthError, match="requires `private_key`"):
            create_token(verifier, "mallory")

    def test_wrong_public_key_rejected(self) -> None:
        cfg = _rs256()
        other = _rs256()
        token = create_token(cfg, "alice")
        with pytest.raises(AuthError):
            decode_token(other, token)
