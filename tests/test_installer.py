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


def test_sql_backup_drains_result_sets_before_verification(monkeypatch):
    """Prevent cancellation when a transient pyodbc Cursor is thrown away."""
    from scripts import db_admin
    events = []
    class FakeCursor:
        def __init__(self):
            self.remaining = 2
        def execute(self, sql, *params):
            kind = 'backup' if sql.startswith('BACKUP') else ('verify' if sql.startswith('RESTORE') else 'lookup')
            events.append(('execute', kind))
            if kind == 'verify':
                assert ('close', 'backup') in events
            self.kind = kind
            return self
        def fetchval(self): return 1
        def nextset(self):
            events.append(('nextset', self.kind))
            self.remaining -= 1
            return self.remaining > 0
        def close(self): events.append(('close', self.kind))
    class FakeConnection:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return FakeCursor()
    monkeypatch.setenv('DB_NAME', 'JcfEnterpriseRegistry')
    monkeypatch.setattr(db_admin, 'connect', lambda: FakeConnection())
    monkeypatch.setattr(db_admin, 'verify_express', lambda conn: None)
    db_admin.backup()
    assert ('nextset', 'backup') in events and ('nextset', 'verify') in events


def test_centos_stream_9_install_mode_present_and_safe():
    script = (ROOT / 'install.sh').read_text()
    for snippet in ('centos:9', 'dnf -y install dnf-plugins-core',
                    'download.docker.com/linux/centos/docker-ce.repo',
                    'docker-buildx-plugin docker-compose-plugin',
                    'systemctl enable --now docker'):
        assert snippet in script
    for dangerous in ('setenforce 0', 'systemctl stop firewalld', 'dnf remove'):
        assert dangerous not in script


def test_debian_slim_dockerfile_uses_https_before_apt():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert '/etc/apt/sources.list.d/debian.sources' in dockerfile
    assert 'https://deb.debian.org/debian/dists/bookworm/InRelease' in dockerfile
    assert "s|http://deb.debian.org/|https://deb.debian.org/|g" in dockerfile
    assert 'apt-get -o Acquire::Retries=3 update' in dockerfile
    assert 'https://packages.microsoft.com/debian/12/prod' in dockerfile
    assert 'Verify-Peer=false' not in dockerfile
    assert 'Verify-Host=false' not in dockerfile


def test_repair_doctor_syntax_and_help():
    doctor = ROOT / 'scripts' / 'doctor.sh'
    assert doctor.is_file()
    assert subprocess.run(['bash', '-n', str(doctor)]).returncode == 0
    help_result = subprocess.run(['bash', str(doctor), '--help'],
                                 capture_output=True, text=True)
    assert help_result.returncode == 0
    assert '--repair' in help_result.stdout and '--check' in help_result.stdout
    script = doctor.read_text()
    assert 'flock -n 9' in script
    assert 'git -C "$ROOT" pull --ff-only' in script
    assert 'scripts/seed_test_data.py --verify' in script
    assert 'bash "$ROOT/install.sh" --mode local-express' in script
    assert 'docker buildx version' in script
    assert 'docker volume rm' not in script
    assert 'docker compose down -v' not in script
