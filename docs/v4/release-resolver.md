# Phase 5 — implemented offline release resolver

## Contract and data flow

Versioned production snapshot v1 → per-series SeasonTarget episode vectors →
ReleaseIdentity variant groups → observed EpisodeCoverage → evidence-bearing
Crosswalk → exact-cover MappingPlan → offline JSON/Markdown replay.

`releases.py` defines SeriesIdentity, SeasonTarget, ReleaseIdentity, EpisodeLink,
EpisodeCoverage, Crosswalk, ReleaseSegment and MappingPlan. A plan can have N
segments and N target IDs. Source, Sonarr, scene and absolute coordinates are
retained per episode; source/absolute/destination ranges and constant offset are
reported when meaningful. Display titles and candidate field provenance survive
in original target/candidate snapshots. Coverage can be complete, partial or
unknown. Two partial Black Lagoon segments constitute one complete target.

Whole-season links require exact native/global/manual/scoped alias (or generic
season-label equivalence corroborated by date and coverage), a trusted provider
premiere within the existing one-day tolerance of target episode 1, finished
provider status, observed contiguous 1..N source numbering, and a complete Sonarr
1..N vector of the same count. These are metadata-supported proposals, not human
verification. Scene segments additionally anchor their release by exact scoped
scene alias or canonical first-part identity and first scene episode date, and
use explicit Sonarr sceneEpisodeNumber coordinates. A title-derived ordinal never
establishes a crosswalk alone. No fuzzy score or threshold is changed.

Release grouping is deterministic clique grouping: exact native/alternate identity,
same premiere date/year, date kind, cour/part, count and observed numbering. Audio
variants and compatible alternative URLs belong to the same release identity;
unknown/conflicting dates or coverage do not merge. Remakes remain separate.
Transport URL selection uses audio preference only within established identity;
without a preference, stable URL ordering is reproducible and carries no narrative
claim. Candidate snapshots retain audio/language and per-field provenance.

Explicit Sonarr season incompatibility is rejected. A scoped alias for another
season cannot be rescued by canonical-title/fuzzy scoring. Exact-cover search uses a
canonical, input-order-independent dynamic program over destination coverage and used
source episodes. It evaluates the full bounded state set, ranks complete covers by
evidence strength, rejects gaps/overlaps, and retains equally best alternatives as an
explicit ambiguity. State-cap overflow becomes `candidate_overflow` review instead of
silently discarding a late candidate. The same source episode cannot be assigned to two
separate season plans. No runtime,
bootstrap, Docker, downloader, review-store or production mapping integration.

## Reliability and inverse coverage

Crosswalk reliability tiers are `explicit`, `metadata_supported`, `inferred`;
inferred links cannot produce a matched plan. This is an evidence hierarchy, not
probability calibration. The generic inverse representation supports a single
release across N targets. Absolute sequence concatenation is generated only when
exact initial identity/premiere, complete adjacent Sonarr target vectors and
observed release numbering agree. It remains inferred until independent source
boundary evidence exists. The contract can represent an explicitly verified
inverse mapping; automatic source-boundary verification is not implemented.

## Real evaluation

Same frozen 192 targets, 114 anime series, 927 catalog detail captures. Baseline
input is unchanged. Additional GET-only Crystal inspection observes episode URLs
and JSON-LD numbering for 14, 15, 26; no episode air dates or boundary identities
are exposed. Stable sanitized observations are a separate versioned test fixture.

| Measure | Before | After |
|---|---:|---:|
| Auto proposals | 15/192 (7.81%) | 145/192 (75.52%) |
| Needs Review | 177/192 (92.19%) | 47/192 (24.48%) |
| Missing-crosswalk baseline cohort | 174 | 46 (128 resolved) |
| Equivalent-candidate baseline cohort | 58 | 12 (46 resolved) |
| Insufficient-metadata baseline cohort | 175 | 47 |
| New resolver insufficient-metadata reviews | — | 46 |
| New resolver equivalent-plan reviews | — | 1 |
| Multiple-release auto proposals | — | 1 |
| Shared-release inferred proposals | — | 1 |
| Shared-release verified proposals | — | 0 |
| Exact URL agreement among paired auto targets | 12/13 (92.31%) | 86/129 (66.67%) |
| Agreement allowing established release audio/mirror variants | — | 110/129 (85.27%) |

145 auto proposals include one retained previous strong V4 decision without a new
coverage certificate. All other proposals are resolver-supported. 24 of 43 exact
URL disagreements are transport variant changes, 19 are other disagreements.
Agreement denominator changes as previously abstained targets become comparable;
this does not measure accuracy. Residual baseline cause cohorts overlap and retain
original causes for the same before/after population; new reason codes are separate.
Every target report keeps original V3 observations, old V4 decision, new plan,
selected variants, alternatives, exclusions, candidate/target snapshots and reasons.

## Required real cases

- Black Lagoon S1: two observed 12-episode releases map to Sonarr 1..12 and 13..24
  through explicit scene coordinates and release dates; no anime-specific rule.
- Nadia: Italian manual global alias + 1990-04-13 + 39 observed episodes establishes
  a metadata-supported whole-target proposal, without fabricated season alias.
- Crystal: 26 episodes support an inferred S1 14 + S2 12 proposal, never S3. Source
  pages 14/15/26 do not certify the internal boundary: both targets remain review.
  Native 2016 release with 13 episodes supports S3 and disagrees with V3's 2014 URL.
- Ranma 2024 S1/S2: corresponding 2024/2025 12-episode releases; SUB/DUB grouped.
  Original 1989 and native Senko-san contamination remain distinct. Future S3 stays review.
- Kobayashi S2: 2021 12 episodes, unlike V3 first-season 2017 13-episode release;
  disagreement retained as real regression.

## Remaining uncertainty and next phase

47 residual reviews include split/part/long-running numbering and absent future
releases. The replay report lists all targets. Chainsaw Man is the one equivalent
plan case: SUB/DUB native premiere dates differ by one day, both individually fit
existing date tolerance, but strict release grouping does not assert equivalence.
This is an optional future judge triage candidate, not a demonstrated need for LLM.
An LLM may explain identity alternatives; it cannot certify missing episode-boundary
facts (Crystal), invent unavailable releases or override structural contradictions.

Next: acquire independently verifiable source segment/boundary metadata where it
exists, allow reviewed explicit boundary crosswalks, and inspect the 19 narrative/
coverage disagreements manually. No threshold tuning, LLM or production writes
are authorized by this phase. Runtime mapping schema/migrations must be redesigned
for N↔N plans before any integration. Existing single-candidate review persistence
is unchanged and does not yet resolve composite plans.

## Reproduction and verification

`python -B -m src.v4.composite_replay --snapshot work/phase4/replay/production-snapshot.json --output-dir work/phase5/replay`

Offline by default; path containment restricts artifacts to V4. Explicit catalog
inspection: `python -B -m src.v4.episode_inspection --url <catalog-play-page> --output work/phase5/episode-inspection/<name>.json`.
Only GET HTML catalog pages, never players/media. No source writes or downloads.

Tests: 90 V4 (61 existing +29 new) and 54 compatible legacy =144, all passed.
Separate processes prevent legacy mock-module contamination. First non-escalated
V4 suite stalled on temporary SQLite cleanup under sandbox permissions; final
suite with V4-local temporary write access passed in 3.4 seconds.
Final bounded Worktree repo-review evidence is in work/phase5/repo-review.md.
