# JCF Enterprise Management Platform (JCF-EMP) — one-command installation and upgrade

**Scope.** The Linux installer installs Docker and Compose when absent, builds the application image,
creates *an isolated development-only SQL Server 2019 Express instance* when selected, provisions
separate runtime/migration database users, runs forward-only Alembic migrations, and starts/validates
the service. On repeat executions it preserves credentials and data, takes a verified local SQL backup
(or requires DBA restore-point evidence for an external AG), applies migrations, and checks readiness.
It never force-drops an existing database, deletes a volume, or silently resets passwords.

## Tested platforms and dependencies

- x86-64 CentOS Stream 9, Ubuntu 22.04/24.04 LTS or Debian 12, with access to approved Docker/Microsoft/PyPI sources. CentOS Stream 9 uses dnf to bootstrap missing prerequisites.
- At least 4 GB RAM on a combined development app + SQL2019 Express VM (larger is preferable).
  An external SQL listener mode does not install SQL Server on the application VM.
- Docker Engine, Compose and Buildx are installed using the official Docker RPM repository on CentOS Stream 9 or apt on Ubuntu/Debian, only when absent. The installer requires root and an exclusive lock.
- SQL Server Express is **not** a member of an Always On Availability Group. Production must use
  an independently operated SQL Server 2019 AG listener and approved licensing/HA configuration.
- SQL Server data is a persistent Docker volume, **not** a disposable container layer. A local SQL
  backup is saved in the same persistent volume for migration recovery. Maintain independent/off-host
  backups for actual disaster recovery.

## CentOS Stream 9 — one-click command line setup

On a CentOS Stream 9 x86_64 VM, clone the authenticated private GitHub development branch:

```bash
git clone -b feat/initial-modular-platform https://github.com/nigelebrown/EnterpriseOrganizationalPlatform.git
cd EnterpriseOrganizationalPlatform
sudo bash install.sh --mode local-express
```

For a previously installed system after updating source:

```bash
git pull
sudo bash install.sh
```

The installer uses `dnf` and Docker's official CentOS repository; SQL Server 2019
Express runs in a persistent Docker container (not as a CentOS RPM).
Do **not** disable SELinux or firewalld. The application binds to loopback
`127.0.0.1:8080`; expose access only through a separately configured
HTTPS reverse proxy on approved firewall ports. Docker-managed named volumes
avoid unlabelled CentOS host bind mounts. External AG mode connects to an
existing SQL Server 2019 listener and does not install SQL locally.

CI includes a CentOS Stream 9 container syntax/dry-run smoke test; full
fresh-VM and upgrade acceptance testing on an actual CentOS Stream 9 host
is required before operational sign-off.

## One-click development install

From the checked-out repository branch containing `install.sh`:

```bash
sudo bash install.sh --mode local-express
```

The first run creates only root-accessible files under `/etc/jcf-registry/`:
`runtime.env`, `migration.env`, `setup.env`, `sqlserver.env`, `mode`, and `deployment.env`.
The SQL administrator credential stays out of the application runtime container; the application
uses a restricted `jcf_registry_app` login and migrations use a separate login.
The SQL test database is named `JcfEnterpriseRegistry` and is not publicly port-mapped.

The service listens on **127.0.0.1:8080** only. A trusted HTTPS reverse proxy, a certificate
and a properly configured identity provider are still needed for end-user administration and
protected REST APIs. **No insecure administrator bypass is created.** Populate the JCF OIDC
settings in `/etc/jcf-registry/runtime.env`, configure `TRUSTED_PROXY_IPS` for the actual proxy,
then rerun `sudo bash install.sh`. The installer does not automatically expose HTTP publicly.

## Repeat installation / upgrade

```bash
git pull
sudo bash install.sh
```

This preserves all `/etc/jcf-registry` environment files and the existing SQL volume. The steps:

1. Acquire exclusive install lock and verify the mode and target environment.
2. Build a release-tagged container from the committed checked-out source.
3. Start the local test SQL container (if applicable) and verify database connectivity.
4. Refuse any non-empty database not already managed by Alembic.
5. Stop the old application for a controlled maintenance window.
6. Back up and `RESTORE VERIFYONLY` the local Express database; **external AG** relies on a
   supplied DBA-confirmed backup/restore reference.
7. Run `alembic upgrade head` with a dedicated migration account.
8. Start the updated application and wait for `/health/ready` to pass inside its container.
9. Save the installed release tag only after health passes. On failure, attempt an
   **application-only rollback** to the last image. Schema upgrades are never downgraded
   automatically and may require DBA-supervised restore if incompatible.

The installer is **idempotent for existing release/schema**. It never overwrites original
credentials or initializes new users when logins already exist. Backups are retained (no automatic
pruning) and should be managed by operations under an approved retention policy.

## External SQL Server 2019 AG environment

Create a pre-provisioned database, a limited runtime account with only required DML permissions,
and a separately privileged migration account. Have the JCF DBA confirm backups, restoration
capability, required schema rights and listener/cluster connectivity. The production certificate
must be trusted by the Linux application's ODBC driver. If the JCF uses an internal issuing CA,
copy its **public root certificate only** to `deploy/ca/<name>.crt` before building; Docker installs
that certificate into the image trust store. These certificates are ignored by Git.

Create two root-only files outside Git, e.g. `/root/jcf-runtime.env` and
`/root/jcf-migration.env` using `.env.example` as the reference. Runtime example fields:

```dotenv
APP_ENV=production
DB_HOST=tcp:AG-LISTENER.INTERNAL,1433
DB_NAME=JcfEnterpriseRegistry
DB_USER=jcf_runtime
DB_PASSWORD=<vault-supplied-credential>
DB_AG_ENABLED=true
DB_TRUST_SERVER_CERT=false
TRUSTED_PROXY_IPS=<actual-HTTPS-reverse-proxy-address>
AUTH_ISSUER=https://<identity-issuer>
AUTH_AUDIENCE=jcf-enterprise-registry
AUTH_JWKS_URL=https://<identity-issuer>/certs
ADMIN_OIDC_CLIENT_ID=<registered-client-id>
ADMIN_OIDC_AUTHORIZATION_ENDPOINT=https://<identity-issuer>/authorize
ADMIN_OIDC_TOKEN_ENDPOINT=https://<identity-issuer>/token
ADMIN_REDIRECT_URI=https://registry.example.jcf/admin
```

Migration file uses `APP_ENV=production`, same `DB_HOST`, `DB_NAME`, `DB_AG_ENABLED=true`,
`DB_TRUST_SERVER_CERT=false`, but a *distinct* `DB_USER` and `DB_PASSWORD` authorized for schema
migrations. Do not place real passwords in a code repository, terminal history or this guide.

```bash
sudo bash install.sh --mode external \
  --runtime-file /root/jcf-runtime.env \
  --migration-file /root/jcf-migration.env \
  --backup-reference 'DBA-verified-restore-point-identifier'
# After later git pull:
sudo bash install.sh --backup-reference 'NEW-DBA-verified-restore-point-identifier'
```

The installer **never installs SQL Server or triggers AG failover** in external mode and cannot
independently verify the DBA's backup reference. Production cutover still requires a real restore
test, SQL Server 2019 AG listener failover and multi-subnet reconnection testing.

## Operator verification

```bash
sudo docker compose --project-name jcf-enterprise \
  --env-file /etc/jcf-registry/deployment.env \
  -f deploy/compose.yaml ps
sudo docker compose --project-name jcf-enterprise \
  --env-file /etc/jcf-registry/deployment.env \
  -f deploy/compose.yaml logs --tail 50 registry
sudo bash install.sh --dry-run
```

For host-side health checks, query the application through the trusted HTTPS proxy. For the
container's local readiness check, `docker inspect` reports `healthy` only when the application
and database respond. TLS is mandatory for client-facing production routes; internal loopback
health is deliberately exempt for Docker readiness checks.

## Recovery and cautions

- A failed health check triggers best-effort old *application-image* restart. It **does not** roll
  back database migrations, so additive/backward-compatible schema revisions are required.
- Schema rollback requires a DBA-approved restore with controlled downtime and a separate
  recovery exercise. Keep the backup identifier and migration logs in the change record.
- Do not change `--mode` or AG database targets in place. Switching from local Express to AG
  requires an approved data migration and independent validation.
- Missing OIDC/AD configuration intentionally leaves protected endpoints unavailable. Installing
  the software is **not** the same as commissioning production security or functionality.
- Rehearse upgrades and restores in non-production, including concurrent sessions and domain
  integrations, before approving a production change window.
