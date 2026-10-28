# JCF Enterprise Organizational Platform

**Implementation baseline – not yet approved for production.**

A Linux-hosted modular monolith built on FastAPI/SQLAlchemy and SQL Server 2019. The organization
registry owns standardized Locations and Section/Unit definitions, each occurrence being separately
identified by `LocationSectionID` (public `sectionId`). Additional modules implement typed matrix
reporting, building/GIS occupancy, a separate Contact Directory boundary, and preliminary HR/posting
entities. The browser administrator console uses enterprise OIDC Authorization Code + PKCE.

## What is implemented

- Four-eyes governed change requests, independent approvals, audit/outbox persistence and rollback.
- Location and Section/Unit master data; historic names, parents, type, restricted visibility, aliases.
- Onboarding endpoints: filtered Location/Section dropdowns and server-side validation of pairs.
- Typed matrix reporting (e.g. CIB -> Division and CIB HQ) and relationship-cycle checks.
- Many-to-many Location/Building/Section occupancy; coordinates and duration rules.
- Contact points scoped by entity and visibility; personnel/posting/leave **foundation only**.
- CSV/XLSX preview and transactional governed batch registration.
- OpenAPI JSON at `/api/openapi.json`; OIDC JWT scopes; structured `application/problem+json` errors.
- SQLAlchemy SQL Server dialect, ODBC 18 TLS, Express direct and AG listener connection configuration.

## One-command installation and safe repeat upgrade (Linux)

From a fresh Ubuntu 22.04/24.04 or Debian 12 x86-64 VM after checkout:

```bash
sudo bash install.sh --mode local-express
```

This bootstraps Docker if necessary, deploys an isolated persistent SQL Server **2019 Express**
development container, creates separate application/migration SQL accounts, applies Alembic and
starts the service on **127.0.0.1:8080**. It does not bypass enterprise authentication. Configure
OIDC and a trusted HTTPS reverse proxy to make authenticated administration usable.

After any later repository update:

```bash
git pull
sudo bash install.sh
```

The installer keeps all `/etc/jcf-registry` secrets and persistent SQL data, takes a native SQL
backup before migrations, validates database readiness and attempts an application-only rollback
if startup fails. External SQL2019 AG mode is supported with separate runtime/migration credentials
and a DBA-confirmed restore point; do **not** deploy an AG on SQL Express.

Full instructions and safety constraints: [docs/INSTALLATION.md](docs/INSTALLATION.md).

## SQL 2019 development vs AG

- **Express 2019:** `DB_AG_ENABLED=false`, direct SQL hostname, TLS CA validation, max DB size 10 GB.
- **Production AG:** `DB_AG_ENABLED=true`, `DB_HOST=tcp:<listener>,1433`, `Encrypt=yes`,
  `TrustServerCertificate=no`, `MultiSubnetFailover=yes`. Client reconnects do not substitute for
  idempotent replays; in-flight SQL operations need transaction-safe recovery.
- Do not attempt Always On AG failover tests on Express; use Enterprise or Basic AG capable
  Standard infrastructure as licensed/approved.

## Example onboarding API

```http
GET /api/v1/registry/locations?search=SAC&limit=100
GET /api/v1/registry/locations/{locationId}/sections?asOf=2026-10-28
POST /api/v1/registry/posting-selections/validate
Content-Type: application/json
Authorization: Bearer <access_token>

{"locationId":"<uuid>","sectionId":"<uuid>","effectiveOn":"2026-10-28"}
```

The `sectionId` is a *location-specific instance*, while `sectionDefinitionId` is the shared
CIB/DIU/etc vocabulary. Name never embeds the parent Division. Empty Section lists are a steward
configuration task; the Registry never automatically invents "General Office".

## Independent approval workflow

1. `POST /api/v1/governance/requests` with `action`, `payload`, `reference` (and module write scope).
2. A *different* actor calls `/api/v1/governance/requests/{id}/decision` with `APPROVED`.
3. An authorized actor calls `/api/v1/governance/requests/{id}/apply`, committing data + audit + outbox.
4. Consumers retrieve approved change events under `integration.events.read`.

## Tests

```bash
python -m pytest tests -q
python scripts/verify_sqlserver_ddl.py
```

Local API integration tests exercise production route handlers against an isolated SQLite fixture;
**they do not prove SQL Server 2019 compatibility, OIDC security, Linux deployment or AG failover**.
See [release gates](docs/RELEASE_GATES.md) for the remaining mandatory tests and approvals.

## Security and development notes

- No shared administrative passwords and no built-in bypass authentication.
- Set `AUTH_ISSUER`, `AUTH_AUDIENCE`, `AUTH_JWKS_URL` and explicit OIDC SPA configuration before production.
- Store DB credentials in a vault, not in Git; deploy a trusted SQL Server certificate chain.
- No hard deletion of referenced formations, official postings, audit or approved events.
- Four-eyes approval cannot replace distinct HRIS authority for official posting confirmation.
- Restrict `registry.restricted.read` to designated clients, not self-service onboarding.
- Rate limits, web gateway hardening, complete HR workflows, real SQL acceptance tests and AG failover testing
  are required before release sign-off.
