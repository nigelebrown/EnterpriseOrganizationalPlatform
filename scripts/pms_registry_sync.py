"""Import HRIS/PMS organizational lookup tables into isolated JCF-EMP SQL2019 Express.

Source: READ-ONLY dbo.Portfolios, Areas, Branches, Divisions, Stations, Sections,
Units. HRIS is an UNTRUSTED lookup source, not the target schema or hierarchy.
No source hierarchy relationships are copied into JCF-EMP automatically.
No PersonnelInformation, Assignments or other staff data are accessed.

Usage inside the existing JCF-EMP Docker migration image:
  python scripts/pms_registry_sync.py inspect
  python scripts/pms_registry_sync.py preview
  python scripts/pms_registry_sync.py apply --confirm-test-import
  python scripts/pms_registry_sync.py verify
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import date
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session

from jcf_platform import config, models as m, service as svc

TABLES = {
    "Portfolios": ("PORTFOLIO", "P", "PortfolioName", "PortfolioCode", ()),
    "Areas": ("AREA", "A", "AreaName", "AreaCode", ("Portfolio",)),
    "Branches": ("BRANCH", "B", "BranchName", "BranchCode", ("PortfolioName",)),
    "Divisions": ("DIVISION", "D", "DivisionName", "DivisionCode", ("Area", "Branch")),
    "Stations": ("STATION", "T", "StationName", "StationCode", ("Division", "Area")),
    "Sections": ("SECTION", "S", "SectionName", None, ("Division",)),
    "Units": ("UNIT", "U", "UnitName", None, ("Division", "Station", "Section")),
}
REFERENCE = "PMS-HRIS-LOOKUP-TEST-IMPORT-NOT-A-FORCE-ORDER"
SOURCE = "PMS_HRIS"
REQUESTER = "PMS-TEST-IMPORT-REQUESTER"
APPROVER = "PMS-TEST-IMPORT-APPROVER"
APPLIER = "PMS-TEST-IMPORT-APPLIER"
START = "2020-01-01"


def check_target():
    if not (os.getenv("APP_ENV") == "development"
            and os.getenv("DB_AG_ENABLED", "false").lower() == "false"
            and os.getenv("DB_HOST") == "sqlserver,1433"
            and os.getenv("DB_NAME") == "JcfEnterpriseRegistry"
            and not os.getenv("DATABASE_URL")):
        raise RuntimeError("Refusing import outside isolated JCF-EMP SQL2019 Express development database")


def source_engine():
    server = os.environ.get("PMS_SOURCE_HOST", "").strip()
    database = os.environ.get("PMS_SOURCE_DB", "").strip()
    login = os.environ.get("PMS_SOURCE_USER", "").strip()
    password = os.environ.get("PMS_SOURCE_PASSWORD", "")
    if not all((server, database, login, password)):
        raise RuntimeError("PMS_SOURCE_HOST, PMS_SOURCE_DB, PMS_SOURCE_USER, PMS_SOURCE_PASSWORD required in root-only secrets file")
    if any(ch in server for ch in (";", "\n", "\r")) or any(ch in database for ch in (";", "\n", "\r")):
        raise RuntimeError("Invalid source host or database")
    # A source with the target DB name is suspicious, even if on a different server.
    if database.casefold() == "jcfenterpriseregistry":
        raise RuntimeError("Refusing to use the destination registry database as PMS source")
    trust = os.getenv("PMS_SOURCE_TRUST_CERT", "false").lower() == "true"
    odbc = ("DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={server};DATABASE={database};UID={login};PWD={password};"
            f"Encrypt=yes;TrustServerCertificate={'yes' if trust else 'no'};"
            "Connection Timeout=12;ApplicationIntent=ReadOnly;"
            "Application Name=JCF_EMP_PMS_READONLY_SYNC;")
    return create_engine("mssql+pyodbc:///?odbc_connect=" + quote_plus(odbc),
                         pool_pre_ping=True)


def source_id(value):
    """Preserve PMS native lookup identifier as an opaque external reference.

    Source Oid may be a SQL uniqueidentifier or integer. Neither is ever used
    as a JCF-EMP entity primary key: SQLAlchemy creates new UUIDv4 values.
    """
    if value is None:
        return None
    key = str(value).strip()
    if not key or len(key) > 180 or any(ord(ch) < 32 for ch in key):
        raise ValueError("Invalid PMS lookup identifier")
    return key.lower()


def clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalized_name(value):
    return clean(value).casefold()


def loc_code(table: str, oid: str) -> str:
    """Stable business import code, not a primary key or original PMS Oid."""
    fingerprint = hashlib.sha256((table + ":" + source_id(oid)).encode("utf-8")).hexdigest()[:32]
    return "PMS-" + TABLES[table][1] + "-" + fingerprint


def section_code(name: str, table: str | None = None, source_oid: str | None = None) -> str:
    """Keep each source Section/Unit distinct until a human reviews duplicates.

    The two-argument legacy form remains for display/search consistency; the
    import always supplies table and source Oid, avoiding automatic merging of
    different HRIS records whose names happen to match.
    """
    identity = (table + ":" + source_id(source_oid)) if table and source_oid else normalized_name(name)
    return "PMS-S-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:25]


def current_columns(conn, table: str):
    return {str(row[0]) for row in conn.execute(text(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:table"), {"table": table})}


def extract(conn):
    """Read active source LOOKUP records only.

    The HRIS formation tables and their relationships are acknowledged as
    unreliable. They remain seven read-only source tables; the destination
    uses a single Location table, one Section/Unit category and separately
    governed effective-dated associations.
    """
    source = {}
    warnings = []
    for table, (kind, short, namecol, codecol, _legacy_parents) in TABLES.items():
        available = current_columns(conn, table)
        required = {"Oid", namecol, "GCRecord"}
        if not required.issubset(available):
            raise RuntimeError(f"PMS table dbo.{table} has missing mandatory columns: {sorted(required-available)}")
        chosen = ["Oid", namecol] + ([codecol] if codecol in available else [])
        sql = "SELECT " + ",".join(f"[{x}]" for x in chosen) + f" FROM dbo.[{table}] WHERE [GCRecord] IS NULL"
        rows = []
        for item in conn.execute(text(sql)).mappings():
            oid = source_id(item["Oid"])
            name = clean(item[namecol])
            if not name:
                warnings.append(f"{table}/{oid}: missing name; skipped")
                continue
            record = {"id": oid, "name": name, "sourceTable": table, "kind": kind,
                      "parents": {}}
            if codecol in chosen:
                record["legacyCode"] = clean(item[codecol])
            rows.append(record)
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"Duplicate active source Oid in dbo.{table}")
        source[table] = rows
    return source, warnings


def plan(source):
    """Import flat source records for reconciliation, never HRIS relationships.

    Each source Location and Section/Unit gets its own independent JCF UUID.
    Even same-name Sections/Units are retained as separate records until
    manually reconciled; the source hierarchy is not authoritative.
    """
    locations = []
    sections = []
    issues = []
    for table in ("Portfolios", "Areas", "Branches", "Divisions", "Stations"):
        for record in source[table]:
            locations.append({**record,
                              "targetCode": loc_code(table, record["id"]),
                              "parent": None})
    for table in ("Sections", "Units"):
        for record in source[table]:
            sections.append({**record,
                             "definitionCode": section_code(record["name"], table, record["id"]),
                             "targetLocationCode": None})
    counts = Counter((row["kind"], normalized_name(row["name"])) for row in locations)
    counts.update(("SECTION_UNIT", normalized_name(row["name"])) for row in sections)
    for kind_name, count in sorted(counts.items()):
        if count > 1:
            issues.append(f"POTENTIAL_DUPLICATE_NAME {kind_name[0]}: {kind_name[1]} ({count} source records, NOT merged)")
    if locations:
        issues.append("SOURCE_HIERARCHY_UNTRUSTED: import all Locations unlinked for manual review")
    if sections:
        issues.append("SOURCE_SECTION_ASSIGNMENTS_UNTRUSTED: import all Sections/Units unassigned for manual review")
    return locations, sections, issues


def import_correlation(key: str) -> str:
    """Stable, unique, <=80-character SQL Server audit_event.correlation_id.

    Source identities and human-readable import keys can exceed SQL Server's
    NVARCHAR(80) field. Store a deterministic digest as the correlation ID;
    the full source Oid remains available in the ExternalID crosswalk.
    """
    return "pms:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:48]


def change(db, action, payload, key):
    idem="pms-sync-"+hashlib.sha256(key.encode()).hexdigest()[:48]
    correlation=import_correlation(key)
    request=svc.create_request(db,action,payload,REFERENCE,REQUESTER,idem)
    if request.status == "SUBMITTED":
        svc.decide_request(db,request.id,"APPROVED",APPROVER,correlation)
    if request.status == "APPLIED":
        raise RuntimeError(f"Stale applied request but record missing: {key}")
    if request.status != "APPROVED":
        raise RuntimeError(f"Review interrupted/rejected change: {key}")
    return svc.apply_request(db,request.id,APPLIER,correlation)


def external_id(db,kind,oid,canonical):
    row=db.scalar(select(m.ExternalID).where(m.ExternalID.source==SOURCE,
                  m.ExternalID.entity_type==kind,m.ExternalID.source_id==oid))
    if row:
        if row.canonical_id!=canonical:
            raise RuntimeError(f"Crosswalk conflict {kind}/{oid}")
        return
    db.add(m.ExternalID(source=SOURCE,entity_type=kind,source_id=oid,
                        canonical_id=canonical))
    db.commit()


def external_id_pdf(db,source,kind,source_id,canonical):
    row=db.scalar(select(m.ExternalID).where(m.ExternalID.source==source,
                  m.ExternalID.entity_type==kind,m.ExternalID.source_id==source_id))
    if row:
        if row.canonical_id!=canonical:
            raise RuntimeError(f"PDF source crosswalk changed: {source_id}")
        return
    db.add(m.ExternalID(source=source,entity_type=kind,source_id=source_id,
                        canonical_id=canonical))
    db.commit()


def apply(db,locs,sections,issues):
    counters=Counter()
    identifiers={}
    # Stage all Locations first; no false parent links are invented.
    for r in locs:
        record=db.scalar(select(m.Location).where(m.Location.code==r["targetCode"]))
        if record is None:
            lid=change(db,"LOCATION_CREATE",
                       {"code":r["targetCode"],"name":r["name"],
                        "locationType":r["kind"],"validFrom":START},
                       f"location:{r['sourceTable']}:{r['id']}")
            counters["locationsCreated"]+=1
        else:
            lid=record.id
            counters["locationsExisting"]+=1
        identifiers[(r["sourceTable"],r["id"])]=lid
        external_id(db,r["sourceTable"],r["id"],lid)
    # Intentionally do NOT import any old HRIS Location parent/child edges.
    # Existing JCF-EMP corrections and effective-dated relationships remain
    # untouched on repeat imports. HRIS is not the authoritative hierarchy.
    for r in locs:
        if r["parent"] is None: continue
        child=identifiers[(r["sourceTable"],r["id"])]
        parent=identifiers[tuple(r["parent"])]
        edge=db.scalar(select(m.LocationParent).where(
            m.LocationParent.child_id==child,
            m.LocationParent.parent_id==parent))
        if not edge:
            existing=db.scalar(select(m.LocationParent).where(m.LocationParent.child_id==child))
            if existing:
                issues.append(f"TARGET_PARENT_CONFLICT {r['sourceTable']}/{r['id']}: already has a different parent")
                continue
            change(db,"LOCATION_PARENT",{"childId":str(child),
                   "parentId":str(parent),"validFrom":START},
                   f"parent:{r['sourceTable']}:{r['id']}:{r['parent'][1]}")
            counters["parentsCreated"]+=1
    defs={}
    for r in sections:
        code=r["definitionCode"]
        if code not in defs:
            record=db.scalar(select(m.SectionDefinition).where(m.SectionDefinition.code==code))
            if record is None:
                did=change(db,"SECTION_DEFINE",{"code":code,
                      "name":r["name"],"validFrom":START},f"definition:{code}")
                counters["definitionsCreated"]+=1
            else:
                did=record.id
            defs[code]=did
        external_id(db,r["sourceTable"],r["id"],defs[code])
    for r in sections:
        target=r["targetLocationCode"]
        if not target:continue
        loc=db.scalar(select(m.Location).where(m.Location.code==target))
        defin=defs[r["definitionCode"]]
        attached=db.scalar(select(m.LocationSection).where(
            m.LocationSection.location_id==loc.id,
            m.LocationSection.definition_id==defin))
        if attached is None:
            attached_id=change(db,"SECTION_ATTACH",{"locationId":str(loc.id),
                "sectionDefinitionId":str(defin),"validFrom":START},
                f"attach:{target}:{r['definitionCode']}")
            counters["sectionsAssigned"]+=1
        else: attached_id=attached.id
        external_id(db,"ATTACH:"+r["sourceTable"],r["id"],attached_id)
    return dict(counters)


def review_import_plan(locations, sections, issues):
    """Report unresolved imported references without blocking this TEST import."""
    if not locations and not sections:
        raise RuntimeError("PMS source contains no usable reference data")
    duplicates={}
    for row in locations:
        key=(row["kind"],normalized_name(row["name"]))
        duplicates.setdefault(key,[]).append(row["id"])
    for row in sections:
        key=("SECTION_UNIT",normalized_name(row["name"]))
        duplicates.setdefault(key,[]).append(row["id"])
    return {
        "status":"IMPORTED_FOR_RECONCILIATION_NOT_APPROVED",
        "unassignedSectionUnits":sum(1 for s in sections if not s["targetLocationCode"]),
        "locationsWithoutParent":sum(1 for loc in locations if
                                      loc["kind"] not in ("PORTFOLIO","HQ") and not loc["parent"]),
        "potentialDuplicateGroups":sum(len(ids)>1 for ids in duplicates.values()),
        "unresolvedIssues":len(issues),
        "nextStep":"Correct names, duplicates, parents and Section assignments in JCF-EMP.",
    }


def verify(db):
    rows=db.scalars(select(m.ExternalID).where(m.ExternalID.source==SOURCE)).all()
    result=Counter(row.entity_type for row in rows)
    return {"crosswalkRows":len(rows),"bySourceType":dict(result)}


def main():
    p=argparse.ArgumentParser(description="PMS/HRIS reference-table sync to TEST registry (never personnel)")
    p.add_argument("action",choices=["inspect","preview","relationships","apply","verify"])
    p.add_argument("--confirm-test-import",action="store_true")
    p.add_argument("--include-pdf",action="store_true",
                   help="Include source-labelled scanned directory address/phone candidates in TEST only")
    args=p.parse_args()
    check_target()
    destination=config.make_engine()
    try:
        with destination.connect() as conn:
            edition=str(conn.execute(text("SELECT SERVERPROPERTY('Edition')")).scalar_one())
            version=int(conn.execute(text(
                "SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int)")).scalar_one())
            if version != 15 or "Express" not in edition:
                raise RuntimeError("Only SQL Server 2019 Express test database allowed")
        if args.action=="verify":
            with Session(destination) as db: print(json.dumps(verify(db),indent=2))
            return
        origin=source_engine()
        try:
            with origin.connect() as source:
                # SQL Server read-only transaction on the source: SELECT only.
                entries,warnings=extract(source)
                relationship_evidence=None
                if args.action in ("relationships","preview","inspect"):
                    # Source-only: reads eight XAF links and current lookup FKs.
                    # Does NOT copy any HRIS relationships into JCF-EMP.
                    from scripts.pms_relationship_evidence import inspect_relationships
                    relationship_evidence=inspect_relationships(source,entries)
        finally: origin.dispose()
        if args.action=="relationships":
            print(json.dumps(relationship_evidence,indent=2))
            print("READ ONLY: No PMS or JCF-EMP rows changed.")
            return
        locations,sections,issues=plan(entries)
        report={"sourceTables":{k:len(v) for k,v in entries.items()},
                "locations":len(locations),"sectionUnitRows":len(sections),
                "uniqueSectionDefinitions":len({s["definitionCode"] for s in sections}),
                "locationSectionMappings":len({(s["targetLocationCode"],s["definitionCode"])
                                               for s in sections if s["targetLocationCode"]}),
                "unresolvedCount":len(issues)+len(warnings),
                "importPolicy":"FLAT_SOURCE_REFERENCES_ONLY; manual hierarchy, section placement and deduplication",
                "unresolvedSample":(warnings+issues)[:30]}
        if relationship_evidence is not None:
            report["hrisRelationshipEvidence"]=relationship_evidence
        if args.include_pdf:
            from scripts import pms_pdf_directory
            matches,unmatched=pms_pdf_directory.match_candidates(locations)
            report["pdfDirectory"]={"matched":len(matches),"unmatched":unmatched,
                                    "sourceStatus":"SCANNED_UNVERIFIED_TEST_ONLY"}
        if args.action in ("inspect","preview"):
            print(json.dumps(report,indent=2))
            print("READ ONLY: No PMS or JCF-EMP rows changed.")
            return
        if not args.confirm_test_import:
            raise RuntimeError("Run preview first, then apply --confirm-test-import (TEST database only)")
        review=review_import_plan(locations,sections,issues)
        with Session(destination) as db:
            applied=apply(db,locations,sections,issues)
            pdf_report=None
            if args.include_pdf:
                pdf_report=pms_pdf_directory.attach_pdf_candidates(db,locations)
            print(json.dumps({"planned":report,"applied":applied,
                              "scannedPdfImport":pdf_report,
                              "reconciliation":review,
                              "verified":verify(db)},indent=2))
        print("PMS/HRIS source was never modified. JCF-EMP test import used distinct synthetic workflow identities.")
    finally: destination.dispose()


if __name__=="__main__":
    main()
