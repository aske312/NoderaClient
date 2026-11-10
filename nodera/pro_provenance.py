"""Verify PRO signatures and pin each HTTPS subscription's server identity."""

import base64
import hashlib
import json
import re
import secrets
import time
from importlib.resources import files
from urllib.parse import urlsplit

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


def trusted_key(identity, public_key=None):
    public = decode(public_key if public_key is not None else TRUSTED_PRO_KEYS[identity])
    if digest(public) != identity:
        raise ValueError("Invalid trusted key identity")
    return Ed25519PublicKey.from_public_bytes(public)


def verify_proof(proof, body, url, nonce, *, now=None, public_key=None, expected_public_key=None):
    """Bootstrap a server key over verified HTTPS, then enforce the saved pin."""
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
        if public_key is not None:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Server key bootstrap requires HTTPS")
        if expected_public_key is not None:
            if public_key is not None and public_key != expected_public_key:
                raise ValueError("Subscription server key changed")
            public_key = expected_public_key
        trusted_key(claims["kid"], public_key).verify(decode(signature), DOMAIN + payload)
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


def verification_record(claims, config, *, public_key=None):
    # This local receipt lives inside the DPAPI-protected library. Expiration
    # limits acceptance of a network proof, not offline use of an accepted copy.
    record = {
        "kid": claims["kid"],
        "verified_at": int(time.time()),
        "url_sha256": claims["url_sha256"],
        "config_sha256": config_digest(config),
    }
    if public_key is not None:
        trusted_key(claims["kid"], public_key)
        record["public_key"] = public_key
    return record


def is_verified_profile(profile):
    if profile is None:
        return False
    try:
        record = profile.pro_verification
        if not isinstance(record, dict) or set(record) not in (
            {
                "kid",
                "verified_at",
                "url_sha256",
                "config_sha256",
            },
            {"kid", "verified_at", "url_sha256", "config_sha256", "public_key"},
        ):
            return False
        if "public_key" in record and urlsplit(profile.url).scheme != "https":
            return False
        trusted_key(record["kid"], record.get("public_key"))
        return (
            type(record["verified_at"]) is int
            and record["verified_at"] > 0
            and record["url_sha256"] == digest(profile.url.encode("utf-8"))
            and record["config_sha256"] == config_digest(profile.config)
        )
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, UnsupportedAlgorithm):
        return False


def profile_public_key(profile):
    if not is_verified_profile(profile):
        return None
    record = profile.pro_verification
    return record.get("public_key") or TRUSTED_PRO_KEYS.get(record["kid"])
