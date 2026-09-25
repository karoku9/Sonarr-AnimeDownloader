# V4 downstream execution contract (Phase 11, Phase 2 runtime hardening)

Status: authorized implementation contract. Phase 2 contains a production-shaped
adapter, but checked-in configuration never constructs or enables production effects.

Phase 2 makes this contract the only runtime execution path. Wanted/missing polling
is scheduling input only: it may submit a current mapping to `ExecutionCoordinator`,
but it never copies URLs into a second effect queue and never downloads, moves, or
rescans itself. Production remains disabled in the checked-in configuration.

## V3 flow and boundary

V3 `Core.job()` obtains Sonarr wanted/missing episodes through `Processor.getData()`.
The processor filters targets and attaches mapped source URLs. `Downloader.download()`
then fetches source episodes and can move files, request Sonarr series rescans and
request Sonarr file renames. V3 mapping automation and table persistence are outside
the V4 boundary and remain unchanged.

V4 hands a downstream adapter one immutable execution envelope. The envelope is
derived only from the current approved item and contains:

- item ID, immutable snapshot digest and human-approved revision;
- a deterministic SHA-256 idempotency key for that item snapshot, approved revision,
  exact wanted target/episode coordinate, and any execution-relevant manual approval
  or audio policy;
- the one target ID selected by the current Sonarr wanted/missing record;
- the approved candidate and the one explicit source-episode to
  Sonarr-season/episode coordinate needed for that wanted episode.

The adapter treats each episode-scoped envelope as one idempotent unit. It must not
select a different plan, candidate or episode mapping, and it must never expand one
wanted episode into already-present episodes. A multi-target snapshot may therefore
have several distinct execution keys, while the exact same target/episode key remains
once-only.

## Capability map and build order

1. **Approved execution policy** validates current ownership, current revision,
   immutable digest, human approval, selected original plan and selected variants.
2. **Durable execution ledger** reserves each immutable episode-scoped execution key
   at most once and records reserved, completed or failed outcomes without changing
   mapping decisions.
3. **Writer ownership guard** gives one owner (`v3` or `v4`) exclusive ownership of
   the target affected by an episode envelope. Mapping currentness is still checked
   across the complete immutable item before reservation.
4. **Adapter boundary** accepts the validated envelope only after policy, ledger and
   ownership checks have committed.
5. **Fake/sandbox adapters** prove routing. The sandbox adapter publishes one JSON
   receipt atomically inside an explicitly supplied isolated directory.

The execution coordinator depends on capabilities 1–4. Capability 5 is the only
implementation allowed to create downstream artifacts in Phase 11.

## State and error contract

- `proposed`, `needs_review`, `rejected` and `superseded` items never reach an adapter.
- An approval is executable only at its current revision and only while every target
  still points to the same current item.
- Automatic and human-approved provider plans require durable provider validation
  state `current`; `pending`, `stale_retry`, and `invalidated` fail before reservation.
  Manual overrides instead require their own live source-fingerprint revalidation.
- One exact snapshot/revision/target/episode execution key can be reserved once. A
  failure remains a consumed, auditable attempt; there is no automatic retry or reset
  path for that key.
- Ownership verification/acquisition and execution reservation share one SQLite
  transaction.
- Adapter execution occurs after reservation. A failure changes only the execution
  ledger to `failed` and appends a sanitized audit event; it never changes the item,
  approval, immutable snapshot or current-target mapping.
- Adapter errors are stored as bounded categories, not raw response bodies, paths,
  credentials or exception messages.
- Sandbox publication uses a temporary file and atomic replacement, so an adapter
  error cannot expose a partial receipt.
- A pre-effect or pre-rescan journal can resume only under the same execution key.
  Once rescan dispatch begins, an unrecorded remote result is `unknown`; it remains
  reserved and is never dispatched again automatically.

## Threat model and trust boundaries

Untrusted inputs are item IDs/revisions, persisted JSON, candidate URLs, adapter
exceptions and concurrent writer claims. Controls are parameterized SQL, immutable
snapshot digest verification, membership validation against the approved snapshot,
strict state/revision checks, exclusive transactions, unique reservations, bounded
error categories and an adapter protocol that receives no Sonarr credentials.

The SQLite ownership guard coordinates participants that use this contract. V3 does
not yet use it and is intentionally not modified in Phase 11. Consequently a future
production cutover must stop and verify the V3 writer (or explicitly integrate it
with this same guard) before V4 ownership can be granted. A database claim alone is
not evidence that an unaware V3 process has stopped.

## Phase 11 acceptance

Focused tests must prove all non-approved states are blocked, only an approved plan
reaches the adapter, duplicate execution is blocked, V3/V4 target ownership is
exclusive under concurrency, and adapter failure is auditable without partial V4
mapping-state mutation. After those pass, one isolated-path smoke may execute a
single approved fixture plan. Production adapters and runtime/API enablement remain
out of scope.
