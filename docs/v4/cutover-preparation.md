# V4 production-cutover preparation contract (Phase 12)

Status: authorized for local fake/test boundaries only. This phase prepares the
control plane; it does not implement or enable production Sonarr/download effects.

## Objective and assumptions

Phase11 proved approved-only handoff but deliberately left interrupted reservations,
writer transfer and operator invocation unresolved. Phase12 makes those boundaries
recoverable and testable without adding a live adapter.

The user's autonomous Phase12 authorization is treated as approval of this bounded
capability map. A later phase must separately authorize actual service credentials,
production endpoints, download paths and effects.

## Capability map

| Module id | Responsibility | Depends on |
|---|---|---|
| execution-recovery | Persist the validated envelope and reconcile an interrupted reservation by idempotency key | Phase11 ledger |
| cutover-ownership | Atomically transfer all target claims with an explicit stop/shared-guard attestation and controlled rollback | Phase11 claims |
| operator-control | Require a bearer secret plus exact confirmation for one approved execution; remain absent from default runtime | execution-recovery, cutover-ownership |
| test-gateway | Exercise lookup/submit semantics over loopback HTTP with strict bounded JSON and no credentials | execution-recovery |

Build order: execution-recovery → cutover-ownership → operator-control and
test-gateway → disposable rehearsal.

## Interface and state contract

- Every new reservation stores the exact validated envelope beside the immutable
  attempt identity. The envelope is SQL-immutable and contains no credentials.
- Recovery always performs `lookup(execution_key)` before dispatch. A confirmed
  completion closes the reservation without resubmission; a confirmed absence may
  submit the same envelope once; an unknown result remains reserved and fails closed.
- Production-shaped execution requires targets to be preclaimed by the exact V4
  owner token. Phase11 fake compatibility may still use its original auto-claim path.
- V3→V4 transfer requires current V3 owner/token matches and an attestation with
  `v3_stopped=true` plus a bounded operator, reason and verification method.
- Rollback requires exact current V4 ownership and no reserved attempt affecting the
  targets. Transfers and audit events commit atomically.
- The manual API is constructor-injected only. Default `create_app()` and runtime
  register no execution route. When injected, it accepts only POST, a constant-time
  checked bearer token, exact confirmation text and a nonnegative expected revision.
- The Phase12 HTTP adapter accepts only explicit loopback `http` endpoints, follows
  no redirects, sends no credentials, limits response bytes and validates a small
  JSON status/receipt schema. Its test service must report `production_effects=false`.

## Threat model

Assets are the execution right, writer ownership, approved envelope, operator secret
and audit history. Threats include forged stop attestations, token disclosure,
duplicate dispatch after crash, SSRF, redirects to production/private services,
oversized or malformed gateway responses and partial multi-target transfers.

Controls are parameterized transactions, immutable payloads, exact owner-token CAS,
bounded attestation fields, constant-time secret comparison, no secret persistence or
logging, loopback URL allowlisting, disabled redirects, response size/schema limits,
idempotency lookup before submit and fail-closed unknown recovery.

## Commands and testing

- Recovery gate: `python -m unittest tests.v4.test_downstream_execution`
- Phase12 focused tests: `python -m unittest tests.v4.test_cutover_preparation`
- V4 regression groups use the existing `python -m unittest` module commands recorded
  in `progress.md`; no external service or production path is permitted.

## Success criteria

1. Interrupted-before-submit and interrupted-after-test-service-submit cases reconcile
   without duplicate dispatch.
2. Unknown reconciliation never submits and remains auditable/reserved.
3. Ownership transfer/rollback is atomic and requires exact attestation/token state.
4. Default app/runtime expose no execution endpoint; injected control rejects missing
   or wrong auth/confirmation without reaching an adapter.
5. One authenticated approved request reaches only a loopback fake gateway and an
   isolated path, exactly once, with no secrets in ledger/audit/output.
6. Existing matcher, resolver, Gemini, V3 and UI behavior remains unchanged.

## Never in Phase 12

No production URL, Sonarr API key, downloader, V3 writer modification, mapping
autosave, production application database, automatic execution, UI execution button,
Git staging/commit/config/reset/discard, or broad unrelated redesign.
