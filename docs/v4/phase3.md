# Phase 3: provenance, season identity and local shadow

Status: offline foundation only. No runtime registration, Sonarr client, mapping writes,
LLM, frontend or downloader integration. Branch v4; baseline deferred, staging removed.

## Implemented contract

Each important Metadata field carries FieldProvenance: value, source, confidence,
derived and implicit. SourceMetadata carries reliability. Explicit field origins override
record origins; absent origins inherit the record as an explicitly marked compatibility
fallback. Inferred/derived values cannot certify identity, season, date or episode conflicts.
Alias entries carry their own source, language/type and season scope. Original display titles
remain intact. Confidence is descriptive, never a way to turn inference into trusted proof.

SeasonCrosswalk links a Sonarr season to provider namespace/season, scene season, cour,
part, season title and/or release ID. Verified entries require source and explanation;
all specified anchors must match reliable candidate fields. A crosswalk can live on target
or candidate. Conflicting explicit comparable seasons remain hard rejects. Unverified,
unanchored or incomplete correspondences produce unknown evidence and Needs Review.
There is no Black Lagoon-specific rule: its S2 identity comes from scoped alias/metadata
or a verified generic crosswalk.

MatcherPolicy exposes only fuzzy_threshold (default .88) and date_tolerance_days
(default 1, bounded 0..7). Neither bypasses structural constraints. Dates require matching
trusted date kinds; episode counts require trusted complete, comparable scopes.
Unicode NFKC, Latin diacritics, half fractions, apostrophes, punctuation, dashes and stacked
non-identifying suffixes normalize to keys; Japanese distinctions remain intact.
Decisions snapshot the actual policy thresholds. Matcher decision version is provenance-2; Review snapshot schema remains 1.

## Local sanitized dataset and report

```powershell
python -B -m src.v4.dataset --output work/phase3/shadow-dataset.json
python -B -m src.v4.meaningful_shadow --dataset work/phase3/shadow-dataset.json --report work/phase3/shadow-report.md
```

Builder whitelists public titles/aliases/season observations and public AnimeWorld URLs.
It omits paths, API keys, credentials, tags and personal runtime data; URLs lose query and
fragment, credential-bearing/foreign hosts are rejected. Inputs resolve inside V4.
Source file SHA-256 accompanies the dataset. Shadow does not persist reviews or mappings;
JSON stdout and optional Markdown are derived report artifacts only.

Cohorts are separate: documentation legacy mapping observations (not verified catalog
release metadata), local Sonarr snapshots, and synthetic regression cases for four titles (including S2/crosswalk and shared-URL cases).
The mandatory four titles have no real local snapshot. Mapping labels must not become
verified release identities. Per-target candidate pools replay observed release lists;
this is not a reproduction of the production search corpus. V3 results use only its pure
ranking/filter/selection functions, never Core or service construction, and are not
historical saved decisions. No live data or V3 path is read.

Report includes each target, V3 replay, V4 decision, chosen/excluded candidates, hard rejects,
reason/evidence, difference and original review proposal snapshot. Ground truth is available
only for the synthetic cohort; observational differences cannot establish production FP/FN.
Absolute legacy buckets remain unknown season numbers; URL ordering does not invent coverage.

## Isolated review repository

ReviewRepository(path) requires storage under ignored V4 work/. SQLite schema 1 stores an
immutable JSON original with SHA-256 and a database update trigger. create/get/list_open,
update(notes), resolve(candidate|null,reason) and dismiss(reason) are explicit operations.
UTC timestamps, revision compare-and-swap and transactions protect lifecycle updates;
resolution is separate from original. Closed reviews reject edits. Unknown or hard-rejected
candidates cannot resolve an old snapshot; new evidence requires a new review.
No automatic mapping, download or Sonarr write follows resolution. Shadow emits proposals
but never calls this repository. Runtime review persistence and API integration are deferred.

## Remaining limits / next phase

Acquire an independently sanitized Sonarr + AnimeWorld detail export with the mandatory
four real titles, per-release season/cour/date scope and known human answers. Validate
crosswalks and calibrate abstentions before runtime integration. Legacy examples are not
sufficient to measure production precision/recall. Implement metadata adapters in read-only
mode and importer dry-run next; keep judge, frontend and downloader disabled.

## Verification snapshot

104 tests pass: 50 V4 and 54 compatible legacy tests. Shadow: 90 series, 100 targets, 105 candidate ID/URL pairs, 104 unique URLs; 87 documentation observations, 6 Sonarr targets, 7 synthetic cases. V3 replay 84 matches, V4 6; 83 URL/outcome differences, 94 review proposals. Six known-answer synthetic targets correct in V4; V3 chooses Ranma remake and abstains on two Crystal seasons. Shared Crystal URL generates review. No production FP/FN claim. Reports under ignored work/phase3; no proposals persisted by shadow.
