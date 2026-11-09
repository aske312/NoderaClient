"""Verify PRO subscription signatures using application-owned public trust anchors."""

import base64
import hashlib
import json
import re
import secrets
import time
from importlib.resources import files

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DOMAIN = b"vps-control/pro-subscription/v1\0"
TTL = 300
TRUSTED_PRO_KEYS = json.loads(
    files("nodera").joinpath("resources/pro-trusted-keys.json").read_text(encoding="utf-8")
)


def new_nonce():
    return secrets.token_urlsafe(32)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid proof encoding")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def trusted_key(identity):
    public = decode(TRUSTED_PRO_KEYS[identity])
    if digest(public) != identity:
        raise ValueError("Invalid trusted key identity")
    return Ed25519PublicKey.from_public_bytes(public)


def verify_proof(proof, body, url, nonce, *, now=None):
    """Verify the wire format shared with PRO; never trust response-provided keys."""
    if not isinstance(proof, str) or len(proof) > 4096:
        raise ValueError("Invalid PRO proof")
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{22,128}", nonce):
            raise ValueError("A fresh client nonce is required")
        encoded, signature = proof.split(".")
        payload = decode(encoded)
        claims = json.loads(payload)
        if not isinstance(claims, dict) or set(claims) != {
            "v",
            "source",
            "alg",
            "kid",
            "body_sha256",
            "url_sha256",
            "nonce",
            "iat",
            "exp",
        }:
            raise ValueError("Invalid PRO claims")
        trusted_key(claims["kid"]).verify(decode(signature), DOMAIN + payload)
        current = int(time.time() if now is None else now)
        if (
            type(claims["v"]) is not int
            or claims["v"] != 1
            or claims["source"] != "pro"
            or claims["alg"] != "Ed25519"
            or type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or claims["exp"] - claims["iat"] != TTL
            or claims["iat"] > current + 30
            or claims["exp"] <= current
            or claims["body_sha256"] != digest(body)
            or claims["url_sha256"] != digest(url.encode("utf-8"))
            or claims["nonce"] != nonce
        ):
            raise ValueError("PRO proof does not match this response")
    except (
        InvalidSignature,
        UnsupportedAlgorithm,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ValueError("Invalid PRO proof") from exc
    return claims


def config_digest(config):
    return digest(
        json.dumps(
            config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    )


def verification_record(claims, config):
    # This local receipt lives inside the DPAPI-protected library. Expiration
    # limits acceptance of a network proof, not offline use of an accepted copy.
    return {
        "kid": claims["kid"],
        "verified_at": int(time.time()),
        "url_sha256": claims["url_sha256"],
        "config_sha256": config_digest(config),
    }


def is_verified_profile(profile):
    if profile is None:
        return False
    try:
        record = profile.pro_verification
        if not isinstance(record, dict) or set(record) != {
            "kid",
            "verified_at",
            "url_sha256",
            "config_sha256",
        }:
            return False
        trusted_key(record["kid"])
        return (
            type(record["verified_at"]) is int
            and record["verified_at"] > 0
            and record["url_sha256"] == digest(profile.url.encode("utf-8"))
            and record["config_sha256"] == config_digest(profile.config)
        )
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, UnsupportedAlgorithm):
        return False
