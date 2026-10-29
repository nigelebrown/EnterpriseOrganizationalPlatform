"""Initial modular monolith schema.

Revision ID: 0001_baseline
Revises:
Create only; production upgrades are managed through Alembic. This baseline assumes a
new empty database and is explicitly not an overwrite of existing HRIS databases.
"""
from alembic import op
from jcf_platform.models import Base
revision='0001_baseline'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    Base.metadata.create_all(bind=op.get_bind(),checkfirst=False)

def downgrade():
    # Downgrades with data are intentionally forbidden to prevent accidental loss.
    raise RuntimeError('Destructive rollback prohibited. Restore an approved backup instead.')
