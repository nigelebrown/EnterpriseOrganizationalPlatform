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


def test_pms_plan_real_table_hierarchy():
    source=example_source()
    locs,units,issues=sync.plan(source)
    assert issues == []
    assert len(locs)==6 and len(units)==3
    by_name={x["name"]:x for x in locs}
    assert by_name["St. James"]["parent"]==["Areas",source["Areas"][0]["id"]]
    assert by_name["Montego Bay"]["parent"]==["Divisions",source["Divisions"][0]["id"]]
    assert by_name["Organized Crime"]["parent"]==["Branches",source["Branches"][0]["id"]]
    assert len({x["definitionCode"] for x in units}) == 2  # reused CIB name
    assert next(x for x in units if x["name"]=="PIU")["targetLocationCode"] == by_name["Montego Bay"]["targetCode"]


def test_parent_conflict_is_flagged_not_guessed():
    source=example_source()
    div=source["Divisions"][0]
    div["parents"]["Branch"]=source["Branches"][0]["id"]
    locs,_,issues=sync.plan(source)
    assert any("AMBIGUOUS_PARENT" in x for x in issues)
    assert next(x for x in locs if x["id"]==div["id"])["parent"] is None


def test_governed_pms_import_writes_crosswalk_without_personnel(app):
    src=example_source()
    locs,units,issues=sync.plan(src)
    with Session(app.state.engine) as db:
        first=sync.apply(db,locs,units,issues)
        assert first["locationsCreated"]==6
        assert first["sectionsAssigned"]==3
        assert db.query(m.Personnel).count()==0
        assert db.query(m.Posting).count()==0
        assert db.query(m.ExternalID).count()==(6+3+3)
        second=sync.apply(db,locs,units,issues)
        assert second.get("locationsCreated",0)==0
        assert db.query(m.Location).count()==6
        assert db.query(m.SectionDefinition).count()==2
        assert db.query(m.LocationSection).count()==3
        assert db.query(m.LocationParent).count()==5


def test_exact_source_ids_stable_and_test_only_target(monkeypatch):
    p=example_source()["Portfolios"][0]
    code=sync.loc_code("Portfolios",p["id"])
    assert code.startswith("PMS-P-") and len(code)<=45
    assert sync.section_code(" CIB ")==sync.section_code("cib")
    monkeypatch.setenv("APP_ENV","production")
    monkeypatch.setenv("DB_AG_ENABLED","true")
    monkeypatch.setenv("DB_HOST","ag-listener,1433")
    monkeypatch.setenv("DB_NAME","JcfEnterpriseRegistry")
    try:sync.check_target()
    except RuntimeError:pass
    else:assert False, "Must refuse any production/AG target"
