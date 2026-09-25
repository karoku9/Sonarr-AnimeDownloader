# Phase 4 - production metadata replay

All local files live in V4. Authorized source reads now include the active V3 container,
Sonarr GET and public AnimeWorld GET. No source mutation, refresh, POST/PUT/DELETE,
Plex access, autosave, download, LLM, frontend, commit or Git configuration performed.
planning-with-files owns root plan/findings/progress. Final repo-review is bounded Worktree.

## Capture and offline use

```powershell
python -B -m src.v4.production_capture --output-dir work/phase4/capture
python -B -m src.v4.production --capture-dir work/phase4/capture --details-dir work/phase4/details --output-dir work/phase4/replay --pools work/phase4/candidate-pools.json
# Explicit public detail GET only when new capture needs enrichment:
# omit --pools to regenerate retrieval; add --enrich for missing detail pages.
```

One-shot exporter uses fixed anidown-v3/python -B, stdlib file reads and Sonarr GET.
API key stays in container memory and never appears in emitted metadata. No V3 component
is imported/constructed. Whitelisting removes paths, credentials, settings, tokens,
images, descriptions, comments and unnecessary runtime fields. Public candidate URLs
retain all semantic identifiers; unexpected host/query/credential URLs are refused rather
than silently rewritten. Detail redirects stay on allowlisted AnimeWorld HTTPS hosts.

Capture-manifest schema 1 records actual file-byte SHA-256 and timestamps. Consumer
verifies hashes before replay. Capture is observational, not a transactionally frozen live
installation. The three source metadata files remained byte-identical before/after.

Production snapshot schema 1 separates raw Sonarr series/seasons/episodes, raw catalog
entry+detail metadata, raw manual alias and persisted V3 mapping observations from typed
derived target/candidates/provenance. V3 observations explicitly have ground_truth=false.
Detail stores only metadata fields and structured series identity, never full HTML/player.
Full sanitized source files remain under capture/; snapshot embeds the selected per-target
source metadata and source hashes. Stable real test subset is production_metadata_v1.json,
separate from all synthetic fixtures. There are 12 real regression cases in that subset.

## Enrichment and identity rules

Catalog capture: 6,786 entries; 927 selected candidate pages, all successfully enriched.
Selection is top8 lexical >=.5 plus all exact ties and existing V3 URLs. This is retrieval,
not matcher calibration, and is not exhaustive search. Pool drift against capture fails.

Provider detail title/series aliases supersede table display labels in derived candidates;
raw labels remain intact. Failed table-only detail is quarantined from matcher input.
Merged table_seasons lack series scope; adapter reconstructs candidate footprints from
only the current Sonarr series mapping rows. Audio source is explicit where available;
legacy detections and title-derived scene/year values remain untrusted with provenance.

Sonarr scene number is distinct from actual season. Generic verified crosswalks require
nonempty explicit episode scene mappings to exactly one Sonarr season and a scoped alias
anchoring the native release title. Proof row count/source is recorded. No anime-specific
rule or equivalence from a title numeral exists. Missing relationships stay unknown.

AnimeWorld Stagione=Primavera/Estate/etc is an air quarter, not a Sonarr season number.
Provider episode counts are catalog_release scope; they do not claim complete Sonarr
season coverage. Date/episode values are preserved. Partial parts/cours need coverage
ranges, not automatic count penalties across different scopes. Matcher/provenance/model/
normalization hashes equal Phase3 baseline; fuzzy .88 and date tolerance1 remain unchanged.

## Results and metric definitions

Source: 121 Sonarr series, 5,595 episode records, 112 persisted mapping rows and one manual
alias title. Anime replay: 114 series (77 single-season,37 multi-season),192 regular targets.
All four mandatory titles are present in real data. No actual URL shared across season
keys was observed in persisted V3 mappings; 23 review duplicate reason codes represent
prospective conflicts/ambiguity, not 23 confirmed existing shared mappings.

183 targets have title/season plus date-or-year and known episode count. Only19 have a
relevant candidate with verified identity/season, date and count fields. These availability
counts do not assert complete crosswalk/coverage or human ground truth.

15 auto-matches (7.8125%),177 reviews (92.1875%). Structural hard rejects40/1,860 candidate
evaluations (2.1505%). Exact URL-set agreement12/13 paired auto decisions (92.3077%);
12/150 V3-mapped targets (8%) includes abstentions. Insufficient reason175/192 (91.1458%).
Primary insufficient_source_metadata category86/192 (44.7917%); categories differ from
multilabel reason prevalence. 192 cases have native source metadata available for manual
inspection, not 192 verified answers. No accuracy, precision or recall claim.

Review causes overlap: crosswalk_missing174, equivalent58, coverage_missing40,
fuzzy_insufficient38, structural/metadata conflict27, dates_missing9. Missing native detail
metadata0. Missing dates alone do not block an otherwise strong title+season match.

Reason codes: insufficient_metadata175, multiple_equivalent_candidates58,
low_confidence_title_match44, duplicate_url_across_seasons23, release_date_conflict21,
season_conflict4. Episode-count conflict0: different catalog/Sonarr scopes are not asserted
to be comparable. Detailed non-selected candidate evidence remains in JSON/Markdown.

Classes: metadata_supported_match_unverified14, confident disagreement1, justified
review80, primary insufficient metadata86, probable V3 error11. confident_correct and
probable_v4_error are unassigned (0), not proof of absence of error. Probable V3 labels
combine unrelated native title, date conflict and another exact supported identity;
related part titles are guarded from this heuristic. Human verification is still needed.

## Interesting cases / remaining work

Black Lagoon: actual Sonarr S1 contains24 episodes split into scene1/2,12 each. Second
Barrage links to Sonarr S1 through episode evidence, but is not a complete24-episode
mapping. Existing synthetic S1/S2 setup is a different numbering organization.
Nadia: manual Italian alias is real, date1990/39 episodes agree; alias lacks season scope.
Crystal: DUB release covers26 episodes; Sonarr splits14+12 with a separate13-episode S3.
V3 assigns the26-episode2014 release to S3; coverage/year evidence is retained for review.
Ranma2024 S1 mapping points to Senko-san2019; native title corrects the polluted table
label. S3 mapping points to2025 second release while Sonarr S3 is future2026.
Miss Kobayashi S2: V3 points to2017 first release; V4 picks2021 S release via verified scene
crosswalk. This is the single paired auto disagreement.

To lower reviews safely: verified single-season identity/coverage contract; provider-owned
season/part identifiers; explicit Sonarr/scene/cour ranges; scoped manual alias storage;
release grouping for SUB/DUB and provider episode/root URL identity; trustworthy mapping
cleanup suggestions. Dates/counts alone must not invent identity. No implementation of
these next policies or threshold tuning is included. Judge remains deferred.

115 offline tests pass (61 V4,54 compatible legacy); all previous104 retained.
Reports: ignored work/phase4/replay/production-report.md and .json; capture metadata and
hashed basis separately retained. Review report: work/phase4/repo-review.md.
