"""Generate local-development secrets once, never overwriting existing configuration."""
import os
import secrets
import sys
from pathlib import Path


def write_new(path: Path, values: dict[str, str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        for k, v in values.items():
            if not k.isidentifier() or '\n' in v or '\r' in v:
                raise ValueError('Invalid env content')
            f.write(f'{k}={v}\n')
    os.chmod(path, 0o600)


def create_local(config_dir: str):
    root = Path(config_dir)
    required = [root / k for k in ('runtime.env', 'migration.env', 'sqlserver.env', 'setup.env')]
    if any(p.exists() for p in required):
        raise RuntimeError('Partial or existing installation config found; no files overwritten. Review manually.')
    password = lambda: 'A' + secrets.token_hex(22) + 'z!8'
    db_name = 'JcfEnterpriseRegistry'
    app_pass, mig_pass, sa_pass = password(), password(), password()
    base = {'APP_ENV': 'development', 'DB_HOST': 'sqlserver,1433', 'DB_NAME': db_name,
            'DB_AG_ENABLED': 'false', 'DB_TRUST_SERVER_CERT': 'true',
            'DB_CONNECT_TIMEOUT': '10'}
    runtime = {**base, 'DB_USER': 'jcf_registry_app', 'DB_PASSWORD': app_pass,
               'TRUSTED_PROXY_IPS': '127.0.0.1',
               'AUTH_ISSUER': '', 'AUTH_AUDIENCE': '', 'AUTH_JWKS_URL': '',
               'ADMIN_OIDC_CLIENT_ID': '', 'ADMIN_OIDC_AUTHORIZATION_ENDPOINT': '',
               'ADMIN_OIDC_TOKEN_ENDPOINT': '', 'ADMIN_REDIRECT_URI': '',
               'ADMIN_OIDC_SCOPE': 'openid profile registry.read'}
    migrate = {**base, 'DB_USER': 'jcf_registry_migrate', 'DB_PASSWORD': mig_pass}
    # Local development only. These values never enter the running registry container.
    setup = {'DB_HOST': 'sqlserver,1433', 'DB_NAME': db_name,
             'JCF_APP_USER': 'jcf_registry_app', 'JCF_APP_PASSWORD': app_pass,
             'JCF_MIGRATE_USER': 'jcf_registry_migrate', 'JCF_MIGRATE_PASSWORD': mig_pass}
    sql = {'MSSQL_SA_PASSWORD': sa_pass}
    # Write SA first; after interrupted runs, installer fails closed rather than generating mismatched passwords.
    for target, data in [(required[2], sql), (required[3], setup), (required[1], migrate), (required[0], runtime)]:
        write_new(target, data)
    write_new(root / 'mode', {'INSTALL_MODE': 'local-express'})

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: init_install_config.py /etc/jcf-registry')
    create_local(sys.argv[1])
