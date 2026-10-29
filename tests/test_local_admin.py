"""End-to-end development login API using the real bearer dependency (no auth bypass)."""
import secrets
import jwt
from jcf_platform import local_auth
from jcf_platform.main import _principal
from sqlalchemy.orm import Session
from scripts import seed_test_data as seed


def configure_test_identity(app, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DB_AG_ENABLED", "false")
    monkeypatch.setenv("DB_HOST", "sqlserver,1433")
    monkeypatch.setenv("DB_NAME", "JcfEnterpriseRegistry")
    monkeypatch.setenv("ADMIN_LOCAL_TEST_ENABLED", "true")
    monkeypatch.setenv("ADMIN_LOCAL_TEST_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_LOCAL_TEST_PASSWORD_HASH", local_auth.password_hash("admin"))
    monkeypatch.setenv("ADMIN_LOCAL_TEST_SIGNING_KEY", secrets.token_hex(32))
    app.state.settings["env"] = "development"
    app.dependency_overrides.pop(_principal, None)


def test_local_admin_login_and_real_registry_read(client, app, monkeypatch):
    with Session(app.state.engine) as db:
        seed.seed_fixture(db)
    configure_test_identity(app, monkeypatch)
    config = client.get("/api/v1/admin/oidc-config")
    assert config.status_code == 200
    assert config.json()["localTestEnabled"] is True
    incorrect = client.post("/api/v1/admin/local-test-login",
                            json={"username": "admin", "password": "wrong"})
    assert incorrect.status_code == 401
    response = client.post("/api/v1/admin/local-test-login",
                           json={"username": "admin", "password": "admin"})
    assert response.status_code == 200, response.text
    access = response.json()["access_token"]
    assert response.json()["expires_in"] == 3600
    bad = client.get("/api/v1/registry/location-hierarchy")
    assert bad.status_code == 401
    headers = {"Authorization": f"Bearer {access}"}
    directory = client.get("/api/v1/registry/location-hierarchy", headers=headers)
    assert directory.status_code == 200, directory.text
    assert len(directory.json()["items"]) == 12
    sections = client.get("/api/v1/registry/section-definitions",headers=headers)
    assert sections.status_code == 200
    assert len(sections.json()["items"]) == 9
    assert client.get("/api/v1/facilities/building-occupancies",headers=headers).status_code == 200
    assert client.get("/api/v1/reporting/relationships",headers=headers).status_code == 200
    assert jwt.decode(access, bytes.fromhex(__import__("os").environ["ADMIN_LOCAL_TEST_SIGNING_KEY"]),
                      algorithms=["HS256"], audience=local_auth.TEST_AUDIENCE,
                      issuer=local_auth.TEST_ISSUER)["sub"] == "local-test:admin"


def test_local_login_is_unavailable_in_production(client, app, monkeypatch):
    configure_test_identity(app, monkeypatch)
    token = client.post("/api/v1/admin/local-test-login",
                        json={"username": "admin", "password": "admin"}).json()["access_token"]
    app.state.settings["env"] = "production"
    assert client.get("/api/v1/admin/oidc-config").json()["localTestEnabled"] is False
    assert client.post("/api/v1/admin/local-test-login",
                       json={"username": "admin", "password": "admin"}).status_code == 404
    assert client.get("/api/v1/registry/locations",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_local_login_disabled_by_default(client, app):
    assert client.get("/api/v1/admin/oidc-config").json()["localTestEnabled"] is False
    assert client.post("/api/v1/admin/local-test-login",
                       json={"username": "admin", "password": "admin"}).status_code == 404


def test_local_login_must_never_enable_in_external_or_ag_mode(monkeypatch):
    env = {"APP_ENV":"development","DB_AG_ENABLED":"true",
           "DB_HOST":"sqlserver,1433","DB_NAME":"JcfEnterpriseRegistry",
           "ADMIN_LOCAL_TEST_ENABLED":"true"}
    for key,value in env.items():monkeypatch.setenv(key,value)
    assert not local_auth.enabled({"env":"development"})
    monkeypatch.setenv("DB_AG_ENABLED","false")
    monkeypatch.setenv("DB_HOST","production-ag,1433")
    assert not local_auth.enabled({"env":"development"})
