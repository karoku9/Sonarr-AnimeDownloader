# AniDown V4 — Phase 7

Scope: only C:/Dev/AniDown-v4. No Git commits/config, V3/Sonarr writes, LLM or downloads.

- [x] Recover planning and read UI/API/architecture contracts.
- [x] Verify installed UI skills; read fresh Vercel guidelines and focused UX guidance.
- [x] Review visual direction against brief (no landing/cards/template treatment).
- [x] RED/GREEN: async job API + same-origin static runtime + local-user audit.
- [x] Build Library / Needs Review first; critique before remaining screens.
- [x] Complete Dashboard / Activity / Settings, loading/error/responsive states.
- [x] Run 176 existing tests + new contract/frontend regressions.
- [x] Start loopback 5004 and inspect all screens, interactions, narrow layout and keyboard.
- [x] UI skill quality gates; correct findings.
- [x] Freeze scope and perform independent Standards/Spec repo-review; final report.

Memory: findings.md / progress.md; visual evidence work/phase7/browser-evidence.md. 210 tests green (138 V4 +54 legacy +18 frontend). Original 5004 preview was inspected on 14 Sep; on 15 Sep Windows excluded ports4926–5025, so latest source was visually checked same-origin on temporary5904. Default code remains5004, no OS changes. Foundation behavior/thresholds frozen. Final bounded Worktree/index repo-review: no open Standards/Spec P0–P3; Phase0 migration-plan status drift corrected before final review basis.

## Phase 8 — runtime integration & E2E

Scope: only C:/Dev/AniDown-v4. Preserve V3; no live Sonarr/V3 writes, download, autosave, LLM or UI redesign. Phase7 baseline210 green.

- [x] Recover Phase7 planning and inspect only V4 bootstrap/port/Compose.
- [x] Set default6004; package isolated V4 runtime and stable sanitized offline fixture.
- [x] Test transport/Compose safety; build and start only anidown-v4.
- [x] Browser/API E2E five screens, async scan, approve/reject, local-user audit.
- [x] Restart only V4 and verify review persistence; responsive and true browser zoom200%.
- [x] Run suites/smoke and bounded Phase8-delta repo-review; report.

## Phase 9 — optional remote LLM judge

Scope: V4 worktree only. Preserve deterministic matcher/resolver and production safety. No UI, downloader, Sonarr/V3 writes, or automatic approval.

- [x] Define admissibility, sanitized shortlist, strict output and fallback tests.
- [x] Implement disabled/mock/Gemini provider abstraction with env-only key and resource limits.
- [x] Persist suggestion/fallback and sanitized input beside immutable review; hash cache.
- [x] Wire optional second level after foundation, expose evidence under Advanced only.
- [x] Forward optional provider/model/key environment only into isolated V4 Compose; validate config without changing Docker architecture.
- [x] Run 211 baseline-compatible tests plus regressions and mock smoke; Phase9 final adds 13 regressions (expected 224 total).
- [x] Freeze Worktree/index and run bounded Phase9-delta repo-review; report.

Memory: Phase9 final bounded Worktree/index review is in
`work/phase9/repo-review.md`; nine-file aggregate SHA-256
`e08ff624cb0ed840eb66d65404c22a7645793e2bfbd670073bf3fedf97374`.
No open Standards/Spec P0-P3. Pertinent final checks: 13 judge +32 application
API tests and Compose config green. Real Gemini behavior remained unverified.

## Phase 10 — one-shot Gemini smoke

Scope: read-only remote smoke only, using the existing environment-provided key
and exactly `gemini-2.5-flash-lite`. Use the frozen Phase4 production snapshot;
exercise at most the four already-selected ambiguous groups, verify one identical
cache hit when a successful response permits it, record only sanitized decisions,
token usage, latency, HTTP/error categories, and confirm Nadia remains
deterministic without an LLM call. No container restart, UI, V3/Sonarr write,
download, autosave, production mapping, or persistent application DB mutation.

- [x] Verify key/model presence without printing secrets and run the existing bounded smoke once. On 2026-09-16 the process exposed a non-empty key and the exact required model; the one authorized invocation completed with one remote call.
- [x] Record sanitized outcome and exact call count; do not retry beyond the existing one-shot script. Chainsaw Man was the sole remote case and returned HTTP 400 `INVALID_ARGUMENT`, producing the safe `provider_error` / `remote_unavailable` fallback with no decision, confidence, reason, or token usage. Nadia remained deterministic (`proposed`) with no LLM call. The temporary cache was removed and no persistent application DB changed.
- [x] Obtain one successful Gemini response and verify one identical cache hit. Completed in Phase10.2 after the model-only migration to stable `gemini-3.1-flash-lite`; the earlier 2.5 attempts remain historical evidence.

## Phase 10.1 — bounded Gemini INVALID_ARGUMENT remediation

Scope: V4 Gemini provider request only. Preserve the frozen Phase4 snapshot,
deterministic matcher/resolver, and every Phase10 production/persistence safety
boundary. One newly authorized real smoke maximum; no retry after it.

- [x] Recover planning and Git state; inspect current official Gemini request/schema requirements.
- [x] RED: add a focused fake-opener regression proving `responseSchema` uses the documented OpenAPI Schema shape. It failed on the prior lowercase/JSON-Schema request.
- [x] GREEN: minimally correct only Gemini request construction and run pertinent local tests. The focused test and all 13 judge tests pass.
- [x] Verify key/model presence without exposure and run exactly one bounded real smoke. The single call returned HTTP404 `NOT_FOUND`; no retry was made.
- [x] Record sanitized provider/cache/Nadia result and stop on the single failure. No successful response or cache hit occurred; Nadia remained deterministic without an LLM call.

## Phase 10.2 — Gemini model-access remediation

Scope: migrate only the optional V4 judge model identifier/default from
`gemini-2.5-flash-lite` to stable `gemini-3.1-flash-lite`, retaining the Phase10.1
request schema and every deterministic/production/persistence boundary. One newly
authorized real smoke maximum; no retry after it.

- [x] Recover planning/Git state and verify the current model code/capabilities against official Google sources.
- [x] RED/GREEN: update focused defaults/endpoint/Compose/smoke regressions and only necessary model references. The focused test failed on 2.5 and passes on stable 3.1.
- [x] Run pertinent local judge/config tests. All 13 judge tests and Compose config validation pass.
- [x] Verify key presence without exposure; override `GEMINI_MODEL` only in the smoke process and run it exactly once.
- [x] Record sanitized provider/cache/Nadia outcome; close Phase10 only on success. Two eligible remote groups returned HTTP200, exactly one identical cache hit was verified, and Nadia made no LLM call.

## Phase 11 — controlled live-adapter and write-path integration

Scope: V4 worktree only. Preserve V3, matcher/resolver and suggestion-only Gemini.
No production Sonarr write, production download, autosave, production mapping
mutation, concurrent V3/V4 writer operation, or Git state mutation.

- [x] Recover planning/Git state and map the V3 downstream write/download flow.
- [x] Freeze the minimum V4 adapter boundary, threat model and capability contract.
- [x] RED: add focused approved-gate, once-only, ownership and failure-atomicity regressions.
- [x] GREEN: implement the durable execution gate/ledger and fake/sandbox adapter.
- [x] Run only pertinent Phase11 tests, then the broader V4 application regressions.
- [x] Validate one approved plan only against an isolated sandbox path.
- [x] Record sanitized evidence and stop before any production adapter is enabled.

## Phase 12 — production-cutover preparation only

Scope: recover after abrupt power loss, then build only disabled-by-default cutover
controls around the Phase11 boundary. No production Sonarr/download adapter,
credential loading, autosave, production mapping/application DB mutation, V3
change, production endpoint call, or production enablement.

- [x] Re-read ledgers/Git state, verify Phase11 artifacts and rerun only the eight focused recovery regressions.
- [x] Map the runtime/control boundary and freeze the Phase12 capability contract and threat model.
- [x] RED/GREEN: persist recoverable envelopes and reconcile interrupted idempotent dispatch safely.
- [x] RED/GREEN: add atomic attested ownership transfer/rollback primitives.
- [x] RED/GREEN: add authenticated explicit execution control, unregistered by default.
- [x] RED/GREEN: add a loopback-only HTTP test gateway and strict response validation.
- [x] Run pertinent tests, then all V4 regressions.
- [x] Perform one disposable loopback-service/isolated-path cutover rehearsal only.
- [x] Update ledgers and stop before any production adapter or enablement.

## Next Step

## Phase 13 — all remaining pre-cutover implementation (production disabled)

Scope: implement and test every remaining software control needed before cutover,
while preserving the current V3/V4 runtime and deployment exactly as-is. No current
runtime wiring, production credentials/endpoints, production filesystem, Compose/UI,
V3 source, download, Sonarr write, autosave, or production enablement.

- [x] Freeze the production-adapter, permit, idempotency and recovery contracts from official Sonarr API evidence.
- [x] RED/GREEN: add a fail-closed production-shaped Sonarr/download adapter with test-only injected ports and durable lookup journal.
- [x] RED/GREEN: add explicit production-effect receipt authorization while preserving the existing default rejection.
- [x] RED/GREEN: add authenticated reconciliation/status controls, unregistered by default.
- [x] RED/GREEN: add backup verification, readiness, single-canary and rollback preflight controls.
- [x] Run the pertinent Phase11–13 regression set, then all V4 tests.
- [x] Rehearse against a disposable loopback Sonarr double and isolated test library only.
- [x] Update the runbook and ledgers; stop with production enablement still absent.

## Next Step

Phase13 implementation is complete under test-only authority. Production remains
disabled and unreachable from the current runtime/deployment. Before cutover can be
authorized, obtain a disposable real-Sonarr/test-library result, review this delta,
verify production backup/restore and secret/path configuration, observe V3 quiescence,
then request a separate one-canary production authorization.

## Phase 14 — final pre-cutover evidence (production untouched)

Scope: freeze and audit the complete Phase11–13 downstream/cutover boundary, add only
test/evidence hardening proven necessary by review, and retain a sanitized reproducible
evidence package. No production endpoint, secret, process, database, path, container,
download, Sonarr command, V3 change, runtime/Compose/UI enablement, or Git mutation.

- [x] Recover planning and complete Worktree/index inventory; freeze the bounded Phase14 review basis.
- [x] Perform independent Standards and Spec review of the Phase11–13 contract chain.
- [x] Reproduce and fix any reachable pre-effect authorization or recovery defect with focused local tests only.
- [x] Exercise isolated crash, journal, backup/restore, secret-redaction and default-off evidence.
- [x] Run the pertinent gate suite and all V4 tests on the final basis.
- [x] Retain a sanitized file/hash/check manifest and final local review verdict.
- [x] Update ledgers/runbook and state the exact external evidence still required before production authorization.

## Next Step

Phase 14 local evidence is complete and production remains disabled. The next phase
is an explicitly authorized operational validation/cutover phase: disposable real
Sonarr plus restart reconciliation, independent review, real backup/restore rehearsal,
reviewed production secret/path/origin deployment, observed V3 quiescence/ownership
transfer, and finally a separately authorized single production canary. None is
authorized by this plan.

## Phase 15 — disposable Sonarr validation harness (production untouched)

Scope: implement and locally prove a dormant operator harness for the first remaining
external gate. It may target only an explicitly marked loopback disposable Sonarr and
isolated paths, use a generated fixture artifact rather than a real download, and
retain sanitized compatibility/fresh-adapter durability evidence. Do not run it against any current
service, production path, credential, database or process. Do not register it in the
runtime, API, UI or Compose, and do not change V3, matching or resolver behavior.

- [x] Recover the Phase 14 basis and freeze the disposable-validation safety contract.
- [x] RED: add focused tests for loopback, marker, path, version and secret-output gates.
- [x] GREEN: implement a dormant disposable Sonarr validation runner and CLI wrapper.
- [x] Prove one isolated execution and fresh-adapter durable lookup against a loopback double.
- [x] Run the pertinent execution/pre-cutover tests, then the full V4 suite.
- [x] Update operator documentation and ledgers with sanitized evidence and remaining gates.

## Next Step

Phase 15 is implemented locally; Phase 15.1 below records the corrective identity
gate applied before final verification.

## Phase 15.1 — corrective disposable-service identity gate

Scope: correct the reachable Phase 15 safety gap without invoking any current or
production service. A local marker and version match alone do not prove the loopback
Sonarr is disposable. Require a fixed remote instance identity from the status probe
before any series lookup, journal, artifact or command. Preserve every production
prohibition and keep the harness unregistered.

- [x] RED: prove a same-version loopback service with the wrong instance identity reaches effects under the current code.
- [x] GREEN: require the exact disposable instance identity before any effect-capable step.
- [x] Verify wrong/missing identity produces zero series calls, commands, journal or artifacts.
- [x] Resume the pertinent Phase 15 gate suite, then all V4 tests.
- [x] Update the dormant-harness documentation and ledgers; do not invoke a real service.

### Phase 7.1 compatibility correction

- [x] Reproduce the invalid canonical identity with a focused regression.
- [x] Replace it with exact identity `Sonarr AniDown V4 Disposable Validation`.
- [x] Encode Sonarr's real first-or-last-word naming constraint in the test suite.
- [x] Rerun the marker/path/version/one-command safety gates and relevant V4 suites.

## Next Step

Phase 15/15.1 implementation is complete and remains dormant. The next authorized
step must be operational evidence against a specifically identified disposable real
Sonarr, including an actual service restart; current or production services remain
forbidden. Independent review and all production backup/deployment/quiescence/canary
gates also remain.

## Phase 16 — frozen-corpus matching correction (shadow/test only)

Scope: iteratively correct V4 discovery, classification, identity, structure and
language selection against the frozen 192-target shadow corpus and the already
captured `work/remote-preview/full-reset/live-zero` evidence. No live fetch, current
service access, production mutation, download, autosave, V3 dependency or production
runtime wiring. Gemini remains optional suggestion-only over a deterministic bounded
shortlist and cannot create candidates, URLs or executable decisions.

- [ ] Freeze a reproducible baseline evaluator and independent audit with outcome buckets: correct proposal, false positive, true review, waiting/airing, unavailable/no-source and language mistake.
- [ ] RED/GREEN: exclude untagged Sonarr anime before candidate/review creation; regress Gintama.
- [ ] RED/GREEN: classify waiting/airing and verified no-source separately from Needs Review; regress Star Blazers 2199 S3/2205.
- [ ] RED/GREEN: add a maintained conservative alias data path; regress Grendizer/Goldrake and reject unsafe global aliases.
- [ ] RED/GREEN: make retrieval independent of legacy V3 mappings and prove native candidates remain discoverable.
- [ ] RED/GREEN: support evidence-backed multipart/composite plans and verified one-release/multi-season boundaries without weakening hard gates.
- [ ] RED/GREEN: prefer equivalent Italian DUB coverage over SUB/Japanese in AUTO; regress Cowboy Bebop, Ergo Proxy, AoT S3 and Hi Score Girl S2.
- [ ] Revalidate Gemini shortlist-only arbitration and deterministic result validation; deduplicate reason codes.
- [ ] Iterate corpus parse/audit/fix until every remaining review is justified and every automatic proposal passes captured link/title/year/episode/language checks with zero false positives.
- [ ] Run focused and full V4 regressions, retain sanitized evaluation evidence, and update the final counts/report.

## Next Step

Inventory the evaluator, frozen corpus and captured audit artifacts; produce the
current outcome/error baseline before the first RED test.
