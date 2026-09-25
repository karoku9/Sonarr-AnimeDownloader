# Matcher V4 foundation — implemented contract

## Scope and authority

Offline Python standard-library implementation under `src/v4`; no runtime registration, network, Sonarr writes, mapping persistence, downloader calls, LLM or frontend integration. This contract describes the implemented foundation and supersedes the draft reason-code spellings for this scope. The existing architecture remains the proposal for future integration.

## Files and model

`models.py`: frozen Target/Candidate, typed Alias/SourceMetadata, Audio, ReasonCode, Evidence/Evaluation/Decision and Review. Metadata includes canonical and alternate titles, alias language/type/source, global versus season/scene scope, season namespace and explicit target-season crosswalk, premiere/first-air date and date kind, release year, episode count/completeness/scope, external IDs, candidate release ID, audio mode/languages, observed mapped seasons and explicit disjoint coverage ranges.

Review is a self-contained schema-versioned serializable value with target, candidate and decision snapshots, evidence and reason codes. JSON round-trip does not need logs or original inputs. This is a model/serialization contract; no review database, lifecycle API or filesystem repository is implemented yet. It does not consult downloaded episode counts.

`normalization.py`: NFKC/casefold, Unicode characters retained, punctuation/dashes/colon separated, apostrophes removed consistently, repeated whitespace compressed and trailing `(ITA)/(SUB)/(DUB)` release tags stripped from comparison keys. Fraction variants `½`, `1/2`, `1⁄2` including full-width forms canonicalize to a semantic token; do not collide with Ranma 12. Subtitles, seasons, parts and edition years are not stripped.

`matching.py`: immutable evaluation of all candidates sorted by stable ID. Reliable identity mismatches, comparable incompatible season metadata and target season-alias contradictions are hard rejects. Different numbering namespaces require an explicit crosswalk. URL usage is checked both in candidate rows and observed mappings; shared seasons need non-overlapping explicit episode ranges. Date/year conflicts and complete comparable episode-count conflicts block selection without pretending that missing metadata is a hard failure.

Ranking: verified identity → verified season → scoped exact alias → comparable date/year → episode evidence → exact title strength → last-resort fuzzy. Fuzzy is attempted only when no usable exact candidate remains, threshold 0.88 and minimum 4 normalized characters; fuzzy is review-only. Unknown season identity is review-only. Aliases never migrate across seasons. Reliable source is record-level plus alias-level in this foundation, not field-level yet.

Audio preference is the final tie-break only among candidates at the same rank belonging to the same reliable provider release ID. UNKNOWN is not SUB. A single better title can match despite informational language_mismatch, with evidence explaining why. Matching returns a proposal; it never authorizes or executes automapping/download.

Reason codes: multiple_equivalent_candidates, season_conflict, release_date_conflict, episode_count_conflict, duplicate_url_across_seasons, language_mismatch, insufficient_metadata, low_confidence_title_match; additionally identity_conflict and no_title_match. Evaluations retain reasons of excluded candidates even when another candidate matches. Decision reason codes describe unresolved issues, not every irrelevant excluded alternative.

## Commands

```powershell
Set-Location C:\Dev\AniDown-v4
python -B -m unittest tests.v4.test_matching tests.v4.test_shadow
python -B -m unittest tests.test_search_v3 tests.test_mapping_management tests.test_runtime_mapping
python -B -m src.v4.shadow --sonarr-series tests/dump/serie.json --catalog src/database/table.json --catalog-format legacy-table
```

Shadow accepts only V4-contained paths after resolution, emits aggregate counters/input hashes, and uses pure V3 scorer/variant/language/selector functions under an isolated import namespace. Never constructs Core, Table or SearchV3Service. Legacy tables/indexes are untrusted observations, not proof of release-season identity. Comparison is a replay on supplied candidates, not a comparison with historical persisted V3 decisions. Existing real local catalog may be empty; synthetic fixtures verify useful match comparisons independently.

## Deferred work

Field-level provenance, verified season/cour crosswalk acquisition, full release intervals/timezone policy, accent-insensitive Latin secondary keys, richer token fuzzy/retrieval indexing, configuration policy object, review repository/lifecycle and API remain future slices. Safe bootstrap/download guardrails still precede runtime activation. No external dependencies were added.
