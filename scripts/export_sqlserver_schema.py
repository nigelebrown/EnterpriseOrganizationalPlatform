"""Export reviewable SQL Server 2019 baseline DDL for DBA review (not run automatically)."""
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.mssql import dialect
from pathlib import Path
from jcf_platform.models import Base
out=Path('dist/SQLServer2019_InitialSchema_Review.sql');out.parent.mkdir(exist_ok=True)
parts=["-- JCF initial schema: GENERATED FOR DBA REVIEW ONLY.\n-- New empty DB only. Use Alembic as the authoritative migration runner.\n-- SQL Server 2019 Express has no Always On AG; perform AG testing on an approved edition.\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;"]
for table in Base.metadata.sorted_tables:
    parts.append(str(CreateTable(table).compile(dialect=dialect()))+';')
    for index in table.indexes:parts.append(str(CreateIndex(index).compile(dialect=dialect()))+';')
parts.append('COMMIT TRANSACTION;')
out.write_text('\n\n'.join(parts)+'\n',encoding='utf-8')
print('Wrote',out,'tables',len(Base.metadata.tables))
