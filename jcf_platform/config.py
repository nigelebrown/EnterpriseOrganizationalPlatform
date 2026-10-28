"""Configuration, fail-closed identity defaults and database connectivity.

Linux application -> ODBC Driver 18 -> SQL Server 2019 Express (dev) or AG listener (prod).
The driver/pool are configured once; all hostnames/credentials come from environment or secrets.
"""
import os
from functools import lru_cache
from urllib.parse import quote_plus
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

@lru_cache
def settings():
    env = os.getenv('APP_ENV', 'production').lower()
    db = os.getenv('DATABASE_URL', '')
    if not db:
        host = os.getenv('DB_HOST', '')
        name = os.getenv('DB_NAME', 'JcfEnterpriseRegistry')
        uid = os.getenv('DB_USER', '')
        pwd = os.getenv('DB_PASSWORD', '')
        if not host or not uid or not pwd:
            raise RuntimeError('DATABASE_URL or DB_HOST, DB_USER and DB_PASSWORD required')
        driver = os.getenv('DB_ODBC_DRIVER', 'ODBC Driver 18 for SQL Server')
        timeout = os.getenv('DB_CONNECT_TIMEOUT', '10')
        # Encrypt=yes and TrustServerCertificate=no are mandatory in production.
        params = (
            f'DRIVER={{{driver}}};SERVER={host};DATABASE={name};UID={uid};PWD={pwd};'
            f'Encrypt=yes;TrustServerCertificate=no;Connection Timeout={timeout};'
            f'MultiSubnetFailover={"yes" if os.getenv("DB_AG_ENABLED", "false").lower() == "true" else "no"};'
            'ApplicationIntent=ReadWrite;'
        )
        db = 'mssql+pyodbc:///?odbc_connect=' + quote_plus(params)
    auth = {'issuer': os.getenv('AUTH_ISSUER', ''), 'audience': os.getenv('AUTH_AUDIENCE', ''),
            'jwks': os.getenv('AUTH_JWKS_URL', '')}
    if env == 'production' and not db.startswith('mssql+pyodbc:'):
        raise RuntimeError('Production requires SQL Server via mssql+pyodbc')
    if env == 'production' and not all(auth.values()):
        raise RuntimeError('AUTH_ISSUER, AUTH_AUDIENCE and AUTH_JWKS_URL are required in production')
    return {'env': env, 'database_url': db, **auth,
            'cors': [x for x in os.getenv('CORS_ORIGINS', '').split(',') if x],
            'require_https': env == 'production'}

def make_engine(url=None):
    url = url or settings()['database_url']
    args = {'pool_pre_ping': True, 'pool_recycle': 900}
    if url.startswith('sqlite'):
        if ':memory:' in url:
            from sqlalchemy.pool import StaticPool
            args['poolclass']=StaticPool
        args['connect_args'] = {'check_same_thread': False, 'timeout': 30}
    else:
        args['pool_size'] = 10
        args['max_overflow'] = 20
        args['pool_timeout'] = 15
    engine = create_engine(url, **args)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def fk_on(dbapi, _):
            dbapi.execute('PRAGMA foreign_keys=ON')
    return engine

def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
