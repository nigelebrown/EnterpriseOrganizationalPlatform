# JCF Enterprise Management Platform (JCF-EMP) — API Standards

All public service APIs **shall be documented using OpenAPI**. No specific OpenAPI release is
mandated by the architecture; implementers shall select a compatible supported release and keep
its machine-readable specification current with actual deployed routes.

## HTTP and resource contracts

- HTTPS/TLS required in production. REST-style nouns, stable IDs, lower-case kebab-case routes and
  namespaces rooted at `/api/v1/`. Do not expose internal SQL table names or raw database accounts.
- `GET` is safe and read-only; `POST` creates a request or performs explicit domain commands.
- Use UTF-8 JSON, explicit nullable properties, ISO 8601 dates and UTC timestamps.
- Successful collection responses use `{items:[...],nextCursor:...}`. Enforce bounded page size.
- Specify URI and request/response JSON schema in OpenAPI; include example successful and failed responses.
- `200` successful read/validation/application; `201` change request submitted; `400` malformed input;
  `401` unauthenticated; `403` forbidden; `404` absent/hidden; `409` duplicate, stale or conflict;
  `422` semantic selection validation rejected; `503` temporarily unavailable.
- Errors follow RFC 9457 Problem Details (`application/problem+json`) and include a stable `code`,
  human-readable `detail` and request-scoped `correlationId`. Do not expose exceptions or internal SQL.

## Identity and authorization

- Access tokens come only from the JCF-approved OIDC/OAuth 2.0 issuer, with verified JWKS signature,
  `iss`, `aud`, `sub`, `exp`, and a documented permissions model.
- Apply OAuth2 Security Best Current Practice (RFC 9700) when selecting identity flows.
- Machine-to-machine onboarding clients obtain minimum scopes; admin SPA uses Authorization Code + PKCE.
- Each API enforces server-side function, object and property authorization; the client is not a
  source of authority. `registry.restricted.read` must not be given to normal self-service onboarding.
- No personal HR, visitor or classified data may be sent to a caller who only needs Location/Section IDs.
- Provision API credentials/scopes, rotation, incident revocation and audit through central IAM.

## Changes and reliability

- Master-data mutations use `ChangeRequest -> independent approval -> apply`, with audit and outbox
  inside the same transaction. The requester cannot approve their own change.
- Onboarders and other writers should submit `Idempotency-Key` for safe retry after client timeouts.
- Event feeds use monotonically increasing outbox cursors and at-least-once delivery semantics;
  consumers must deduplicate by immutable `eventId`.
- Use correlation IDs end-to-end. Handle SQLAG listener reconnection carefully, including dropped
  in-flight transactions and retries only for idempotent operations.
- HTTP read endpoints should implement cache and conditional request policy per data sensitivity;
  do not permit intermediary caching of restricted or personal payloads.
- Avoid breaking existing consumers; schedule and document any incompatible API changes in a new
  API contract/version, with migration guidance and retirement period.

## Security verification

- At minimum test OWASP API Security Top 10 categories, particularly Broken Object-Level Authorization
  (BOLA), Broken Authentication, Broken Object Property Level Authorization and unrestricted resource
  consumption. Rate limits belong at an approved reverse proxy/API gateway backed by shared counters.
- No credentials, API keys, tokens or personal phone numbers appear in analytics and error logs.
- Maintain OpenAPI examples and automated contract tests; review deployed spec against API behavior.

## Phase 1 onboarding endpoints

- `GET /api/v1/registry/locations?asOf=YYYY-MM-DD&search=...` returns approved locations.
- `GET /api/v1/registry/locations/{locationId}/sections?asOf=...&search=...` returns specific
  location-associated Section/Unit instances, not a global Section list.
- `POST /api/v1/registry/posting-selections/validate` validates `locationId`, `sectionId`, and
  `effectiveOn`, returning HTTP 422 for a cross-Location selection.
- `sectionId` means the immutable `LocationSectionID`; `sectionDefinitionId` is the reusable
  standardized name; neither is derived by concatenating display names.
