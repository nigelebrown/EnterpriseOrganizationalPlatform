"""Isolated SQL2019 Express management (development only). No production/AG mutations."""
import os
import re
import sys
import time
from datetime import datetime, timezone


def identifier(raw: str) -> str:
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,90}', raw):
        raise ValueError('Invalid SQL identifier')
    return '[' + raw + ']'


def connect(database='master', timeout=8):
    import pyodbc
    password = os.environ['MSSQL_SA_PASSWORD']
    server = os.environ.get('DB_HOST', 'sqlserver,1433')
    conn = f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={database};UID=sa;PWD={password};Encrypt=yes;TrustServerCertificate=yes;Connection Timeout={timeout};'
    return pyodbc.connect(conn, autocommit=True, timeout=timeout)


def verify_express(conn):
    version, edition = conn.cursor().execute("SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int), CAST(SERVERPROPERTY('Edition') AS nvarchar(200))").fetchone()
    if version != 15 or 'Express' not in edition:
        raise RuntimeError('Local provisioning/backup only supports SQL Server 2019 Express, found ' + str(edition))


def wait():
    for attempt in range(70):
        try:
            with connect(timeout=4) as c:
                verify_express(c)
                print('SQL Server 2019 Express ready')
                return
        except Exception:
            if attempt == 69:
                raise RuntimeError('SQL2019 Express failed readiness check after repeated attempts')
            time.sleep(3)


def provision():
    db = os.environ['DB_NAME']
    db_id = identifier(db)
    app = os.environ['JCF_APP_USER']
    migrator = os.environ['JCF_MIGRATE_USER']
    with connect() as c:
        verify_express(c)
        if not c.cursor().execute('SELECT DB_ID(?)', db).fetchval():
            c.cursor().execute('CREATE DATABASE ' + db_id)
        for user, key in [(app, 'JCF_APP_PASSWORD'), (migrator, 'JCF_MIGRATE_PASSWORD')]:
            identifier(user)
            secret = os.environ[key]
            if not c.cursor().execute('SELECT SUSER_ID(?)', user).fetchval():
                # Parameterized dynamic SQL avoids quoting raw passwords in DDL text.
                sql = 'CREATE LOGIN ' + identifier(user) + ' WITH PASSWORD = ' + "'" + secret.replace("'", "''") + "', CHECK_POLICY = ON"
                c.cursor().execute(sql)
    with connect(database=db) as c:
        for user, role in [(app, 'db_datareader'), (app, 'db_datawriter'), (migrator, 'db_owner')]:
            if not c.cursor().execute('SELECT 1 FROM sys.database_principals WHERE name=?', user).fetchone():
                c.cursor().execute('CREATE USER ' + identifier(user) + ' FOR LOGIN ' + identifier(user))
            membership = c.cursor().execute('SELECT IS_ROLEMEMBER(?,?)', role, user).fetchval()
            if membership != 1:
                c.cursor().execute('ALTER ROLE ' + identifier(role) + ' ADD MEMBER ' + identifier(user))
    print('Isolated development database and users provisioned (existing login passwords preserved)')


def backup():
    db = os.environ['DB_NAME']
    identifier(db)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    location = f'/var/opt/mssql/data/JCFRegistry_{name}.bak'
    with connect() as c:
        verify_express(c)
        if not c.cursor().execute('SELECT DB_ID(?)', db).fetchval():
            raise RuntimeError('Missing database; backup aborted')
        # Local SQL data is on a persistent named volume, so the backup survives container replacement.
        c.cursor().execute('BACKUP DATABASE ' + identifier(db) + ' TO DISK = ? WITH INIT, CHECKSUM', location)
        c.cursor().execute('RESTORE VERIFYONLY FROM DISK = ? WITH CHECKSUM', location)
    print('Verified SQL backup:', location)

if __name__ == '__main__':
    actions = {'wait': wait, 'provision': provision, 'backup': backup}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit('Usage: db_admin.py wait|provision|backup')
    if os.getenv('DB_AG_ENABLED', 'false').lower() == 'true':
        raise SystemExit('Refusing local DB administration against AG')
    actions[sys.argv[1]]()
