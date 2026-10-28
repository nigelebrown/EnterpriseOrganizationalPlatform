"""CI-only real SQL2019 Alembic bootstrap/repeat upgrade against disposable TEST DB."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from jcf_platform.models import Base

url = os.getenv('DATABASE_URL', '')
if not url.startswith('mssql+pyodbc:') or os.getenv('CI_EPHEMERAL_DB') != 'YES':
    raise SystemExit('CI database URL and CI_EPHEMERAL_DB=YES required')
e = create_engine(url)
with e.connect() as c:
    name = c.scalar(text('SELECT DB_NAME()'))
    major = c.scalar(text("SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int)"))
    if major != 15 or name != 'JcfEnterpriseMigration_TEST':
        raise SystemExit('Refusing non-isolated or non-SQL2019 CI DB')
config = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
command.upgrade(config, 'head')
assert 'alembic_version' in inspect(e).get_table_names()
assert len(inspect(e).get_table_names()) >= len(Base.metadata.tables)
command.upgrade(config, 'head')
with e.connect() as c:
    assert c.scalar(text('SELECT version_num FROM alembic_version')) == '0001_baseline'
e.dispose()
print('SQL2019 Express live Alembic initial migration and repeat upgrade: PASS')
