# AniDown V4 — UI contract

Status: Phase8 runtime integration of the Phase7 frontend-v4. Product owner: AniDown V4 maintainer. Frontend/API same origin, runtime 127.0.0.1:6004. No login; local-user is an unauthenticated audit label. API frontend-api-contract.md is transport authority; frontend-v4/DESIGN.md owns implementation tokens/layout; frontend-design.md retains the preimplementation direction and skill choices.

## Product
A local application to understand season mappings and resolve exceptions. Content and decisions drive hierarchy. Dashboard, Library, Needs Review, Activity, Settings are the five main routes. Advanced is contextual and collapsed, loaded on demand. No legacy frontend imports, metric-card walls, decorative gradients, normal raw URLs, giant data tables or always-open debug. Dark neutral software aesthetic, not anime/cyberpunk.

## Skill gates
Explicitly applied installed frontend-design, ui-ux-pro-max and web-design-guidelines under C:/Users/Willi/.agents/skills. Fresh Vercel guideline source and skill identities are retained in frontend-design.md and work/phase7/ui-skill-hashes.json. UI Pro Max's utility Flat Design/accessibility guidance is adopted; irrelevant marketing/entertainment patterns are rejected. Library and Needs Review are the sample surfaces for visual critique. Final source and browser checks are recorded under work/phase7.

## Screens
Dashboard answers service status, proposals, open reviews and last scan with a status sentence, two task rows and one scan result. Run scan is the single global main operation. No downloads follow scans or approval.

Library uses an expandable ruled list, search/state/page/detail encoded in the hash URL. Default title, season, mapping state, coverage and chosen SUB/DUB/language; proposed differs from approved. Expanded plans explain release-to-destination episodes, including N-release/N-season composition. Raw source numbering, per-field provenance, identifiers and JSON only under Advanced. Black Lagoon: Season1 /24 episodes, two releases covering1–12 and13–24. Display native titles; no narrative knowledge added.

Needs Review uses a queue and one decision detail; queue is omitted when the filtered view contains only one item. Show the factual reason, proposal, supported alternatives, readable evidence and approve/choose/reject. Dismiss/reopen secondary. Crystal: a26-episode release fits S1+S2 totals, but boundary14/12 is unverifiable. Inferred approval requires explicit uncertainty acknowledgement and reason; rejecting does not require accepting uncertainty. No unsupported/excluded candidate can be chosen by the normal flow. No Sonarr completion filtering. Empty plans remain readable and rejectable; acquire evidence before approval.

Activity is latest-first, cursor-paginated and readable: show scan lifecycle, human decisions and settings changes. Per-plan creation/review/supersede events stay in raw append-only audit under Advanced, while their resulting plans remain in Library/Needs Review. The default timeline must not repeat 50 automatic scan item events. Human decision entries link to relevant plans. Settings exposes language and operating safety facts; no credentials, endpoints or matcher knobs.

## Interaction/state
HTTP DTOs only; no direct model/storage imports. JSON mutations carry expected_revision/reason; server confirms persistence, then counts/detail/activity refresh.409 requires reload and explicit new decision, never a blind retry. Native modal has keyboard focus containment, Escape/cancel handling, labelled fields, inline errors and unsaved-navigation protection.

Scan creates/reuses a durable asynchronous job; stages/progress remain visible while navigation works. Refresh recovers the active job. Completion refreshes plans/reviews; failure preserves earlier decision snapshots and gives retry guidance. Restarted queued/running jobs fail with scan_interrupted, never silently rerun. No scan or old review import on runtime startup.

Semantic headings/navigation/forms, skip-to-main, focus-visible, aria-live notices, reduced-motion, contrast checks, long title wrapping, pagination and responsive navigation. Empty/loading/recoverable error/stale connection/running/success states are explicit. Narrow layout may scroll only its local review queue/Advanced content, never the whole app horizontally.

## Acceptance and drift
Owner revalidates frontend unit tests, API/runtime contract tests and browser flows on any token/DTO/layout/lifecycle change. Verify composite Black Lagoon, Crystal uncertainty, Nadia approval, Ranma remake isolation, approve/reject/alternate decisions, job polling, Advanced closed, keyboard and narrow/zoom layout. UI gates are checks, not optional inspiration. No LLM, downloader, Sonarr writes or definitive V3 migration in Phase7.
