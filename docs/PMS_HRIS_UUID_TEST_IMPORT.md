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

**HRIS is a source, not the new data model.** The five HRIS Location
lookup categories (Portfolios, Areas, Branches, Divisions and Stations)
are ingested into **one JCF-EMP Locations table**. The old HRIS
parent/child linkage is considered unreliable and is **not copied**.
LocationParent is a separate effective-dated relationship table that
administrators correct through the application's reconciliation workflow.

The HRIS `Sections` and `Units` tables are likewise source-only; both
are imported into **one JCF-EMP Section/Unit category**. Every distinct
source Section/Unit gets its own JCF-EMP UUID, even when two source rows have
the same name. Duplicates are **flagged, not automatically merged**.
After review, administrators establish the proper local `LocationSection`
relationships and standardize/reconcile definitions under governance.
No PMS record numbers become primary keys.

## Run on CentOS 9 server 172.17.102.101

After `bash install.sh` has completed, first pull the latest importer:

```bash
cd /root/EnterpriseOrganizationalPlatform
git pull --ff-only origin feat/initial-modular-platform
bash install.sh
```

The latest source connection explicitly supplied for PMS/HRIS uses **SQL
server 172.17.51.49:1433**, **database HRIS_Duplicate**, **user HRISUser**,
`Encrypt=True`, `TrustServerCertificate=True`. This supersedes the older
`HRIS_Copy` appsettings value for this test connection.
The older server `172.17.75.123` was an unverified historical reference.
Do **not** commit the password to Git or bundle it into a Docker image.

If you already configured PMS source credentials on the CentOS server, do
**not** re-enter the password. Preserve the existing root-only secret and
update only the source database and TLS settings:

```bash
cd /root/EnterpriseOrganizationalPlatform
git pull --ff-only origin feat/initial-modular-platform
sudo python3 scripts/configure_pms_source.py --reuse-existing
sudo bash scripts/diagnose_pms_network.sh
sudo bash scripts/run_pms_sync.sh preview
```

The diagnostic checks whether both the **CentOS host** and the **isolated
import container** can reach port 1433 on the HRIS SQL host; it never opens
a SQL session, prints a password or changes routing. The source IP is in
`172.17.0.0/16`, which may overlap a Docker bridge route: review the reported
interfaces before attempting to readdress Docker or change firewall rules.
The previously observed SQLAlchemy traceback alone does not prove which
network, TLS or login step failed.

`TrustServerCertificate=True` encrypts the ODBC session but does **not**
validate the identity of the SQL Server. Limit it to the isolated test
environment and replace it with a CA-trusted server certificate for production.

For **no-prompt** setup, choose either of the following methods:

**Preferred**, if the original AD Reset application ZIP already exists on
the CentOS server:

```bash
sudo python3 scripts/configure_pms_source.py \
  --from-zip /root/JcfAdReset-COMPLETE-2026-10-17.zip
```

Or if the existing `Api/appsettings.json` file is accessible on the server:

```bash
sudo python3 scripts/configure_pms_source.py \
  --from-json /path/to/Api/appsettings.json
```

The utility reads the existing HRIS password itself and writes it only to
`/etc/jcf-registry/pms-source.env` with mode `0600`. It prints **no
password** and accepts a `--password-stdin` alternative for a one-time
protected server-side paste when the original archive/config file is not on
the VM. The saved source connection is never passed to the running web
application and is not modified by later `bash install.sh` upgrades.

The prior interactive `bash scripts/run_pms_sync.sh setup` remains
available but is not required. The SQL login must have SELECT-only permission
or be explicitly reviewed by the DBA; merely supplying read-only query code
does not revoke any preexisting write permissions.

## Reconciling the source HRIS formation schema

An initial PMS preview against `HRIS_Duplicate` reported **14 Locations, 4
Section/Unit rows, 3 unique Section/Unit definitions and zero Location–Section
assignments**. It found no expected `Portfolio` column in `Areas`, no
`Division` in `Sections`, and none of the expected `Division`, `Station`
or `Section` fields in `Units`. The tool must **not** invent those parent
relationships. The original AD Reset integration reads `Stations` and
`Sections` independently as lookup lists; that alone is not a hierarchical
directory.

The following **read-only** command now compares the two named HRIS databases
on the same SQL host, using the already-protected password:

```bash
cd /root/EnterpriseOrganizationalPlatform
git pull --ff-only origin feat/initial-modular-platform
bash scripts/run_pms_sync.sh schema
```

It prints active record counts and actual column names for the seven
allowlisted formation tables, available foreign-key constraints, and related
table **names only** that might contain mappings. It also attempts to read
`HRIS_Copy` on the same SQL host for comparison. If the SQL login cannot
access that database, it reports a non-secret accessibility error and does
not change the connection used by normal `preview`.

**No new Docker build is necessary for importer-only changes.** The launcher
mounts the checked-out `scripts/` directory into the isolated import
container as read-only, using the already-installed application image and
the existing protected source/migration environment files.

**Updated import-first test policy:** `apply` and `sync` now accept
PMS reference records even when there are **zero** Location–Section
assignments, missing parents, non-standard names or apparent duplicates.
They import Locations and distinct source Section/Unit records without
importing **any** HRIS parent/child or Section assignment relationships.
Administrators create the correct links in JCF-EMP, and the original PMS
identifiers are preserved as source cross-references alongside independent
JCF-EMP UUIDv4 keys. An earlier failed import may have left some valid
JCF-EMP relationship records, which the new importer will not automatically
delete or rewrite; review those in the GUI before approving them.

The import reports `IMPORTED_FOR_RECONCILIATION_NOT_APPROVED` and counts
orphan Locations, missing Section assignments and potential duplicate names.
A source with no usable reference records is still rejected. No changes are
made to PMS/HRIS.

After `sync`, open `https://172.17.102.101/admin`, log in with
**Local test login**, and choose **Data Reconciliation**. The screen lists
imported Locations and Sections and offers **Correct name**, **Assign/Move
parent**, **Assign Section/Unit**, and **Retire duplicate** operations.
For an existing assigned Section, use **Location–Section Matrix → Manage
records → SECTION_MOVE** to create a new location-specific Section instance,
retaining the old Section instance and personnel history. A Location parent
move closes the old parent edge and creates a new effective-dated edge.

The existing governance process still requires an independent reviewer
to approve/apply submitted corrections. Two development-only logins are
provided on the isolated SQL Express installation:

- `admin / admin` — creates Location, Section and hierarchy change requests
- `reviewer / reviewer` — separately approves and applies those requests,
  without permission to create new changes

Run `bash scripts/enable_test_admin.sh` after updating the code to provision
both logins. The identities remain disabled in production/AG mode.
Sign out after submitting a correction, sign in as reviewer, and approve it
under **Change Approvals**. This retains an auditable maker–checker flow
rather than silently applying direct edits.

An importer-created administrative reference does not automatically approve
future manual corrections. The complete structure should be reconciled and
approved before AD Self-Service consumes the dataset.

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

## Test-import audit correlation fix (29 October 2026)

SQL Server `audit_event.correlation_id` is 80 characters. Earlier PMS sync
attempts generated longer values and failed when logging parent-link changes.
The importer now uses stable `pms:<sha256-digest>` identifiers within the
column's capacity. Idempotency keys and source-ID crosswalks are unchanged;
rerunning the test import reuses existing records and does not require a database
reset or password re-entry. Use `bash scripts/run_pms_sync.sh verify` to
review the crosswalk after import.
