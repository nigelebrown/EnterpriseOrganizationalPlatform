"""Evidence from the HRIS lookup parent fields and eight XAF link tables.

Source evidence is NOT the JCF-EMP hierarchy. HRIS contains conflicting and
potentially obsolete relationships; none are applied automatically.

The supplied Personnel Formation Mapping(2).xlsx, dated 2026-10-21, records:
  * seven source formation lookup tables, five of which are Location types
  * eight XAF many-to-many relationship/link tables
  * parent-field versus link-table disagreement, particularly Units and Sections

This module uses only allowlisted organizational tables/columns, no personnel,
and no SQL mutations. It works on HRIS_Copy or HRIS_Duplicate.
"""
from collections import Counter

from sqlalchemy import text

from scripts import pms_registry_sync as sync

# (link table, parent source table, parent FK column, child source table, child FK column)
LINK_TABLES = (
    ("PortfoliosPortfolios_BranchesBranches", "Portfolios", "Portfolios", "Branches", "Branches"),
    ("PortfoliosPortfolios_AreasAreas", "Portfolios", "Portfolios", "Areas", "Areas"),
    ("DivisionsDivisions_BranchesBranches", "Branches", "Branches", "Divisions", "Divisions"),
    ("DivisionsDivisions_AreasAreas", "Areas", "Areas", "Divisions", "Divisions"),
    ("StationsStations_DivisionsDivisions", "Divisions", "Divisions", "Stations", "Stations"),
    ("SectionsSections_DivisionsDivisions", "Divisions", "Divisions", "Sections", "Sections"),
    ("UnitsUnits_SectionsSections", "Sections", "Sections", "Units", "Units"),
    ("UnitsUnits_StationsStations", "Stations", "Stations", "Units", "Units"),
)

# The workbook describes these source-side FK fields. They are only evidence,
# not constraints or parent assignments in the new enterprise registry.
SOURCE_PARENT_FIELDS = (
    ("Branches","PortfolioName","Portfolios"),
    ("Areas","Portfolio","Portfolios"),
    ("Divisions","Branch","Branches"),
    ("Divisions","Area","Areas"),
    ("Stations","Division","Divisions"),
    ("Stations","Area","Areas"),
    ("Sections","Division","Divisions"),
    ("Units","Division","Divisions"),
    ("Units","Station","Stations"),
    ("Units","Section","Sections"),
)
# Unit.Division and Station.Area have no corresponding direct link table in
# the workbook. Keep these separately visible, not mistaken for confirmed edges.
LINK_PAIR = {(parent,child):table for table,parent,_,child,_ in LINK_TABLES}


def read_source_parents(conn, records):
    """Read optional parent-reference columns from all seven source lookups."""
    by_table = {table:{r["id"]:r for r in records.get(table,[])}
                for table in sync.TABLES}
    diagnostics=[]
    fields_by_table={}
    for table,column,parent in SOURCE_PARENT_FIELDS:
        fields_by_table.setdefault(table,[]).append((column,parent))
    edges=[]
    for child_table,fields in fields_by_table.items():
        available=sync.current_columns(conn,child_table)
        readcols=[column for column,_ in fields if column in available]
        if not readcols:
            diagnostics.append({"table":child_table,"missingParentFields":[c for c,_ in fields]})
            continue
        sql="SELECT [Oid],"+",".join("["+x+"]" for x in readcols)+f" FROM dbo.[{child_table}]"
        if "GCRecord" in available:
            sql+=" WHERE [GCRecord] IS NULL"
        result=conn.execute(text(sql))
        for row in result.mappings():
            child_id=sync.source_id(row["Oid"])
            if child_id not in by_table.get(child_table,{}):
                continue
            for column,parent_table in fields:
                if column not in readcols or row[column] is None:
                    continue
                edges.append({
                    "parentTable":parent_table,"parentId":sync.source_id(row[column]),
                    "childTable":child_table,"childId":child_id,
                    "source":"PARENT_COLUMN","sourceName":f"{child_table}.{column}"
                })
        missing=[col for col,_ in fields if col not in readcols]
        if missing:
            diagnostics.append({"table":child_table,"missingParentFields":missing})
    return edges,diagnostics


def read_source_links(conn, records):
    """Inspect eight allowlisted XAF relationship tables and collect actual pairs."""
    by_table={table:{r["id"]:r for r in records.get(table,[])} for table in sync.TABLES}
    status=[]
    edges=[]
    for table,parent_t,parent_c,child_t,child_c in LINK_TABLES:
        columns=sync.current_columns(conn,table)
        if not columns:
            status.append({"table":table,"status":"TABLE_ABSENT","totalEdges":0})
            continue
        if not {parent_c,child_c}.issubset(columns):
            status.append({"table":table,"status":"COLUMNS_MISSING",
                           "missingColumns":sorted({parent_c,child_c}-columns),"totalEdges":0})
            continue
        sql=f"SELECT [{parent_c}], [{child_c}] FROM dbo.[{table}]"
        if "GCRecord" in columns:
            sql+=" WHERE [GCRecord] IS NULL"
        try:
            results=conn.execute(text(sql))
            count=0
            dangling=0
            for row in results.mappings():
                if row[parent_c] is None or row[child_c] is None:
                    continue
                count+=1
                pid=sync.source_id(row[parent_c])
                cid=sync.source_id(row[child_c])
                if pid not in by_table[parent_t] or cid not in by_table[child_t]:
                    dangling+=1
                edges.append({"parentTable":parent_t,"parentId":pid,
                              "childTable":child_t,"childId":cid,
                              "source":"LINK_TABLE","sourceName":table})
            status.append({"table":table,"status":"READ","totalEdges":count,
                           "referencesOutsideActiveLookups":dangling})
        except Exception as exc:
            status.append({"table":table,"status":"QUERY_FAILED",
                           "errorType":type(exc).__name__,"totalEdges":None})
    return edges,status


def summarize(records,column_edges,link_edges,link_status,field_warnings,
              max_examples=30):
    """Classify evidence without installing any organization relationships."""
    lookup={table:{r["id"]:r["name"] for r in rows} for table,rows in records.items()}
    grouped={}
    for edge in column_edges+link_edges:
        key=(edge["parentTable"],edge["parentId"],edge["childTable"],edge["childId"])
        item=grouped.setdefault(key,{"columnSources":set(),"linkSources":set()})
        item["columnSources" if edge["source"]=="PARENT_COLUMN" else "linkSources"].add(edge["sourceName"])
    classes=Counter()
    differences=[]
    orphaned=0
    for (pt,pid,ct,cid),info in sorted(grouped.items()):
        has_col=bool(info["columnSources"])
        has_link=bool(info["linkSources"])
        pair=(pt,ct)
        if pid not in lookup[pt] or cid not in lookup[ct]:
            label="REFERENCES_INACTIVE_OR_MISSING_LOOKUP"
            orphaned+=1
        elif pair not in LINK_PAIR:
            label="COLUMN_ONLY_NO_LINK_TABLE_DEFINED"
        elif has_col and has_link:
            label="AGREEMENT"
        elif has_col:
            label="PARENT_COLUMN_ONLY"
        else:
            label="LINK_TABLE_ONLY"
        classes[label]+=1
        if label!="AGREEMENT" and len(differences)<max_examples:
            differences.append({
                "parentType":pt,"parentName":lookup[pt].get(pid),
                "parentSourceId":pid,
                "childType":ct,"childName":lookup[ct].get(cid),
                "childSourceId":cid,
                "evidence":label,
                "sourceNames":sorted(info["columnSources"]|info["linkSources"]),
            })
    return {
        "sourceLookupTables":{table:len(records[table]) for table in sync.TABLES},
        "sourceLinkTables":list(link_status),
        "parentFieldAvailability":field_warnings,
        "edgeEvidenceCounts":dict(classes),
        "totalDistinctSourcePairs":len(grouped),
        "unresolvedExternalRefs":orphaned,
        "disagreementExamples":differences,
        "note":"Unverified HRIS relation evidence only. No new parent/Section assignments have been applied to JCF-EMP.",
    }


def inspect_relationships(conn, records, max_examples=30):
    column_edges,field_warnings=read_source_parents(conn,records)
    link_edges,status=read_source_links(conn,records)
    return summarize(records,column_edges,link_edges,status,field_warnings,max_examples)
