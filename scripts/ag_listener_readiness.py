"""Non-destructive listener readiness inspection. Does NOT perform an AG failover.

Operator uses production-equivalent credentials and separately schedules a controlled AG
failover/recovery exercise. This script only checks read-write primary/listener connectivity.
"""
import os
from sqlalchemy import text
from jcf_platform.config import make_engine

if os.getenv('DB_AG_ENABLED')!='true':
    raise SystemExit('DB_AG_ENABLED=true required; point DB_HOST at an AG listener (not a replica hostname).')
engine=make_engine()
try:
    with engine.connect() as db:
        name=db.scalar(text('SELECT DB_NAME()'))
        hadr=db.scalar(text("SELECT SERVERPROPERTY('IsHadrEnabled')"))
        primary=db.scalar(text("SELECT sys.fn_hadr_is_primary_replica(DB_NAME())"))
        print({'database':name,'hadrEnabled':int(hadr or 0),'primaryReplica':primary})
        if int(hadr or 0)!=1 or primary not in (1,True):
            raise SystemExit('Listener is not resolving to a writable primary in an AG.')
        print('Listener read-write connectivity: PASS (not failover test)')
finally:engine.dispose()
