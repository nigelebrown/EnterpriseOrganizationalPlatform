"""Explicit operator-initiated SQL Server 2019 Express integration smoke test.

Requires a brand-new dedicated disposable SQL database named with TEST or DEV;
never runs against AG/production. Not part of offline unit tests. Performs one governed
Location + Section create/read flow with the actual SQL Server dialect and ODBC driver.
"""
import os
from datetime import date
from uuid import uuid4
from sqlalchemy import text
from fastapi.testclient import TestClient
from jcf_platform.config import make_engine
from jcf_platform.main import create_app,_principal
from jcf_platform.models import Base

url=os.getenv('TEST_SQLSERVER_URL','')
if not url.startswith('mssql+pyodbc:'):
    raise SystemExit('Set TEST_SQLSERVER_URL=mssql+pyodbc:///... to a dedicated TEST database')
if os.getenv('ALLOW_DESTRUCTIVE_TEST_DDL')!='YES':
    raise SystemExit('Set ALLOW_DESTRUCTIVE_TEST_DDL=YES after reviewing test database')
if os.getenv('DB_AG_ENABLED','false').lower()=='true':
    raise SystemExit('Refusing to run destructive integration test with DB_AG_ENABLED=true')
engine=make_engine(url)
with engine.connect() as connection:
    database=str(connection.scalar(text('SELECT DB_NAME()')))
    edition=str(connection.scalar(text("SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(200))")))
    major=int(connection.scalar(text("SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int)")))
    if major!=15 or ('TEST' not in database.upper() and 'DEV' not in database.upper()):
        raise SystemExit(f'Refusing test: expected SQL Server 2019 and isolated TEST/DEV DB, got {major=} {database=}')
    print(f'Connected: SQL Server 2019 {edition}, database {database}')
if any(Base.metadata.tables):
    with engine.connect() as connection:
        existing={row[0] for row in connection.execute(text('SELECT name FROM sys.tables'))}
    if existing & set(Base.metadata.tables):
        raise SystemExit('Expected brand-new empty TEST DB. Existing platform tables detected; refusing to overwrite.')
try:
    Base.metadata.create_all(engine)
    app=create_app(url,engine=engine)
    app.state.claims={'sub':'integration-maker','_scopes':{'registry.write','registry.read','registry.approve','registry.apply'}}
    app.dependency_overrides[_principal]=lambda:app.state.claims
    with TestClient(app) as client:
        def applied(action,payload):
            app.state.claims['sub']='integration-maker'
            r=client.post('/api/v1/governance/requests',json={'action':action,'payload':payload,'reference':'INTEGRATION-TEST-2026'})
            assert r.status_code==201,r.text
            cid=r.json()['changeRequestId']
            app.state.claims['sub']='integration-approver'
            r=client.post(f'/api/v1/governance/requests/{cid}/decision',json={'decision':'APPROVED'})
            assert r.status_code==200,r.text
            r=client.post(f'/api/v1/governance/requests/{cid}/apply')
            assert r.status_code==200,r.text
            return r.json()['entityId']
        loc=applied('LOCATION_CREATE',{'code':'TEST-'+uuid4().hex[:7],'name':'Test Division',
                                       'locationType':'DIVISION','validFrom':'2020-01-01'})
        defin=applied('SECTION_DEFINE',{'code':'TESTSEC-'+uuid4().hex[:7],'name':'Test Section','validFrom':'2020-01-01'})
        sec=applied('SECTION_ATTACH',{'locationId':loc,'sectionDefinitionId':defin,'validFrom':'2020-01-01'})
        r=client.post('/api/v1/registry/posting-selections/validate',json={'locationId':loc,'sectionId':sec,'effectiveOn':str(date.today())})
        assert r.status_code==200,r.text
        print('SQL Server 2019 live governed create, association and lookup: PASS')
finally:
    # Only target our isolated test tables; *never* target an existing production DB.
    Base.metadata.drop_all(engine)
    engine.dispose()
