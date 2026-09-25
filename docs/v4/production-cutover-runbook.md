# AniDown V4 production cutover runbook

Status: preparation only. Every production step below is a stop gate, not current
authorization. Phase13 added a production-shaped adapter and explicit effect
authorization. Phase 2 lets an explicitly injected, revision-bound adapter enter the
runtime only after capability preflight; the CLI and checked-in deployment do not
construct that capability, and the environment enable flag alone remains insufficient.
See `phase13-precutover.md` for the local evidence and remaining gaps.
The dormant Phase 15 disposable validation harness is documented in
`disposable-sonarr-validation.md`; its local loopback-double result is not real-Sonarr
or service-restart evidence.

## Preconditions — all required

1. A separately reviewed, idempotent Sonarr/download adapter exists. Its lookup by
   V4 `execution_key` must distinguish completed, definitely absent and unknown.
2. The adapter has passed the Phase12 contract suite plus a disposable Sonarr and
   isolated-library test using production-equivalent versions and permissions.
3. Production credentials are injected by the deployment secret mechanism, never
   stored in SQLite, configuration committed to Git, audit events or UI/API output.
4. The V4 application database and target library have verified backups and a tested
   restore procedure. Record identifiers/checksums, not secret-bearing paths, in the
   change record.
5. The operator execution route is explicitly injected with a newly generated secret,
   remains loopback/reverse-proxy restricted, and has no UI or automatic caller.
6. No reserved or unknown execution exists. Resolve each through adapter lookup;
   never resubmit an unknown outcome.
7. The one-canary production authorization is bound to the deterministic execution
   key and is verified before reservation, journal creation or any external effect.
8. Startup preflight has verified the secret-backed adapter, Sonarr status, writable
   staging/library/journal roots, same-filesystem move semantics, exact V4 writer
   claims and a clean reconciliation result. API/UI capability must still say disabled
   if any one of these checks fails.

## Single-writer transfer

1. Stop the V3 scheduler/service and prevent automatic restart.
2. Verify no V3 scan/download worker or queued post-download Sonarr command remains.
3. If V3 is integrated with the shared guard instead, verify its exact V3 claims and
   that it has relinquished the complete atomic target set.
4. Record operator, reason and verification method. Never record process tokens,
   credentials or raw service output.
5. Atomically transfer the complete target set from its exact V3 owner token to the
   intended V4 owner token using `ApplicationStore.transfer_writer`.
6. Recheck V3 remains stopped. A V4 database claim alone does not prove this.

## Canary execution

1. Select one low-risk, explicitly human-approved current plan with verified backup.
2. Confirm target set, revision, plan, variants and expected episode coordinates.
   Generate the authorization only for that envelope's deterministic execution key.
3. Submit exactly once through the authenticated manual control with the exact
   confirmation phrase. Do not script a batch or enable autosave.
4. Verify adapter receipt, downloaded artifact, Sonarr import/rescan result and audit
   record independently before considering another target.
5. On timeout or power loss, run lookup/reconciliation by execution key. Never create
   a second reservation or alter the immutable envelope.

## Failure and rollback

- `unknown`: stop. Keep the reservation open and investigate the remote idempotency
  record. Do not dispatch.
- `failed` before any external effect: retain audit evidence; retry/reset policy
  requires separate authorization and a new reviewed workflow.
- completed remote effect but incomplete local record: lookup must recover the same
  sanitized receipt, then close the existing reservation.
- rollback to V3 is prohibited while any affected execution is reserved. After the
  downstream state is reconciled, use an explicit `operator_rollback` attestation,
  transfer ownership atomically, then start only one writer.
- If source files or Sonarr state are inconsistent, keep both writers stopped and
  restore from the verified backup before any additional action.

## Current hard stops

Do not perform production cutover from the current code. Missing evidence is:

- a disposable real-Sonarr/test-library integration and restart-reconciliation result;
- reviewed production deployment wiring for the explicit secret/config factory;
- an observed V3 stop/shared-guard verification on the production host;
- production database/library backup and restore evidence plus canary monitoring;
- explicit user authorization for production writes and downloads.
