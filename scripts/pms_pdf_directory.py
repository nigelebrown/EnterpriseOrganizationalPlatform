"""Source-labelled scanned JCF directory candidate addresses & phones for the test registry.

Never treats scanned contact details as verified current operational information.
Only exact normalized formation-name matches are eligible; ambiguous matches skip.
All writes use the same governed synthetic test workflow as PMS lookups.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select
from jcf_platform import models as m

SOURCE_NAME = "JCF_SCANNED_PDF_2026_10_29"
CATALOG = Path(__file__).parent / "data" / "jcf_pdf_directory_candidates.json"


def normalize(value):
    s=str(value or "").upper().replace("&"," AND ")
    s=re.sub(r"\bSAINT\b", "ST", s)
    s=re.sub(r"[^A-Z0-9 ]+"," ",s)
    s=re.sub(r"\bDIVISION(AL)?\b|\bHQ\b|\bHEADQUARTERS\b","",s)
    s=re.sub(r"\bAREA ONE\b","AREA 1",s)
    s=re.sub(r"\bAREA TWO\b","AREA 2",s)
    s=re.sub(r"\bAREA THREE\b","AREA 3",s)
    s=re.sub(r"\bAREA I\b","AREA 1",s)
    s=re.sub(r"\bAREA II\b","AREA 2",s)
    s=re.sub(r"\bAREA III\b","AREA 3",s)
    return re.sub(r"\s+"," ",s).strip()


def load_candidates():
    data=json.loads(CATALOG.read_text(encoding="utf-8"))
    return data["entries"]


def match_candidates(locations):
    """Return (source entry, location) only when type and name agree uniquely."""
    matched,skipped=[],[]
    for entry in load_candidates():
        targets=[row for row in locations if row["kind"]==entry["type"] and
                 normalize(row["name"])==normalize(entry["locationName"])]
        if len(targets)==1:
            matched.append((entry,targets[0]))
        else:
            skipped.append(f"PDF p.{entry['sourcePage']}: {entry['locationName']} => {len(targets)} matches; not imported")
    return matched,skipped


def attach_pdf_candidates(db,locations):
    from scripts import pms_registry_sync as sync
    matched,unmatched=match_candidates(locations)
    created={"buildings":0,"locationBuildings":0,"contacts":0}
    for candidate,loc in matched:
        locrow=db.scalar(select(m.Location).where(m.Location.code==loc["targetCode"]))
        if locrow is None:
            unmatched.append(f"PDF {candidate['locationName']}: PMS Location not yet imported")
            continue
        source_id=normalize(candidate["address"]+" "+candidate["parish"])
        code="PDF-BLD-"+hashlib.sha256(source_id.encode()).hexdigest()[:30]
        building=db.scalar(select(m.Building).where(m.Building.code==code))
        if not building:
            bid=sync.change(db,"BUILDING_CREATE",{
                "code":code, "name":candidate["buildingName"],
                "address":candidate["address"],"parish":candidate["parish"],
                "validFrom":sync.START}, f"pdf:building:{code}")
            building=db.get(m.Building,bid)
            created["buildings"]+=1
        sync.external_id_pdf(db,SOURCE_NAME,"BUILDING",source_id,building.id)
        assignment=db.scalar(select(m.LocationBuilding).where(
            m.LocationBuilding.location_id==locrow.id,
            m.LocationBuilding.building_id==building.id))
        if assignment is None:
            sync.change(db,"LOCATION_BUILDING",{
                "locationId":str(locrow.id),"buildingId":str(building.id),
                "occupancyType":"SHARED","validFrom":sync.START},
                f"pdf:attach:{locrow.code}:{code}")
            created["locationBuildings"]+=1
        if candidate["phone"]:
            purpose=f"PDF_P{candidate['sourcePage']}_UNVERIFIED"
            existing=db.scalars(select(m.ContactAssignment).where(
                m.ContactAssignment.entity_kind=="BUILDING",
                m.ContactAssignment.entity_id==building.id,
                m.ContactAssignment.purpose==purpose)).all()
            if not any((db.get(m.Contact,x.contact_id) or m.Contact()).value==candidate["phone"] for x in existing):
                sync.change(db,"CONTACT_ASSIGN",{
                    "entityKind":"BUILDING","entityId":str(building.id),
                    "kind":"PHONE","value":candidate["phone"],
                    "purpose":purpose,"visibility":"INTERNAL",
                    "validFrom":sync.START},
                    f"pdf:phone:{code}:{candidate['phone']}")
                created["contacts"]+=1
    return {"matched":len(matched),"skipped":unmatched,"created":created}
