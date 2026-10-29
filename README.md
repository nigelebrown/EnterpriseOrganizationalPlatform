# JCF Enterprise Management Platform (JCF-EMP)

**One Platform. Standardized Data. Integrated Services.**

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

From a fresh **CentOS Stream 9**, Ubuntu 22.04/24.04 or Debian 12 x86-64 VM after checkout (the installer automatically selects dnf or apt):

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

CentOS Stream 9 uses the official Docker RPM repository and does not disable SELinux or firewalld. The application remains bound to 127.0.0.1 until a trusted HTTPS proxy is configured. CentOS full-VM testing remains outstanding.

Full instructions and safety constraints: [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Synthetic development sample data

The one-click `--mode local-express` installation now seeds an **illustrative test-only**
JCF-EMP dataset after schema migrations: 12 example Locations, 9 Section/Unit
definitions, 16 LocationSection assignments, 10 matrix/command relationships,
2 fictional buildings, 3 building occupancy records, and 3 synthetic member
postings. The Section/Unit catalogue includes **CIB**, **PIU**, **General Office**,
**Armoury**, **Operations Office** and **DIU**, plus the existing illustrative
Administrative Office, ICT Support and Operations definitions. Search **SAC**,
**SAS**, **J**, **CIB**, or **DIU**.

New Section/Unit definitions are **not** automatically assigned to every
Location. A separate, approved Location–Section mapping is required before a
Section/Unit appears in that Location's dependent dropdown. The synthetic
`DEMO-*` codes and `[TEST]` display-name prefixes distinguish these fixtures
from official JCF reference records.

All generated Location and Section codes are `DEMO-*`, all sample person numbers
are `TEST-JCF-*`, and formation names are prefixed `[TEST]`. Organizational
hierarchies and associations are **illustrative, not an approved JCF master list**.
No real officers or actual building addresses are inserted.

The directory UI hides literal `[TEST]` and `DEMO-` prefixes **only
when rendering labels**; underlying IDs and stored values are unchanged.
A visible development-data notice distinguishes demos from the approved JCF
registry. Recorded Building addresses and Contact Directory entries are shown
in their respective views; unknown values are not fabricated.

The seed uses governed change requests with **distinct synthetic test identities**
(not real human approvals), never overwrites existing samples, and rejects
production/Always On AG connections. Existing non-demo reference registers
are skipped unless test seed data are already present. On local Express you can
opt out with `sudo bash install.sh --no-test-seed`. Repeat upgrades preserve data.
See [installation guidance](docs/INSTALLATION.md#test-only-sample-data).

## Check and resume an interrupted CentOS installation

```bash
cd /root/EnterpriseOrganizationalPlatform
git pull --ff-only origin feat/initial-modular-platform
bash scripts/doctor.sh --check   # diagnostics only
bash scripts/doctor.sh --repair  # completes missing setup steps and verifies DB seed
```

The doctor refuses overlapping installations and preserves existing data and passwords.
See [installation recovery](docs/INSTALLATION.md).

See [scope alignment and outstanding gaps](docs/SCOPE_ALIGNMENT_GAP_AUDIT_2026-10-29.md) for the Location, Building, Contact, HRIS, and AD integration delivery matrix.

## SQL 2019 development vs AG

- **Express 2019:** `DB_AG_ENABLED=false`, direct SQL hostname, TLS CA validation, max DB size 10 GB.
- **Production AG:** `DB_AG_ENABLED=true`, `DB_HOST=tcp:<listener>,1433`, `Encrypt=yes`,
  `TrustServerCertificate=no`, `MultiSubnetFailover=yes`. Client reconnects do not substitute for
  idempotent replays; in-flight SQL operations need transaction-safe recovery.
- Do not attempt Always On AG failover tests on Express; use Enterprise or Basic AG capable
  Standard infrastructure as licensed/approved.

## Temporary local administrator for isolated SQL Express testing

For the current **CentOS development/test installation only**, enable a
simple local administrator with username `admin` and password `admin`:

```bash
cd /root/EnterpriseOrganizationalPlatform
git pull --ff-only origin feat/initial-modular-platform
bash scripts/enable_test_admin.sh
```

The setup script verifies `INSTALL_MODE=local-express`,
`APP_ENV=development`, the local Docker SQL2019 Express host and disabled
AG mode; it refuses external/production installs. It hashes the test password
and generates a private signing key in the existing root-only
`/etc/jcf-registry/runtime.env`. It runs the normal upgrade installer, which
preserves existing database data, and verifies that the test login was enabled.

Open `https://172.17.102.101/admin`, click **Sign in**, enter `admin`
and `admin`, and browse the live seeded Locations, Sections, Buildings and
reporting relationships. The 1-hour test bearer token is in browser memory
only. The JWT is trusted only by the local development-mode API; production
remains OIDC/Entra ID-only. Access to seeded data is **not** anonymously
exposed. Independent approval rules remain enforced and a test admin cannot
approve their own submitted change request.

This weak test password is explicitly for a controlled, isolated test
system. Replace the self-signed test certificate and configure real Entra ID
before using actual JCF organizational or personnel records.

## Entra ID and local test login together

Both sign-in choices now coexist in the same GUI. `Local test login` remains
available only in the isolated SQL Server Express development mode and does
not depend on Entra configuration. `Microsoft Entra ID` appears when the
registered client is configured.

The current JCF-EMP Entra app has client ID
`9eb6b06d-0975-4991-bfff-6a8937d68fbd` in tenant
`1ea2e2cf-0bfb-42f2-96a9-52a3a96317e1`. The current browser application
requires:

- **Authentication → Add platform → Single-page application**: register the
  *exact* trusted HTTPS redirect URI ending in `/admin`, in addition to
  any existing Web redirect URI.
- **Expose an API**: Application ID URI
  `api://9eb6b06d-0975-4991-bfff-6a8937d68fbd` with delegated scopes
  `registry.read`, `facilities.read`, `reporting.read`,
  `contacts.read`. Add appropriate API permissions and grant admin consent.
- **Trusted HTTPS certificate**: the self-signed test certificate can prevent
  browser PKCE from running in a secure context. A JCF-approved FQDN and
  trusted PKI certificate are recommended before testing Entra sign-in.

After completing the Entra application registration, run:

```bash
sudo bash scripts/configure_entra.sh \
  --redirect-uri https://YOUR-APPROVED-JCF-DNS/admin
```

This writes OIDC endpoints and API scope requests into the existing
root-only runtime configuration and invokes the repeat-safe installer.
**It does not modify `ADMIN_LOCAL_TEST_*` settings or the local administrator.**
Entra application credentials/secrets are not needed for the SPA public
client flow. The app registration itself must be configured in Entra by
an authorized administrator.

## Location hierarchy and organizational directory views

The administration GUI at `/admin` now prioritizes **read-only, searchable
directory views**: an overview, expandable parent–child Location Hierarchy
(with Tree/Flat List modes), standard Section/Unit definitions,
Location–Section mappings, Building-to-Location occupancy, and reporting
relationships. Selecting a Location displays its assigned Section/Unit
instances, Buildings and reporting authorities.

Before OIDC is configured, **Preview sample** shows only fictional,
in-browser `[TEST]` records for UI inspection. It is NOT a public read
endpoint into the SQL Server database; all live lists remain protected by
OIDC scopes and visibility filtering.

Authorized consumers, including the future AD self-service application, can
read the hierarchy and relationships through:

```http
GET /api/v1/registry/location-hierarchy?asOf=2026-10-29&limit=200&cursor=0
GET /api/v1/registry/locations
GET /api/v1/registry/locations/{locationId}/sections
GET /api/v1/registry/location-sections
GET /api/v1/facilities/building-occupancies
GET /api/v1/reporting/relationships
```

`location-hierarchy` returns each visible active Location (including
non-selectable administrative parents), its effective parent
`parentLocationId` or `null`, and its `selectable` flag. Parent IDs of
restricted Locations are masked from accounts lacking permission.
It deliberately returns **flat parent-linked records**, so consumers can
build their own trees without SQL access. The onboarding selection API
continues to return *selectable* Locations only, preserving validation of
Location + subordinate Section/Unit pairs.

*The current hierarchy is illustrative; approved JCF master data must
replace the test fixture before external ingestion.*

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
