import base64
import hashlib
import json
import time
from dataclasses import replace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nodera import pro_provenance as provenance
from nodera.profile_view import is_pro_subscription
from nodera.storage import Store
from nodera.subscription import fetch_profile

YAML = b"proxies:\n  - name: test\n    type: ss\n    server: example.com\n    port: 443\n"
URL = "https://pro.example/s/PRIVATE?format=yaml"


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.fixture
def signer(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    identity = hashlib.sha256(public).hexdigest()
    monkeypatch.setattr(provenance, "TRUSTED_PRO_KEYS", {identity: encode(public)})

    def sign(body, url, request_nonce, **overrides):
        issued = int(time.time())
        claims = {
            "v": 1,
            "source": "pro",
            "alg": "Ed25519",
            "kid": identity,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
            "nonce": request_nonce,
            "iat": issued,
            "exp": issued + 300,
        }
        claims.update(overrides)
        payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return (
            encode(payload) + "." + encode(key.sign(b"vps-control/pro-subscription/v1\0" + payload))
        )

    return sign


def fetch_signed(signer, **claims):
    def handle(request):
        proof = signer(YAML, str(request.url), request.headers["x-subscription-nonce"], **claims)
        return httpx.Response(200, content=YAML, headers={"X-Subscription-Proof": proof})

    return fetch_profile(URL, "Subscription", "device", httpx.MockTransport(handle))


def test_signed_response_is_verified_and_saved_for_offline_use(signer, tmp_path):
    profile = fetch_signed(signer)
    assert is_pro_subscription(profile)
    store = Store(tmp_path)
    store.save_profile(profile)
    restored = store.load_library().active
    assert is_pro_subscription(restored)
    assert restored.pro_verification == profile.pro_verification
    # Proof expiry concerns the time of receipt; restarting offline keeps the receipt.
    assert is_pro_subscription(replace(restored, updated_at="2020-01-01T00:00:00Z"))


@pytest.mark.parametrize(
    "claims",
    [
        {"body_sha256": "0" * 64},
        {"url_sha256": "0" * 64},
        {"nonce": "different_nonce_123456789"},
        {"kid": "f" * 64},
        {"v": True},
        {"v": 2},
        {"alg": "HS256"},
        {"source": "fake"},
        {"iat": 1000, "exp": 1300},
        {"iat": 9999999999, "exp": 10000000299},
    ],
)
def test_bad_proof_never_blocks_normal_subscription_or_confirms_it(signer, claims):
    profile = fetch_signed(signer, **claims)
    assert profile.config["proxies"]
    assert not profile.pro_verification
    assert not is_pro_subscription(profile)


def test_revoking_key_or_changing_cached_body_or_url_removes_verified_badge(signer, monkeypatch):
    profile = fetch_signed(signer)
    assert not is_pro_subscription(replace(profile, url=URL + "2"))
    assert not is_pro_subscription(replace(profile, config={**profile.config, "mode": "direct"}))
    monkeypatch.setattr(provenance, "TRUSTED_PRO_KEYS", {})
    assert not is_pro_subscription(profile)


def test_new_nonce_for_every_fetch_and_previous_proof_cannot_be_replayed(signer):
    nonces = []
    proof = None

    def handle(request):
        nonlocal proof
        nonces.append(request.headers["x-subscription-nonce"])
        if proof is None:
            proof = signer(YAML, str(request.url), nonces[-1])
        return httpx.Response(200, content=YAML, headers={"X-Subscription-Proof": proof})

    transport = httpx.MockTransport(handle)
    assert is_pro_subscription(fetch_profile(URL, "P", "device", transport))
    assert not is_pro_subscription(fetch_profile(URL, "P", "device", transport))
    assert nonces[0] != nonces[1]
    assert all(len(nonce) == 43 for nonce in nonces)


@pytest.mark.parametrize(
    "proof", ["", ".", "a.a", "a.b.c", "a" * 4097, encode(b"[" * 1100 + b"]" * 1100) + ".AA"]
)
def test_headers_and_domain_cannot_substitute_for_signature(proof):
    profile = fetch_profile(
        "https://312node.net/s/token",
        "PRO",
        "device",
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=YAML,
                headers={"X-Subscription-Source": "pro", "X-Subscription-Proof": proof},
            )
        ),
    )
    assert not is_pro_subscription(profile)
    assert profile.config["proxies"]


def test_unknown_self_signed_key_does_not_become_trusted(signer, monkeypatch):
    profile = fetch_signed(signer)
    assert is_pro_subscription(profile)
    monkeypatch.setattr(provenance, "TRUSTED_PRO_KEYS", {})
    assert not is_pro_subscription(fetch_signed(signer))


def test_modified_signature_is_rejected(signer):
    nonce = provenance.new_nonce()
    proof = signer(YAML, URL, nonce)
    payload, signature = proof.split(".")
    changed = bytearray(provenance.decode(signature))
    changed[0] ^= 1
    with pytest.raises(ValueError):
        provenance.verify_proof(payload + "." + encode(bytes(changed)), YAML, URL, nonce)


def test_failed_verification_logs_no_link_credentials_or_proof(signer):
    events = []

    def handle(request):
        return httpx.Response(
            200,
            content=YAML,
            headers={
                "X-Subscription-Proof": signer(YAML, str(request.url), "old_nonce_1234567890123456")
            },
        )

    result = fetch_profile(
        URL,
        "P",
        "device",
        httpx.MockTransport(handle),
        log=lambda message, level="info": events.append(message),
    )
    assert not is_pro_subscription(result)
    assert any("subscription_pro_verification state=invalid" in event for event in events)
    assert "PRIVATE" not in str(events)
    assert "old_nonce" not in str(events)
