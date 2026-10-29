"""Temporary LOCAL-EXPRESS test identity; completely disabled outside opt-in development.

This module is NOT an Entra ID replacement. It accepts one explicitly enabled
test account, backed by a PBKDF2 password hash in the root-only runtime env.
"""
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from threading import Lock

import jwt
from fastapi import HTTPException

TEST_ISSUER = "jcf-emp-isolated-express-test"
TEST_AUDIENCE = "jcf-emp-local-dev-api"
# The demo identity can exercise read/write and inspect the approvals queue,
# but four-eyes checks still forbid approving its own submitted requests.
# Separate reviewer identity allows testing the existing four-eyes workflow.
TEST_REVIEWER_SCOPES = (
    "registry.read registry.approve registry.apply "
    "facilities.read facilities.approve facilities.apply "
    "reporting.read reporting.approve reporting.apply "
    "contacts.read contacts.approve contacts.apply"
)
TEST_SCOPES = (
    "registry.read registry.write registry.approve registry.apply "
    "reporting.read reporting.write reporting.approve reporting.apply "
    "facilities.read facilities.write facilities.approve facilities.apply "
    "contacts.read contacts.write contacts.approve contacts.apply "
    "hr.postings.read hr.leave.read hr.write hr.approve hr.apply "
    "integration.events.read"
)
_failure_times = defaultdict(deque)
_lock = Lock()


def password_hash(value: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(24)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, rounds)
    return f"pbkdf2_sha256:{rounds}:{salt.hex()}:{digest.hex()}"


def password_matches(value: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, expected_hex = stored.split(":")
        count = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 200_000 <= count <= 1_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt_hex), count)
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False


def enabled(cfg: dict) -> bool:
    return (
        cfg.get("env") == "development"
        and os.getenv("APP_ENV") == "development"
        and os.getenv("DB_AG_ENABLED", "").lower() == "false"
        and os.getenv("DB_HOST") == "sqlserver,1433"
        and os.getenv("DB_NAME") == "JcfEnterpriseRegistry"
        and os.getenv("ADMIN_LOCAL_TEST_ENABLED", "").lower() == "true"
    )


def validate_configuration(cfg: dict) -> None:
    if os.getenv("ADMIN_LOCAL_TEST_ENABLED", "").lower() != "true":
        return
    if not enabled(cfg):
        raise RuntimeError("Local test admin requires isolated SQL Server Express development mode")
    for key in ("ADMIN_LOCAL_TEST_USERNAME","ADMIN_LOCAL_TEST_PASSWORD_HASH",
                "ADMIN_LOCAL_TEST_SIGNING_KEY"):
        if not os.getenv(key):
            raise RuntimeError(f"Local test admin: missing {key}")
    if len(bytes.fromhex(os.environ["ADMIN_LOCAL_TEST_SIGNING_KEY"])) < 32:
        raise RuntimeError("Local test admin: signing key must be at least 256 bits")


def test_configured(cfg: dict) -> bool:
    return enabled(cfg) and all(os.getenv(k) for k in (
        "ADMIN_LOCAL_TEST_USERNAME","ADMIN_LOCAL_TEST_PASSWORD_HASH",
        "ADMIN_LOCAL_TEST_SIGNING_KEY"))


def _rate_limit(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        times = _failure_times[key]
        while times and times[0] < now-600:
            times.popleft()
        if len(times) >= 10:
            return False
        times.append(now)
        return True


def _clear_failures(key: str) -> None:
    with _lock:
        _failure_times.pop(key, None)


def login(username: str, password: str, cfg: dict, source: str) -> dict:
    if not test_configured(cfg):
        raise HTTPException(404, "Local test login is not enabled")
    client = source or "unknown"
    if not _rate_limit(client):
        raise HTTPException(429, "Too many test login attempts; retry later")
    stored_user = os.environ["ADMIN_LOCAL_TEST_USERNAME"]
    stored_hash = os.environ["ADMIN_LOCAL_TEST_PASSWORD_HASH"]
    reviewer_user = os.getenv("ADMIN_LOCAL_TEST_REVIEWER_USERNAME","")
    reviewer_hash = os.getenv("ADMIN_LOCAL_TEST_REVIEWER_PASSWORD_HASH","")
    is_admin = hmac.compare_digest(username, stored_user) and password_matches(password, stored_hash)
    is_reviewer = (bool(reviewer_user and reviewer_hash)
                   and hmac.compare_digest(username, reviewer_user)
                   and password_matches(password, reviewer_hash))
    if not (is_admin or is_reviewer):
        raise HTTPException(401, "Incorrect test credentials")
    granted_scopes = TEST_SCOPES if is_admin else TEST_REVIEWER_SCOPES
    _clear_failures(client)
    now = datetime.now(timezone.utc)
    claims = {
        "sub": f"local-test:{username}",
        "preferred_username": username,
        "name": "Development Test Administrator" if is_admin else "Development Test Reviewer",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(hours=1),
        "scope": granted_scopes,
        "testOnly": True,
    }
    access = jwt.encode(claims, bytes.fromhex(os.environ["ADMIN_LOCAL_TEST_SIGNING_KEY"]),
                        algorithm="HS256")
    return {"access_token": access, "token_type": "Bearer", "expires_in": 3600,
            "scope": granted_scopes}


def decode_test_token(token: str, cfg: dict) -> dict:
    if not test_configured(cfg):
        raise jwt.InvalidTokenError("Local test authentication is disabled")
    return jwt.decode(token, bytes.fromhex(os.environ["ADMIN_LOCAL_TEST_SIGNING_KEY"]),
                      algorithms=["HS256"], audience=TEST_AUDIENCE, issuer=TEST_ISSUER,
                      options={"require":["exp","iss","aud","sub"]}, leeway=10)
