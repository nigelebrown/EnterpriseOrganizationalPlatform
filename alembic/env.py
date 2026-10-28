"""Schema migrations run as an explicit administrative deployment step, never at web start."""
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from jcf_platform.config import database_url
from jcf_platform.models import Base
config=context.config
if config.config_file_name:fileConfig(config.config_file_name)
config.set_main_option('sqlalchemy.url',database_url().replace('%','%%'))
target_metadata=Base.metadata

def run_migrations_online():
    connectable=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as conn:
        context.configure(connection=conn,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()

if context.is_offline_mode():
    context.configure(url=database_url(),target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction():context.run_migrations()
else:run_migrations_online()
