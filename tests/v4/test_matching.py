import json
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from src.v4.models import Target, Candidate, Alias, Audio, ReasonCode, SourceMetadata, Review
from src.v4.matching import match
from src.v4.normalization import normalize_title

F = Path(__file__).parent / "fixtures"

def fixture(name):
    data = json.loads((F / (name + ".json")).read_text(encoding="utf-8"))
    return Target.from_dict(data["target"]), tuple(Candidate.from_dict(c) for c in data["candidates"])

class MatcherRegression(unittest.TestCase):
    def test_black_lagoon_s1_rejects_second_barrage(self):
        target, candidates = fixture("black_lagoon")
        decision = match(target, candidates)
        self.assertEqual("bl1", decision.selected_candidate_id)
        self.assertIn(ReasonCode.SEASON_CONFLICT, decision.evaluations[1].reason_codes)
        self.assertTrue(decision.evaluations[1].hard_rejected)
        self.assertTrue(decision.evaluations[1].evidence)

    def test_black_lagoon_s2_and_season_alias_without_candidate_number(self):
        target, candidates = fixture("black_lagoon")
        self.assertEqual("bl2", match(replace(target, season_number=2), candidates).selected_candidate_id)
        decision = match(target, (replace(candidates[1], season_number=None),))
        self.assertIn(ReasonCode.SEASON_CONFLICT, decision.evaluations[0].reason_codes)
        self.assertIsNone(decision.selected_candidate_id)

    def test_nadia_alias_wins_over_radiant(self):
        target, candidates = fixture("nadia")
        decision = match(target, candidates)
        self.assertEqual("nadia", decision.selected_candidate_id)
        self.assertEqual("exact", decision.evaluations[0].title_method)
        self.assertEqual("none", decision.evaluations[1].title_method)

    def test_sailor_moon_season_and_shared_url(self):
        target, candidates = fixture("sailor_moon")
        self.assertEqual("sm2", match(target, candidates).selected_candidate_id)
        shared = replace(candidates[1], mapped_seasons=(1,))
        decision = match(target, (shared,))
        self.assertIn(ReasonCode.DUPLICATE_URL_ACROSS_SEASONS, decision.reason_codes)
        self.assertIsNone(decision.selected_candidate_id)

    def test_shared_coverage_must_be_explicit(self):
        target, candidates = fixture("sailor_moon")
        c = replace(candidates[1], mapped_seasons=(1,), season_coverage=((1,1,13),(2,14,26)))
        self.assertEqual("sm2", match(target, (c,)).selected_candidate_id)
        bad = replace(c, season_coverage=((1,1,15),(2,14,26)))
        self.assertIsNone(match(target, (bad,)).selected_candidate_id)

    def test_external_season_namespace_requires_crosswalk(self):
        target, candidates = fixture("sailor_moon")
        c = replace(candidates[1], season_number=1, season_namespace="catalog")
        decision = match(target, (c,))
        self.assertIsNone(decision.selected_candidate_id)
        self.assertIn(ReasonCode.INSUFFICIENT_METADATA, decision.reason_codes)
        self.assertEqual("sm2", match(target, (replace(c, target_seasons=(2,)),)).selected_candidate_id)

    def test_ambiguous_unknown_seasons_review_snapshot(self):
        target, candidates = fixture("sailor_moon")
        candidates = tuple(replace(c, season_number=None) for c in candidates)
        decision = match(target, candidates)
        self.assertIsNone(decision.selected_candidate_id)
        self.assertIn(ReasonCode.INSUFFICIENT_METADATA, decision.reason_codes)
        review = Review.from_decision("review1", target, candidates, decision)
        exported = review.to_dict()
        self.assertEqual(2, len(exported["candidate_snapshots"]))
        self.assertTrue(exported["decision_snapshot"]["reason_codes"])
        self.assertEqual(review.to_dict(), Review.from_dict(exported).to_dict())

    def test_ranma_unicode_and_release_conflict(self):
        target, candidates = fixture("ranma")
        self.assertEqual("original", match(target, candidates).selected_candidate_id)
        self.assertIn(ReasonCode.RELEASE_DATE_CONFLICT, match(target, candidates).evaluations[1].reason_codes)
        for title in ["Ranma ½", "Ranma 1/2", "Ranma 1⁄2", "RANMA １／２"]:
            self.assertEqual(normalize_title("Ranma ½"), normalize_title(title))
        self.assertNotEqual(normalize_title("Ranma ½"), normalize_title("Ranma 12"))

    def test_normalization_punctuation_unicode_and_idempotence(self):
        for title in ["NADIA: Il mistero", "Nadia – Il mistero (ITA)", " Nadia -  Il   mistero "]:
            self.assertEqual("nadia il mistero", normalize_title(title))
        self.assertEqual(normalize_title("Gals Can’t"), normalize_title("Gals Can't"))
        self.assertEqual(normalize_title("Gals Can't"), normalize_title("Gals Cant"))
        self.assertEqual("ブラックラグーン", normalize_title("ブラックラグーン"))
        for text in ["Ranma ½", "L’ANIME – titolo (ITA)", "ナディア"]:
            normalized = normalize_title(text)
            self.assertEqual(normalized, normalize_title(normalized))

    def test_audio_never_overrides_better_title(self):
        target = Target("Black Lagoon", season_number=1, audio_preference=Audio.DUB)
        candidates = (Candidate("exact", "Black Lagoon", "https://catalog.example/exact", season_number=1, audio=Audio.SUB), Candidate("fuzzy", "Black Lagooon", "https://catalog.example/fuzzy", season_number=1, audio=Audio.DUB))
        self.assertEqual("exact", match(target, candidates).selected_candidate_id)
        self.assertEqual("none", match(target, candidates).evaluations[1].title_method)

    def test_audio_tiebreak_only_verified_same_release(self):
        target = Target("Title", season_number=1, audio_preference=Audio.SUB)
        a = Candidate("a", "Title", "https://catalog.example/a", season_number=1, audio=Audio.UNKNOWN, release_id="release1")
        b = replace(a, id="b", url="https://catalog.example/b", audio=Audio.SUB)
        self.assertEqual("b", match(target, (a,b)).selected_candidate_id)
        decision = match(target, (replace(a, release_id=None),replace(b, release_id=None)))
        self.assertIsNone(decision.selected_candidate_id)
        self.assertIn(ReasonCode.MULTIPLE_EQUIVALENT_CANDIDATES, decision.reason_codes)

    def test_episode_and_premiere_conflicts(self):
        target = Target("Title", season_number=1, premiere_date=date(2020,1,1), episode_count=12, episodes_complete=True)
        c = Candidate("a", "Title", "https://catalog.example/a", season_number=1, premiere_date=date(2021,1,1), episode_count=24, episodes_complete=True)
        decision = match(target,(c,))
        self.assertIn(ReasonCode.RELEASE_DATE_CONFLICT, decision.reason_codes)
        self.assertIn(ReasonCode.EPISODE_COUNT_CONFLICT, decision.reason_codes)
        self.assertIsNone(decision.selected_candidate_id)
        c = replace(c, premiere_date=None, episode_count=6, episodes_complete=False)
        self.assertEqual("a", match(target,(c,)).selected_candidate_id)

    def test_missing_year_does_not_hard_fail(self):
        target = Target("Title",season_number=1,release_year=2020)
        c = Candidate("a","Title","https://catalog.example/a",season_number=1)
        self.assertEqual("a",match(target,(c,)).selected_candidate_id)

    def test_fuzzy_is_review_only(self):
        target = Target("Black Lagoon",season_number=1)
        c = Candidate("a","Black Lagooon","https://catalog.example/a",season_number=1)
        decision = match(target,(c,))
        self.assertEqual("fuzzy",decision.evaluations[0].title_method)
        self.assertIsNone(decision.selected_candidate_id)
        self.assertIn(ReasonCode.LOW_CONFIDENCE_TITLE_MATCH,decision.reason_codes)

    def test_permutation_and_no_input_mutation(self):
        target,candidates=fixture("black_lagoon")
        before=tuple(c.to_dict() for c in candidates)
        a=match(target,candidates);b=match(target,tuple(reversed(candidates)))
        self.assertEqual(a.to_dict(),b.to_dict())
        self.assertEqual(before,tuple(c.to_dict() for c in candidates))

    def test_untrusted_season_does_not_prove_match(self):
        target=Target("Title",season_number=1)
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,source=SourceMetadata("legacy",reliable=False))
        self.assertIn(ReasonCode.INSUFFICIENT_METADATA,match(target,(c,)).reason_codes)


    def test_candidate_alias_s2_does_not_apply_to_s1(self):
        target=Target("Second Barrage",season_number=1)
        c=Candidate("a","Different Title","https://catalog.example/a",season_number=1,alternate_titles=(Alias("Second Barrage",season_number=2),))
        self.assertIsNone(match(target,(c,)).selected_candidate_id)
        self.assertEqual("none",match(target,(c,)).evaluations[0].title_method)

    def test_url_collision_across_candidate_rows_is_detected(self):
        target,candidates=fixture("sailor_moon")
        candidates=(candidates[0],replace(candidates[1],url=candidates[0].url))
        decision=match(target,candidates)
        self.assertIn(ReasonCode.DUPLICATE_URL_ACROSS_SEASONS,decision.reason_codes)
        self.assertIsNone(decision.selected_candidate_id)

    def test_same_url_duplicate_is_not_a_false_ambiguity(self):
        c=Candidate("a","Title","https://catalog.example/a",season_number=1)
        self.assertEqual("a",match(Target("Title",season_number=1),(c,replace(c,id="b"))).selected_candidate_id)

    def test_scene_alias_scope_and_unknown_namespace(self):
        alias=Alias("Second Barrage",scene_season_number=2)
        self.assertEqual(2,alias.scope())
        alias=replace(alias,scene_namespace="unmapped")
        target=Target("Title",season_number=1,alternate_titles=(alias,))
        c=Candidate("a","Second Barrage","https://catalog.example/a",season_number=1)
        self.assertIsNone(match(target,(c,)).selected_candidate_id)

    def test_invalid_url_cannot_be_selected(self):
        c=Candidate("a","Title","",season_number=1)
        decision=match(Target("Title",season_number=1),(c,))
        self.assertIn(ReasonCode.INSUFFICIENT_METADATA,decision.reason_codes)
        self.assertIsNone(decision.selected_candidate_id)

    def test_source_record_id_retained_in_evidence(self):
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,source=SourceMetadata("sonarr","record-17"))
        decision=match(Target("Title",season_number=1),(c,))
        self.assertTrue(any("record-17" in e.source for e in decision.evaluations[0].evidence))

    def test_identity_conflict_strong_reject(self):
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,external_ids=(("tvdb","other"),))
        decision=match(Target("Title",season_number=1,external_ids=(("tvdb","expected"),)),(c,))
        self.assertTrue(decision.evaluations[0].hard_rejected)
        self.assertIn(ReasonCode.IDENTITY_CONFLICT,decision.reason_codes)


    def test_date_to_year_comparison_does_not_ignore_conflict(self):
        target=Target("Title",season_number=1,premiere_date=date(2020,1,1))
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,release_year=2024)
        self.assertIn(ReasonCode.RELEASE_DATE_CONFLICT,match(target,(c,)).reason_codes)
        self.assertIsNone(match(target,(c,)).selected_candidate_id)

    def test_audio_tiebreak_explains_selected_version(self):
        target=Target("Title",season_number=1,audio_preference=Audio.DUB)
        a=Candidate("a","Title","https://catalog.example/a",season_number=1,audio=Audio.SUB,release_id="r")
        b=replace(a,id="b",url="https://catalog.example/b",audio=Audio.DUB)
        decision=match(target,(a,b))
        self.assertTrue(any(e.signal=="audio_tiebreak" for x in decision.evaluations if x.candidate_id=="b" for e in x.evidence))

    def test_different_date_kind_does_not_create_false_conflict(self):
        t=Target("Title",season_number=1,release_year=2020)
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,release_year=2024,date_kind="local_dub_release")
        self.assertEqual("a",match(t,(c,)).selected_candidate_id)

    def test_snapshot_is_immutable_even_when_input_uses_lists(self):
        aliases=[Alias("Alias")]
        c=Candidate("a","Title","https://catalog.example/a",alternate_titles=aliases)
        aliases.append(Alias("Injected"))
        self.assertEqual(1,len(c.alternate_titles))

    def test_no_short_fuzzy_or_empty_exact(self):
        target=Target("",season_number=1)
        c=Candidate("a","", "https://catalog.example/a",season_number=1)
        self.assertIsNone(match(target,(c,)).selected_candidate_id)
        target=replace(target,canonical_title="Abc")
        self.assertEqual("none",match(target,(replace(c,canonical_title="Abd"),)).evaluations[0].title_method)


    def test_global_alias_cannot_certify_an_unknown_target_season(self):
        target=Target("Title",alternate_titles=(Alias("Translated Title"),))
        c=Candidate("a","Translated Title","https://catalog.example/a")
        decision=match(target,(c,))
        self.assertIsNone(decision.selected_candidate_id)
        self.assertIn(ReasonCode.INSUFFICIENT_METADATA,decision.reason_codes)


    def test_unverified_exact_does_not_block_last_resort_fuzzy(self):
        t=Target("Black Lagoon",season_number=1)
        unknown=Candidate("a","Black Lagoon","https://catalog.example/a")
        typo=Candidate("b","Black Lagooon","https://catalog.example/b",season_number=1)
        decision=match(t,(unknown,typo))
        self.assertEqual("fuzzy",decision.evaluations[1].title_method)
        self.assertIsNone(decision.selected_candidate_id)
        self.assertIn(ReasonCode.LOW_CONFIDENCE_TITLE_MATCH,decision.reason_codes)

if __name__ == "__main__":
    unittest.main()
