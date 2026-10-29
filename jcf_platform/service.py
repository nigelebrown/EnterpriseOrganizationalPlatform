"""Domain commands with explicit, testable invariants.

A structural change is always submitted, independently approved and applied as one
transaction. Dates are half-open; application services recheck relationships before writes.
For SQL Server, the change-application transaction takes a SERIALIZABLE isolation level
and a transaction-scoped application lock; SQLite only simulates these for local tests.
"""
from datetime import date, timedelta
from uuid import UUID, uuid4
from sqlalchemy import select, text, or_, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from . import models as m

class DomainError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

def day(x):
    if isinstance(x, date): return x
    try: return date.fromisoformat(x)
    except (TypeError, ValueError): raise DomainError('INVALID_DATE','Date must be YYYY-MM-DD',400)

def identifier(x):
    try: return UUID(str(x))
    except (ValueError, TypeError): raise DomainError('INVALID_ID','Invalid UUID',400)

def asof(row, at):
    return row.valid_from <= at and (row.valid_to is None or at < row.valid_to)

def overlap(a,b,c,d):
    """Half-open date ranges [a,b) intersect [c,d), where None means infinity."""
    return (d is None or a < d) and (b is None or c < b)

def contained(child_start,child_end,parent_start,parent_end):
    return child_start >= parent_start and (parent_end is None or (child_end is not None and child_end <= parent_end))

def require(condition, code, message, status=422):
    if not condition: raise DomainError(code,message,status)

def get(db:Session, cls, identity, kind=None):
    obj = db.get(cls,identifier(identity))
    if obj is None: raise DomainError('NOT_FOUND',f'{kind or cls.__name__} not found',404)
    return obj

def validate_pair(db, location_id, section_id, effective_on, *, allow_restricted=False):
    on = day(effective_on)
    loc = get(db,m.Location,location_id,'Location')
    sec = get(db,m.LocationSection,section_id,'Section')
    require(loc.id == sec.location_id,'SECTION_NOT_IN_LOCATION','Section does not belong to selected Location')
    require(asof(loc,on) and asof(sec,on),'SELECTION_NOT_ACTIVE','Selection not valid on effective date')
    define = get(db,m.SectionDefinition,sec.definition_id)
    require(asof(define,on),'SELECTION_NOT_ACTIVE','Section definition not valid on effective date')
    require(loc.selectable,'LOCATION_NOT_SELECTABLE','Location cannot be selected')
    require(allow_restricted or (loc.visibility == 'STANDARD' and sec.visibility == 'STANDARD'),
            'SELECTION_UNAVAILABLE','Selected option is not available',404)
    return loc,sec

def names_for(db, cls, key, ids, at):
    if not ids: return {}
    rows = db.scalars(select(cls).where(getattr(cls,key).in_(ids))).all()
    return {getattr(n,key):n.name for n in rows if asof(n,at)}

def location_options(db, at, q='', restricted=False):
    at=day(at)
    locs=db.scalars(select(m.Location).order_by(m.Location.code)).all()
    locs=[x for x in locs if asof(x,at) and x.selectable and (restricted or x.visibility=='STANDARD')]
    names=names_for(db,m.LocationName,'location_id',[x.id for x in locs],at)
    aliases={}
    for alias in db.scalars(select(m.LocationAlias)).all():
        aliases.setdefault(alias.location_id,set()).add(alias.normalized_alias)
    query=q.strip().casefold()
    return [{'locationId':str(l.id),'code':l.code,'name':names[l.id],'type':l.location_type}
            for l in locs if l.id in names and (not query or query in l.code.casefold() or query in names[l.id].casefold()
                                               or any(query in a for a in aliases.get(l.id,set())))]

def section_options(db, location_id, at, restricted=False, search=''):
    at=day(at)
    loc=get(db,m.Location,location_id)
    require(asof(loc,at) and loc.selectable and (restricted or loc.visibility=='STANDARD'),
            'NOT_FOUND','Location not available',404)
    secs=db.scalars(select(m.LocationSection).where(m.LocationSection.location_id==loc.id)).all()
    secs=[s for s in secs if asof(s,at) and (restricted or s.visibility=='STANDARD')]
    defs={d.id:d for d in db.scalars(select(m.SectionDefinition)).all() if asof(d,at)}
    name=names_for(db,m.SectionName,'definition_id',list(defs),at)
    items=[{'sectionId':str(s.id),'sectionDefinitionId':str(s.definition_id),'locationId':str(s.location_id),
             'code':defs[s.definition_id].code,'name':name[s.definition_id],'isDefault':s.is_default}
            for s in sorted(secs,key=lambda s: (not s.is_default,str(s.id))) if s.definition_id in defs and s.definition_id in name]
    if search.strip():
        q=search.strip().casefold()
        aliases={}
        for alias in db.scalars(select(m.SectionAlias)).all():
            aliases.setdefault(alias.definition_id,set()).add(alias.normalized_alias)
        items=[x for x in items if q in x['name'].casefold() or q in x['code'].casefold() or
               any(q in value for value in aliases.get(UUID(x['sectionDefinitionId']),set()))]
    return items

def _node(db, key, kind):
    node=get(db,m.OrgNode,key)
    require(node.kind==kind,'INVALID_NODE_KIND',f'Expected {kind}')
    return node

def _range(data):
    a=day(data.get('validFrom'))
    b=day(data['validTo']) if data.get('validTo') else None
    require(b is None or b > a,'INVALID_INTERVAL','End must be after start')
    return a,b

def _no_overlap(rows,a,b,code='INTERVAL_OVERLAP'):
    require(not any(overlap(a,b,r.valid_from,r.valid_to) for r in rows),code,'Overlapping effective date interval',409)

def _change(db, action, p):
    """Apply approved command. Caller handles transaction, audit, outbox and error rollback."""
    a=action.upper()
    if a=='BATCH_REGISTER':
        rows=p.get('operations',[])
        require(0 < len(rows) <= 500,'BATCH_SIZE','Import must contain 1 to 500 operations',400)
        ids=[]
        # Canonical definitions and Locations first, then mappings; import row order is irrelevant.
        order={'LOCATION_CREATE':0,'SECTION_DEFINE':1,'LOCATION_ALIAS':2,'SECTION_ALIAS':2,'SECTION_ATTACH':3,'SECTION_ATTACH_BY_CODE':3}
        for item in sorted(rows,key=lambda x:order.get(x.get('action'),99)):
            require(item.get('action') in order,
                    'INVALID_BATCH','Batch import is limited to Registry master-data operations',403)
            payload=dict(item['payload'])
            action=item['action']
            if action=='SECTION_ATTACH_BY_CODE':
                loc=db.scalar(select(m.Location).where(m.Location.code==payload.pop('locationCode')))
                defin=db.scalar(select(m.SectionDefinition).where(m.SectionDefinition.code==payload.pop('sectionCode')))
                require(loc is not None and defin is not None,'IMPORT_REFERENCE_MISSING',
                        'Mapping references a missing Location or Section code',422)
                payload['locationId']=str(loc.id);payload['sectionDefinitionId']=str(defin.id)
                action='SECTION_ATTACH'
            ids.append(_change(db,action,payload))
        return ids[0]
    if a=='LOCATION_CREATE':
        start,end=_range(p)
        require(bool(p.get('name','').strip()),'INVALID_NAME','Official name is required')
        require(bool(p.get('code','').strip()),'INVALID_CODE','Location code required')
        require(db.scalar(select(m.Location.id).where(m.Location.code==p['code'])) is None,'DUPLICATE_CODE','Location code exists',409)
        n=m.OrgNode(kind='LOCATION'); db.add(n);db.flush()
        l=m.Location(id=n.id,code=p['code'],location_type=p['locationType'],valid_from=start,valid_to=end,
                     visibility=p.get('visibility','STANDARD'),selectable=bool(p.get('selectable',True)))
        db.add(l); db.add(m.LocationName(location_id=n.id,name=p['name'].strip(),valid_from=start,valid_to=end));db.flush()
        return n.id
    if a=='SECTION_DEFINE':
        start,end=_range(p)
        require(bool(p.get('name','').strip()),'INVALID_NAME','Section name required')
        require(db.scalar(select(m.SectionDefinition.id).where(m.SectionDefinition.code==p['code'])) is None,
                'DUPLICATE_CODE','Section code exists',409)
        d=m.SectionDefinition(code=p['code'],valid_from=start,valid_to=end);db.add(d);db.flush()
        db.add(m.SectionName(definition_id=d.id,name=p['name'].strip(),valid_from=start,valid_to=end));return d.id
    if a=='SECTION_ATTACH':
        start,end=_range(p)
        loc=get(db,m.Location,p['locationId']);definition=get(db,m.SectionDefinition,p['sectionDefinitionId'])
        require(contained(start,end,loc.valid_from,loc.valid_to),'OUTSIDE_LOCATION_DATES','Section exceeds Location lifetime')
        require(contained(start,end,definition.valid_from,definition.valid_to),'OUTSIDE_DEFINITION_DATES','Section exceeds definition lifetime')
        previous=db.scalars(select(m.LocationSection).where(m.LocationSection.location_id==loc.id,
                                           m.LocationSection.definition_id==definition.id)).all()
        _no_overlap(previous,start,end,'DUPLICATE_LOCATION_SECTION')
        if p.get('isDefault',False):
            defaults=db.scalars(select(m.LocationSection).where(m.LocationSection.location_id==loc.id,
                                                   m.LocationSection.is_default==True)).all()
            _no_overlap(defaults,start,end,'DUPLICATE_DEFAULT_SECTION')
        n=m.OrgNode(kind='LOCATION_SECTION');db.add(n);db.flush()
        db.add(m.LocationSection(id=n.id,location_id=loc.id,definition_id=definition.id,
                                  valid_from=start,valid_to=end,is_default=bool(p.get('isDefault',False)),
                                  visibility=p.get('visibility','STANDARD')))
        return n.id
    if a=='SECTION_MOVE':
        """Move an assigned Section/Unit to another Location, retaining its old UUID/history."""
        old=get(db,m.LocationSection,p['sectionId']);at=day(p['effectiveFrom'])
        require(at>old.valid_from and (old.valid_to is None or at<old.valid_to),
                'INVALID_DATE','Section move must occur within its active assignment period')
        require(old.location_id != identifier(p['targetLocationId']),
                'SAME_LOCATION','Choose a different target Location')
        existing_posts=db.scalars(select(m.Posting).where(m.Posting.section_id==old.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in existing_posts),
                'ACTIVE_POSTINGS','Transfer associated personnel postings first',409)
        existing_building=db.scalars(select(m.SectionBuilding).where(m.SectionBuilding.section_id==old.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in existing_building),
                'ACTIVE_OCCUPANCY','Close Section building assignments before moving',409)
        old.valid_to=at
        return _change(db,'SECTION_ATTACH',{
            'locationId':p['targetLocationId'],
            'sectionDefinitionId':str(old.definition_id),
            'validFrom':str(at),'isDefault':old.is_default,
            'visibility':old.visibility})
    if a=='SECTION_DEFINITION_RETIRE':
        """Retire an unused duplicate Section definition; preserve all historical IDs."""
        definition=get(db,m.SectionDefinition,p['sectionDefinitionId'])
        at=day(p['effectiveFrom'])
        require(at>definition.valid_from and (definition.valid_to is None or at<definition.valid_to),
                'INVALID_DATE','Retirement must be within the current definition period')
        mappings=db.scalars(select(m.LocationSection).where(
            m.LocationSection.definition_id==definition.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in mappings),
                'ACTIVE_SECTIONS','Close or move all active Section instances before retiring a duplicate',409)
        definition.valid_to=at
        names=db.scalars(select(m.SectionName).where(m.SectionName.definition_id==definition.id)).all()
        for name in names:
            if asof(name,at): name.valid_to=at
        return definition.id
    if a=='LOCATION_RENAME':
        l=get(db,m.Location,p['locationId']); at=day(p['effectiveFrom'])
        require(asof(l,at),'INVALID_DATE','Location not effective on rename date')
        ns=db.scalars(select(m.LocationName).where(m.LocationName.location_id==l.id)).all()
        current=next((n for n in ns if asof(n,at)),None)
        require(current is not None,'NAME_NOT_FOUND','No official name at date')
        require(at>current.valid_from,'INVALID_DATE','Rename requires later effective date')
        require(not any(n.valid_from>=at and n is not current for n in ns),'FUTURE_NAME_EXISTS','Resolve future names first',409)
        previous_end=current.valid_to;current.valid_to=at
        db.add(m.LocationName(location_id=l.id,name=p['name'].strip(),valid_from=at,valid_to=previous_end));return l.id
    if a=='SECTION_RENAME':
        defin=get(db,m.SectionDefinition,p['sectionDefinitionId']);at=day(p['effectiveFrom'])
        require(asof(defin,at),'INVALID_DATE','Section definition not effective at rename date')
        ns=db.scalars(select(m.SectionName).where(m.SectionName.definition_id==defin.id)).all()
        current=next((n for n in ns if asof(n,at)),None)
        require(current is not None and at>current.valid_from,'INVALID_DATE','Rename requires later effective date')
        require(not any(n.valid_from>=at and n is not current for n in ns),'FUTURE_NAME_EXISTS','Resolve future names first',409)
        prior=current.valid_to;current.valid_to=at
        db.add(m.SectionName(definition_id=defin.id,name=p['name'].strip(),valid_from=at,valid_to=prior))
        return defin.id
    if a=='SECTION_RETIRE':
        sec=get(db,m.LocationSection,p['sectionId']);at=day(p['effectiveFrom'])
        require(at>sec.valid_from and (sec.valid_to is None or at<sec.valid_to),
                'INVALID_DATE','Section retirement outside valid period')
        has_postings=db.scalars(select(m.Posting).where(m.Posting.section_id==sec.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in has_postings),
                'ACTIVE_POSTINGS','Transfer or close affected personnel postings before retirement',409)
        occupancies=db.scalars(select(m.SectionBuilding).where(m.SectionBuilding.section_id==sec.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in occupancies),
                'ACTIVE_OCCUPANCY','Close Section building occupancy before retirement',409)
        sec.valid_to=at
        return sec.id
    if a=='LOCATION_RETIRE':
        loc=get(db,m.Location,p['locationId']);at=day(p['effectiveFrom'])
        require(at>loc.valid_from and (loc.valid_to is None or at<loc.valid_to),
                'INVALID_DATE','Location retirement outside valid period')
        children=db.scalars(select(m.LocationParent)).all()
        require(not any((x.child_id==loc.id or x.parent_id==loc.id) and (x.valid_to is None or x.valid_to>at)
                        for x in children),'ACTIVE_HIERARCHY','Close affected parent relationships before retirement',409)
        secs=db.scalars(select(m.LocationSection).where(m.LocationSection.location_id==loc.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in secs),
                'ACTIVE_SECTIONS','Close or transfer Section/Units before retirement',409)
        posts=db.scalars(select(m.Posting).where(m.Posting.location_id==loc.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in posts),
                'ACTIVE_POSTINGS','Transfer all posted personnel before retirement',409)
        occupancies=db.scalars(select(m.LocationBuilding).where(m.LocationBuilding.location_id==loc.id)).all()
        require(not any(x.valid_to is None or x.valid_to>at for x in occupancies),
                'ACTIVE_OCCUPANCY','Close location building occupancy before retirement',409)
        loc.valid_to=at
        names=db.scalars(select(m.LocationName).where(m.LocationName.location_id==loc.id)).all()
        for n in names:
            if asof(n,at):n.valid_to=at
        return loc.id
    if a=='LOCATION_PARENT_CLOSE':
        edge=get(db,m.LocationParent,p['relationshipId']);at=day(p['effectiveFrom'])
        require(at>edge.valid_from and (edge.valid_to is None or at<edge.valid_to),
                'INVALID_DATE','Closing date must follow start and precede current end')
        edge.valid_to=at
        return edge.id
    if a=='LOCATION_PARENT_REASSIGN':
        """Close the current effective parent and assign a new parent atomically."""
        child=get(db,m.Location,p['childId']);at=day(p['effectiveFrom'])
        previous=db.scalars(select(m.LocationParent).where(
            m.LocationParent.child_id==child.id)).all()
        active=[x for x in previous if asof(x,at)]
        require(len(active)<=1,'MULTIPLE_PRIMARY_PARENTS','Existing hierarchy needs review',409)
        if active:
            edge=active[0]
            require(at>edge.valid_from,'INVALID_DATE',
                    'Effective date must be later than existing parent start')
            require(edge.parent_id!=identifier(p['parentId']),
                    'SAME_PARENT','Select a different parent')
            edge.valid_to=at
        return _change(db,'LOCATION_PARENT',{'childId':str(child.id),
                                              'parentId':p['parentId'],'validFrom':str(at)})
    if a=='LOCATION_PARENT':
        start,end=_range(p)
        child=get(db,m.Location,p['childId']);parent=get(db,m.Location,p['parentId'])
        require(child.id!=parent.id,'PARENT_CYCLE','Cannot parent itself')
        for loc in (child,parent):
            require(contained(start,end,loc.valid_from,loc.valid_to),'INVALID_INTERVAL','Parent outside lifetime')
        parents=db.scalars(select(m.LocationParent)).all()
        _no_overlap([x for x in parents if x.child_id==child.id],start,end,'MULTIPLE_PRIMARY_PARENTS')
        # Evaluate entire overlapped time topology at every change boundary, not just at new start.
        boundaries={start}
        if end: boundaries.add(end-timedelta(days=1))
        for edge in parents:
            if overlap(start,end,edge.valid_from,edge.valid_to):
                boundaries.add(max(start,edge.valid_from))
                if edge.valid_to and edge.valid_to>start:
                    boundaries.add(edge.valid_to-timedelta(days=1))
        for at in boundaries:
            if at<start or (end and at>=end):continue
            cursor=parent.id;seen=set()
            while cursor not in seen:
                require(cursor!=child.id,'PARENT_CYCLE','Parent would create hierarchical cycle',409)
                seen.add(cursor)
                active=next((x for x in parents if x.child_id==cursor and asof(x,at)),None)
                if active is None:break
                cursor=active.parent_id
        row=m.LocationParent(child_id=child.id,parent_id=parent.id,valid_from=start,valid_to=end)
        db.add(row);db.flush();return row.id
    if a=='REPORTING_EDGE':
        start,end=_range(p)
        src=get(db,m.OrgNode,p['sourceId']);target=get(db,m.OrgNode,p['authorityId'])
        require(src.id != target.id,'REPORTING_CYCLE','Cannot report to itself')
        require(p['relationType'] in ('LOCAL','FUNCTIONAL','OPERATIONAL','ADMINISTRATIVE'),
                'INVALID_TYPE','Reporting type invalid')
        existing=db.scalars(select(m.ReportingEdge).where(m.ReportingEdge.source_id==src.id,
                                   m.ReportingEdge.authority_id==target.id,m.ReportingEdge.relation_type==p['relationType'])).all()
        _no_overlap(existing,start,end,'DUPLICATE_REPORTING')
        # Reject circular reporting within the same relation type, but permit mixed matrix chains.
        edges=db.scalars(select(m.ReportingEdge).where(m.ReportingEdge.relation_type==p['relationType'])).all()
        cursor=[target.id];seen=set()
        while cursor:
            c=cursor.pop()
            require(c!=src.id,'REPORTING_CYCLE','Circular reporting relationship',409)
            if c in seen:continue
            seen.add(c)
            cursor.extend(e.authority_id for e in edges if e.source_id==c and overlap(start,end,e.valid_from,e.valid_to))
        row=m.ReportingEdge(source_id=src.id,authority_id=target.id,relation_type=p['relationType'],
                            scope=p.get('scope',''),valid_from=start,valid_to=end,reference=p['reference'])
        db.add(row);db.flush();return row.id
    if a=='LOCATION_ALIAS':
        loc=get(db,m.Location,p['locationId']);alias=p['alias'].strip()
        require(bool(alias),'INVALID_ALIAS','Alias required')
        row=m.LocationAlias(location_id=loc.id,alias=alias,normalized_alias=alias.casefold());db.add(row);db.flush();return row.id
    if a=='SECTION_ALIAS':
        defin=get(db,m.SectionDefinition,p['definitionId']);alias=p['alias'].strip()
        require(bool(alias),'INVALID_ALIAS','Alias required')
        row=m.SectionAlias(definition_id=defin.id,alias=alias,normalized_alias=alias.casefold());db.add(row);db.flush();return row.id
    if a=='BUILDING_CREATE':
        start,end=_range(p);lat=p.get('latitude');lon=p.get('longitude')
        require((lat is None and lon is None) or
                (lat is not None and lon is not None and -90<=lat<=90 and -180<=lon<=180),
                'INVALID_COORDINATES','Coordinates must be paired and within bounds')
        row=m.Building(code=p['code'],name=p['name'],address=p['address'],parish=p['parish'],
                       latitude=lat,longitude=lon,valid_from=start,valid_to=end)
        db.add(row);db.flush();return row.id
    if a=='LOCATION_BUILDING':
        start,end=_range(p);loc=get(db,m.Location,p['locationId']);building=get(db,m.Building,p['buildingId'])
        for parent in (loc,building):
            require(contained(start,end,parent.valid_from,parent.valid_to),'INVALID_INTERVAL','Occupancy outside parent lifetime')
        existing=db.scalars(select(m.LocationBuilding).where(m.LocationBuilding.location_id==loc.id,
                                          m.LocationBuilding.building_id==building.id)).all()
        _no_overlap(existing,start,end,'DUPLICATE_OCCUPANCY')
        row=m.LocationBuilding(location_id=loc.id,building_id=building.id,valid_from=start,valid_to=end,
                               occupancy_type=p.get('occupancyType','PERMANENT'));db.add(row);db.flush();return row.id
    if a=='SECTION_BUILDING':
        start,end=_range(p);sec=get(db,m.LocationSection,p['sectionId']);lb=get(db,m.LocationBuilding,p['locationBuildingId'])
        require(sec.location_id==lb.location_id,'INVALID_BUILDING_LOCATION','Building occupancy belongs to another Location')
        for parent in (sec,lb):
            require(contained(start,end,parent.valid_from,parent.valid_to),'INVALID_INTERVAL','Occupancy outside parent lifetime')
        existing=db.scalars(select(m.SectionBuilding).where(m.SectionBuilding.section_id==sec.id)).all()
        if p.get('assignmentType','SECONDARY')=='PRIMARY':
            _no_overlap([x for x in existing if x.assignment_type=='PRIMARY'],start,end,'MULTIPLE_PRIMARY_BUILDINGS')
        row=m.SectionBuilding(section_id=sec.id,location_id=sec.location_id,location_building_id=lb.id,
                              valid_from=start,valid_to=end,assignment_type=p.get('assignmentType','SECONDARY'))
        db.add(row);db.flush();return row.id
    if a=='CONTACT_ASSIGN':
        start,end=_range(p)
        kind=p['entityKind']; eid=identifier(p['entityId'])
        known={'LOCATION':m.Location,'LOCATION_SECTION':m.LocationSection,'BUILDING':m.Building,
               'PERSONNEL':m.Personnel}
        require(kind in known,'UNSUPPORTED_ENTITY','Contact entity type not yet supported')
        get(db,known[kind],eid)
        contact=m.Contact(kind=p['kind'],value=p['value'],telephone_system=p.get('telephoneSystem'),
                          visibility=p.get('visibility','INTERNAL'))
        db.add(contact);db.flush()
        db.add(m.ContactAssignment(contact_id=contact.id,entity_kind=kind,entity_id=eid,
                                   purpose=p.get('purpose','OFFICE'),valid_from=start,valid_to=end));return contact.id
    if a=='PERSONNEL_CREATE':
        row=m.Personnel(computer_number=p['computerNumber'],display_name=p['displayName'])
        db.add(row);db.flush();return row.id
    if a=='POSTING_CREATE':
        start,end=_range(p)
        person=get(db,m.Personnel,p['personnelId'])
        validate_pair(db,p['locationId'],p['sectionId'],start,allow_restricted=True)
        loc=get(db,m.Location,p['locationId']);sec=get(db,m.LocationSection,p['sectionId'])
        for parent in (loc,sec):
            require(contained(start,end,parent.valid_from,parent.valid_to),'INVALID_INTERVAL','Posting exceeds Location/Section lifetime')
        primary=bool(p.get('isPrimary',True))
        if primary:
            postings=db.scalars(select(m.Posting).where(m.Posting.personnel_id==person.id,m.Posting.is_primary==True)).all()
            _no_overlap(postings,start,end,'PRIMARY_POSTING_OVERLAP')
        row=m.Posting(personnel_id=person.id,location_id=loc.id,section_id=sec.id,is_primary=primary,
                      valid_from=start,valid_to=end,reference=p['reference'])
        db.add(row);db.flush();return row.id
    if a=='LEAVE_REQUEST':
        person=get(db,m.Personnel,p['personnelId']);start=day(p['startsOn']);end=day(p['endsBefore'])
        require(start<end,'INVALID_INTERVAL','Leave end must follow start')
        row=m.LeaveRequest(personnel_id=person.id,category=p['category'],starts_on=start,ends_before=end)
        db.add(row);db.flush();return row.id
    raise DomainError('UNSUPPORTED_COMMAND','Unsupported change type',400)

def create_request(db,action,payload,reference,actor,idempotency_key=None):
    require(len(reference.strip())>=3,'REFERENCE_REQUIRED','Authority/reference required')
    if idempotency_key:
        require(10<=len(idempotency_key)<=120,'INVALID_IDEMPOTENCY_KEY','Idempotency key length invalid',400)
        previous=db.scalar(select(m.ChangeRequest).where(m.ChangeRequest.idempotency_key==idempotency_key))
        if previous:
            require(previous.action==action and previous.payload==payload and previous.reference==reference
                    and previous.requested_by==actor,'IDEMPOTENCY_CONFLICT',
                    'Idempotency key already used for different change',409)
            return previous
    row=m.ChangeRequest(action=action,payload=payload,reference=reference,requested_by=actor,
                        idempotency_key=idempotency_key)
    db.add(row);db.commit();db.refresh(row);return row

def decide_request(db,request_id,decision,actor,correlation_id):
    if db.bind.dialect.name=='mssql':
        db.connection(execution_options={'isolation_level':'SERIALIZABLE'})
        db.execute(text("EXEC sp_getapplock @Resource=:key, @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=10000"),
                   {'key':'JCF_CHANGE_'+str(request_id)})
    row=get(db,m.ChangeRequest,request_id)
    require(row.status=='SUBMITTED','INVALID_STATE','Request already decided',409)
    require(actor != row.requested_by,'SELF_APPROVAL','Requester cannot approve own change',403)
    require(decision in ('APPROVED','REJECTED'),'INVALID_DECISION','Decision must be APPROVED or REJECTED',400)
    # SQL Server lock guards concurrent decisions and application. Caller additionally selects update lock for MSSQL.
    row.status=decision;row.decided_by=actor;row.decided_at=m.now()
    db.add(m.AuditEvent(actor=actor,action='CHANGE_DECISION',entity_kind='ChangeRequest',entity_id=row.id,
                        outcome=decision,correlation_id=correlation_id,detail={'action':row.action}))
    db.commit();db.refresh(row);return row

def apply_request(db,request_id,actor,correlation_id):
    if db.bind.dialect.name=='mssql':
        db.connection(execution_options={'isolation_level':'SERIALIZABLE'})
    row=get(db,m.ChangeRequest,request_id)
    require(row.status=='APPROVED','NOT_APPROVED','Request must be independently approved',409)
    try:
        # On SQL Server, serializable isolation is configured for mutation transactions in production;
        # use transaction-scoped sp_getapplock on an entity/registry namespace.
        if db.bind.dialect.name=='mssql':
            db.execute(text("EXEC sp_getapplock @Resource='JCF_REGISTRY_MASTERDATA', @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=10000"))
            db.refresh(row)
            require(row.status=='APPROVED','INVALID_STATE','Request has already been applied',409)
        p=dict(row.payload)
        if row.action in ('REPORTING_EDGE','POSTING_CREATE') and 'reference' not in p:p['reference']=row.reference
        entity_id=_change(db,row.action,p)
        db.flush()
        row.status='APPLIED';row.applied_at=m.now()
        db.add(m.AuditEvent(actor=actor,action=row.action,entity_kind='ChangeRequest',entity_id=entity_id,
                            outcome='SUCCEEDED',correlation_id=correlation_id,
                            detail={'changeRequestId':str(row.id),'reference':row.reference}))
        db.add(m.OutboxEvent(kind=row.action,aggregate_id=entity_id,
                             payload={'id':str(entity_id),'action':row.action,'changeRequestId':str(row.id)}))
        db.commit();return entity_id
    except IntegrityError:
        db.rollback()
        raise DomainError('DATABASE_CONSTRAINT','Conflicting value or invalid database relationship',409)
    except Exception:
        db.rollback()
        raise
