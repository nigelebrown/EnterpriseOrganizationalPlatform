"""Compile every SQLAlchemy table/index to SQL Server 2019-compatible T-SQL (not execution)."""
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import mssql
from jcf_platform.models import Base
compiler=mssql.dialect()
for table in Base.metadata.sorted_tables:
    sql=str(CreateTable(table).compile(dialect=compiler))
    assert 'CREATE TABLE' in sql and not 'CREATE TABLE NONE' in sql
    for index in table.indexes:
        assert 'CREATE INDEX' in str(CreateIndex(index).compile(dialect=compiler))
print(f'SQL Server DDL syntax compilation passed for {len(Base.metadata.tables)} tables (not executed).')
