# PMS (HRIS) → JCF-EMP Location/Section test import

**Purpose:** populate the isolated CentOS 9 / SQL Server 2019 Express JCF-EMP
development database with organizational structure from the existing PMS/HRIS
SQL Server lookup tables and source-labelled address/phone candidates from
the uploaded five-page JCF organizational directory PDF.

## UUID identity rule

JCF-EMP always generates **new UUIDv4 primary keys** for every imported
Location, SectionDefinition, LocationSection, LocationParent, Building,
LocationBuilding, Contact and ContactAssignment. **No PMS/HRIS Oid, PersonnelNo,
numeric record ID, division code or station code becomes a JCF-EMP PK.**

The `external_id` crosswalk retains the original PMS lookup table name,
opaque source Oid (UUID or integer, as provided), and the independent
JCF-EMP UUID. This is for idempotent reimports and reconciliation only.

Example (illustrative; not a real database row):

| System | Field | Example |
|---|---|---|
| PMS/HRIS | `Divisions.Oid` | `12345` (or source GUID) |
| JCF-EMP | `Location.id` | `550e8400-e29b-41d4-a716-446655440000` |
| JCF-EMP | `ExternalID.source_id` | `12345` |
| JCF-EMP | `ExternalID.canonical_id` | `550e8400-e29b-41d4-a716-446655440000` |

The generated UUID stays unchanged on reruns. Business `code` is a separate,
stable hash-based import code, not an external or internal numeric ID.

## Source contract verified

The October 21, 2026 *Personnel Formation Mapping* workbook and the original
AD Reset application source identify HRIS SQL Server tables:
`dbo.Portfolios`, `dbo.Areas`, `dbo.Branches`, `dbo.Divisions`,
`dbo.Stations`, `dbo.Sections`, `dbo.Units`. The importer reads only the
`Oid`, official name, optional code and parent reference columns on these
seven tables, filtering out rows with `GCRecord IS NOT NULL`. **No personnel
information or member assignments are imported.**

Geographic: Portfolio → Area → Division → Station. Nongeographic:
Portfolio → Branch → Division. Sections and Units are merged into one
standardized definition catalogue; a separate `LocationSection` UUID exists
for each approved Location-to-Section/Unit attachment. Conflicts, missing
parents, duplicate source IDs and unsupported references are reported
for reconciliation, not guessed.

## Run on CentOS 9 server 172.17.102.101

After `bash install.sh` has completed, run once:

```bash
cd /root/EnterpriseOrganizationalPlatform
git pull --ff-only origin feat/initial-modular-platform
bash install.sh
sudo bash scripts/run_pms_sync.sh setup
```

The setup prompts locally for the approved PMS SQL host, database, a
**SELECT-only** SQL username and its password. Historical references are
`172.17.75.123` and `HRIS_Copy`, which must be verified by your DBA;
the importer does **not** assume these are current. Save credentials only
in root-only `/etc/jcf-registry/pms-source.env`, never chat or GitHub.

Inspect the source and preview the import **without writing to either DB**:

```bash
sudo bash scripts/run_pms_sync.sh inspect
sudo bash scripts/run_pms_sync.sh preview
```

After confirming the source counts, discrepancies and match candidate report,
apply to the development database only:

```bash
sudo bash scripts/run_pms_sync.sh apply
sudo bash scripts/run_pms_sync.sh verify
```

Or use `sudo bash scripts/run_pms_sync.sh sync` for the preview, import and
verification steps together. Repeating the import reuses JCF-EMP UUIDs through
the source crosswalk and does not create duplicate records.

The importer explicitly checks development mode, local SQL Express host and
SQL Server 2019 Express edition, and refuses production/AG targets.
Source connection uses encryption; certificate trust is enabled by default
and should be validated via the JCF SQL certificate chain.
The source SQL login should have SELECT-only privileges.

## Scanned directory data

`scripts/data/jcf_pdf_directory_candidates.json` contains a conservative
subset of addresses and public organizational contacts manually extracted
from the supplied five-page scanned document. Its historical currency is
**unverified**. For the test database, exact unique formation-name + type
matches can be attached as separate `Building` records and optional
`Contact` entries. They are explicitly marked by origin in the test
import, are not considered approved master records, and may not be pushed to
other JCF systems without reconciliation.

## Limitations and release gates

The process is a **read-only snapshot extraction and repeat-safe development
import**, not a tested production reconciliation service. Existing approved
Location names/parents are not overwritten automatically. Source deletes,
renames and changed parent assignments require a separate reviewed transfer/
replacement process. Imported data must be reviewed for current status,
duplicates, retired formations and hierarchy before AD Self-Service consumes
it. The PDF may omit locations and contain old or handwritten contact details.

Do not run against the live HRIS with write credentials. Do not publish or
upload SQL passwords, full connection strings or personal data.
