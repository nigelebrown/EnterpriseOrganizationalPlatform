"""Fail closed on databases with non-Alembic-managed pre-existing tables."""
from sqlalchemy import inspect, text
from jcf_platform.config import make_engine


def check(engine):
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    if names and 'alembic_version' not in names:
        raise RuntimeError('Non-empty, unversioned database. Refusing automatic baseline; DBA reconciliation required.')
    if 'alembic_version' in names:
        with engine.connect() as c:
            version = c.execute(text('SELECT version_num FROM alembic_version')).scalar()
        if not version:
            raise RuntimeError('Alembic version table is empty; DBA review required')
    print('Database migration preflight OK; tables:', len(names))

if __name__ == '__main__':
    e = make_engine()
    try: check(e)
    finally: e.dispose()
