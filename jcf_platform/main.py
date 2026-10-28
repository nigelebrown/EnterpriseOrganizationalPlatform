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
from . import config, models as m, service as s

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
  'LOCATION_RENAME':'registry','LOCATION_PARENT':'registry','LOCATION_ALIAS':'registry',
  'LOCATION_RETIRE':'registry','SECTION_RETIRE':'registry','SECTION_RENAME':'registry',
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
    eng=engine or config.make_engine(cfg['database_url'])
    @asynccontextmanager
    async def lifespan(app):
        yield
        eng.dispose()
    app=FastAPI(title='JCF Enterprise Organizational Platform',version='1.0.0',
                description='Authoritative organizational registry, reporting, facilities and governed shared APIs',
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
        if cfg['require_https'] and request.url.scheme!='https':
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
    @app.get('/api/v1/registry/locations/{location_id}/sections',tags=['Registry'])
    def sections(location_id:UUID,asOf:date|None=None,search:str='',claims=Depends(permission('registry.read')),
                 db:Session=Depends(db_session)):
        return {'locationId':str(location_id),'asOf':str(asOf or date.today()),
                'items':s.section_options(db,location_id,asOf or date.today(),
                                          'registry.restricted.read' in claims['_scopes'],search)}
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
        row=s.decide_request(db,request_id,body.decision,claims['sub'],request.state.correlation_id)
        return {'changeRequestId':str(row.id),'status':row.status}
    @app.post('/api/v1/governance/requests/{request_id}/apply',tags=['Governance'])
    def apply(request_id:UUID,request:Request,claims=Depends(_principal),
              db:Session=Depends(db_session)):
        require_action_scope(s.get(db,m.ChangeRequest,request_id).action,'apply',claims)
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
                'redirectUri':os.getenv('ADMIN_REDIRECT_URI','')}
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
