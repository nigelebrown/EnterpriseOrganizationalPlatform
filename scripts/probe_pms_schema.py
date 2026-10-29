"""Read-only PMS/HRIS formation schema diagnostic; never reads personnel rows.

Used when HRIS_Duplicate / HRIS_Copy formation tables have different columns
and when sections are unassigned because the expected parent references are
missing. No SQL UPDATE/INSERT/DELETE statements, no credentials printed.

Usage (inside the existing JCF-EMP image with root-only env):
  python -m scripts.probe_pms_schema
  python -m scripts.probe_pms_schema --compare HRIS_Copy
"""
from __future__ import annotations
import argparse
import json
import os
import re
from contextlib import contextmanager
from sqlalchemy import text

from scripts import pms_registry_sync as sync

SAFE_DATABASES = {"HRIS_Copy", "HRIS_Duplicate"}
RELATIONSHIP_TERMS = (
    "portfolio", "area", "branch", "division", "station", "section",
    "unit", "formation", "location", "parent",
)

def is_candidate(name: str) -> bool:
    normalized = name.casefold()
    return any(term in normalized for term in RELATIONSHIP_TERMS)


@contextmanager
def selected_database(database: str):
    if database not in SAFE_DATABASES:
        raise ValueError("Schema comparison limited to the two known PMS/HRIS databases")
    previous = os.environ.get("PMS_SOURCE_DB")
    os.environ["PMS_SOURCE_DB"] = database
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PMS_SOURCE_DB", None)
        else:
            os.environ["PMS_SOURCE_DB"] = previous


def foreign_keys(conn, table_name: str):
    """Metadata only. Some HRIS XAF lookup tables may have no FK constraints."""
    return [dict(sourceColumn=str(row[0]), targetTable=str(row[1]),
                 targetColumn=str(row[2]))
            for row in conn.execute(text("""
                SELECT src.name, OBJECT_NAME(fk.referenced_object_id),
                       dest.name
                FROM sys.foreign_key_columns AS fk
                JOIN sys.columns AS src
                  ON src.object_id=fk.parent_object_id
                 AND src.column_id=fk.parent_column_id
                JOIN sys.columns AS dest
                  ON dest.object_id=fk.referenced_object_id
                 AND dest.column_id=fk.referenced_column_id
                WHERE fk.parent_object_id=OBJECT_ID(:source)
                ORDER BY src.name
                """), {"source": "dbo." + table_name})]


def schema_snapshot(conn, database: str) -> dict:
    # INFORMATION_SCHEMA and seven explicit allowlisted lookup tables ONLY.
    metadata = conn.execute(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='dbo'
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """))
    tables: dict[str, list[dict]] = {}
    for row in metadata:
        tables.setdefault(str(row[0]), []).append(
            {"name": str(row[1]), "type": str(row[2])})
    summary = []
    for name, (_, _, name_col, _, parent_cols) in sync.TABLES.items():
        columns = tables.get(name, [])
        actual = {col["name"] for col in columns}
        active_count = None
        count_error = None
        if {"Oid", "GCRecord"}.issubset(actual):
            try:
                # Names from hardcoded sync.TABLES, not from input.
                active_count = int(conn.execute(text(
                    f"SELECT COUNT_BIG(*) FROM dbo.[{name}] WHERE [GCRecord] IS NULL"
                )).scalar_one())
            except Exception as e:
                count_error = type(e).__name__
        else:
            count_error = "MissingOidOrGCRecord"
        try:
            fks = foreign_keys(conn,name) if name in tables else []
        except Exception as e:
            fks = [{"unavailable": type(e).__name__}]
        summary.append({
            "table": name,
            "activeRows": active_count,
            "countError": count_error,
            "nameFieldPresent": name_col in actual,
            "expectedParentFields": list(parent_cols),
            "availableParentFields": [key for key in parent_cols if key in actual],
            "missingParentFields": [key for key in parent_cols if key not in actual],
            "columns": columns,
            "foreignKeys": fks,
        })
    related_tables = [name for name in tables if is_candidate(name) and name not in sync.TABLES]
    return {"database":database, "lookupTables":summary,
            "activeRowsTotal":sum(t["activeRows"] or 0 for t in summary),
            "relationshipCandidateTables": sorted(related_tables)[:60],
            "notes": [
                "Metadata and count queries only; no personnel records or credentials returned.",
                "Missing columns are schema facts, not a reason to invent parent assignments.",
                "HRIS/AD Reset Sections and Stations may be independent lookup lists without formation relationships.",
            ]}


def compare_snapshots(left:dict, right:dict) -> list[dict]:
    lhs={t["table"]:t for t in left["lookupTables"]}
    rhs={t["table"]:t for t in right["lookupTables"]}
    return [{
        "table":table,
        left["database"]+"Count":lhs[table]["activeRows"],
        right["database"]+"Count":rhs[table]["activeRows"],
        left["database"]+"MissingParentFields":lhs[table]["missingParentFields"],
        right["database"]+"MissingParentFields":rhs[table]["missingParentFields"],
    } for table in sync.TABLES]


def read_snapshot(database:str)->dict:
    with selected_database(database):
        engine=sync.source_engine()
        try:
            with engine.connect() as conn:
                return schema_snapshot(conn,database)
        finally:
            engine.dispose()


def main():
    parser=argparse.ArgumentParser(description="Read-only PMS formation schema and metadata comparison")
    parser.add_argument("--compare", choices=["HRIS_Copy","HRIS_Duplicate"])
    args=parser.parse_args()
    sync.check_target() # Verify only the isolated SQL2019 Express dev deployment can run this tool.
    current=os.environ.get("PMS_SOURCE_DB","")
    if current not in SAFE_DATABASES:
        parser.error("PMS source must be HRIS_Duplicate or HRIS_Copy")
    primary=read_snapshot(current)
    if args.compare and args.compare!=current:
        try:
            secondary=read_snapshot(args.compare)
        except Exception as exc:
            # Do not print SQLAlchemy connection strings or credentials.
            primary["comparison"]={"database":args.compare,
                                   "accessible":False,
                                   "errorType":type(exc).__name__,
                                   "message":"Cannot query comparison database; ask DBA to verify access."}
        else:
            primary["comparison"]={"database":args.compare,"accessible":True,
                                   "tableCountsAndParentFields":compare_snapshots(primary,secondary),
                                   "details":secondary}
    print(json.dumps(primary,indent=2))
    print("READ ONLY: No PMS or JCF-EMP rows changed.")


if __name__=="__main__":
    main()
