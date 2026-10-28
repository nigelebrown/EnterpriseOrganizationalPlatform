# JCF release-gate evidence matrix

This is a development baseline, **not a certificate of production readiness**.

| Gate | Evidence required | Local result |
|---|---|---|
| A1 Governance | independent approval; no self-approval; audit & outbox atomic | HTTP test PASS |
| A2 Lookup | location -> section filtering; invalid selection 422; no fabricated default section | HTTP test PASS |
| A3 Restricted | unauthorized names/IDs inaccessible; OIDC scopes | mock-token HTTP test PASS; real OIDC PENDING |
| A4 History | half-open dates; name history; hierarchy cycle check | logical/HTTP PASS |
| B1 Matrix | CIB to division and CIB HQ; DIU to NIB HQ | CIB HTTP PASS; full JCF UAT PENDING |
| C1 Facilities | multiple sections per building and multi-building section | HTTP PASS |
| D1 Contact | contact privacy and links | HTTP PASS; policy UAT PENDING |
| E1 HR | posting pair integrity, leave request persistence | HTTP PASS; comprehensive HRIS PENDING |
| F1 Linux | container startup, ODBC Driver 18 certificate validation | PENDING |
| F2 SQL Express 2019 | Alembic upgrade, CRUD/locking and rollback on actual SQL2019 Express | PENDING |
| F3 AG production | listener MultiSubnetFailover, failover/auto-return, transaction retry & backup restore | PENDING |
| F4 Security | SAST/DAST, rate limiting, OIDC end-to-end, BOLA, SSRF, audit immutability | PENDING |
| F5 Scale | JCF-sized import, read p95 target, write contention, soak & failure tests | PENDING |

No test labelled PENDING may be silently treated as passed. Database version and edition must be read from production instances; SQL Express does not support AG. Full leave workflow, transfer reconciliation, contact validation for visitors/offices and advanced reporting are not complete.
