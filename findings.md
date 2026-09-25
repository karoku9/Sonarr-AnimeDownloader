# Findings
Read docs/v4/architecture.md, matching-spec.md and migration-plan.md.
repo-map preflight: CAPABILITY_MISSING repository.asset.scan compatible v1; no Forgeway runtime/tools installed. Recovery: provide compatible host runtime. Graphify AST navigation is an alternative, not a validated repository asset snapshot.
No AGENTS.md found in worktree. Existing V3 offline suites: search, mapping_management, runtime_mapping. tests/test_components.py and src/test_search_v3.py are integration/manual runners with network, startup or writes; not compatible with read-only verification.
Baseline must omit runtime JSON, personal dump fixtures, env files, backup files and detected credentials; preserve excluded files on disk. No broad staging.
User's current reason-code names supersede draft names in matching-spec. Foundation is offline only; no confidence score authorizes automapping.

Foundation now passes 23 tests. URL collisions detected across same-snapshot candidate rows and prior mapped seasons. Different provider season numbers require explicit target_seasons crosswalk. Legacy table/index source is untrusted in shadow to avoid circular season proof. Shadow compares pure V3 ranking replay, not historical persisted decisions.

Date-vs-year precision now compared; mismatches block selection, missing dates do not hard-fail. Audio tie-break and informational mismatch have explicit evidence. Source provenance currently record/alias level; field-level reliability deferred. Real V4-local src/database/table.json is empty: shadow cannot assess production parity without a future sanitized local catalog snapshot.

repo-review Spec pass found unverified exact preventing last-resort fuzzy suggestion with verified season. Focused regression reproduced; corrected strong-candidate gate to require season verification. Fuzzy remains review-only. Standards unknown-season/global-alias issue likewise reproduced/fixed.

Phase3: V4 unstaged with git restore --staged; no working files changed. Local documented mapping sample contains 81 real title/URL entries; this is documentation data, not production metadata or independently verified season identity. Mandatory four series currently available as synthetic regression fixtures only. Do not relabel them real; include explicitly separated regression and local-documentation cohorts in shadow. repo-map compatible runtime remains unavailable.

Phase3 final: field provenance supersedes earlier deferred status. Verified crosswalk anchors require reliable fields; derived URL usage explicitly marked derived. SQLite original review immutable, revision CAS and lifecycle separate. Shadow 90 series/100 targets/105 candidate ID+URL pairs (104 URLs), 87 doc observations/6 Sonarr targets/7 synthetic cases; 94 review proposals, never persisted by shadow. 83 selection differences. V3 synthetic Ranma remake wrong; Crystal S1/S2 abstentions. V4 six known-answer cases correct and shared URL review. Real required four absent: production FP/FN not verified.

Phase4 real source supersedes prior missing-real-data gap: 121 Sonarr series,5595 episodes,6786 catalog entries,112 mappings; mandatory four all present. Anime replay114 series/192 targets/927 enriched native candidates. Actual Black Lagoon Sonarr S1=24 split scene1/2; generic explicit episode crosswalk, not same as synthetic split S1/S2. Catalog counts have catalog_release scope; no invented Sonarr coverage. 15 auto,177 review;174 missing crosswalk,58 equivalents,40 coverage,38 fuzzy,9 missing dates. One paired auto disagreement Kobayashi S2;11 probable V3 errors not ground truth. Provider-native label replaces false table label e.g Ranma->Senko. Thresholds/model/matcher unchanged. Windows byte hashing manifest fixed with semantic-payload checks. Future failed table detail quarantined.


Phase5: 192 frozen targets; 128/174 missing crosswalk resolved; 145 auto,47 review; one Black composite; one inferred Crystal shared release, zero verified boundaries. 144 tests green (90+54). Source dates/numbering retained, thresholds unchanged. 24 transport-only/19 other disagreements; Chainsaw date-variant ambiguity is one possible future judge triage. See docs/v4/release-resolver.md and work/phase5/replay. Repo-review pending.

Phase5 repo-review complete: Worktree HEAD e979b64c5a51c0ec9e33ac9c135bd5d641f7846f, branch v4, staged empty. Independent sequential Standards and Spec passes; no open P0–P2. 14 prior source hashes unchanged; exact192 target set and baseline unchanged. Scope offline only; runtime/CI/production inverse-boundary verification not verified. Evidence work/phase5/review-basis.json and repo-review.md. Stop: no LLM/UI/downloader/runtime writes.

Phase6: application orchestrates offline metadata adapter/native candidate rebuild/frozen foundation/local storage. Composite reviews group target IDs atomically (Crystal S1+S2). ApplicationStore extends ReviewRepository DB primitives in separate v1 tables; original and audit immutable via SQL. WSGI standalone /api/v4 DTO API; no web dependency install, mount or server startup. 29 integration tests green after fixing a test-only unclosed sqlite connection. Contract docs/v4/frontend-api-contract.md. Full144 regression suite and repo-review pending.

Phase6 real application scan:192 targets→191 atomic plan units;144 proposed,47 open reviews,0 approved. All network patched forbidden. One foundation retained match without concrete plan becomes application review, not changed matcher behavior. JSON report work/phase6/application-scan-report.json; local DB work/phase6/application-v1.sqlite3. Baseline144 maintained;31 new tests passed,32nd scan-never-approves invariant added; final176 verification and repo-review pending.

Phase6 final:176 tests green (90 foundation V4 +32 API integration +54 legacy,
separate processes). False whole-season date conflict for Black composite
presentation reproduced by failing test and fixed using segment crosswalk evidence;
raw historical evidence remains Advanced. Foundation12 protected Phase5 source
hashes unchanged. Independent Standards/Spec repo-review bounded pass; all29 scope
hashes fixed, staged empty. SQLite read-only integrity/digest/current-scope checks
passed,192target set preserved,0automatic approvals. Frontend contract4.0 complete;
API standalone/unmounted, WSGI live/proxy/operator-authentication not verified.
Future decisions: runtime mounting/same-origin, authenticated actor if needed,
explicit old-review import, async scans and downstream routing guards for approved
inferred plans. No frontend/LLM/downloader enabled. Stop after report.


## Phase 7 direction / skill gate
Verified installed frontend-design, ui-ux-pro-max, web-design-guidelines under C:/Users/Willi/.agents/skills. Vercel v1.0.0 source fetched 2026-09-14: https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md. Skill source hashes retained in phase7 design direction. UI Pro Max initial media query returned irrelevant entertainment landing guidance; narrower utility query returned relevant Flat Design, no gradients/shadows, fast controls; conversion pattern and generic font palette rejected against user brief. Target: serious local library/review tool, dark neutral surfaces and restrained blue. ES modules + semantic HTML + CSS tokens, no new dependency/build/CDN. Frontend calls HTTP DTOs only. Native dialog for audited decisions; no raw URL except Advanced. Async adapter around unchanged scan service; process restart marks interrupted jobs failed. No automatic scan/import on startup. local-user is an explicit unauthenticated audit label.


## Phase7 current findings
Windows TCP excluded range4926–5025 contains the requested live port5004 on15 Sep; code still binds5004 by default and prior14 Sep browser pass used actual5004. Temporary5904 allows final UI verification without OS/network config mutation. Browser JPEG capture is compressed visual evidence; DOM/computed measurements are separate. Browser native zoom command did not change page scale; effectiveCSS640 across five screens and CSS375 Crystal support responsive layout, but native zoom200% cannot be claimed. DefaultActivity must filter automatic scan item audit events, or a191-item scan floods the operation timeline; immutable raw audit remains under Advanced. Main preview has144proposed47openReviews/0 approved. Old source paths outside src/v4/frontend-v4 Phase7 are inherited dirty worktree context and not part of this implementation review.
# Phase 9 findings

The original V4 review snapshot is fingerprinted and immutable. Placing a judge timestamp or outcome inside it would supersede the same scan and could disrupt human resolutions. A linked append-only attempt avoids that and exposes the suggestion only via Advanced evidence. Existing matcher evaluations carry both `hard_rejected` and structured `reason_codes`; the judge now excludes either explicit hard rejection or season/duplicate-URL structural reason, guarding even against a missing flag. Gemini API key is not present in the process environment, so provider behavior was verified through the deterministic mock and a fake HTTP opener rather than billed API calls.

Phase9 bounded repo-review is complete: no open Standards/Spec P0-P3 across the
nine-file delta;13 judge +32 application API checks and Compose config passed.
Real Gemini/model/quota/token behavior is intentionally deferred to the bounded
Phase10 smoke. The no-UI Phase9 scope leaves normal Settings status presentation
unchanged; operational provider selection remains environment-only.

## Phase 10 finding

The agent process has no Gemini credential/model configuration. The existing smoke
fails closed before opening the Phase4 snapshot or making HTTP calls. Docker API
access is denied in this managed session, so the running container cannot provide a
verified alternate environment. Completion requires a future process launched with
the key and exact `gemini-2.5-flash-lite` model already in its environment.

The variables now exist in William's Windows User configuration, but the managed
agent executes as a different restricted Windows identity and did not inherit them.
William's User environment hive is access-denied through all bounded read paths
tested. This is an environment propagation boundary, not evidence that the values
are missing or invalid. Restart/injection at host process creation is required; do
not copy the key into repository files, logs, commands, or chat.

## Phase 11 findings

The operative V3 write boundary is later than matching: `Processor` turns Sonarr
wanted/missing records plus persisted/automatic mappings into series/season URL
bundles, then `Downloader` resolves and downloads episodes, optionally moves files,
and calls Sonarr rescan/rename commands. Reusing either class would also import V3
mapping automation and runtime policy, so the minimum clean V4 boundary is an
immutable execution envelope containing the approved source variant and explicit
episode coordinates.

Approval alone is insufficient for safe execution. The gate must also prove the
item revision is current, every target still belongs to that item, the human-selected
plan and variants are members of the immutable snapshot, the snapshot has never had
an execution attempt, and every target is owned by the same V4 writer token. These
checks and the reservation must be one transaction; otherwise two processes can
both pass a check before either records ownership.

The Phase11 SQLite claim prevents concurrent cooperating `v3` and `v4` owners and
the concurrency regression proves only one wins. It cannot stop the existing V3
process by itself because V3 intentionally remains unchanged and does not consult
the V4 database. Production safety therefore requires external stop-and-verify of
V3 or a separately authorized shared-guard integration before transferring any
target to V4. Claim presence is not proof that V3 is inactive.

An external download/Sonarr workflow cannot be made transactionally atomic with a
local SQLite row. The Phase11 sandbox adapter is atomic at its isolated receipt
boundary and every real adapter must be idempotent by `execution_key`. Before live
use, a durable reconciliation path must handle a crash after a remote side effect
but before the local completed outcome is stored. Phase11 deliberately consumes a
failed snapshot reservation and provides no retry/reset or ownership-release path;
those require explicit operator policy in the cutover phase.

The successful isolated smoke is evidence for approved-only routing and local
state safety, not Sonarr compatibility or download correctness. Remaining cutover
evidence must come from a disposable Sonarr/test-library environment, followed by
an explicit production authorization; no production adapter, credential loading,
API/UI execution action, mapping autosave or production write was enabled here.

## Phase 10 credentialed smoke finding

The resumed process inherited both required environment values, allowing the
planned one-shot smoke to cross the pre-network guard. The harness made exactly one
Gemini call, for the eligible Chainsaw Man group from the frozen Phase4 snapshot.
The provider returned HTTP400 with sanitized category `INVALID_ARGUMENT`; the V4
judge converted that to the expected safe `provider_error` / `remote_unavailable`
fallback and made no suggestion. No token metadata was available. Because the
response was unsuccessful, no result was cached and the explicitly conditional
identical cache-hit check was not permitted. Nadia was independently found in the
deterministic `proposed` state and did not invoke the LLM.

The smoke intentionally retains neither the raw Gemini error message nor the
temporary cache, so the evidence does not identify which request argument Gemini
rejected. A schema or other field-level diagnosis would be a new bounded
remediation task, not a justified inference from `INVALID_ARGUMENT`. Phase10 cannot
close—and AniDown V4 should not advance—until a newly authorized attempt first
proves the compatibility correction locally, then obtains one successful bounded
response and verifies its identical cache hit. All production and persistence
safety boundaries remained intact.

## Phase 10.1 official-contract diagnosis

Google's current `generateContent` reference defines `generationConfig.responseSchema`
as an OpenAPI-style `Schema`. Its `type` field is one enum value, nullability is the
separate boolean `nullable` field, and its documented object fields do not include
`additionalProperties`. Google's current Gemini 2.5 migration example likewise uses
`responseSchema` with scalar `OBJECT`, `STRING`, and `ARRAY` type values. AniDown's
request sends a JSON-Schema union (`type: ["string", "null"]`) plus
`additionalProperties: false` through `responseSchema`. Those two shape mismatches
are the evidence-backed cause hypothesis for the observed HTTP400
`INVALID_ARGUMENT`; the bounded correction is to keep `responseSchema` and express
the candidate ID as `{type: "STRING", nullable: true}`, with only documented Schema
fields. Sources: https://ai.google.dev/api/generate-content#Schema and
https://ai.google.dev/gemini-api/docs/migrate-to-interactions#structured-output.

## Phase 10.1 real-smoke finding

The documented OpenAPI `responseSchema` correction is locally verified by a
fake-opener capture and all13 judge regressions. The one authorized real request
crossed the former schema rejection boundary—the status changed from HTTP400
`INVALID_ARGUMENT` to HTTP404 `NOT_FOUND`—but it still produced no model response.
The exact sanitized remote category is `NOT_FOUND`; no raw remote response was
retained. The application therefore has no tokens, decision, confidence, reason or
cache entry to report. This evidence verifies the local request shape but does not
establish why the remote endpoint could not find the requested model/resource.

The bounded attempt made one call only, used the frozen Phase4 Chainsaw Man group,
and left Nadia deterministic with no LLM call. Phase10 cannot close because its
successful-response and identical-cache-hit conditions remain unmet. Per the
authorized stop rule, further model/endpoint diagnosis and any additional remote
request are outside Phase10.1 and require new user authorization.

## Phase 10.2 model contract

Google's current Gemini 3.1 Flash-Lite model page identifies the stable model code
as `gemini-3.1-flash-lite` and lists structured outputs and caching as supported.
Google's release notes record the stable GA release on 7 May 2026 and the shutdown
of `gemini-3.1-flash-lite-preview`; the stable model remains a documented API
model. This supports a model-identifier-only migration while retaining the proven
`generateContent` request shape. Sources:
https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite and
https://ai.google.dev/gemini-api/docs/changelog.

The necessary live-path references are the `GeminiProvider` default, the configured
provider fallback, isolated V4 Compose default, V4 judge operator documentation,
and Phase10 smoke guard. Historical Phase10/10.1 ledger entries must retain the
2.5 identifier because they describe calls that actually occurred. Diagnostic
one-off scripts not used by the authorized smoke are outside the required live
path and must not be executed.

## Phase 10.2 successful model-access finding

Stable `gemini-3.1-flash-lite` resolved the model-access failure with the unchanged
Phase10.1 request schema: both eligible remote requests returned HTTP200. The frozen
snapshot exercised Chainsaw Man and JoJo S2 remotely; Sailor Moon Crystal and
Helluva Boss were rejected locally as not judge-eligible. Chainsaw selected
`ecb5dbdce2f9d9dd` at confidence0.90 and JoJo selected `4cc45e8785216e15` at
confidence0.90. The two calls used1074 and1925 total tokens respectively. Exactly
one identical Chainsaw evaluation was served from the temporary V4-local cache with
no third remote call. Nadia remained deterministic `proposed` and never reached the
provider.

This proves optional suggestion-only Gemini transport, strict structured output,
local validation and identical-input cache behavior for the bounded production
snapshot sample. It does not authorize automatic approval or production routing.
All output remained advisory and temporary; no production mapping/application DB
mutation occurred. Phase10 is closed.

Phase11 subsequently completed the approved-only fake/isolated adapter boundary as
recorded above. Production cutover remains separately gated by the real-adapter,
reconciliation, V3-stop/shared-guard, disposable-service and operator-control work
listed in the Phase11 findings.

## Phase 12 findings

The Phase11 ledger could identify a reservation after a crash but could not recreate
the exact adapter input; durable reconciliation therefore requires the validated
canonical envelope to be persisted immutably at reservation time. JSON
canonicalization before both return and storage avoids an otherwise subtle tuple/list
representation drift across process restart.

Idempotent recovery has three materially different outcomes. Confirmed completion
closes the existing reservation with the recovered receipt; confirmed absence may
submit the same persisted envelope; unknown state must remain reserved and must not
dispatch. Conflating absence with timeout/error would create the duplicate-download
risk the ledger is intended to prevent.

Target claims are useful only when production-shaped execution requires an exact
preclaim. Phase12 retains Phase11 auto-claim compatibility for fake callers but adds
`require_preclaimed=True` for cutover control. Transfer uses exact owner/token CAS
across the whole target set and records only operator/reason/method—not owner tokens.
The `v3_stopped` attestation is an auditable assertion, not technical proof that an
unaware V3 process is stopped; the runbook therefore requires independent host
verification before and after transfer.

The operator route is safe by absence: normal factories do not inject it, Compose
does not configure it, and there is no UI action. Injection requires an in-memory
bearer token, constant-time comparison and an exact confirmation phrase. This is a
cutover control primitive, not a complete production authentication system; proxy/
deployment authentication and secret lifecycle remain future gates.

The loopback HTTP adapter proves production-shaped lookup/submit semantics without
being a production adapter. It accepts only explicit loopback IP origins, rejects
credentials and redirects, bounds JSON responses and sends no service secret. The
successful one-shot rehearsal proves local orchestration only—not real Sonarr API,
download, import, filesystem permission or rollback correctness.

## Phase 13 findings

Production safety is strongest when authorization is a capability object, not a
mutable setting. The default coordinator still rejects every
`production_effects:true` receipt. A production receipt is accepted only when its
adapter/mode/receipt execution key match an exact one-canary authorization manifest,
while construction of the real adapter separately requires explicit paths, origin
and secret file. None is loaded
from the environment or created by the normal runtime.

The immutable execution envelope needed two additional downstream coordinates:
`target_id` and integer `sonarr_series_id` per episode link. They are derived from the
already selected immutable targets and do not alter matching/resolution. Carrying them
explicitly avoids ambiguous series inference during import and lets the adapter verify
every destination against the approved target set.

Adapter idempotency requires evidence before the first filesystem effect. A journal
left in `preparing` or `dispatching` is deliberately `unknown`, not absent. In
particular, a Sonarr HTTP error after moving a file cannot prove that the command had
no effect. Phase13 therefore keeps the application reservation open and requires
authenticated lookup/reconciliation rather than finalizing a false failure or trying
again. Receipt write failure after successful effects is handled the same way.

Security regressions found that Python integers compare equal to booleans in sets;
receipt policy now uses identity checks so `1` cannot impersonate `True`. Sonarr
redirects are disabled, origins reject embedded credentials/path/query fragments,
responses and secrets are bounded, filesystem roots cannot be volume roots, download
artifacts cannot escape their execution directory, and Sonarr-provided library paths
must remain inside explicit allowlists.

The backup primitive intentionally restores only to a new path. It verifies SQLite
integrity, byte size and SHA-256 but does not overwrite a live DB. Readiness remains
an evidence aggregator: a V3 stop attestation is auditable evidence, not process-level
proof, and temporary backup tests are not production backup evidence.

Official Sonarr source confirms `X-Api-Key` header authentication in its OpenAPI host
configuration. Local tests prove our bounded request/command shape against a loopback
double, not a real Sonarr version. Docker engine access was denied for the current
account, so the disposable real-Sonarr parity exercise remains an explicit pre-cutover
gate. Production enablement, V3 shutdown, real backup, secret mounting and the first
canary are operational actions requiring separate authority.

## Phase 14 initial Worktree review finding

The bounded Worktree review froze HEAD `e979b64c5a51c0ec9e33ac9c135bd5d641f7846f`,
an empty staged delta, the complete inherited status inventory and SHA-256 identities
for the 16 Phase11–13 contract-chain files. Standards and Spec both found one reachable
P1 defect: `ProductionEffectAuthorization` is checked by the coordinator only after
`adapter.execute()` returns a production receipt. A production adapter constructed
with one permit can therefore download, move files and submit Sonarr commands before
an omitted or mismatched coordinator capability rejects the receipt. The adapter also
does not compare its permit's execution key with the incoming envelope before its
journal/download path. This contradicts the documented one-execution pre-effect gate.

Required correction: reject absent/mismatched production authorization before
reservation/dispatch, and make the production adapter independently reject an
envelope whose execution key differs from its permit. The reproduction must use only
fake adapters, loopback/test inputs and temporary paths. Real Sonarr, Docker runtime,
production configuration and deployment remain Not verified and out of scope.

The backup review also found an evidence-quality gap: restore verification checked
SQLite integrity but replaced the expected file hash with the restored file's own
hash, so it did not independently compare restored content with the verified backup.
Phase14 adds a deterministic SHA-256 over SQLite's logical dump to the backup evidence
and requires the restored database to reproduce that fingerprint. This is local
evidence hardening; it does not claim a production backup or filesystem restore.

## Phase 14 final review finding

The P1 is closed on the final bounded basis. The coordinator validates the exact
adapter-required capability and deterministic execution key before creating a
reservation and again before dispatch. The production-shaped adapter independently
validates the same key before lookup or journal creation. Focused regression evidence
shows a missing capability produces zero adapter calls and zero execution attempts,
while an exact capability reaches the fake adapter once. A direct mismatched adapter
call creates no journal.

The logical backup fingerprint, bound-canary preflight and corrupt/incomplete journal
regressions also pass. Final Standards and Spec review found no open P0–P3 findings in
the 16-file Phase 11–14 contract chain. The sanitized manifest reverified all 16 file
sizes and SHA-256 values, including aggregate hash
`da6801200e4dab7a994b583520585384444d23ad8ab33797e87cd5c9743a7133`.

This verdict is deliberately local. A production-equivalent disposable Sonarr and
restart test, independent review, actual database/library backup and restore,
deployment secret/path/origin validation, observed V3 quiescence and a separately
authorized one-envelope production canary remain Not verified. The unrelated global
`hf-gradio`/`gradio-client` environment conflict also means global dependency
consistency is Not verified; AniDown declares neither dependency.

## Phase 15.1 corrective finding

The initial disposable validation harness binds filesystem effects to a literal local
marker and requires loopback plus an exact Sonarr version, but it does not positively
identify the remote loopback instance. A current Sonarr exposed on loopback could
share the expected version; in that case the harness would proceed to series lookup,
move the fixture artifact and submit `RescanSeries`. The local marker does not attest
the remote service. The runner must require one fixed disposable instance name from
the already bounded status response and reject wrong or missing identity before it
creates a journal or performs any effect-capable request.

The regression confirmed this was reachable rather than theoretical: with the exact
expected version but instance name `Current Sonarr`, the prior runner completed instead
of raising. The fixed runner requires the literal disposable identity from the status
response before series lookup; wrong or missing identity leaves series calls, commands,
journal and library at zero.

## Phase 7.1 compatibility finding

The original dedicated identity `AniDown V4 Disposable Validation` was safe in the
loopback double but unusable with Sonarr 4.0.20.3014: the real host-config API requires
the first or last word of the instance name to be `Sonarr`. The harness now requires
the exact dedicated identity `Sonarr AniDown V4 Disposable Validation`. This changes
only the canonical literal; the exact match remains before series lookup, journal,
artifact or command effects, and all marker, loopback, version, containment, emptiness
and one-command gates remain intact.

Evidence language is also a safety control. Reconstructing
`ProductionDownstreamAdapter` and reading the same local journal proves fresh-adapter
durability, not a Sonarr process/container restart. Phase 15 therefore exposes
`fresh_adapter_lookup` and deliberately makes no restart claim. A separately
authorized real-Sonarr exercise must still restart the disposable service and verify
post-restart status/reconciliation independently.
# Phase 16 corrective findings (in progress)

- `production_replay.snapshot()` currently admits every Sonarr row whose `seriesType` is `anime`; it does not enforce the required `animeworld` tag.
- `production_replay.retrieve()` lexically ranks native catalog observations but then injects every URL observed in matching V3 mappings. That makes candidate availability depend on legacy mappings and must be removed without weakening downstream match checks.
- Candidate parsing already trusts AnimeWorld detail pages over legacy catalog audio/year, and V3 mapping provenance is explicitly marked unreliable. This is a sound base for the corrective work.
- The current title-derived scene-season regex handles simple `Season N` labels only; multipart/composite coverage is delegated to `release_resolver.py` and requires separate corpus-backed proofs.
- `release_resolver.resolve_series()` already requires contiguous observed AnimeWorld numbering, complete trusted detail metadata, exact/scoped identity, date corroboration, and complete Sonarr episode vectors. It can compose multiple disjoint release segments into one target, but only recognizes whole-target equality or explicit Sonarr scene coordinates; ordinary title-labelled cours and verified multi-season boundaries are not yet represented.
- Its existing inverse multi-season proposal is deliberately `needs_review`: absolute concatenation and a matching first date are not considered independent evidence of an internal boundary. Phase 16 must add a stronger boundary proof rather than auto-accepting that inference.
- `SnapshotEnricher` reconstructs candidates from each case's captured native catalog pool, but also reconstructs the V3 mapping observations. Removing retrieval dependence must preserve these observations only as audit context, never candidate-discovery evidence.
- The frozen evidence contains native AnimeWorld detail records for the requested multipart/language/alias examples, including Italian and subtitle variants and provider alternate names such as `UFO Robo Grendizer` on `UFO Robot Goldrake (ITA)`. Broad recursive text search is too noisy; subsequent corpus inspection will use parsed, title-scoped summaries.
- The prior `comprehensive_audit.py` already provides an independent native-catalog ranking and live-link sanity layer, but its lifecycle labels (`upcoming`, `airing`, `complete`) and verdicts are report-only and were never fed into the V4 result taxonomy.
- Stage 2 explicitly deduped equivalent DUB/SUB candidates in audit code by preferring `Audio: Italiano`; production matching/resolution lacks that release-variant preference, explaining the language regressions.
- Stage 3 deliberately looked back at old V3-seeded URLs. Those results may remain historical audit evidence but cannot qualify as Phase 16 retrieval proof; the corrective evaluator must use the frozen native catalog/details independently.
- Baseline ledger: 192 targets = 60 correct proposals + 132 reviews; lifecycle evidence splits the reviews into 17 waiting/airing and 115 completed targets. The old audit labeled 59 native-catalog false negatives (57 single + 2 composite), but those labels still need deterministic resolver validation before being counted as corrected.
- The frozen Sonarr-series captures omit tag fields. A prior read-only tag-check script exists, and the user explicitly identifies Gintama as untagged; Phase 16 must carry eligibility as separate captured evidence instead of pretending the old series JSON contains it. The runtime gate itself now enforces `tag_labels` whenever that metadata is present.
- Requested multipart boundaries are independently visible in captured Sonarr episode dates: AoT S3 switches after 12, Dr. Stone S3 after 11, and Spy x Family S1 after 12. This supports a generic boundary rule using each later release's premiere date, not count arithmetic alone.
- Sailor Moon Crystal S1/S2 has the expected Sonarr absolute 1..26 split at 14/15, but the captured AnimeWorld source observations contain no per-episode publication date at episode 15. It remains non-automatic unless a separately captured source-boundary observation is supplied.
