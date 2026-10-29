"""XAF link table evidence tests based on user-supplied Formation Mapping workbook."""
from scripts import pms_relationship_evidence as evidence


def test_workbook_eight_xaf_relation_tables_and_fk_pairs():
    assert len(evidence.LINK_TABLES)==8
    assert set((p,c) for _,p,_,c,_ in evidence.LINK_TABLES)=={
        ("Portfolios","Branches"),("Portfolios","Areas"),
        ("Branches","Divisions"),("Areas","Divisions"),
        ("Divisions","Stations"),("Divisions","Sections"),
        ("Sections","Units"),("Stations","Units"),
    }
    # The source-only schema additionally contains Unit.Division and
    # Station.Area hints without their own XAF link tables.
    assert ("Units","Division","Divisions") in evidence.SOURCE_PARENT_FIELDS


def test_reconciliation_distinguishes_conflicting_column_and_link_table():
    records={name:[] for name in ("Portfolios","Branches","Areas","Divisions","Stations","Sections","Units")}
    records["Portfolios"]=[{"id":"p1","name":"HQ"},{"id":"p2","name":"Other HQ"}]
    records["Areas"]=[{"id":"a1","name":"Area One"}]
    records["Branches"]=[{"id":"b1","name":"Special Branch"}]
    records["Divisions"]=[{"id":"d1","name":"St James"}]
    records["Sections"]=[{"id":"s1","name":"CIB"}]
    records["Units"]=[{"id":"u1","name":"PIU"}]
    records["Stations"]=[{"id":"t1","name":"Montego Bay"}]
    def edge(pt,pid,ct,cid,source,name):
        return {"parentTable":pt,"parentId":pid,"childTable":ct,
                "childId":cid,"source":source,"sourceName":name}
    source_parents=[
        edge("Portfolios","p1","Areas","a1","PARENT_COLUMN","Areas.Portfolio"),
        edge("Portfolios","p1","Branches","b1","PARENT_COLUMN","Branches.PortfolioName"),
        edge("Divisions","d1","Sections","s1","PARENT_COLUMN","Sections.Division"),
        edge("Divisions","d1","Units","u1","PARENT_COLUMN","Units.Division"),
    ]
    links=[
        edge("Portfolios","p2","Areas","a1","LINK_TABLE","PortfoliosPortfolios_AreasAreas"),
        edge("Portfolios","p1","Branches","b1","LINK_TABLE","PortfoliosPortfolios_BranchesBranches"),
        edge("Divisions","d1","Sections","s1","LINK_TABLE","SectionsSections_DivisionsDivisions"),
    ]
    result=evidence.summarize(records,source_parents,links,[],[])
    assert result["edgeEvidenceCounts"]["AGREEMENT"]==2
    assert result["edgeEvidenceCounts"]["PARENT_COLUMN_ONLY"]==1
    assert result["edgeEvidenceCounts"]["LINK_TABLE_ONLY"]==1
    assert result["edgeEvidenceCounts"]["COLUMN_ONLY_NO_LINK_TABLE_DEFINED"]==1
    assert result["totalDistinctSourcePairs"]==5
    assert result["unresolvedExternalRefs"]==0
    assert any(x["evidence"]=="LINK_TABLE_ONLY" and x["parentName"]=="Other HQ"
               for x in result["disagreementExamples"])
    assert "not" in result["note"].lower()


def test_source_evidence_reports_orphans_without_autolinking():
    records={name:[] for name in ("Portfolios","Branches","Areas","Divisions","Stations","Sections","Units")}
    records["Sections"]=[{"id":"s1","name":"CIB"}]
    edges=[{"parentTable":"Divisions","parentId":"deleted-division",
            "childTable":"Sections","childId":"s1",
            "source":"LINK_TABLE","sourceName":"SectionsSections_DivisionsDivisions"}]
    result=evidence.summarize(records,[],edges,[],[])
    assert result["edgeEvidenceCounts"]["REFERENCES_INACTIVE_OR_MISSING_LOOKUP"]==1
    assert result["unresolvedExternalRefs"]==1
    assert result["disagreementExamples"][0]["parentName"] is None
