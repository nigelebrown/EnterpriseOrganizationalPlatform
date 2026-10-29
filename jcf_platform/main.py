"""REST/OpenAPI host, RBAC, OIDC validation, governance, operational modules and health.

No shared admin password or hard-coded credentials. API clients authenticate with trusted
OIDC access tokens; the static admin SPA uses Authorization Code + PKCE.
"""
import csv, io, json, os, uuid
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from uuid import UUID
import jwt
from jwt import PyJWKClient
from fastapi import FastAPI, Depends, HTTPException, Header, Request, Response, UploadFile, File, Query, Security
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, text, and_, or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from openpyxl import load_workbook
from . import config, models as m, service as s, local_auth

class LocalTestLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)

class SubmitChange(BaseModel):
    action: str = Field(min_length=3, max_length=60)
    payload: dict
    reference: str = Field(min_length=3, max_length=160)
class Decision(BaseModel):
    decision: str
class PostingSelection(BaseModel):
    locationId: UUID
    sectionId: UUID
    effectiveOn: date
class ImportOperation(BaseModel):
    action: str
    payload: dict

bearer_scheme=HTTPBearer(auto_error=False,scheme_name='OIDC_Bearer')

@lru_cache
def jwk_client(url):return PyJWKClient(url,cache_jwk_set=True,lifespan=300)

def _principal(request:Request,credentials:HTTPAuthorizationCredentials|None=Security(bearer_scheme)):
    if credentials is None or credentials.scheme.lower()!='bearer':
        raise HTTPException(status_code=401,detail='Bearer access token required',headers={'WWW-Authenticate':'Bearer'})
    raw=credentials.credentials
    settings=request.app.state.settings
    try:
        algorithm=jwt.get_unverified_header(raw).get('alg')
        if algorithm=='HS256':
            # Separate development-only token trust, impossible in production.
            claims=local_auth.decode_test_token(raw,settings)
        else:
            signing_key=jwk_client(settings['jwks']).get_signing_key_from_jwt(raw).key
            claims=jwt.decode(raw,signing_key,algorithms=['RS256','ES256'],audience=settings['audience'],
                              issuer=settings['issuer'],options={'require':['exp','iss','aud','sub']},leeway=30)
    except Exception:
        raise HTTPException(status_code=401,detail='Invalid access token',headers={'WWW-Authenticate':'Bearer'})
    claim_scope=claims.get('scope',claims.get('scp',[]))
    if isinstance(claim_scope,str):claim_scope=claim_scope.split()
    claims['_scopes']=set(claim_scope or [])
    return claims

ACTION_DOMAIN = {
  'LOCATION_CREATE':'registry','SECTION_DEFINE':'registry','SECTION_ATTACH':'registry',
  'LOCATION_RENAME':'registry','LOCATION_PARENT':'registry','LOCATION_PARENT_REASSIGN':'registry','LOCATION_ALIAS':'registry',
  'LOCATION_RETIRE':'registry','SECTION_RETIRE':'registry','SECTION_MOVE':'registry','SECTION_DEFINITION_RETIRE':'registry','SECTION_RENAME':'registry',
  'LOCATION_PARENT_CLOSE':'registry',
  'SECTION_ALIAS':'registry','BATCH_REGISTER':'registry',
  'REPORTING_EDGE':'reporting',
  'BUILDING_CREATE':'facilities','LOCATION_BUILDING':'facilities','SECTION_BUILDING':'facilities',
  'CONTACT_ASSIGN':'contacts',
  'PERSONNEL_CREATE':'hr','POSTING_CREATE':'hr','LEAVE_REQUEST':'hr',
}

def require_action_scope(action, verb, claims):
    domain=ACTION_DOMAIN.get(action)
    if domain is None:raise s.DomainError('UNSUPPORTED_COMMAND','Unknown or disabled change type',400)
    if f'{domain}.{verb}' not in claims['_scopes']:
        raise s.DomainError('FORBIDDEN','Insufficient permissions for this module',403)
    return domain

def permission(scope):
    def check(claims=Depends(_principal)):
        if scope not in claims['_scopes']:raise HTTPException(status_code=403,detail='Insufficient permission')
        return claims
    return check

def db_session(request:Request):
    db=request.app.state.sessionmaker()
    try:yield db
    finally:db.close()

def problem(status,code,message,correlation):
    return JSONResponse(status_code=status,media_type='application/problem+json',content={
        'type':'about:blank','title':code,'status':status,'detail':message,'code':code,'correlationId':correlation})

def create_app(database_url=None, *, engine=None):
    # Config must fail closed on production startup. Test mode explicitly opt-in via APP_ENV=test.
    cfg=config.settings() if database_url is None else {
        'env':'test','database_url':database_url,'issuer':'','audience':'','jwks':'','cors':[], 'require_https':False}
    local_auth.validate_configuration(cfg)
    eng=engine or config.make_engine(cfg['database_url'])
    @asynccontextmanager
    async def lifespan(app):
        yield
        eng.dispose()
    app=FastAPI(title='JCF Enterprise Management Platform (JCF-EMP)',version='1.0.0',
                description='JCF-EMP: standardized organizational data, integrated management modules and governed enterprise APIs',
                docs_url='/api/docs' if cfg['env']!='production' else None,
                openapi_url='/api/openapi.json',lifespan=lifespan)
    app.state.settings=cfg
    app.state.engine=eng
    app.state.sessionmaker=config.session_factory(eng)
    # Production schemas must be applied through alembic migration, never automatically by a public request.
    if cfg['cors']:app.add_middleware(CORSMiddleware,allow_origins=cfg['cors'],allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type','If-Match','X-Correlation-ID'])

    @app.middleware('http')
    async def headers(request:Request,call_next):
        correlation=request.headers.get('x-correlation-id',str(uuid.uuid4()))[:80]
        request.state.correlation_id=correlation
        if cfg['require_https'] and request.url.scheme!='https' and not (request.url.path in ('/health/live','/health/ready') and request.client and request.client.host in ('127.0.0.1','::1')):
            return problem(400,'HTTPS_REQUIRED','TLS is required',correlation)
        response=await call_next(request)
        response.headers['X-Correlation-ID']=correlation
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        token_origin=''
        oidc_token=os.getenv('ADMIN_OIDC_TOKEN_ENDPOINT','')
        if oidc_token.startswith('https://'):
            u=urlparse(oidc_token);token_origin=f'https://{u.netloc}'
        response.headers['Content-Security-Policy']=("default-src 'self'; connect-src 'self' "+token_origin+"; base-uri 'none'; frame-ancestors 'none'; object-src 'none'")
        if cfg['require_https']:response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
        return response
    @app.exception_handler(s.DomainError)
    async def domain_error(request,ex):
        return problem(ex.status,ex.code,ex.message,request.state.correlation_id)
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,ex):
        return problem(422,'INVALID_REQUEST','Request failed schema validation',request.state.correlation_id)
    @app.exception_handler(HTTPException)
    async def http_error(request,ex):
        return problem(ex.status_code,'HTTP_'+str(ex.status_code),str(ex.detail),request.state.correlation_id)

    @app.get('/health/live',include_in_schema=False)
    def live():return {'status':'alive'}
    @app.get('/health/ready',include_in_schema=False)
    def ready(db:Session=Depends(db_session)):
        try:db.execute(text('SELECT 1'))
        except Exception:return JSONResponse(status_code=503,content={'status':'unready'})
        return {'status':'ready'}

    @app.get('/api/v1/registry/locations',tags=['Registry'])
    def locations(asOf:date|None=None,search:str='',selectable:bool=True,limit:int=Query(100,ge=1,le=200),
                  cursor:int=Query(0,ge=0), claims=Depends(permission('registry.read')),db:Session=Depends(db_session)):
        rows=s.location_options(db,asOf or date.today(),search,'registry.restricted.read' in claims['_scopes'])
        # selectable=false reserved for privileged management retrieval (currently no unselectable entries here).
        return {'asOf':str(asOf or date.today()),'items':rows[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(rows)>cursor+limit else None}
    @app.get('/api/v1/registry/location-hierarchy',tags=['Registry'])
    def location_hierarchy(asOf:date|None=None,limit:int=Query(200,ge=1,le=200),
                           cursor:int=Query(0,ge=0),
                           claims=Depends(permission('registry.read')),
                           db:Session=Depends(db_session)):
        """Effective-dated organizational tree edges for authorized directory consumers.

        Parent IDs are only returned when both ends are visible to the caller.
        Non-selectable administrative formations are included for tree navigation,
        but the existing onboarding Locations endpoint remains selectable-only.
        """
        at=asOf or date.today()
        restricted='registry.restricted.read' in claims['_scopes']
        visible=[loc for loc in db.scalars(select(m.Location).order_by(m.Location.code)).all()
                 if s.asof(loc,at) and (restricted or loc.visibility=='STANDARD')]
        names=s.names_for(db,m.LocationName,'location_id',[loc.id for loc in visible],at)
        by_id={loc.id:loc for loc in visible if loc.id in names}
        edges=db.scalars(select(m.LocationParent)).all()
        parents={edge.child_id:edge.parent_id for edge in edges
                 if s.asof(edge,at) and edge.child_id in by_id and edge.parent_id in by_id}
        items=[{'locationId':str(loc.id),'parentLocationId':str(parents[loc.id]) if loc.id in parents else None,
                'code':loc.code,'name':names[loc.id],'type':loc.location_type,
                'selectable':loc.selectable}
               for loc in visible if loc.id in names]
        return {'asOf':str(at),'items':items[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(items)>cursor+limit else None}

    @app.get('/api/v1/registry/reconciliation',tags=['Registry'])
    def reconciliation(asOf:date|None=None,
                       claims=Depends(permission('registry.read')),
                       db:Session=Depends(db_session)):
        """Imported PMS test records with review flags; never alter or publish them."""
        at=asOf or date.today()
        restricted='registry.restricted.read' in claims['_scopes']
        locs=[l for l in db.scalars(select(m.Location)).all() if s.asof(l,at)
              and (restricted or l.visibility=='STANDARD')]
        defs=[x for x in db.scalars(select(m.SectionDefinition)).all() if s.asof(x,at)]
        loc_by_id={x.id:x for x in locs}
        def_by_id={x.id:x for x in defs}
        loc_names=s.names_for(db,m.LocationName,'location_id',list(loc_by_id),at)
        def_names=s.names_for(db,m.SectionName,'definition_id',list(def_by_id),at)
        parents={x.child_id:x.parent_id for x in db.scalars(select(m.LocationParent)).all()
                 if s.asof(x,at)}
        mappings=[x for x in db.scalars(select(m.LocationSection)).all() if s.asof(x,at)
                  and x.location_id in loc_by_id
                  and (restricted or x.visibility=='STANDARD')]
        sections_attached={x.definition_id for x in mappings}
        imported=db.scalars(select(m.ExternalID).where(m.ExternalID.source=='PMS_HRIS')).all()
        # Reconciliation must not expose restricted formation names or IDs.
        source_locs={x.canonical_id for x in imported if x.entity_type in
                     ('Portfolios','Areas','Branches','Divisions','Stations')}
        source_defs={x.canonical_id for x in imported if x.entity_type in ('Sections','Units')}
        all_names={}
        for loc in locs:
            name=loc_names.get(loc.id)
            if name:
                key=(loc.location_type,' '.join(name.casefold().split()))
                all_names.setdefault(key,[]).append(loc.id)
        items=[]
        for loc in locs:
            if loc.id not in source_locs or loc.id not in loc_names:continue
            name=loc_names[loc.id]
            possible=all_names.get((loc.location_type,' '.join(name.casefold().split())),[])
            issues=[]
            if loc.location_type not in ('PORTFOLIO','HQ') and loc.id not in parents:
                issues.append('PARENT_NOT_ASSIGNED')
            if len(possible)>1:issues.append('POTENTIAL_DUPLICATE_NAME')
            items.append({'entityType':'LOCATION','id':str(loc.id),'name':name,
                          'code':loc.code,'locationType':loc.location_type,
                          'parentLocationId':str(parents[loc.id]) if loc.id in parents else None,
                          'potentialDuplicateIds':[str(i) for i in possible if i!=loc.id],
                          'issues':issues,'status':'IMPORTED_FOR_REVIEW'})
        for defin in defs:
            if defin.id not in source_defs or defin.id not in def_names:continue
            name=def_names[defin.id]
            issues=[]
            if defin.id not in sections_attached:issues.append('NO_LOCATION_ASSIGNMENT')
            items.append({'entityType':'SECTION_UNIT','id':str(defin.id),'name':name,
                          'code':defin.code,'locationType':None,'parentLocationId':None,
                          'potentialDuplicateIds':[],'issues':issues,
                          'status':'IMPORTED_FOR_REVIEW'})
        items.sort(key=lambda row:(row['entityType'],row['name'].casefold(),row['id']))
        totals={'importedLocations':sum(x['entityType']=='LOCATION' for x in items),
                'importedSections':sum(x['entityType']=='SECTION_UNIT' for x in items),
                'recordsWithIssues':sum(bool(x['issues']) for x in items)}
        return {'asOf':str(at),'source':'PMS_HRIS','totals':totals,'items':items,
                'notice':'Test reference data for manual review, not approved master data'}

    @app.get('/api/v1/registry/locations/{location_id}/sections',tags=['Registry'])
    def sections(location_id:UUID,asOf:date|None=None,search:str='',claims=Depends(permission('registry.read')),
                 db:Session=Depends(db_session)):
        return {'locationId':str(location_id),'asOf':str(asOf or date.today()),
                'items':s.section_options(db,location_id,asOf or date.today(),
                                          'registry.restricted.read' in claims['_scopes'],search)}
    @app.get('/api/v1/registry/section-definitions',tags=['Registry'])
    def section_definitions(asOf:date|None=None,search:str='',limit:int=Query(200,ge=1,le=200),
                            cursor:int=Query(0,ge=0),claims=Depends(permission('registry.read')),db:Session=Depends(db_session)):
        at=asOf or date.today()
        definitions=[d for d in db.scalars(select(m.SectionDefinition).order_by(m.SectionDefinition.code)).all() if s.asof(d,at)]
        names=s.names_for(db,m.SectionName,'definition_id',[d.id for d in definitions],at)
        q=search.strip().casefold()
        items=[{'sectionDefinitionId':str(d.id),'code':d.code,'name':names[d.id]}
               for d in definitions if d.id in names and (not q or q in d.code.casefold() or q in names[d.id].casefold())]
        return {'items':items[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(items)>cursor+limit else None}
    @app.get('/api/v1/registry/location-sections',tags=['Registry'])
    def location_sections(asOf:date|None=None,locationId:UUID|None=None,search:str='',
                          limit:int=Query(200,ge=1,le=200),cursor:int=Query(0,ge=0),
                          claims=Depends(permission('registry.read')),db:Session=Depends(db_session)):
        """Read-only, location-scoped section instances for onboarding and directory views.

        Both identifiers are stable UUIDs; the consumer must use sectionId, not
        sectionDefinitionId, when recording a member's placement.
        """
        at=asOf or date.today()
        restricted='registry.restricted.read' in claims['_scopes']
        locs=s.location_options(db,at,'',restricted)
        if locationId is not None:locs=[l for l in locs if l['locationId']==str(locationId)]
        items=[]
        q=search.strip().casefold()
        for loc in locs:
            for sec in s.section_options(db,UUID(loc['locationId']),at,restricted):
                row={**sec,'locationCode':loc['code'],'locationName':loc['name'],
                     'locationType':loc['type']}
                if not q or any(q in str(row[k]).casefold() for k in ('name','code','locationName','locationCode')):
                    items.append(row)
        items.sort(key=lambda x:(x['locationName'].casefold(),x['name'].casefold(),x['sectionId']))
        return {'asOf':str(at),'items':items[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(items)>cursor+limit else None}

    @app.get('/api/v1/registry/org-nodes',tags=['Registry'])
    def org_nodes(asOf:date|None=None,search:str='',limit:int=Query(200,ge=1,le=200),
                  claims=Depends(permission('registry.read')),db:Session=Depends(db_session)):
        at=asOf or date.today();allowed='registry.restricted.read' in claims['_scopes']
        entries=[]
        locs=db.scalars(select(m.Location).order_by(m.Location.code)).all()
        names=s.names_for(db,m.LocationName,'location_id',[x.id for x in locs],at)
        for loc in locs:
            if not s.asof(loc,at) or (not allowed and loc.visibility!='STANDARD'):continue
            if loc.id in names:entries.append({'orgNodeId':str(loc.id),'label':names[loc.id],'kind':'LOCATION'})
            for sec in s.section_options(db,loc.id,at,allowed):
                entries.append({'orgNodeId':sec['sectionId'],'label':sec['name']+' — '+names.get(loc.id,loc.code),
                                'kind':'LOCATION_SECTION'})
        q=search.strip().casefold()
        return {'items':[e for e in entries if not q or q in e['label'].casefold()][:limit]}
    @app.get('/api/v1/facilities/buildings',tags=['Facilities'])
    def buildings(asOf:date|None=None,search:str='',limit:int=Query(200,ge=1,le=200),
                  cursor:int=Query(0,ge=0),claims=Depends(permission('facilities.read')),db:Session=Depends(db_session)):
        at=asOf or date.today();q=search.strip().casefold()
        rows=db.scalars(select(m.Building).order_by(m.Building.code)).all()
        items=[{'buildingId':str(b.id),'name':b.name,'code':b.code} for b in rows
               if s.asof(b,at) and (not q or q in b.name.casefold() or q in b.code.casefold())]
        return {'items':items[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(items)>cursor+limit else None}
    @app.get('/api/v1/facilities/building-occupancies',tags=['Facilities'])
    def building_occupancies(asOf:date|None=None,buildingId:UUID|None=None,
                             locationId:UUID|None=None,search:str='',
                             limit:int=Query(200,ge=1,le=200),cursor:int=Query(0,ge=0),
                             claims=Depends(permission('facilities.read')),
                             registry_claims=Depends(permission('registry.read')),
                             db:Session=Depends(db_session)):
        """Active Building-to-Location occupancy directory; no personnel details."""
        at=asOf or date.today();q=search.strip().casefold()
        allowed={l['locationId']:l for l in s.location_options(
            db,at,'','registry.restricted.read' in registry_claims['_scopes'])}
        rows=[]
        for link in db.scalars(select(m.LocationBuilding)).all():
            if not s.asof(link,at):continue
            if buildingId is not None and link.building_id!=buildingId:continue
            if locationId is not None and link.location_id!=locationId:continue
            loc=allowed.get(str(link.location_id));building=db.get(m.Building,link.building_id)
            if loc is None or building is None or not s.asof(building,at):continue
            row={'locationBuildingId':str(link.id),'locationId':str(link.location_id),
                 'locationName':loc['name'],'locationCode':loc['code'],
                 'buildingId':str(building.id),'buildingName':building.name,
                 'buildingCode':building.code,'address':building.address,
                 'parish':building.parish,'occupancyType':link.occupancy_type}
            if not q or any(q in str(row[k]).casefold() for k in
                            ('locationName','locationCode','buildingName','buildingCode','parish')):
                rows.append(row)
        rows.sort(key=lambda x:(x['buildingName'].casefold(),x['locationName'].casefold()))
        return {'asOf':str(at),'items':rows[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(rows)>cursor+limit else None}

    @app.get('/api/v1/facilities/location-building-links',tags=['Facilities'])
    def location_building_links(locationId:UUID|None=None,claims=Depends(permission('facilities.read')),
                                db:Session=Depends(db_session)):
        links=db.scalars(select(m.LocationBuilding).limit(200)).all()
        return {'items':[{'locationBuildingId':str(link.id),'locationId':str(link.location_id),
                          'buildingId':str(link.building_id)} for link in links if locationId is None or link.location_id==locationId]}
    @app.post('/api/v1/registry/posting-selections/validate',tags=['Registry'])
    def validate(selection:PostingSelection,claims=Depends(permission('registry.read')),db:Session=Depends(db_session)):
        s.validate_pair(db,selection.locationId,selection.sectionId,selection.effectiveOn,
                         allow_restricted='registry.restricted.read' in claims['_scopes'])
        return {'isValid':True,'locationId':str(selection.locationId),'sectionId':str(selection.sectionId),
                'effectiveOn':str(selection.effectiveOn)}
    @app.get('/api/v1/registry/changes',tags=['Integration'])
    def changes(after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200),
                claims=Depends(permission('integration.events.read')),db:Session=Depends(db_session)):
        # Replayable sequence, ordered by insertion timestamp + UUID. Numeric offset is sufficient for small
        # baseline feeds but production persistent cursor must be upgraded to monotonic EventSequence.
        events=db.scalars(select(m.OutboxEvent).where(m.OutboxEvent.sequence>after).order_by(m.OutboxEvent.sequence).limit(limit+1)).all()
        return {'items':[{'id':str(e.id),'kind':e.kind,'aggregateId':str(e.aggregate_id),'at':e.at.isoformat(),
                          'payload':e.payload} for e in events[:limit]],
                'nextCursor':str(events[limit-1].sequence) if len(events)>limit else None}

    @app.get('/api/v1/reporting/relationships',tags=['Reporting'])
    def reporting_relationships(asOf:date|None=None,relationType:str|None=None,
                                limit:int=Query(200,ge=1,le=200),cursor:int=Query(0,ge=0),
                                claims=Depends(permission('reporting.read')),
                                registry_claims=Depends(permission('registry.read')),
                                db:Session=Depends(db_session)):
        """Active typed reporting edges with visible, resolved names for directory display."""
        at=asOf or date.today()
        restricted='registry.restricted.read' in registry_claims['_scopes']
        allowed={}
        for loc in s.location_options(db,at,'',restricted):
            allowed[loc['locationId']]=loc['name']
            for sec in s.section_options(db,UUID(loc['locationId']),at,restricted):
                allowed[sec['sectionId']]=sec['name']+' — '+loc['name']
        rows=[]
        for edge in db.scalars(select(m.ReportingEdge)).all():
            if not s.asof(edge,at):continue
            if relationType is not None and edge.relation_type!=relationType:continue
            source=allowed.get(str(edge.source_id));authority=allowed.get(str(edge.authority_id))
            if source is None or authority is None:continue
            rows.append({'sourceOrgNodeId':str(edge.source_id),'sourceName':source,
                         'authorityOrgNodeId':str(edge.authority_id),'authorityName':authority,
                         'type':edge.relation_type,'scope':edge.scope})
        rows.sort(key=lambda x:(x['sourceName'].casefold(),x['type'],x['authorityName'].casefold()))
        return {'asOf':str(at),'items':rows[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(rows)>cursor+limit else None}

    @app.get('/api/v1/org-nodes/{node_id}/reporting',tags=['Reporting'])
    def reporting(node_id:UUID,asOf:date|None=None,claims=Depends(permission('reporting.read')),
                  db:Session=Depends(db_session)):
        node=s.get(db,m.OrgNode,node_id);at=asOf or date.today()
        entity=db.get(m.Location,node_id) if node.kind=='LOCATION' else db.get(m.LocationSection,node_id)
        if entity and entity.visibility=='RESTRICTED' and 'registry.restricted.read' not in claims['_scopes']:
            raise s.DomainError('NOT_FOUND','Reporting entity not available',404)
        edges=db.scalars(select(m.ReportingEdge).where(m.ReportingEdge.source_id==node_id)).all()
        return {'orgNodeId':str(node_id),'asOf':str(at),'authorities':[
            {'authorityOrgNodeId':str(e.authority_id),'type':e.relation_type,'scope':e.scope}
            for e in edges if s.asof(e,at) and (
              'registry.restricted.read' in claims['_scopes'] or not (
                (db.get(m.Location,e.authority_id) or db.get(m.LocationSection,e.authority_id)) and
                (db.get(m.Location,e.authority_id) or db.get(m.LocationSection,e.authority_id)).visibility=='RESTRICTED'))]}
    @app.get('/api/v1/registry/locations/{location_id}/buildings',tags=['Facilities'])
    def location_buildings(location_id:UUID,asOf:date|None=None,claims=Depends(permission('facilities.read')),
                           db:Session=Depends(db_session)):
        s.get(db,m.Location,location_id);at=asOf or date.today()
        links=db.scalars(select(m.LocationBuilding).where(m.LocationBuilding.location_id==location_id)).all()
        items=[]
        for l in links:
            if not s.asof(l,at):continue
            b=db.get(m.Building,l.building_id)
            if b and s.asof(b,at):items.append({'buildingId':str(b.id),'name':b.name,'address':b.address,
                       'parish':b.parish,'latitude':b.latitude,'longitude':b.longitude,'occupancyType':l.occupancy_type})
        return {'locationId':str(location_id),'items':items}
    @app.get('/api/v1/registry/sections/{section_id}/buildings',tags=['Facilities'])
    def section_buildings(section_id:UUID,asOf:date|None=None,claims=Depends(permission('facilities.read')),
                          db:Session=Depends(db_session)):
        s.get(db,m.LocationSection,section_id);at=asOf or date.today();items=[]
        for a in db.scalars(select(m.SectionBuilding).where(m.SectionBuilding.section_id==section_id)).all():
            if not s.asof(a,at):continue
            link=db.get(m.LocationBuilding,a.location_building_id);b=db.get(m.Building,link.building_id)
            if b and s.asof(b,at):items.append({'buildingId':str(b.id),'name':b.name,'assignmentType':a.assignment_type})
        return {'sectionId':str(section_id),'items':items}
    @app.get('/api/v1/contacts/directory',tags=['Contact Directory'])
    def contact_directory(asOf:date|None=None,entityKind:str|None=None,
                          limit:int=Query(200,ge=1,le=200),cursor:int=Query(0,ge=0),
                          claims=Depends(permission('contacts.read')),
                          registry_claims=Depends(permission('registry.read')),
                          facilities_claims=Depends(permission('facilities.read')),
                          db:Session=Depends(db_session)):
        """Directory-facing contacts only; personnel, guests and offices excluded.

        Independent contact records retain their entity ownership. Consumers must
        use entityKind + entityId to resolve an organizational or physical address.
        """
        at=asOf or date.today()
        kinds={'LOCATION','LOCATION_SECTION','BUILDING'}
        if entityKind and entityKind.upper() not in kinds:
            raise s.DomainError('INVALID_ENTITY_KIND','Unsupported directory entity kind',422)
        restricted='registry.restricted.read' in claims['_scopes']
        visible_locations={x.id for x in db.scalars(select(m.Location)).all()
                           if s.asof(x,at) and (restricted or x.visibility=='STANDARD')}
        visible_sections={x.id for x in db.scalars(select(m.LocationSection)).all()
                          if s.asof(x,at) and x.location_id in visible_locations
                          and (restricted or x.visibility=='STANDARD')}
        visible_buildings={x.id for x in db.scalars(select(m.Building)).all()
                           if s.asof(x,at)}
        allowed={'LOCATION':visible_locations,'LOCATION_SECTION':visible_sections,
                 'BUILDING':visible_buildings}
        rows=[]
        for assignment in db.scalars(select(m.ContactAssignment)).all():
            if assignment.entity_kind not in kinds or not s.asof(assignment,at):continue
            if entityKind and assignment.entity_kind!=entityKind.upper():continue
            if assignment.entity_id not in allowed[assignment.entity_kind]:continue
            contact=db.get(m.Contact,assignment.contact_id)
            if not contact or (contact.visibility=='PERSONAL' and
                               'contacts.personal.read' not in claims['_scopes']):continue
            rows.append({'contactAssignmentId':str(assignment.id),
                         'entityKind':assignment.entity_kind,'entityId':str(assignment.entity_id),
                         'kind':contact.kind,'value':contact.value,'purpose':assignment.purpose,
                         'telephoneSystem':contact.telephone_system})
        rows.sort(key=lambda x:(x['entityKind'],x['entityId'],x['kind'],x['value']))
        return {'asOf':str(at),'items':rows[cursor:cursor+limit],
                'nextCursor':str(cursor+limit) if len(rows)>cursor+limit else None}

    @app.get('/api/v1/contacts',tags=['Contact Directory'])
    def contacts(entityKind:str,entityId:UUID,asOf:date|None=None,claims=Depends(permission('contacts.read')),
                 db:Session=Depends(db_session)):
        at=asOf or date.today();kind=entityKind.upper()
        if kind=='PERSONNEL' and 'contacts.personnel.read' not in claims['_scopes']:
            raise s.DomainError('FORBIDDEN','Personnel contact access required',403)
        owner_class={'LOCATION':m.Location,'LOCATION_SECTION':m.LocationSection}.get(kind)
        if owner_class is not None:
            owner=s.get(db,owner_class,entityId)
            if owner.visibility=='RESTRICTED' and 'registry.restricted.read' not in claims['_scopes']:
                raise s.DomainError('NOT_FOUND','Contact entity not available',404)
        rows=db.scalars(select(m.ContactAssignment).where(m.ContactAssignment.entity_kind==kind,
                             m.ContactAssignment.entity_id==entityId)).all()
        visible=[]
        for r in rows:
            if not s.asof(r,at):continue
            c=db.get(m.Contact,r.contact_id)
            if c and (c.visibility!='PERSONAL' or 'contacts.personal.read' in claims['_scopes']):
                visible.append({'kind':c.kind,'value':c.value,'purpose':r.purpose,'telephoneSystem':c.telephone_system})
        return {'entityKind':kind,'entityId':str(entityId),'items':visible}
    @app.get('/api/v1/hr/personnel/{person_id}/postings',tags=['HR'])
    def postings(person_id:UUID,claims=Depends(permission('hr.postings.read')),db:Session=Depends(db_session)):
        s.get(db,m.Personnel,person_id)
        rows=db.scalars(select(m.Posting).where(m.Posting.personnel_id==person_id).order_by(m.Posting.valid_from)).all()
        return {'personnelId':str(person_id),'items':[{'postingId':str(r.id),'locationId':str(r.location_id),
                'sectionId':str(r.section_id),'isPrimary':r.is_primary,'validFrom':str(r.valid_from),
                'validTo':str(r.valid_to) if r.valid_to else None} for r in rows]}
    @app.get('/api/v1/hr/personnel/{person_id}/leave',tags=['HR'])
    def leave(person_id:UUID,claims=Depends(permission('hr.leave.read')),db:Session=Depends(db_session)):
        s.get(db,m.Personnel,person_id)
        return {'items':[{'id':str(r.id),'category':r.category,'status':r.status,'startsOn':str(r.starts_on),
                'endsBefore':str(r.ends_before)} for r in db.scalars(select(m.LeaveRequest).where(
                 m.LeaveRequest.personnel_id==person_id)).all()]}

    @app.post('/api/v1/governance/requests',status_code=201,tags=['Governance'])
    def submit(body:SubmitChange,idempotency_key:str|None=Header(None,alias='Idempotency-Key'),
               claims=Depends(_principal),db:Session=Depends(db_session)):
        require_action_scope(body.action,'write',claims)
        row=s.create_request(db,body.action,body.payload,body.reference,claims['sub'],idempotency_key)
        return {'changeRequestId':str(row.id),'status':row.status}
    @app.get('/api/v1/governance/requests',tags=['Governance'])
    def requests(status:str|None=None,claims=Depends(_principal),db:Session=Depends(db_session)):
        q=select(m.ChangeRequest).order_by(m.ChangeRequest.created_at.desc())
        if status:q=q.where(m.ChangeRequest.status==status)
        return {'items':[{'id':str(r.id),'action':r.action,'status':r.status,'reference':r.reference,
                'requestedBy':r.requested_by,'decidedBy':r.decided_by,'payload':r.payload}
                for r in db.scalars(q.limit(200)).all() if f"{ACTION_DOMAIN.get(r.action,'unknown')}.approve" in claims['_scopes']]}
    @app.post('/api/v1/governance/requests/{request_id}/decision',tags=['Governance'])
    def decision(request_id:UUID,body:Decision,request:Request,
                 claims=Depends(_principal),db:Session=Depends(db_session)):
        require_action_scope(s.get(db,m.ChangeRequest,request_id).action,'approve',claims)
        # End this read transaction so the domain handler can acquire SERIALIZABLE
        # isolation and its exclusive SQL Server application lock before re-reading.
        db.rollback()
        row=s.decide_request(db,request_id,body.decision,claims['sub'],request.state.correlation_id)
        return {'changeRequestId':str(row.id),'status':row.status}
    @app.post('/api/v1/governance/requests/{request_id}/apply',tags=['Governance'])
    def apply(request_id:UUID,request:Request,claims=Depends(_principal),
              db:Session=Depends(db_session)):
        require_action_scope(s.get(db,m.ChangeRequest,request_id).action,'apply',claims)
        # Recheck the request under the mutation transaction / application lock.
        db.rollback()
        entity_id=s.apply_request(db,request_id,claims['sub'],request.state.correlation_id)
        return {'changeRequestId':str(request_id),'entityId':str(entity_id),'status':'APPLIED'}

    @app.post('/api/v1/import/preview',tags=['Import'])
    async def import_preview(file:UploadFile=File(...),claims=Depends(permission('registry.write'))):
        content=await file.read(2_000_001)
        if len(content)>2_000_000:raise s.DomainError('FILE_TOO_LARGE','Import must be 2 MB or less',413)
        ext=Path(file.filename or '').suffix.lower()
        if ext=='.csv':
            try:rows=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            except UnicodeDecodeError:raise s.DomainError('INVALID_ENCODING','CSV must be UTF-8',400)
        elif ext=='.xlsx':
            try:
                w=load_workbook(io.BytesIO(content),read_only=True,data_only=True)
                sheet=w.active;v=sheet.iter_rows(values_only=True)
                headers=[str(x or '').strip() for x in next(v)]
                rows=[dict(zip(headers,r)) for r in v]
                w.close()
            except Exception:raise s.DomainError('INVALID_EXCEL','Invalid XLSX spreadsheet',400)
        else:raise s.DomainError('INVALID_FORMAT','Only CSV and XLSX accepted',400)
        if len(rows)>500:raise s.DomainError('TOO_MANY_ROWS','Maximum 500 rows per import',400)
        required={'type','validFrom'}
        parsed=[];errors=[]
        for i,row in enumerate(rows,2):
            kind=str(row.get('type') or '').strip().upper()
            if not required.issubset(row) or kind not in ('LOCATION','SECTION','MAPPING'):
                errors.append({'row':i,'error':'Required: type,validFrom; type LOCATION, SECTION or MAPPING'})
                continue
            try:
                start=str(row['validFrom'].date() if hasattr(row['validFrom'],'date') else row['validFrom'])
                s.day(start)
                if kind=='MAPPING':
                    payload={'locationCode':str(row.get('locationCode') or '').strip(),
                             'sectionCode':str(row.get('sectionCode') or '').strip(),'validFrom':start}
                    if not payload['locationCode'] or not payload['sectionCode']:
                        raise ValueError('Missing mapping codes')
                    parsed.append({'action':'SECTION_ATTACH_BY_CODE','payload':payload})
                else:
                    payload={'code':str(row.get('code') or '').strip(),
                             'name':str(row.get('name') or '').strip(),'validFrom':start}
                    if kind=='LOCATION':payload['locationType']=str(row.get('locationType') or 'OTHER')
                    if not payload['code'] or not payload['name']:raise ValueError('Missing code/name')
                    parsed.append({'action':'LOCATION_CREATE' if kind=='LOCATION' else 'SECTION_DEFINE','payload':payload})
            except Exception as e:errors.append({'row':i,'error':'Invalid date/code/name'})
        return {'operations':parsed,'errors':errors,'totalRows':len(rows),
                'instruction':'Submit these operations as a BATCH_REGISTER change request; an independent approver must approve before apply.'}

    @app.get('/api/v1/admin/oidc-config',include_in_schema=False)
    def oidc_config():
        return {'clientId':os.getenv('ADMIN_OIDC_CLIENT_ID',''),'authorizationEndpoint':os.getenv('ADMIN_OIDC_AUTHORIZATION_ENDPOINT',''),
                'tokenEndpoint':os.getenv('ADMIN_OIDC_TOKEN_ENDPOINT',''),'scope':os.getenv('ADMIN_OIDC_SCOPE','openid profile registry.read registry.write registry.approve registry.apply'),
                'redirectUri':os.getenv('ADMIN_REDIRECT_URI',''),
                'localTestEnabled':local_auth.test_configured(cfg)}

    @app.post('/api/v1/admin/local-test-login',include_in_schema=False)
    def local_test_login(body:LocalTestLogin,request:Request):
        # Test identity is explicitly configured on the isolated Express environment.
        # Never return a usable token from production, external SQL, or an AG.
        return local_auth.login(body.username,body.password,cfg,
                                request.client.host if request.client else 'unknown')
    @app.get('/assets/admin.js',include_in_schema=False)
    def admin_js(): return FileResponse(Path(__file__).parent/'admin.js',media_type='application/javascript')
    @app.get('/assets/admin.css',include_in_schema=False)
    def admin_css(): return FileResponse(Path(__file__).parent/'admin.css',media_type='text/css')
    @app.get('/',include_in_schema=False)
    @app.get('/admin',include_in_schema=False)
    def admin():
        return FileResponse(Path(__file__).parent/'admin.html',media_type='text/html',headers={'Cache-Control':'no-store'})
    return app

# Production app imported by uvicorn. Database is never auto-initialized on import.
# For test suites call create_app(url) and explicitly create tables in a test fixture.
app = None

def get_app():
    return create_app()
