import unittest
from dataclasses import replace
from src.v4.models import Target, Candidate, SourceMetadata, Review
from src.v4.provenance import FieldProvenance, SeasonCrosswalk, MatcherPolicy
from src.v4.matching import match
from src.v4.normalization import normalize_title

class ProvenanceRegression(unittest.TestCase):
    def test_inferred_season_is_not_equal_to_sonarr(self):
        t=Target("Title",season_number=1)
        inferred=FieldProvenance.create("season_number",1,SourceMetadata("inferred",reliable=False),confidence=.99,derived=True)
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,field_provenance=(inferred,))
        self.assertIsNone(match(t,(c,)).selected_candidate_id)
        explicit=FieldProvenance.create("season_number",1,SourceMetadata("sonarr","17"))
        self.assertEqual("a",match(t,(replace(c,field_provenance=(explicit,)),)).selected_candidate_id)

    def test_field_provenance_does_not_inherit_record_trust(self):
        t=Target("Title",season_number=1,release_year=2020)
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,release_year=2024,field_provenance=(FieldProvenance.create("release_year",2024,SourceMetadata("inferred",reliable=False),derived=True),))
        self.assertEqual("a",match(t,(c,)).selected_candidate_id)
        self.assertTrue(any(e.source.startswith("inferred") for e in match(t,(c,)).evaluations[0].evidence))

    def test_verified_crosswalk_maps_cour_scene_and_season(self):
        t=Target("Title",season_number=2)
        cross=SeasonCrosswalk("cw1",2,candidate_namespace="catalog",candidate_season=1,scene_season=3,cour=2,part=1,season_title="Title",verified=True,source=SourceMetadata("manual_alias","approval-1"),reason="Verified release and episode correspondence")
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,season_namespace="catalog",scene_season_number=3,cour=2,part=1,crosswalks=(cross,))
        self.assertEqual("a",match(t,(c,)).selected_candidate_id)
        self.assertIsNone(match(t,(replace(c,crosswalks=(replace(cross,verified=False),)),)).selected_candidate_id)
        conflict=replace(c,crosswalks=(replace(cross,sonarr_season=1),))
        self.assertTrue(match(t,(conflict,)).evaluations[0].hard_rejected)

    def test_crosswalk_must_match_candidate_identity_and_cour(self):
        cw=SeasonCrosswalk("cw",2,candidate_namespace="catalog",candidate_season=1,cour=2,candidate_release_id="r1",verified=True,source=SourceMetadata("sonarr"),reason="Linked metadata")
        t=Target("Title",season_number=2)
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,season_namespace="catalog",cour=3,release_id="r2",crosswalks=(cw,))
        self.assertIsNone(match(t,(c,)).selected_candidate_id)

    def test_crosswalk_conflicting_with_explicit_season_rejects(self):
        cw=SeasonCrosswalk("cw",2,candidate_namespace="sonarr",candidate_season=1,verified=True,source=SourceMetadata("sonarr"),reason="Conflicting metadata")
        t=Target("Title",season_number=2)
        c=Candidate("a","Title","https://catalog.example/a",season_number=1,crosswalks=(cw,))
        self.assertTrue(match(t,(c,)).evaluations[0].hard_rejected)

    def test_normalization_diacritics_suffixes_and_original(self):
        for text in ["École – L’ANIMÉ (ITA)","Ecole - L'ANIME [DUB]","école: l’anime (Sub ITA)"]:
            self.assertEqual("ecole lanime",normalize_title(text))
        original="École – L’ANIMÉ (ITA)"
        c=Candidate("a",original,"https://catalog.example/a")
        self.assertEqual(original,c.canonical_title)
        self.assertEqual("ナディア",normalize_title("ナディア"))

    def test_policy_and_hard_reject(self):
        policy=MatcherPolicy(fuzzy_threshold=.5)
        t=Target("Black Lagoon",season_number=1)
        c=Candidate("a","Black Lagoon","https://catalog.example/a",season_number=2)
        self.assertTrue(match(t,(c,),policy=policy).evaluations[0].hard_rejected)
        with self.assertRaises(ValueError):MatcherPolicy(fuzzy_threshold=1.1)

    def test_review_provenance_roundtrip(self):
        t=Target("Title",season_number=1)
        c=Candidate("a","Title","https://catalog.example/a",field_provenance=(FieldProvenance.create("canonical_title","Title",SourceMetadata("catalog")),))
        review=Review.from_decision("r",t,(c,),match(t,(c,)))
        self.assertEqual(review.to_dict(),Review.from_dict(review.to_dict()).to_dict())

if __name__=="__main__":unittest.main()

class AdditionalProvenanceTests(unittest.TestCase):
    def test_inferred_crosswalk_anchor_cannot_certify_season(self):
        from src.v4.provenance import FieldProvenance,SeasonCrosswalk
        from src.v4.models import SourceMetadata,Target,Candidate
        from src.v4.matching import match
        manual=SourceMetadata("manual_alias")
        cw=SeasonCrosswalk("map",2,candidate_season=1,verified=True,source=manual,reason="Verified provider season")
        c=Candidate("c","Title","https://catalog.example/c",season_number=1,season_namespace="catalog",crosswalks=(cw,),field_provenance=(FieldProvenance.create("season_number",1,SourceMetadata("inferred"),derived=True),))
        self.assertEqual(match(Target("Title",season_number=2),(c,)).outcome,"needs_review")

    def test_stacked_suffix_and_japanese_dakuten(self):
        from src.v4.normalization import normalize_title
        self.assertEqual(normalize_title("Ecole (ITA) [1080p]"),normalize_title("École"))
        self.assertNotEqual(normalize_title("ガ"),normalize_title("カ"))
