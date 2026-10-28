"""GitHub-hosted ephemeral SQL Server 2019 Express TEST DB only.

Never use against a real JCF instance. Requires explicit CI test hostname/password flags.
"""
import os,time
import pyodbc
if os.getenv('CI_EPHEMERAL_DB') != 'YES':
    raise SystemExit('Refusing database creation without CI_EPHEMERAL_DB=YES')
server=os.getenv('CI_SQL_HOST','127.0.0.1,1433')
if server not in ('127.0.0.1,1433','localhost,1433'):
    raise SystemExit('Only local ephemeral SQL Server is allowed')
password=os.environ['CI_SQL_SA_PASSWORD']
constr=(f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE=master;'
        f'UID=sa;PWD={password};Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=3')
connection=None
for i in range(90):
    try:
        connection=pyodbc.connect(constr,autocommit=True)
        break
    except pyodbc.Error:
        time.sleep(2)
if connection is None:raise SystemExit('SQL Server 2019 Express did not start within 180 seconds')
try:
    major=connection.cursor().execute("SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int)").fetchone()[0]
    edition=connection.cursor().execute("SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(200))").fetchone()[0]
    if major!=15 or 'Express' not in edition:
        raise SystemExit(f'Wrong SQL Server version/edition: {major} {edition}')
    connection.cursor().execute('CREATE DATABASE JcfEnterprise_TEST')
    connection.cursor().execute('CREATE DATABASE JcfEnterpriseMigration_TEST')
    print('Created ephemeral JcfEnterprise_TEST on SQL Server 2019 Express')
finally:connection.close()
