"""TEST-ONLY sample organizational register for JCF-EMP.

Uses application governance commands, but synthetic requester and approver are
*not* real JCF approvals. Every code is DEMO- and member number TEST-JCF-.
Parent structures, buildings and personnel below are illustrative, NOT JCF
authoritative records. Never run against production databases or AG listeners.
"""
import argparse
import hashlib
import os
from datetime import date
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from jcf_platform import config, models as m, service as svc

REFERENCE = "TEST-FIXTURE-NOT-A-FORCE-ORDER"
REQUESTER = "SYSTEM-TEST-FIXTURE-REQUESTER"
APPROVER = "SYSTEM-TEST-FIXTURE-APPROVER"
APPLIER = "SYSTEM-TEST-FIXTURE-APPLIER"
START = "2020-01-01"

LOCATIONS = [
    ("HQ", "JCF Headquarters", "HQ", None),
    ("SO", "Strategic Operations", "PORTFOLIO", "HQ"),
    ("A1", "Area One (illustrative)", "AREA", "SO"),
    ("A2", "Area Two (illustrative)", "AREA", "SO"),
    ("TECH", "Technology Branch", "BRANCH", "HQ"),
    ("SAC", "Saint Andrew Central", "DIVISION", "A1"),
    ("SAS", "Saint Andrew South", "DIVISION", "A1"),
    ("J", "J Division (illustrative)", "DIVISION", "A2"),
    ("STATION", "Example Police Station (fictional)", "STATION", "SAC"),
    ("CIBHQ", "CIB Headquarters (illustrative)", "BRANCH", "SO"),
    ("NIBHQ", "NIB Headquarters (illustrative)", "BRANCH", "SO"),
    ("EXT", "External Attachment (illustrative)", "EXTERNAL_ATTACHMENT", None),
]
SECTIONS = [
    ("CIB", "CIB"), ("DIU", "DIU"), ("ADMIN", "Administrative Office"),
    ("ICT", "ICT Support"), ("OPS", "Operations"),
]
ATTACHMENTS = [
    ("HQ", "ADMIN"), ("SO", "OPS"), ("A1", "OPS"), ("A2", "OPS"),
    ("TECH", "ICT"), ("SAC", "ADMIN"), ("SAC", "CIB"), ("SAC", "DIU"),
    ("SAS", "ADMIN"), ("SAS", "CIB"), ("SAS", "DIU"), ("J", "ADMIN"),
    ("STATION", "ADMIN"), ("CIBHQ", "CIB"), ("NIBHQ", "DIU"), ("EXT", "ADMIN"),
]


def code(token):
    return "DEMO-" + token


def marker_check(db: Session):
    """Never seed a user-populated development master list that has no demo data."""
    codes = db.scalars(select(m.Location.code)).all()
    return not codes or any(c.startswith("DEMO-") for c in codes)


def do_change(db: Session, action: str, payload: dict, step: str):
    """Resumable governed write; separate *synthetic* requester and approver."""
    key = "test-seed-" + hashlib.sha256(step.encode("utf8")).hexdigest()[:48]
    request = svc.create_request(db, action, payload, REFERENCE, REQUESTER, key)
    if request.status == "SUBMITTED":
        svc.decide_request(db, request.id, "APPROVED", APPROVER, "test-seed:" + step)
    elif request.status not in ("APPROVED", "APPLIED"):
        raise RuntimeError(f"Previously rejected test seed request: {step}")
    if request.status == "APPLIED":
        raise RuntimeError(f"Applied test seed target missing; requires review: {step}")
    return svc.apply_request(db, request.id, APPLIER, "test-seed:" + step)


def ensure(db: Session, model, predicates, action, payload, step):
    existing = db.scalar(select(model).where(*predicates))
    if existing is not None:
        return existing.id, False
    return do_change(db, action, payload, step), True


def seed_fixture(db: Session) -> dict:
    if not marker_check(db):
        return {"skipped": "Non-demo master data already present; fixture was not loaded."}
    counts = {key: 0 for key in (
        "locations", "sections", "attachments", "parents", "aliases", "reporting",
        "buildings", "occupancy", "contacts", "personnel", "postings", "leave")}
    loc_ids = {}
    for key, name, kind, parent in LOCATIONS:
        ident, created = ensure(
            db, m.Location, (m.Location.code == code(key),), "LOCATION_CREATE",
            {"code": code(key), "name": "[TEST] " + name,
             "locationType": kind, "validFrom": START}, "location:" + key)
        loc_ids[key] = ident
        counts["locations"] += int(created)
    for key, _, _, parent in LOCATIONS:
        if parent:
            _, created = ensure(
                db, m.LocationParent,
                (m.LocationParent.child_id == loc_ids[key],
                 m.LocationParent.parent_id == loc_ids[parent]),
                "LOCATION_PARENT", {"childId": str(loc_ids[key]),
                                    "parentId": str(loc_ids[parent]),
                                    "validFrom": START}, "parent:" + key)
            counts["parents"] += int(created)
    for alias, key in [("SAC", "SAC"), ("SAS", "SAS"), ("J", "J")]:
        _, created = ensure(
            db, m.LocationAlias,
            (m.LocationAlias.location_id == loc_ids[key],
             m.LocationAlias.normalized_alias == alias.casefold()),
            "LOCATION_ALIAS",
            {"locationId": str(loc_ids[key]), "alias": alias}, "alias:" + key)
        counts["aliases"] += int(created)
    definitions = {}
    for key, name in SECTIONS:
        ident, created = ensure(
            db, m.SectionDefinition, (m.SectionDefinition.code == code(key),),
            "SECTION_DEFINE", {"code": code(key), "name": "[TEST] " + name,
                               "validFrom": START}, "definition:" + key)
        definitions[key] = ident
        counts["sections"] += int(created)
    section_ids = {}
    for loc_key, def_key in ATTACHMENTS:
        ident, created = ensure(
            db, m.LocationSection,
            (m.LocationSection.location_id == loc_ids[loc_key],
             m.LocationSection.definition_id == definitions[def_key]),
            "SECTION_ATTACH",
            {"locationId": str(loc_ids[loc_key]),
             "sectionDefinitionId": str(definitions[def_key]),
             "validFrom": START},
            "attach:" + loc_key + ":" + def_key)
        section_ids[(loc_key, def_key)] = ident
        counts["attachments"] += int(created)
    relationships = [
        (("SAC", "CIB"), "SAC", "LOCAL"),
        (("SAC", "CIB"), "CIBHQ", "FUNCTIONAL"),
        (("SAC", "DIU"), "SAC", "LOCAL"),
        (("SAC", "DIU"), "NIBHQ", "FUNCTIONAL"),
        (("SAS", "CIB"), "SAS", "LOCAL"),
        (("SAS", "CIB"), "CIBHQ", "FUNCTIONAL"),
        (("SAS", "DIU"), "SAS", "LOCAL"),
        (("SAS", "DIU"), "NIBHQ", "FUNCTIONAL"),
        ("SAC", "SO", "OPERATIONAL"),
        ("SAS", "SO", "OPERATIONAL"),
    ]
    for source, target, relation in relationships:
        from_id = section_ids[source] if isinstance(source, tuple) else loc_ids[source]
        to_id = loc_ids[target]
        _, created = ensure(
            db, m.ReportingEdge,
            (m.ReportingEdge.source_id == from_id,
             m.ReportingEdge.authority_id == to_id,
             m.ReportingEdge.relation_type == relation),
            "REPORTING_EDGE",
            {"sourceId": str(from_id), "authorityId": str(to_id),
             "relationType": relation, "validFrom": START, "reference": REFERENCE},
            "report:" + str(source) + ":" + target + ":" + relation)
        counts["reporting"] += int(created)
    buildings = {}
    for key, name in [
        ("NORTH", "Example Building North (fictional)"),
        ("SOUTH", "Example Building South (fictional)")]:
        ident, created = ensure(
            db, m.Building, (m.Building.code == code("BLD-" + key),),
            "BUILDING_CREATE",
            {"code": code("BLD-" + key), "name": "[TEST] " + name,
             "address": "Fictional testing address - not a JCF facility",
             "parish": "Kingston", "validFrom": START}, "building:" + key)
        buildings[key] = ident
        counts["buildings"] += int(created)
    for loc_key, bld_key in [
        ("SAC", "NORTH"), ("SAC", "SOUTH"), ("SAS", "SOUTH")]:
        _, created = ensure(
            db, m.LocationBuilding,
            (m.LocationBuilding.location_id == loc_ids[loc_key],
             m.LocationBuilding.building_id == buildings[bld_key]),
            "LOCATION_BUILDING",
            {"locationId": str(loc_ids[loc_key]),
             "buildingId": str(buildings[bld_key]),
             "validFrom": START, "occupancyType": "SHARED"},
            "occupancy:" + loc_key + ":" + bld_key)
        counts["occupancy"] += int(created)
    test_email = "test-registry@example.invalid"
    if db.scalar(select(m.Contact).where(m.Contact.value == test_email)) is None:
        do_change(db, "CONTACT_ASSIGN",
                  {"entityKind": "LOCATION", "entityId": str(loc_ids["SAC"]),
                   "kind": "EMAIL", "value": test_email, "purpose": "TEST_DIRECTORY",
                   "visibility": "INTERNAL", "validFrom": START}, "contact:SAC")
        counts["contacts"] += 1
    test_members = [
        ("TEST-JCF-0001", "Example Member One", "SAC", "CIB"),
        ("TEST-JCF-0002", "Example Member Two", "SAC", "DIU"),
        ("TEST-JCF-0003", "Example Member Three", "TECH", "ICT")]
    for number, name, location, unit in test_members:
        person_id, created = ensure(
            db, m.Personnel, (m.Personnel.computer_number == number,),
            "PERSONNEL_CREATE",
            {"computerNumber": number, "displayName": "[TEST] " + name},
            "person:" + number)
        counts["personnel"] += int(created)
        _, created = ensure(
            db, m.Posting,
            (m.Posting.personnel_id == person_id, m.Posting.is_primary.is_(True)),
            "POSTING_CREATE",
            {"personnelId": str(person_id), "locationId": str(loc_ids[location]),
             "sectionId": str(section_ids[(location, unit)]),
             "validFrom": START, "reference": REFERENCE},
            "posting:" + number)
        counts["postings"] += int(created)
    member = db.scalar(select(m.Personnel.id).where(
        m.Personnel.computer_number == "TEST-JCF-0001"))
    if db.scalar(select(m.LeaveRequest).where(
            m.LeaveRequest.personnel_id == member,
            m.LeaveRequest.category == "TEST_ANNUAL")) is None:
        do_change(db, "LEAVE_REQUEST",
                  {"personnelId": str(member), "category": "TEST_ANNUAL",
                   "startsOn": "2026-11-02", "endsBefore": "2026-11-06"},
                  "leave:0001")
        counts["leave"] += 1
    return counts


def verify_fixture(db: Session):
    for key, _, _, _ in LOCATIONS:
        assert db.scalar(select(m.Location).where(
            m.Location.code == code(key))) is not None, key
    for key, _ in SECTIONS:
        assert db.scalar(select(m.SectionDefinition).where(
            m.SectionDefinition.code == code(key))) is not None, key
    assert len(db.scalars(select(m.LocationSection)).all()) >= len(ATTACHMENTS)
    sac = db.scalar(select(m.Location.id).where(m.Location.code == code("SAC")))
    cib = db.scalar(select(m.SectionDefinition.id).where(
        m.SectionDefinition.code == code("CIB")))
    section_id = db.scalar(select(m.LocationSection.id).where(
        m.LocationSection.location_id == sac,
        m.LocationSection.definition_id == cib))
    svc.validate_pair(db, sac, section_id, date(2026, 10, 28))
    edges = db.scalars(select(m.ReportingEdge).where(
        m.ReportingEdge.source_id == section_id)).all()
    assert {e.relation_type for e in edges} >= {"LOCAL", "FUNCTIONAL"}
    for code_ in ["TEST-JCF-0001", "TEST-JCF-0002", "TEST-JCF-0003"]:
        pid = db.scalar(select(m.Personnel.id).where(
            m.Personnel.computer_number == code_))
        assert pid is not None
        assert db.scalar(select(m.Posting.id).where(
            m.Posting.personnel_id == pid)) is not None
    print("TEST fixture verified: 12 locations, 5 definitions, "
          "16 mappings, 10 reporting edges, 3 fake members")


def validate_test_target():
    if os.getenv("APP_ENV") != "development":
        raise RuntimeError("Only APP_ENV=development can use the test seed")
    if os.getenv("DB_AG_ENABLED", "false").lower() != "false":
        raise RuntimeError("Cannot seed an AG")
    if (os.getenv("DB_HOST") != "sqlserver,1433" or
            os.getenv("DB_NAME") != "JcfEnterpriseRegistry"):
        raise RuntimeError("Only isolated local Docker SQL Express target supported")
    if os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL overrides not allowed for test seeding")


def main():
    parser = argparse.ArgumentParser(description="JCF-EMP synthetic testing fixture")
    parser.add_argument("--check", action="store_true", help="Check database environment")
    parser.add_argument("--verify", action="store_true", help="Verify records, no writes")
    args = parser.parse_args()
    validate_test_target()
    engine = config.make_engine()
    try:
        with engine.connect() as conn:
            version = conn.execute(text(
                "SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int)")).scalar_one()
            edition = conn.execute(text(
                "SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(200))")).scalar_one()
            if version != 15 or "Express" not in edition:
                raise RuntimeError("Requires real SQL Server 2019 Express")
        if args.check:
            print("Approved isolated SQL 2019 Express test target")
            return
        with Session(engine) as db:
            if args.verify:
                verify_fixture(db)
            else:
                report = seed_fixture(db)
                if "skipped" not in report:
                    verify_fixture(db)
                print("Synthetic seed report:", report)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
