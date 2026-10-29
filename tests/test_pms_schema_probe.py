"""Read-only PMS database comparison and safe import gating tests."""
import os
import pytest

from scripts import probe_pms_schema as probe
from scripts import pms_registry_sync as importer


def test_related_schema_candidate_names_only():
    assert probe.is_candidate("StationAreaMapping")
    assert probe.is_candidate("LocationSections")
    assert probe.is_candidate("PersonnelStationAssignments")
    assert not probe.is_candidate("PayrollTransaction")


def test_db_selector_is_reversible_and_restricted(monkeypatch):
    monkeypatch.setenv("PMS_SOURCE_DB", "HRIS_Duplicate")
    with probe.selected_database("HRIS_Copy"):
        assert os.environ["PMS_SOURCE_DB"] == "HRIS_Copy"
    assert os.environ["PMS_SOURCE_DB"] == "HRIS_Duplicate"
    with pytest.raises(ValueError):
        with probe.selected_database("master"):
            pass
    assert os.environ["PMS_SOURCE_DB"] == "HRIS_Duplicate"


def test_schema_diff_is_metadata_only():
    source = {
        "database": "HRIS_Duplicate",
        "lookupTables": [
            {"table":name,"activeRows":2 if name=="Sections" else 1,
             "missingParentFields":list(spec[4])}
            for name,spec in importer.TABLES.items()
        ],
    }
    other = {
        "database": "HRIS_Copy",
        "lookupTables": [
            {"table":name,"activeRows":12 if name=="Sections" else 20,
             "missingParentFields":[]}
            for name,spec in importer.TABLES.items()
        ],
    }
    comparison=probe.compare_snapshots(source,other)
    assert len(comparison)==7
    assert comparison[0]["HRIS_CopyCount"]==20
    assert comparison[-2]["HRIS_DuplicateCount"]==2
    assert comparison[-2]["HRIS_CopyMissingParentFields"]==[]
    assert all("password" not in str(row).lower() for row in comparison)


def test_zero_section_mapping_is_allowed_for_test_reconciliation():
    locations=[{"kind":"AREA","id":"area-1","name":"Area One","parent":None}]
    sections=[{"targetLocationCode":None,"name":"CIB","id":"section-1"}]
    review=importer.review_import_plan(locations,sections,["UNASSIGNED_SECTION_UNIT"])
    assert review["unassignedSectionUnits"]==1
    assert review["locationsWithoutParent"]==1
    assert review["status"]=="IMPORTED_FOR_RECONCILIATION_NOT_APPROVED"
    with pytest.raises(RuntimeError,match="no usable reference data"):
        importer.review_import_plan([],[],[])
