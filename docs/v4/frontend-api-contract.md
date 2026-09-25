# V4 frontend API contract — 4.1

## Status and ownership

Implemented same-origin WSGI runtime: `src.v4.runtime.create_runtime`, binding
127.0.0.1:6004 when explicitly launched. New ES-module frontend in frontend-v4.
Nothing is mounted in V3; Phase8 uses a separate minimal Dockerfile.v4 and a V4-only SQLite volume. Frontend integration
must call HTTP `/api/v4/...` only; it cannot import matcher/resolver classes or read
SQLite, table.json, snapshots or internal files. Application orchestration owns
adapter → enrichment → deterministic matcher → release resolver → local plan/review.
The foundation code and thresholds are unchanged.

The metadata adapter reads configured sanitized offline production snapshots or, when
explicitly enabled, live read-only Sonarr and AnimeWorld metadata. Live provider
observations use status-aware positive/negative TTLs and content fingerprints; stale
successes are revalidated and transient failures retry without erasing the last known
success. Revalidation is bounded per scan and overflow is persisted as an explicit
pending validation state. Normal scans consume the last atomically published catalog
generation and never refresh it. An explicit manual `deep_audit` scan refreshes the
catalog before full rematching. Cached derived candidates and V3 mapping labels are not
authoritative. Configured scans run as durable asynchronous jobs. Scan dataset IDs are server-configured;
clients cannot send paths, external URLs, credentials or Sonarr settings.

## Transport and envelope

Same-origin loopback Host (localhost/127.0.0.1/::1), no CORS. The normal local runtime
requires a loopback REMOTE_ADDR. Docker/reverse-proxy access requires explicit
`ANIDOWN_V4_TRUSTED_PROXY_CIDRS`; no private address or spoofed Host header is promoted
to loopback trust. Non-loopback mutations additionally require the bearer secret read
from `ANIDOWN_V4_AUTH_TOKEN_FILE`. Optional public hostnames are explicitly listed in
`ANIDOWN_V4_ALLOWED_HOSTS`; forwarded-client headers do not grant trust. Host/Origin
checks stay active for assets and API.
The checked-in Docker preview publishes no port from the application container. A
separate non-root `src.v4.loopback_proxy` sidecar publishes only host
`127.0.0.1:6004`, relays raw TCP to the single fixed `anidown-v4:6004` upstream, and
joins both the internal application network and a normal ingress bridge. The app joins
only the internal network and therefore retains no outbound route. The sidecar has no
destination arguments, mounts, credentials, or application volume. It does not promote
forwarded headers or bypass the bearer requirement for non-loopback mutations.
Mutations require JSON objects, max64KiB. Errors do not expose file/DB details.
All responses use application/json UTF-8, no-store and nosniff.

Success: `{ "data": <DTO>, "meta": { "contract_version": "4.1",
"mutations_allowed": true|false } }`.
Error: `{ "error": { "code": "invalid_request|conflict|not_found|forbidden|unavailable",
"message": "..." }, "meta": { "contract_version": "4.1",
"mutations_allowed": true|false } }`.
`mutations_allowed` is a transport capability, not an authorization grant. It is true
only for a directly loopback-originated request. It remains false for proxy traffic,
including a bearer-authenticated mutation response. The browser frontend uses it to
label proxy access as read-only and disable scan, override and settings-write controls;
the server still independently enforces the bearer and production-effect gates.
200 success;202 queued/running shadow job;400 invalid fields/JSON/filter;403 transport
boundary;404 unknown resource/endpoint;409 stale/closed/unsupported decision;503 failure.
Unknown action fields/settings are rejected. IDs are opaque; UTC ISO timestamps.
Do not derive workflow permissions from score/confidence: use `actions` and state.

## Endpoints

| Method | Path after /api/v4 | Response |
|---|---|---|
| GET | /overview | OverviewDTO |
| GET | /series | SeriesListDTO |
| GET | /series/{series_id} | SeriesDetailDTO |
| GET | /series/{series_id}/seasons/{season}/override | SeasonOverrideDTO |
| PATCH | /series/{series_id}/seasons/{season}/override | SeasonOverrideDTO |
| DELETE | /series/{series_id}/seasons/{season}/override | reset to AUTO |
| GET | /notifications | NotificationOutboxDTO |
| GET | /mappings | MappingListDTO |
| GET | /proposals | MappingListDTO, default state=proposed |
| GET | /reviews | MappingListDTO, default review state=open |
| GET | /mappings/{id}, /reviews/{id} | MappingDetailDTO |
| GET | /mappings/{id}/metadata, /reviews/{id}/metadata | MetadataDTO |
| POST | /mappings/{id}/approve, /reviews/{id}/approve | MappingDetailDTO |
| POST | /mappings/{id}/choose, /reviews/{id}/choose | MappingDetailDTO |
| POST | /mappings/{id}/reject, /reviews/{id}/reject | MappingDetailDTO |
| POST | /mappings/{id}/dismiss, /reviews/{id}/dismiss | MappingDetailDTO |
| POST | /mappings/{id}/reopen, /reviews/{id}/reopen | MappingDetailDTO |
| GET | /activity | ActivityDTO |
| GET | /settings | SettingsDTO |
| PATCH | /settings | SettingsDTO |
| GET | /scans | Configured dataset IDs/modes + latest20 jobs |
| POST | /scans | 202 ScanJobDTO (active requests coalesce) |
| GET | /scans/{job_id} | ScanJobDTO |
| GET | /advanced/mappings/{id}/evidence, /advanced/reviews/{id}/evidence | EvidenceDTO |
| GET | /advanced/activity | Raw append-only audit events |

Mapping/proposal lists: `limit=1..100` (default50), `offset>=0` (default0), optional
`state=proposed|approved|rejected|needs_review|superseded`.
Review lists use `state=open|resolved|rejected|dismissed|superseded`, defaultopen.
Activity: `before>=0` sequence cursor, `limit=1..100`; events latest-first.
The simple 4.1 timeline contains scan started/completed/failed, human decision
actions and settings changes. Automatic per-plan created/review/superseded events
are retained in the append-only raw audit under Advanced and reflected in plan/review
state; they do not flood the simple timeline. ActivityDTO returns descending
items and next_cursor=oldest returned sequence; request before=next_cursor for
earlier visible events. Advanced remains the complete ascending audit after cursor.
Offset lists are not a transactionally frozen multi-page view; refresh after actions.

## DTOs received by each page

### Dashboard

OverviewDTO: `mapping_counts` with all five mapping states; `open_reviews` count;
`current_target_count`; `mode="shadow"`; `external_writes=false`;
`service_status="running"`; nullable `last_scan` and `active_scan` ScanJobDTO.
Counts are PLAN units, not episode/season counts. Crystal S1+S2 is one review unit
covering two targets. Never label proposed as approved or ready for download.

### Library / Mappings

MappingListDTO: `items: MappingSummaryDTO[]`, `total`, `offset`, `limit`.
MappingSummaryDTO keys: `id`, `title`, `mapping_state`, `review_state`,
`revision`, `created_at`, `updated_at`, `targets`, `reasons`, `actions`,
`approved_by_human`, `coverage_status=complete|partial|unknown`, mapped `episode_count`,
`selected_variants:[{audio:"SUB|DUB|UNKNOWN",audio_language:null|string}]`.
Selection summary uses existing deterministic variant preference inside the established
release identity. Full coverage does not verify inferred boundaries.
TargetDTO: `target_id`, `season`, nullable `episode_count`.
ReasonDTO: stable `code` and deterministic readable `message` (English baseline;
frontend may localize by code; stored display preference does not translate server text).
ActionsDTO booleans: `approve`, `choose`, `reject`, `dismiss`, `reopen`.

MappingDetailDTO adds `plan`, `alternate_plans`, `human_decision` (nullable),
`evidence_summary`. The plan reflects a chosen human alternative when applicable;
the immutable original always remains available under Advanced.
EvidenceSummaryDTO[]: `candidate_id`, native `title`, `messages[]`; no raw URLs/logs.

PlanDTO: opaque `id`; `coverage_status=complete|partial|unknown`; `targets[]`;
`segments[]`; mapped `episode_count`; `requires_uncertainty_acknowledgement` boolean.
SegmentDTO: `release_id`, native `title`, nullable `release_year`, nullable `premiere_date`, nullable `source_episode_range:[start,end]`,
nullable `source_episode_count`, `coverage_status`,
`reliability=explicit|metadata_supported|inferred`, `destinations[]`, `variants[]`.
DestinationDTO: `target_id`, `season`, `episode_range:[start,end]`, `episode_count`.
VariantDTO: `candidate_id`, native `title`, `audio=SUB|DUB|UNKNOWN`, nullable
`audio_language`, nullable `subtitle_language`, `selected` boolean.
No URL is needed to render a simple mapping. Audio/mirrors are alternatives inside
an established release identity, not independent narrative matches.

Black Lagoon plan presentation (opaque IDs omitted):
```
Targets: S1 /24 episodes; overall coverage complete
Black Lagoon: source1–12 → S1 E1–12
The Second Barrage: source1–12 → S1 E13–24
```
Two segment coverages may be partial relative to the target while the total is complete.
A single release can have two destinations: source1–26 → S1 E1–14 +S2 E1–12.
Full numeric coverage does NOT certify identity or episode boundaries; inferred
crosswalks remain uncertainty-bearing even after an explicit human approval.
Exact per-episode source/absolute/scene links are retained in Advanced.

### Series-first library and season overrides

`/series` is the normal landing model and returns SQL-paginated series/season summaries
only; it never includes source or episode arrays. The frontend follows every page when it
needs the whole local library. `/series/{series_id}` lazily returns that series' season,
source and episode detail. Each detailed season exposes the immutable automatic result
plus an `override` layer and an `effective` result.
Strong deterministic `proposed/not_required` plans render as `automation=auto` and do
not require human approval. `needs_review` is reserved for genuine ambiguity.

SeasonOverrideDTO supports up to eight validated AnimeWorld HTTPS `source_urls`, optional
`audio=SUB|DUB`, `ignore_errors`, `manual_approved`, `excluded`, a structured
`episode_map:[{sonarr_episode,source_index,source_episode}]`, and an operator note.
Legacy `source_url` remains the first URL for compatibility. Manual episode destinations
must be unique and within the Sonarr season. `manual_approved=true` additionally requires
exact destination coverage, explicit audio, and a live source check proving canonical
identity, audio and every referenced source episode. The approval stores the target ID,
snapshot digest/revision, normalized mapping and source fingerprints. Any target revision
or source fingerprint change makes it non-executable; execution revalidates the source.
Any active override blocks automatic execution. DELETE returns the season to AUTO.
Overrides are local V4 state only: they never enable downloads or external writes.
Automatic seasons with provider validation `pending`, `stale_retry`, or `invalidated`
retain their mapping decision but expose `auto_ready=false` and `can_execute=false`.

The presentation keeps `automatic`, `override`, and `effective` separate so a manual
decision cannot erase the resolver result or its evidence.

### Needs Review

Use open reviews list and detail; do not filter by Sonarr episode-file completion.
Original snapshot keeps target(s), native candidate(s), proposed MappingPlan,
alternate plans, reason codes, raw evidence, foundation decisions, V3 observations
marked not ground truth, matcher/resolver versions and original timestamp/digest.
Frontend presents readable reasons and coverage, with optional Advanced evidence.
A shared multi-target plan is one atomic decision: no separate S1/S2 approvals.

ActionRequest requires `expected_revision` (nonnegative integer, not boolean) and
`reason` (nonblank, max2000 chars). approve/choose additionally accept optional
`plan_id`, `variant_choices: {release_id: candidate_id}`,
`acknowledge_uncertainty:true`. Approve selects the original plan; choose selects a
known original alternate or supported variant. Missing variant choices are filled
by existing deterministic audio tie-break/stable transport selection. Arbitrary
candidate IDs, fabricated plans or plans affecting different targets are rejected.
A proposal with no concrete plan is not approvable and becomes application review.
Inferred plans require explicit human acknowledgement; this records responsibility,
not invented verification. No download follows any action.

Lifecycle:
- scan-supported plan → mapping proposed /review not_required;
- unresolved scan → mapping needs_review /review open;
- approve/choose → mapping approved /review resolved, separate human decision;
- reject → mapping rejected /review rejected;
- dismiss → mapping needs_review /review dismissed (not approval);
- reopen closed decision → mapping needs_review /review open, withdraw approval;
- changed scan evidence → old item superseded, immutable original and human history
  retained; new item requires fresh approval. Superseded items cannot reopen.
- identical scan evidence keeps ID, revision and approval/dismissal unchanged.

Every action/creation/supersede is append-only audited. A409 requires refetch, never
blind retry with updated revision. Timestamp/actor/action/plan/variants/reason remain
auditable after reopen; human decision field is latest, prior decisions are events.

### Activity

ActivityDTO: `items[]` with `sequence`, nullable `entity_id`, `action`, `actor`,
`timestamp`, readable `message`, nullable `entity_title`, nullable human `reason`; `next_cursor`.
Actors are `shadow_scan` or `local-user`; local-user is a transport label,
NOT authenticated proof of a user's identity. Advanced returns full audit payloads.
No raw runtime/download logs are required by the simple page.

### Settings

SettingsDTO includes `display_language=it|en`, notification preferences
`notify_review|notify_error|notify_anomaly|notify_new_season`, `mode`,
`downloads_enabled`, `external_writes_enabled`, `llm_enabled=false`,
`notification_bus="ready"`, transport status, and a sanitized `download_capability`
with `ready`, `status`, and bounded diagnostic categories. Downloads/external writes
are true only after the complete execution capability preflight succeeds; the enable
environment flag alone never changes them. PATCH accepts only the display
language and those notification booleans and is locally audited. No API keys, DB paths,
Sonarr/Plex endpoints, matcher knobs, Telegram credentials or LLM provider controls.

The notification bus is a persistent transport-neutral outbox derived from selected
append-only audit events. Current topics cover review, execution/scan errors, provider
failure and mapping/regression anomalies. Credentialed Telegram relay URLs require
verified HTTPS unless the relay is loopback-local. Delivery failures keep the cursor
on the unsent event and use durable capped exponential backoff with jitter. GET `/notifications` exposes bounded
outbox events plus backend status for diagnostics.

GET scans: `datasets:[{id,mode:"shadow",scan_modes:["normal","deep_audit"]}]`,
`jobs:ScanJobDTO[]` latest20, `external_writes:false`. POST requires
`{ "dataset": "<configured-id>" }` and may include `mode:"deep_audit"`; omitted mode
is `normal`.
The server permits one active scan; repeated requests return the existing job
(including its actual dataset). The current UI uses the configured production dataset.
Clients cannot submit source URLs or paths.

ScanJobDTO: opaque `job_id`, `dataset`, `scan_mode=normal|deep_audit`,
`status=queued|running|completed|failed`,
`progress=0..100`, `stage`, UTC `created_at`, nullable `started_at`, `completed_at`,
nullable `result:ScanResultDTO`, nullable `error:{code,message}`,
`mode="shadow"`, `external_writes=false`. Progress is orchestration-stage progress,
not a measured fraction of processed episodes. Live stages are queued, inventory_read,
inventory_diff, provider_verification, dirty_mapping, catalog_generation_load (or
catalog_refresh for deep audit), candidate_retrieval, provider_detail,
candidate_enrichment, deterministic_matcher_and_release_resolver, v4_snapshot_storage,
completed and failed. Stages with no work may be skipped. No invented per-candidate progress.

ScanResultDTO retains existing offline result: `scan_id`, completed status, target_count,
item_count,new_items,item_ids,external_writes=false,local_storage_written=true,stages.
JobID and audit scanID are separate stable identifiers. Failures use sanitized
scan_failed or scan_interrupted messages and leave prior snapshots/audits retained.
Queued/running jobs become failed on runtime restart; no implicit retry. Completed
jobs remain pollable. Jobs are stored in V4-only v4_scan_jobs; one bounded worker.
The service is unchanged except optional progress callbacks around existing stages.
No remote GET, external mutation, downloads or legacy autosave. No scan/import on startup.

Transport compatibility: the unmounted Phase6 `src.v4.api.create_app` remains an
internal synchronous4.0 factory so its existing32 integration contracts stay valid.
The public frontend runtime is exclusively4.1 with202 jobs and latest-first activity;
it never exposes a synchronous scan endpoint. No foundation threshold/behavior changes.

## Advanced and storage boundary

MetadataDTO: public title/season/scene/date/year/count/cour/part for targets and
candidates, with candidate ID and source name. Unknown facts remain null.
EvidenceDTO: `snapshot_digest`, `original_snapshot`, `human_decision`, `created_at`,
`matcher_version`, `resolver_version`. Original contains per-field provenance,
raw reason/evidence and episode links. Only this Advanced endpoint exposes source
URLs, original technical IDs and V3 observations. Escape all future UI text.

ApplicationStore extends existing ReviewRepository connection/containment/clock,
using the same V4-contained SQLite database (`work/application-v1.sqlite3` default).
Connections use WAL, a bounded busy timeout, and bounded retry only for operations
declared idempotent; mapping revision conflicts are never retried or hidden.
Old reviews schema stays version1 and compatible; composite items/events/current
scope/settings have separate application schema version1. Rebuildable projection schema3
contains current series, current season detail, target inventory, lightweight item summaries,
and item titles for the activity join. Ordinary overview/list/series/activity reads do not
decode immutable history; detail/evidence/execution still validates it. Original payload/digest/
created timestamp cannot update, item history cannot delete, audit cannot update/delete.
Batch scan superseding and human transitions are transactions with revision checks.
Partial scans cannot silently split/retire a current multi-target plan.
No old review import or table.json migration occurs implicitly.

## Decisions and safety before next phase

Frontend integration is approved and implemented. Same origin and loopback6004 remain
the default; explicitly configured proxy peers use bearer authentication for mutations.
`local-user` is an audit actor label, not an identity provider. There is no automatic V3
review import, and scans remain asynchronous and pollable. Do not expose this runtime
outside the configured loopback/proxy boundary. Download execution
must independently require safe verified episode routing; human approval of an inferred
plan does not certify boundaries. Live metadata adapter policy changes and any explicit
old review migration need separate contracts/approval.
LLM, downloader and final V3 migration remain
outside Phase8.

Runtime: `python -B -m src.v4.runtime` from the V4 root. `--storage` accepts only a
V4-contained work path. Port6004 default; Phase8 Dockerfile.v4 runs only this V4 runtime with the stable sanitized12-target fixture and V4-only persistent SQLite. The local CLI without `--dataset-source` retains the larger offline replay; neither mode reads V3 live or writes Sonarr. Frontend needs no package
installation/build and no external asset requests. Node built-in tests verify pure DTO
presentation; runtime/API tests verify job transport, storage, loopback and audit.

## Contract ownership and revalidation

Owner: V4 application/API maintainers; consumer: frontend-v4.
`application_dto.py`, `runtime.py` and `scan_jobs.py` produce this4.1 contract.
The32 Phase6 integration tests independently preserve the internal4.0 transport. Executable integration
validation: `tests/v4/test_application_api.py`, `tests/v4/test_frontend_runtime.py`
and `frontend-v4/tests/presentation.test.mjs`. Update this normative contract and
rerun tests whenever DTO fields, routes, lifecycle or adapter guarantees change.
Frozen environment numbers and review/capture evidence remain under ignored
work/phase6 and in progress.md, rather than being live API guarantees.
