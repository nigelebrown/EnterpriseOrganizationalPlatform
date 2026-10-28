"""Synthetic JCF-EMP test fixture integrity, idempotence and isolation."""
from datetime import date
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from jcf_platform import models as m, service
from scripts import seed_test_data as seed


def test_seed_first_run_and_repeat(client, app):
    with Session(app.state.engine) as db:
        result = seed.seed_fixture(db)
        assert result["locations"] == 12
        assert result["sections"] == 5
        assert result["attachments"] == 16
        assert result["reporting"] == 10
        assert result["personnel"] == 3
        seed.verify_fixture(db)
        audit_count = db.scalar(select(func.count()).select_from(m.AuditEvent))
        request_count = db.scalar(select(func.count()).select_from(m.ChangeRequest))
        event_count = db.scalar(select(func.count()).select_from(m.OutboxEvent))
        db.rollback()
    found = client.get("/api/v1/registry/locations?search=SAC").json()["items"]
    assert len(found) == 1 and found[0]["code"] == "DEMO-SAC"
    sections = client.get(
        "/api/v1/registry/locations/" + found[0]["locationId"] + "/sections"
    ).json()["items"]
    assert {x["code"] for x in sections} == {
        "DEMO-ADMIN", "DEMO-CIB", "DEMO-DIU"}
    with Session(app.state.engine) as db:
        again = seed.seed_fixture(db)
        assert all(value == 0 for value in again.values())
        assert db.scalar(select(func.count()).select_from(m.AuditEvent)) == audit_count
        assert db.scalar(select(func.count()).select_from(m.ChangeRequest)) == request_count
        assert db.scalar(select(func.count()).select_from(m.OutboxEvent)) == event_count
        assert db.scalar(select(func.count()).select_from(m.LocationSection)) == 16
        assert db.scalar(select(func.count()).select_from(m.Posting)) == 3
        assert db.scalar(select(func.count()).select_from(m.LeaveRequest)) == 1
        assert all(r.requested_by != r.decided_by
                   for r in db.scalars(select(m.ChangeRequest)))


def test_user_populated_registry_is_not_modified(app):
    with Session(app.state.engine) as db:
        node = m.OrgNode(kind="LOCATION")
        db.add(node)
        db.flush()
        db.add(m.Location(id=node.id, code="OWNED-LOCATION",
                          location_type="DIVISION", valid_from=date(2020, 1, 1)))
        db.commit()
        assert "skipped" in seed.seed_fixture(db)
        assert db.scalar(select(func.count()).select_from(m.Location)) == 1


@pytest.mark.parametrize("overrides", [
    {"APP_ENV": "production"},
    {"DB_AG_ENABLED": "true"},
    {"DB_HOST": "production-ag,1433"},
    {"DB_NAME": "ProductionHRIS"},
    {"DATABASE_URL": "mssql+pyodbc://test"},
])
def test_seeder_rejects_unsafe_environment(monkeypatch, overrides):
    base = {"APP_ENV": "development", "DB_AG_ENABLED": "false",
            "DB_HOST": "sqlserver,1433", "DB_NAME": "JcfEnterpriseRegistry"}
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        seed.validate_test_target()


def test_seeder_accepts_only_local_express(monkeypatch):
    for key, value in {
        "APP_ENV": "development", "DB_AG_ENABLED": "false",
        "DB_HOST": "sqlserver,1433", "DB_NAME": "JcfEnterpriseRegistry"
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    seed.validate_test_target()
