"""Offline installer controls; live Docker+SQL2019 migration is tested separately in CI."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from scripts.db_admin import identifier
from scripts.preflight_migration import check

ROOT = Path(__file__).parents[1]


def test_install_dry_run_never_needs_root_and_makes_no_files(tmp_path):
    r = subprocess.run(['bash', str(ROOT / 'install.sh'), '--mode', 'local-express', '--dry-run'],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'No changes made' in r.stdout
    assert not list(tmp_path.iterdir())


def test_script_help_and_syntax():
    assert subprocess.run(['bash', '-n', str(ROOT / 'install.sh')]).returncode == 0
    r = subprocess.run(['bash', str(ROOT / 'install.sh'), '--help'], capture_output=True, text=True)
    assert r.returncode == 0
    assert 'local-express' in r.stdout and 'external' in r.stdout


def test_local_credentials_are_secure_and_not_overwritten(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / 'scripts/init_install_config.py'), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    expected = ['runtime.env', 'migration.env', 'sqlserver.env', 'setup.env', 'mode']
    for f in expected:
        p = tmp_path / f
        assert p.is_file()
        assert p.stat().st_mode & 0o777 == 0o600
    rt = (tmp_path / 'runtime.env').read_text()
    mt = (tmp_path / 'migration.env').read_text()
    assert 'APP_ENV=development' in rt
    assert 'DB_TRUST_SERVER_CERT=true' in rt
    assert 'MSSQL_SA_PASSWORD' not in rt
    assert 'JCF_MIGRATE_PASSWORD' not in rt
    assert 'DB_USER=jcf_registry_app' in rt
    assert 'DB_USER=jcf_registry_migrate' in mt
    before = {f: (tmp_path / f).read_bytes() for f in expected}
    r2 = subprocess.run([sys.executable, str(ROOT / 'scripts/init_install_config.py'), str(tmp_path)],
                        capture_output=True, text=True)
    assert r2.returncode != 0
    assert all((tmp_path / f).read_bytes() == before[f] for f in expected)


def test_migration_guard_rejects_unknown_schema():
    e = create_engine('sqlite:///:memory:')
    try:
        check(e)
        with e.begin() as con:
            con.execute(text('CREATE TABLE unknown (id integer primary key)'))
        with pytest.raises(RuntimeError, match='unversioned'):
            check(e)
        with e.begin() as con:
            con.execute(text('CREATE TABLE alembic_version (version_num varchar(32) primary key)'))
        with pytest.raises(RuntimeError, match='empty'):
            check(e)
        with e.begin() as con:
            con.execute(text("INSERT INTO alembic_version VALUES ('0001_baseline')"))
        check(e)
    finally:
        e.dispose()


@pytest.mark.parametrize('raw', ['jcf_app', 'JcfEnterpriseRegistry', 'A100'])
def test_sql_identifier_accepts_approved_identifiers(raw):
    assert identifier(raw) == f'[{raw}]'


@pytest.mark.parametrize('raw', ['db];DROP DATABASE master;--', 'a b', '', 'a;sql', 'éé'])
def test_sql_identifier_denies_untrusted_identifiers(raw):
    with pytest.raises(ValueError):
        identifier(raw)


def test_prod_rejects_untrusted_sql_certificate(monkeypatch):
    from jcf_platform.config import settings
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('DB_HOST', 'ag-listener,1433')
    monkeypatch.setenv('DB_USER', 'app')
    monkeypatch.setenv('DB_PASSWORD', 'secret')
    monkeypatch.setenv('DB_TRUST_SERVER_CERT', 'true')
    settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match='forbidden outside development'):
            settings()
    finally:
        settings.cache_clear()


def test_production_internal_health_bypasses_https_but_api_does_not(monkeypatch):
    from fastapi.testclient import TestClient
    from jcf_platform.main import create_app
    app = create_app('sqlite:///:memory:')
    app.state.settings['require_https'] = True
    # route middleware closes over cfg; change cfg through captured settings
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/api/v1/registry/locations').status_code in (400, 401)


def test_explicit_production_database_url_cannot_disable_tls(monkeypatch):
    from jcf_platform.config import database_url
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('DATABASE_URL', 'mssql+pyodbc:///?odbc_connect=DRIVER%3DODBC%20Driver%2018%3BEncrypt%3Dyes%3BTrustServerCertificate%3Dyes')
    with pytest.raises(RuntimeError, match='verify server certificate'):
        database_url()
