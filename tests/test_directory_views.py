"""Read-only registry/reporting/occupancy APIs used by the JCF-EMP GUI and onboarding consumers."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from jcf_platform import models as m
from scripts import seed_test_data as seed


def seed_data(app):
    with Session(app.state.engine) as db:
        count = seed.seed_fixture(db)
        assert count["locations"] == 12


def test_flattened_directory_and_pagination(client, app):
    seed_data(app)
    response = client.get("/api/v1/registry/location-sections")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 16
    assert all(x["locationId"] and x["sectionId"] and x["sectionDefinitionId"] for x in items)
    sac = client.get("/api/v1/registry/location-sections",
                     params={"search": "SAC", "limit": 3})
    assert sac.status_code == 200
    assert len(sac.json()["items"]) == 3
    page1 = client.get("/api/v1/registry/location-sections", params={"limit": 5}).json()
    assert page1["nextCursor"] == "5"
    page2 = client.get("/api/v1/registry/location-sections",
                       params={"limit": 5, "cursor": page1["nextCursor"]}).json()
    assert len(page2["items"]) == 5
    assert {x["sectionId"] for x in page1["items"]}.isdisjoint(
        {x["sectionId"] for x in page2["items"]})
    assert client.get("/api/v1/registry/section-definitions",
                      params={"limit": 4, "cursor": 4}).json()["items"]


def test_building_occupancy_and_reporting_names(client, app):
    seed_data(app)
    response = client.get("/api/v1/facilities/building-occupancies")
    assert response.status_code == 200, response.text
    occupancies = response.json()["items"]
    assert len(occupancies) == 3
    assert all(x["locationName"] and x["buildingName"] for x in occupancies)
    r = client.get("/api/v1/facilities/building-occupancies",
                   params={"search": "SAC", "limit": 1})
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["nextCursor"] == "1"
    reporting = client.get("/api/v1/reporting/relationships")
    assert reporting.status_code == 200
    assert len(reporting.json()["items"]) == 10
    functional = client.get("/api/v1/reporting/relationships",
                            params={"relationType": "FUNCTIONAL"})
    assert len(functional.json()["items"]) == 4
    assert all(x["authorityName"] and x["sourceName"] for x in functional.json()["items"])
    assert len(client.get("/api/v1/facilities/buildings",
                          params={"limit": 1, "cursor": 1}).json()["items"]) == 1


def test_restricted_locations_cannot_leak_through_list_endpoints(client, app):
    seed_data(app)
    with Session(app.state.engine) as db:
        sac = db.scalar(select(m.Location).where(m.Location.code == "DEMO-SAC"))
        sac.visibility = "RESTRICTED"
        db.commit()
    flattened = client.get("/api/v1/registry/location-sections").json()["items"]
    occupancies = client.get("/api/v1/facilities/building-occupancies").json()["items"]
    relationships = client.get("/api/v1/reporting/relationships").json()["items"]
    assert all(x["locationCode"] != "DEMO-SAC" for x in flattened)
    assert all(x["locationCode"] != "DEMO-SAC" for x in occupancies)
    assert all("Saint Andrew Central" not in x["sourceName"]
               and "Saint Andrew Central" not in x["authorityName"]
               for x in relationships)


def test_read_api_scopes_are_enforced(client, app):
    seed_data(app)
    app.state.claims["_scopes"].discard("registry.read")
    assert client.get("/api/v1/registry/location-sections").status_code == 403
    assert client.get("/api/v1/facilities/building-occupancies").status_code == 403
    assert client.get("/api/v1/reporting/relationships").status_code == 403
    app.state.claims["_scopes"].add("registry.read")
    app.state.claims["_scopes"].discard("facilities.read")
    assert client.get("/api/v1/facilities/building-occupancies").status_code == 403


def test_directory_gui_contains_anonymous_synthetic_preview_only(client):
    html = client.get("/admin")
    js = client.get("/assets/admin.js")
    assert html.status_code == js.status_code == 200
    assert 'id="preview"' in html.text
    assert "function sampleSnapshot()" in js.text
    assert "Preview sample" in html.text
    assert "readDirectory()" in js.text
