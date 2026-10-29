# JCF Enterprise Management Platform — scope alignment and delivery gaps

Date: 2026-10-29. This is a code inspection and UAT planning matrix, **not**
production sign-off. The uploaded five-page organizational directory is a
scanned source containing historical contacts, addresses and handwriting; it
must be reviewed before import. The database still contains illustrative
`DEMO-*` records rather than the approved JCF master registry.

| Agreed requirement | Current state | Remaining work |
|---|---|---|
| HQ/Portfolio → Area or Branch → Division → Station hierarchy | Parent-linked effective-dated model, read API and GUI tree | Load approved complete JCF structure; check all geographic and non-geographic formations |
| Location list and section roster | Searchable GUI; REST lists, location-specific sections | Confirm Section/Unit assignments for each formation; distinguish master definitions from their local instances |
| Single combined Section/Unit selection | Present as one domain and location-scoped IDs | Complete authoritative CIB, PIU, General Office, Armoury, Operations Office, DIU mappings |
| Location rename lifecycle | **GAP:** LOCATION_RENAME currently writes a new LocationName under the same LocationID | Implement specified retire-old/create-new-UUID workflow with explicit personnel transfers and preserved history |
| 5 Areas, 19 divisions, stations | Only fictional development fixture | Import and reconcile approved real hierarchy; verify names/aliases/effective dates |
| Multiple Buildings per Location, multiple Locations per Building | Many-to-many effective-dated model and occupancy directory | Import physical facility register, reconcile exact addresses and occupancy dates |
| Sections/Units operating in particular Buildings | SectionBuilding model and API per section | Add joined GUI section-to-building assignment detail, move history and multi-building displays |
| Floors, rooms, space and office allocations | **GAP:** no independent space/floor registry | Add Building→Floor→Room/Space and effective-dated section occupancy |
| Contacts separate from HRIS/Location Registry | Logical Contact and ContactAssignment tables; privacy; new directory read API | Implement official Contact Directory management UX, PBX scope, validation and office/visitor/space owner support |
| Location addresses & contact information | Building has address/parish; Contacts linked separately; GUI now joins recorded data | Import/reconcile source PDF; add provenance/approval dates; no automatic use of scanned or handwritten numbers |
| Matrix reporting (division, CIB, DIU) | Typed reporting edges, API and report table | Validate authority graph against approved structure, include cross-reporting views and historical exports |
| Change approval/audit | Maker/approver separation, approved action application, audit/outbox events | Approver assignment at Location level, workflow escalation, comprehensive role segregation and approvals UI |
| Personnel authoritative posting + history | Basic Personnel/Postings records, section-pair validation | HRIS onboarding, matching, local staging/drafts, approval before write-back, rank/status and conflict reconciliation |
| HRIS and AD connectors | **GAP:** no configured production HRIS/AD client or integration administration screen | Read-only HRIS source interfaces; approval-driven HRIS updates; credential vault, retries, error dashboard |
| AD self-service consumer | Read REST Locations, hierarchy, Sections, Buildings and Contact Directory | Register separate API consumer identity, app-only authorization, versioned contracts, rate limits, UAT |
| API synchronization/change feed | Preliminary outbox change feed | Operational sync strategy, consumer cursors, recovery/idempotence, stable revision/ETag and contract tests |
| Authentication | Local Express-only test admin; OIDC implementation | Register SPA redirect URI, API scopes and admin consent; token roles/claims and user provisioning |
| Search/listing/reporting UI | Basic directory, tree, sections, facility occupancy, reporting and contacts | Comprehensive dashboard drill-down, PDF/Excel/CSV exports, reconciliation exceptions and completeness reports |
| Production architecture | Python/FastAPI container + Nginx + SQL2019 Express (test) | Future separate .NET/Blazor Clean/Onion build with web/API/middleware/SQL on independently deployable systemd hosts |
| HA/security/operations | Test backups, health check, TLS proxy, SQL2019 CI | SQL Always On AG/DR, load, penetration, operational monitoring, immutable backups, backup restoration tests |

## High-priority data reconciliation

1. Ingest the uploaded five-page scanned *List of Portfolios, Areas, Branches,
   Divisions, Stations, Sections and Units — JCF* into a **staging/review area**.
   It is not a full formally approved register and may be out of date.
2. Separate **organizational Location** from **physical Building**, **Section
   instance**, and **Contact Directory record**. No flat CSV import should
   force one-to-one Location↔Building or persist section names that include
   the parent division.
3. Show source page, validation status and approval evidence before publishing
   a staged Location, address or phone number to AD self-service consumers.
4. Preserve the existing development seed and root-only secrets during upgrades.

## Microsoft Entra ID and local testing

The local Express-only account remains enabled independently of Entra ID.
JCF-EMP app registration: client ID
`9eb6b06d-0975-4991-bfff-6a8937d68fbd`, tenant
`1ea2e2cf-0bfb-42f2-96a9-52a3a96317e1`. The SPA must be registered
under **Authentication → Single-page application**, with a redirect URI
matching the HTTPS browser origin and `/admin`. Expose an API with delegated
scopes `registry.read`, `facilities.read`, `reporting.read`,
`contacts.read` and grant admin consent. Run
`bash scripts/configure_entra.sh --redirect-uri https://<approved-host>/admin`
from the server; it does not clear local login keys. The current IP's
self-signed certificate does not establish browser-trusted identity.

## Not an operational directory yet

The GUI now hides literal `[TEST]` and `DEMO-` prefixes in **display only**
for readability. Underlying SQL values remain namespaced as demo fixtures.
The source and test status remain indicated in the UI. Test records must
not be published to production consumers or represented as approved JCF data.
