# Phase 13 pre-cutover implementation

Status: implemented and locally verified; production remains disabled.

## Authority and non-goals

Phase 13 is implementation/test authorization only. The default `create_app`, V4
runtime, Compose files, UI, V3 code and current deployment do not import or construct
the production adapter. No environment loader exists for it. No production endpoint,
secret, database, library path, download or Sonarr command was used.

Sonarr request construction is grounded in Sonarr's official sources:

- the host OpenAPI configuration defines `X-Api-Key` as an API-key header and the
  HTTP/HTTPS server variables: <https://github.com/Sonarr/Sonarr/blob/v5-develop/src/NzbDrone.Host/Startup.cs>;
- the supported API documentation entry remains <https://sonarr.tv/docs/api/>;
- the adapter uses the V3 series lookup and command path already used by the frozen
  V3 client, with only `{name:"RescanSeries",seriesId:<integer>}` posted after files
  are placed.

## Dormant capability boundary

`src/v4/production_adapter.py` contains the only production-shaped path:

1. `ProductionEffectAuthorization.from_manifest` requires an exact manifest, one
   exact execution key and the exact `ENABLE PRODUCTION EFFECTS` confirmation.
   Nothing creates it automatically and the capability cannot authorize a batch.
   The coordinator verifies the adapter-required capability before reservation and
   dispatch; the production adapter independently verifies its permit before creating
   a journal or invoking a downloader/Sonarr client.
2. `ProductionAdapterConfig` accepts only explicit origins/paths and an API-key file.
   The secret is bounded, read only while constructing the client, sent only in the
   `X-Api-Key` header, and absent from receipts, journals and audit output.
3. `ProductionEffectPermit` is required to construct a production-mode adapter.
   Tests instead use `IsolatedTestPermit`, which requires an explicit loopback HTTP
   origin and separate non-root temporary staging/library boundaries.
4. `ExecutionCoordinator` continues to reject `production_effects:true` by default.
   It accepts such a receipt only when given the matching authorization object.
5. The runtime/API factory has not been changed to inject either authorization or
   execution control. Consequently, the production path is unreachable by the
   running application.

## Execution and recovery contract

The immutable envelope now carries each episode's target ID and Sonarr series ID;
matcher and resolver behavior are unchanged. The adapter:

- checks Sonarr's series path and contains every destination inside an allowlisted
  library root;
- downloads every exact source episode into an execution-scoped staging directory;
- records a durable journal before moving any file;
- moves the prepared artifacts, then submits one `RescanSeries` per affected series;
- writes only a four-field sanitized receipt;
- returns `completed`, `absent`, or `unknown` from lookup by execution key.

Any interruption after dispatch begins is `unknown`: the local execution remains
reserved, an audit event is appended, and neither automatic nor manual reconciliation
may resubmit until lookup proves absence. Preparation failures are final and sanitized.

## Operator and readiness controls

The separately injected operator control now provides exact-confirmation execution,
exact-confirmation reconciliation and authenticated sanitized status. These routes
remain unregistered by default.

`src/v4/precutover.py` adds:

- SQLite online backup, file SHA-256/integrity verification, logical-dump SHA-256,
  and restore to a new path only with logical-content equality;
- sanitized execution health counts;
- fail-closed readiness over backup evidence, adapter probe, zero open reservations,
  and a V3 stop/shared-guard attestation;
- one-envelope canary validation. It reports `production_enabled:false` and cannot
  enable any service.

## Local evidence

- 7 focused Phase 13 tests passed.
- 21 Phase 11–13 gate/recovery/integration tests passed together.
- all 173 V4 tests passed.
- the isolated integration used a temporary database, fake downloader, temporary
  staging/library/journal roots, and an in-process loopback Sonarr double. It observed
  one bounded status probe and one `RescanSeries` family of commands, with no secret
  in stored or returned evidence.

Docker CLI discovery found a client but the current account cannot connect to the
Docker engine. Therefore no disposable real-Sonarr container result is claimed.

Phase 14 superseding evidence adds three regressions for pre-effect authorization,
bound canary validation and corrupt/incomplete journal handling. The final local V4
suite is 176 tests; see `phase14-evidence.md`.

## Remaining evidence before production authorization

The software boundary is implemented, but production cutover must not be authorized
until all of the following are supplied and independently verified:

1. Run the same adapter contract against a disposable real Sonarr matching the target
   production version and an isolated test library, including restart reconciliation.
2. Review the Phase 13 delta and the downloader/Sonarr permissions.
3. Create verified backups of the real V4 database and affected library and perform a
   restore rehearsal; the local temporary backup test is not production evidence.
4. Mount a new production Sonarr secret and explicit paths/origin without changing or
   exposing the existing secret; validate the adapter probe only.
5. Stop V3/prevent restart or deploy the shared guard, then observe and attest that no
   V3 worker or queued downstream command remains.
6. Create a separate production authorization manifest, inject the operator control,
   transfer exactly one canary target atomically, and obtain explicit user authorization
   for that production write/download. None of those actions is authorized now.
