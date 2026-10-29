"""Imported PMS correction flow: keep UUIDs, display defects, review governed moves."""
from uuid import UUID, uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select
from jcf_platform import models as m


def getid(r):
    assert r.status_code==200, r.text
    return r.json()["entityId"]


def location(code,name):
    return {"code":code,"name":name,"locationType":"DIVISION","validFrom":"2020-01-01"}


def test_imported_reference_records_are_reviewable_and_correctable(client,app,service_client):
    hq=getid(service_client("LOCATION_CREATE",location("HQ","HQ")))
    other=getid(service_client("LOCATION_CREATE",location("OTHER","Other HQ")))
    a=getid(service_client("LOCATION_CREATE",location("PMS-D-1","Misspelled Dvision")))
    dup=getid(service_client("LOCATION_CREATE",location("PMS-D-2","Misspelled Dvision")))
    section=getid(service_client("SECTION_DEFINE",{
        "code":"PMS-S-CIB","name":"CIB","validFrom":"2020-01-01"}))
    unused=getid(service_client("SECTION_DEFINE",{
        "code":"PMS-S-DUP","name":"CIB","validFrom":"2020-01-01"}))
    with Session(app.state.engine) as db:
        for kind,oid,cid in [
            ("Divisions","n-101",a),("Divisions","n-102",dup),
            ("Sections","sec101",section),("Units","sec102",unused)]:
            db.add(m.ExternalID(source="PMS_HRIS",entity_type=kind,
                source_id=oid,canonical_id=UUID(cid)))
        db.commit()
    initial=client.get("/api/v1/registry/reconciliation")
    assert initial.status_code==200,initial.text
    rows=initial.json()["items"]
    assert len(rows)==4
    assert initial.json()["totals"]["recordsWithIssues"]==4
    assert {"PARENT_NOT_ASSIGNED","POTENTIAL_DUPLICATE_NAME"} <= set(next(
        x for x in rows if x["id"]==a)["issues"])
    assert "NO_LOCATION_ASSIGNMENT" in next(x for x in rows if x["id"]==section)["issues"]
    assert "POTENTIAL_DUPLICATE_NAME" in next(x for x in rows if x["id"]==section)["issues"]
    assert next(x for x in rows if x["id"]==section)["potentialDuplicateIds"]==[unused]
    getid(service_client("LOCATION_RENAME",{"locationId":a,"name":"Division Correct",
                                            "effectiveFrom":"2026-07-01"}))
    getid(service_client("LOCATION_PARENT",{"childId":a,"parentId":hq,
                                           "validFrom":"2026-07-01"}))
    attached=getid(service_client("SECTION_ATTACH",{"locationId":a,"sectionDefinitionId":section,
                                                     "validFrom":"2026-07-01"}))
    after=client.get("/api/v1/registry/reconciliation").json()
    reviewed={x["id"]:x for x in after["items"]}
    assert reviewed[a]["issues"]==[]
    assert reviewed[section]["issues"]==[]
    assert reviewed[dup]["issues"]==["PARENT_NOT_ASSIGNED"]
    getid(service_client("LOCATION_PARENT_REASSIGN",{"childId":a,"parentId":other,
                                                      "effectiveFrom":"2026-08-01"}))
    earlier={x["code"]:x for x in client.get(
        "/api/v1/registry/location-hierarchy?asOf=2026-07-20").json()["items"]}
    later={x["code"]:x for x in client.get(
        "/api/v1/registry/location-hierarchy?asOf=2026-08-20").json()["items"]}
    assert earlier["PMS-D-1"]["parentLocationId"]==hq
    assert later["PMS-D-1"]["parentLocationId"]==other
    new_section=getid(service_client("SECTION_MOVE",{"sectionId":attached,
                                                     "targetLocationId":other,
                                                     "effectiveFrom":"2026-09-01"}))
    assert new_section!=attached
    with Session(app.state.engine) as db:
        old=db.get(m.LocationSection,UUID(attached))
        replacement=db.get(m.LocationSection,UUID(new_section))
        assert str(old.location_id)==a and str(old.valid_to)=="2026-09-01"
        assert str(replacement.location_id)==other
        assert db.query(m.ExternalID).count()==4
    getid(service_client("SECTION_DEFINITION_RETIRE",{
        "sectionDefinitionId":unused,"effectiveFrom":"2026-10-01"}))
    assert unused not in {x["sectionDefinitionId"] for x in client.get(
        "/api/v1/registry/section-definitions").json()["items"]}


def test_reconciliation_is_permission_scoped(client,app,service_client):
    loc=getid(service_client("LOCATION_CREATE",{
        **location("PMS-SECRET","Restricted PMS Location"),"visibility":"RESTRICTED"}))
    with Session(app.state.engine) as db:
        db.add(m.ExternalID(source="PMS_HRIS",entity_type="Divisions",
                            source_id="s001",canonical_id=UUID(loc)))
        db.commit()
    assert client.get("/api/v1/registry/reconciliation").json()["items"]==[]
    app.state.claims["_scopes"].add("registry.restricted.read")
    assert len(client.get("/api/v1/registry/reconciliation").json()["items"])==1
    app.state.claims["_scopes"].discard("registry.read")
    assert client.get("/api/v1/registry/reconciliation").status_code==403


def test_reconciliation_gui_assets(client):
    assert client.get("/assets/reconciliation.js").status_code==200
    html=client.get("/admin").text
    assert 'data-view="reconciliation"' in html
    assert "/assets/reconciliation.js" in html
