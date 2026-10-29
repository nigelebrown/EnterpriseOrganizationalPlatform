"""Tests for PMS->JCF-EMP import: no live HRIS access and no real personnel data."""
from uuid import uuid4
from sqlalchemy.orm import Session
from scripts import pms_registry_sync as sync
from jcf_platform import models as m


def row(name,kind,parents=None,code=""):
    return {"id":str(uuid4()),"name":name,"sourceTable":kind,
            "kind":sync.TABLES[kind][0],"legacyCode":code,
            "parents":parents or {}}


def example_source():
    p=row("Operations Portfolio","Portfolios")
    a=row("Area 1","Areas",{"Portfolio":p["id"]})
    b=row("Special Branch","Branches",{"PortfolioName":p["id"]})
    d=row("St. James","Divisions",{"Area":a["id"]})
    d2=row("Organized Crime","Divisions",{"Branch":b["id"]})
    st=row("Montego Bay","Stations",{"Division":d["id"],"Area":a["id"]})
    s=row("CIB","Sections",{"Division":d["id"]})
    u=row("PIU","Units",{"Station":st["id"],"Division":d["id"]})
    duplicate=row("CIB","Sections",{"Division":d2["id"]})
    return {"Portfolios":[p],"Areas":[a],"Branches":[b],"Divisions":[d,d2],
            "Stations":[st],"Sections":[s,duplicate],"Units":[u]}


def test_pms_plan_does_not_copy_incorrect_hris_hierarchy_or_merge_names():
    source=example_source()
    locs,units,issues=sync.plan(source)
    assert len(locs)==6 and len(units)==3
    assert all(x["parent"] is None for x in locs)
    assert all(x["targetLocationCode"] is None for x in units)
    assert len({x["definitionCode"] for x in units})==3
    assert any("POTENTIAL_DUPLICATE_NAME" in issue for issue in issues)
    assert any("SOURCE_HIERARCHY_UNTRUSTED" in issue for issue in issues)


def test_conflicting_legacy_parents_are_ignored_not_inherited():
    source=example_source()
    div=source["Divisions"][0]
    div["parents"]["Branch"]=source["Branches"][0]["id"]
    locs,sections,issues=sync.plan(source)
    assert all(x["parent"] is None for x in locs)
    assert all(x["targetLocationCode"] is None for x in sections)
    assert any("SOURCE_HIERARCHY_UNTRUSTED" in issue for issue in issues)


def test_governed_pms_import_writes_crosswalk_without_personnel(app):
    src=example_source()
    locs,units,issues=sync.plan(src)
    with Session(app.state.engine) as db:
        first=sync.apply(db,locs,units,issues)
        assert first["locationsCreated"]==6
        assert first.get("sectionsAssigned",0)==0
        assert db.query(m.Personnel).count()==0
        assert db.query(m.Posting).count()==0
        assert db.query(m.ExternalID).count()==(6+3)
        second=sync.apply(db,locs,units,issues)
        assert second.get("locationsCreated",0)==0
        assert db.query(m.Location).count()==6
        assert db.query(m.SectionDefinition).count()==3
        # JCF-EMP generates its OWN UUIDv4 keys; PMS IDs are external references only.
        from uuid import UUID
        for model in (m.Location,m.SectionDefinition,m.LocationSection,m.LocationParent):
            for record in db.query(model).all():
                assert isinstance(record.id,UUID)
                assert record.id.version == 4
        for row in db.query(m.ExternalID).all():
            assert row.canonical_id.version == 4
        assert db.query(m.LocationSection).count()==0
        assert db.query(m.LocationParent).count()==0


def test_exact_source_ids_stable_and_test_only_target(monkeypatch):
    p=example_source()["Portfolios"][0]
    code=sync.loc_code("Portfolios",p["id"])
    assert code.startswith("PMS-P-") and len(code)<=45
    assert sync.section_code(" CIB ")==sync.section_code("cib")
    assert sync.section_code("CIB","Sections","101")!=sync.section_code("CIB","Sections","102")
    monkeypatch.setenv("APP_ENV","production")
    monkeypatch.setenv("DB_AG_ENABLED","true")
    monkeypatch.setenv("DB_HOST","ag-listener,1433")
    monkeypatch.setenv("DB_NAME","JcfEnterpriseRegistry")
    try:sync.check_target()
    except RuntimeError:pass
    else:assert False, "Must refuse any production/AG target"


def test_scanned_directory_addresses_match_only_exact_formation_names():
    from scripts import pms_pdf_directory as pdf
    source=example_source()
    locs,_,_=sync.plan(source)
    matched,skipped=pdf.match_candidates(locs)
    assert len(matched)==2  # Demo source fixture happens to share two official names
    assert len(skipped)==len(pdf.load_candidates())-len(matched)
    assert {x["locationName"] for x,_ in matched}=={"Area One","St James"}
    assert pdf.normalize("St. Andrew Central Divisional Hq.") == pdf.normalize("ST ANDREW CENTRAL")


def test_scanned_directory_import_uses_provenance_and_is_idempotent(app):
    from scripts import pms_pdf_directory as pdf
    src=example_source()
    # Match the user-supplied PDF's high-confidence Portland HQ record.
    p=src["Portfolios"][0]
    src["Divisions"].append(row("Portland","Divisions",{"Branch":src["Branches"][0]["id"]}))
    locs,units,issues=sync.plan(src)
    with Session(app.state.engine) as db:
        sync.apply(db,locs,units,issues)
        first=pdf.attach_pdf_candidates(db,locs)
        assert first["matched"]==3
        assert first["created"]["buildings"]==3
        assert first["created"]["locationBuildings"]==3
        assert first["created"]["contacts"]>=1
        assert db.query(m.Building).count()==3
        again=pdf.attach_pdf_candidates(db,locs)
        assert again["created"]=={"buildings":0,"locationBuildings":0,"contacts":0}
        assert db.query(m.Contact).count()==first["created"]["contacts"]
        assert db.query(m.ExternalID).filter(
            m.ExternalID.source==pdf.SOURCE_NAME).count()==3


def test_integer_pms_oid_does_not_become_destination_primary_key(app):
    from uuid import UUID
    # SQL XAF Oid can be GUID or integer; both must remain opaque source IDs.
    assert sync.source_id(12345) == "12345"
    assert sync.source_id("000045") == "000045"
    src=example_source()
    source_row=src["Portfolios"][0]
    source_row["id"]="12345"
    src["Areas"][0]["parents"]["Portfolio"]="12345"
    src["Branches"][0]["parents"]["PortfolioName"]="12345"
    locs,units,issues=sync.plan(src)
    assert any("SOURCE_HIERARCHY_UNTRUSTED" in x for x in issues)
    with Session(app.state.engine) as db:
        sync.apply(db,locs,units,issues)
        cross=db.query(m.ExternalID).filter(
            m.ExternalID.source==sync.SOURCE,
            m.ExternalID.entity_type=="Portfolios",
            m.ExternalID.source_id=="12345").one()
        assert isinstance(cross.canonical_id, UUID)
        assert cross.canonical_id.version==4
        assert str(cross.canonical_id)!="12345"
        assert db.get(m.Location,cross.canonical_id).id==cross.canonical_id


def test_import_first_without_any_location_section_mapping(app):
    """The actual HRIS_Duplicate shape has flat Sections/Units with no parent references."""
    source=example_source()
    for table in ("Areas","Branches","Divisions","Stations","Sections","Units"):
        for item in source[table]:
            item["parents"]={}
    locations,sections,issues=sync.plan(source)
    review=sync.review_import_plan(locations,sections,issues)
    assert review["status"]=="IMPORTED_FOR_RECONCILIATION_NOT_APPROVED"
    assert review["unassignedSectionUnits"]==len(sections)
    assert review["locationsWithoutParent"]>=4
    with Session(app.state.engine) as db:
        result=sync.apply(db,locations,sections,issues)
        assert result["locationsCreated"]==len(locations)
        assert result["definitionsCreated"]==3
        assert result.get("sectionsAssigned",0)==0
        assert db.query(m.Location).count()==len(locations)
        assert db.query(m.LocationSection).count()==0
        assert db.query(m.SectionDefinition).count()==3
        assert db.query(m.ExternalID).filter(m.ExternalID.source==sync.SOURCE).count()==len(locations)+len(sections)
        repeat=sync.apply(db,locations,sections,issues)
        assert repeat.get("locationsCreated",0)==0
        assert repeat.get("definitionsCreated",0)==0
        assert db.query(m.Location).count()==len(locations)


def test_import_correlation_is_short_and_deterministic_and_sql_audit_compatible(app):
    long_key="parent:Branches:"+str(uuid4())+":"+str(uuid4())+"-additional-source-reference"
    cid=sync.import_correlation(long_key)
    assert len(cid)<=80 and cid==sync.import_correlation(long_key)
    assert cid!=sync.import_correlation(long_key+"x")
    with Session(app.state.engine) as db:
        entity_id=sync.change(db,"LOCATION_CREATE",{
            "code":"PMS-CORR-TEST","name":"Audit correlation test",
            "locationType":"BRANCH","validFrom":"2020-01-01"
        }, long_key)
        assert entity_id
        auditevents=db.query(m.AuditEvent).all()
        assert auditevents
        assert all(len(row.correlation_id)<=80 for row in auditevents)
        assert db.query(m.Location).count()==1


def test_distinct_same_name_section_rows_preserve_uuid_crosswalks(app):
    src=example_source()
    locs,sections,issues=sync.plan(src)
    with Session(app.state.engine) as db:
        sync.apply(db,locs,sections,issues)
        same=[x for x in sections if x["name"]=="CIB"]
        external=[db.query(m.ExternalID).filter(
            m.ExternalID.source==sync.SOURCE,
            m.ExternalID.entity_type==x["sourceTable"],
            m.ExternalID.source_id==x["id"]).one() for x in same]
        assert len({x.canonical_id for x in external})==2
