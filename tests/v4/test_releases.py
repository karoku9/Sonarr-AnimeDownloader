"""Stable real-snapshot regressions plus synthetic negative contract tests."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from src.v4.release_resolver import resolve_series,group_releases,numbers,key
from src.v4.releases import EpisodeCoverage,EpisodeLink,Crosswalk,MappingPlan,ReleaseIdentity,ReleaseSegment
from src.v4.models import Candidate

FIXTURE=Path(__file__).parent/"fixtures"/"production_metadata_v1.json"
PHASE16_FIXTURE=Path(__file__).parent/"fixtures"/"phase16_regressions_v1.json"
PHASE16_REVIEW_FIXTURE=Path(__file__).parent/"fixtures"/"phase16_reviews_v1.json"

class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot=json.loads(FIXTURE.read_text(encoding="utf-8"))

    def cases(self,series):
        return [c for c in self.snapshot["cases"] if c["raw"]["sonarr_series"]["id"]==series]

    def phase16_cases(self,series):
        snapshot=json.loads(PHASE16_FIXTURE.read_text(encoding="utf-8"))
        return [c for c in snapshot["cases"] if c["raw"]["sonarr_series"]["id"]==series]

    def test_black_lagoon_composes_scene_releases(self):
        r=resolve_series(self.cases(70));p=r["plans"]["70:1"]
        self.assertEqual(p.outcome,"matched");self.assertEqual(len(p.segments),2)
        self.assertEqual([s.coverage.offset for s in p.segments],[0,12])
        self.assertEqual([s.coverage.status for s in p.segments],["partial","partial"])
        self.assertEqual({x.sonarr_episode for s in p.segments for x in s.coverage.links},set(range(1,25)))

    def test_black_second_is_not_first_twelve(self):
        p=resolve_series(self.cases(70))["plans"]["70:1"]
        second=next(s for s in p.segments if "Barrage" in s.release.title)
        self.assertEqual(second.coverage.absolute_range,(13,24))
        self.assertEqual({x.scene_season for x in second.coverage.links},{2})

    def test_missing_black_scene_coordinates_does_not_guess(self):
        cases=json.loads(json.dumps(self.cases(70)))
        for row in cases[0]["raw"]["sonarr_episodes"]:row.pop("sceneSeasonNumber",None);row.pop("sceneEpisodeNumber",None)
        self.assertEqual(resolve_series(cases)["plans"]["70:1"].outcome,"needs_review")

    def test_nadia_global_manual_alias_full_coverage(self):
        p=resolve_series(self.cases(182))["plans"]["182:1"]
        self.assertEqual(p.outcome,"matched");self.assertEqual(p.segments[0].coverage.episode_count,39)
        self.assertIn("pietra azzurra",p.segments[0].release.title)

    def test_nadia_date_conflict_blocks(self):
        cases=json.loads(json.dumps(self.cases(182)))
        cases[0]["raw"]["sonarr_episodes"][0]["airDate"]="2000-01-01"
        self.assertEqual(resolve_series(cases)["plans"]["182:1"].outcome,"needs_review")

    def test_crystal_inverse_is_inferred_not_auto(self):
        r=resolve_series(self.cases(189));p=r["inferred_plans"][0]
        self.assertEqual(p.target_ids,("189:1","189:2"));self.assertEqual(p.outcome,"needs_review")
        self.assertEqual(p.segments[0].coverage.destination_ranges,((1,1,14),(2,1,12)))
        self.assertFalse(p.segments[0].crosswalk.eligible)
        self.assertEqual(r["plans"]["189:1"].outcome,"needs_review")

    def test_attack_on_titan_s4_parts_are_composed_from_verified_boundaries(self):
        r=resolve_series(self.cases(69));p=r["plans"]["69:4"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in p.segments],[16,12,2])
        self.assertEqual([s.coverage.destination_ranges for s in p.segments],[((4,1,16),),((4,17,28),),((4,29,30),)])

    def test_mapping_plan_reason_codes_are_stable_and_unique(self):
        p=MappingPlan(("x",),(),"needs_review",("crosswalk_missing","crosswalk_missing","insufficient_metadata"))
        self.assertEqual(p.reason_codes,("crosswalk_missing","insufficient_metadata"))

    def test_crystal_s3_uses_2016_thirteen_not_v3_2014_twenty_six(self):
        p=resolve_series(self.cases(189))["plans"]["189:3"]
        self.assertEqual(p.outcome,"matched");self.assertEqual(p.segments[0].coverage.episode_count,13)
        self.assertEqual(p.segments[0].release.premiere_date,"2016-04-04")

    def test_crystal_boundary_gap_cannot_concatenate(self):
        cases=json.loads(json.dumps(self.cases(189)))
        c=next(c for c in cases if c["derived"]["target"]["target_id"]=="189:2")
        c["raw"]["sonarr_episodes"][0]["absoluteEpisodeNumber"]=99
        self.assertEqual(resolve_series(cases)["inferred_plans"],[])

    def test_ranma_reboot_original_contamination_distinct(self):
        r=resolve_series(self.cases(143))
        for tid in ("143:1","143:2"):
            p=r["plans"][tid];self.assertEqual(p.outcome,"matched")
            self.assertIn("2024",p.segments[0].release.title);self.assertEqual(p.segments[0].coverage.episode_count,12)
        self.assertEqual(r["plans"]["143:3"].outcome,"needs_review")

    def test_ranma_audio_variants_group(self):
        r=resolve_series(self.cases(143));p=r["plans"]["143:2"]
        self.assertEqual(len(p.segments[0].release.candidate_ids),2)

    def test_kobayashi_s2_preserves_disagreement(self):
        p=resolve_series(self.cases(98))["plans"]["98:2"]
        self.assertEqual(p.outcome,"matched");self.assertEqual(p.segments[0].release.premiere_date,"2021-07-07")
        self.assertEqual(p.segments[0].coverage.episode_count,12)

    def test_grouping_does_not_merge_remakes(self):
        cases=self.cases(143);candidates={c["id"]:Candidate.from_dict(c) for case in cases for c in case["derived"]["candidates"]}
        details={u:d for case in cases for u,d in case["raw"]["details"].items()}
        groups=group_releases(tuple(candidates.values()),details)
        for _,group in groups:self.assertEqual(len({c.release_year for c in group}),1)

    def test_noninteger_special_numbering_is_unknown(self):
        self.assertEqual(numbers({"raw":{"available_episode_numbers":[1,1.5,2]}}),())
        self.assertEqual(numbers(None),())

    def test_title_season_labels_equivalent_without_dropping_year(self):
        self.assertEqual(key("Example 2nd Season"),key("Example Season II"))
        self.assertNotEqual(key("Example (1989)"),key("Example (2024)"))

    def test_duplicate_destination_rejected(self):
        with self.assertRaises(ValueError):EpisodeCoverage((1,2),(EpisodeLink(1,1,1),EpisodeLink(2,1,1)),"partial")

    def test_unobserved_source_episode_rejected(self):
        with self.assertRaises(ValueError):EpisodeCoverage((1,),(EpisodeLink(2,1,1),))

    def test_unknown_reliability_rejected(self):
        with self.assertRaises(ValueError):Crosswalk("test","certain",("evidence",))

    def test_inferred_plan_cannot_auto_match(self):
        p=resolve_series(self.cases(189))["inferred_plans"][0]
        with self.assertRaises(ValueError):replace(p,outcome="matched")

    def test_plan_overlap_rejected(self):
        p=resolve_series(self.cases(70))["plans"]["70:1"]
        with self.assertRaises(ValueError):replace(p,segments=(p.segments[0],p.segments[0]))

    def test_inverse_contract_supports_explicit_plan(self):
        p=resolve_series(self.cases(189))["inferred_plans"][0]
        explicit=replace(p.segments[0],crosswalk=Crosswalk("synthetic provider boundaries","explicit",("independent boundary proof",)))
        accepted=replace(p,segments=(explicit,),outcome="matched",reason_codes=())
        self.assertEqual(accepted.target_ids,("189:1","189:2"))

    def test_observed_crystal_episode_boundaries_have_no_air_date_proof(self):
        fixture=json.loads((FIXTURE.parent/"crystal_episode_boundaries_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["observations"][0]["episode_entries"]),26)
        for observation in fixture["observations"][1:]:
            ld=observation["raw"]["structured"][0]
            self.assertIn(int(ld["episodeNumber"]),(14,15,26))
            self.assertNotIn("datePublished",ld)
            self.assertNotIn("airDate",ld)

    def test_replay_only_offline_and_retains_original_evidence(self):
        from unittest.mock import patch
        from src.v4.composite_replay import replay
        with patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("network forbidden")):
            result=replay(self.snapshot)
        self.assertEqual(result["counts"]["targets"],len(self.snapshot["cases"]))
        self.assertFalse(result["policy_changed"])
        self.assertTrue(all(r["target_snapshot"] and "candidate_snapshots" in r for r in result["targets"]))

    def test_shared_source_episodes_cannot_auto_assign_two_seasons(self):
        cases=json.loads(json.dumps(self.cases(182)))
        second=json.loads(json.dumps(cases[0]));second["derived"]["target"]["target_id"]="182:2"
        second["derived"]["target"]["season_number"]=2;second["derived"]["target"]["field_provenance"]=[]
        for row in second["raw"]["sonarr_episodes"]:row["seasonNumber"]=2
        cases.append(second)
        r=resolve_series(cases)
        self.assertTrue(all(p.outcome=="needs_review" for p in r["plans"].values()))
        self.assertTrue(all("duplicate_url_across_seasons" in p.reason_codes for p in r["plans"].values()))

    def test_inspector_refuses_non_catalog_and_external_urls(self):
        from src.v4.episode_inspection import catalog_page
        for url in ("https://www.animeworld.ac/video.mp4","https://example.com/play/id"):
            with self.assertRaises(ValueError):catalog_page(url)

    def test_inspector_refuses_non_html_response(self):
        from unittest.mock import patch,MagicMock
        from src.v4.episode_inspection import inspect
        response=MagicMock();response.url="https://www.animeworld.ac/play/example.id"
        response.headers={"Content-Type":"video/mp4"}
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch("urllib.request.build_opener",return_value=opener):
            with self.assertRaises(ValueError):inspect(response.url)
        response.read.assert_not_called()

    def test_unknown_audio_is_not_a_transport_preference(self):
        from src.v4.composite_replay import choose_variant
        from src.v4.models import Audio
        a=Candidate("a","Title","https://www.animeworld.ac/play/a",audio=Audio.SUB)
        b=Candidate("b","Title","https://www.animeworld.ac/play/b",audio=Audio.UNKNOWN)
        self.assertEqual(choose_variant([b,a],Audio.UNKNOWN),a)


    def test_phase16_rezero_s2_composes_13_plus_12_regression_only(self):
        p=resolve_series(self.phase16_cases(112))["plans"]["112:2"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in p.segments],[13,12])
        self.assertEqual([s.coverage.destination_ranges for s in p.segments],[((2,1,13),),((2,14,25),)])

    def test_phase16_spy_family_s1_composes_12_plus_13(self):
        p=resolve_series(self.phase16_cases(113))["plans"]["113:1"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in p.segments],[12,13])

    def test_phase16_dr_stone_s3_composes_11_plus_11(self):
        p=resolve_series(self.phase16_cases(79))["plans"]["79:3"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in p.segments],[11,11])

    def test_series_master_does_not_fallback_to_other_audio(self):
        from src.v4.composite_replay import replay
        snapshot=json.loads(PHASE16_FIXTURE.read_text(encoding="utf-8"))
        row=next(r for r in replay(snapshot)["targets"] if r["target_id"]=="79:4")
        self.assertEqual(row["after"],"unavailable")
        self.assertEqual(row["audio_preference"],"DUB")
        self.assertEqual(row["selected"],[])
        self.assertEqual(row["reason_codes"],["required_audio_unavailable"])
        self.assertEqual(row["plan"]["segments"],[])

    def test_plan_audio_prefers_full_dub_then_full_sub_and_never_mixes(self):
        from src.v4.composite_replay import choose_plan_variants
        from src.v4.models import Audio
        def c(id,a):return Candidate(id,"Title",f"https://www.animeworld.ac/play/{id}",audio=a)
        audio,selected=choose_plan_variants([[c("d1",Audio.DUB),c("s1",Audio.SUB)],[c("d2",Audio.DUB),c("s2",Audio.SUB)]])
        self.assertEqual(audio,Audio.DUB);self.assertEqual({x.audio for x in selected},{Audio.DUB})
        audio,selected=choose_plan_variants([[c("d1",Audio.DUB),c("s1",Audio.SUB)],[c("s2",Audio.SUB)]])
        self.assertEqual(audio,Audio.SUB);self.assertEqual({x.audio for x in selected},{Audio.SUB})
        audio,selected=choose_plan_variants([[c("d1",Audio.DUB)],[c("s2",Audio.SUB)]])
        self.assertEqual(audio,Audio.UNKNOWN);self.assertEqual(selected,[])
        audio,selected=choose_plan_variants([[c("d1",Audio.DUB)]],Audio.SUB,strict=True)
        self.assertEqual(audio,Audio.UNKNOWN);self.assertEqual(selected,[])
        audio,selected=choose_plan_variants([[c("d1",Audio.DUB),c("s1",Audio.SUB)]],Audio.DUB,strict=True)
        self.assertEqual(audio,Audio.DUB);self.assertEqual([x.id for x in selected],["d1"])

    def test_phase16_goldrake_alias_resolves_grendizer(self):
        p=resolve_series(self.phase16_cases(119))["plans"]["119:1"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual(p.segments[0].coverage.episode_count,74)
        self.assertIn("goldrake",p.segments[0].release.title.casefold())

    def test_phase16_hi_score_girl_s2_uses_verified_scoped_alias_year_and_dub(self):
        from src.v4.composite_replay import choose_variant
        from src.v4.models import Audio
        cases=self.phase16_cases(87);resolution=resolve_series(cases);plan=resolution["plans"]["87:2"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(plan.segments[0].crosswalk.basis,"verified_scoped_alias_year_numbering")
        candidates={c["id"]:Candidate.from_dict(c) for case in cases for c in case["derived"]["candidates"]}
        variants=[candidates[i] for i in plan.segments[0].release.candidate_ids]
        self.assertEqual(choose_variant(variants,Audio.UNKNOWN).audio,Audio.DUB)

    def test_series_s1_audio_becomes_strict_master_in_same_scan(self):
        from src.v4.composite_replay import replay
        snapshot=json.loads(PHASE16_FIXTURE.read_text(encoding="utf-8"))
        snapshot["cases"]=[c for c in snapshot["cases"] if c["raw"]["sonarr_series"]["id"]==87]
        snapshot["language_policy"]={"default":"SUB","overrides":{}}
        result={r["target_id"]:r for r in replay(snapshot)["targets"]}
        self.assertEqual({x["audio"] for x in result["87:1"]["selected"]},{"DUB"})
        self.assertEqual(result["87:2"]["audio_preference"],"DUB")
        self.assertEqual({x["audio"] for x in result["87:2"]["selected"]},{"DUB"})

    def test_phase16_auto_prefers_dub_for_real_equivalent_cowboy_and_ergo(self):
        from src.v4.composite_replay import choose_variant
        from src.v4.models import Audio
        for sid,tid in ((127,"127:1"),(161,"161:1")):
            cases=self.phase16_cases(sid);resolution=resolve_series(cases);plan=resolution["plans"][tid]
            self.assertEqual(plan.outcome,"matched")
            candidates={c["id"]:Candidate.from_dict(c) for case in cases for c in case["derived"]["candidates"]}
            for segment in plan.segments:
                variants=[candidates[i] for i in segment.release.candidate_ids]
                if any(v.audio==Audio.DUB for v in variants):
                    self.assertEqual(choose_variant(variants,Audio.UNKNOWN).audio,Audio.DUB)

    def test_auto_audio_prefers_dub_for_equivalent_release(self):
        from src.v4.composite_replay import choose_variant
        from src.v4.models import Audio
        sub=Candidate("sub","Title","https://www.animeworld.ac/play/a",audio=Audio.SUB)
        dub=Candidate("dub","Title (ITA)","https://www.animeworld.ac/play/z",audio=Audio.DUB)
        self.assertEqual(choose_variant([sub,dub],Audio.UNKNOWN),dub)

    def test_terminal_classifier_separates_waiting_airing_and_no_source(self):
        from src.v4.composite_replay import classify_unmatched
        from src.v4.models import Target
        target=Target("Example",season_number=2,episode_count=12,target_id="1:2")
        waiting={"raw":{"sonarr_season":{"statistics":{"episodeCount":0,"totalEpisodeCount":2}},"sonarr_episodes":[{"airDate":"2099-01-01"},{"airDate":"2099-01-08"}]}}
        airing={"raw":{"sonarr_season":{"statistics":{"episodeCount":0,"totalEpisodeCount":2}},"sonarr_episodes":[{"airDate":"2000-01-01"},{"airDate":"2099-01-01"}]}}
        complete={"raw":{"sonarr_season":{"statistics":{"episodeCount":0,"totalEpisodeCount":2}},"sonarr_episodes":[{"airDate":"2000-01-01"},{"airDate":"2000-01-08"}]}}
        verified_absence={"raw":{"sonarr_season":{"statistics":{"episodeCount":12,"totalEpisodeCount":12}},"sonarr_episodes":[],"source_absence":{"state":"unavailable","verified":True,"method":"independent_catalog_audit"}}}
        self.assertEqual(classify_unmatched(waiting,target,()),"waiting")
        self.assertEqual(classify_unmatched(airing,target,()),"airing")
        self.assertEqual(classify_unmatched(complete,target,()),"needs_review")
        self.assertEqual(classify_unmatched(verified_absence,target,()),"unavailable")

    def test_conflicting_trusted_external_identity_rejected(self):
        cases=json.loads(json.dumps(self.cases(182)))
        for c in cases[0]["derived"]["candidates"]:
            c["external_ids"]=[["tvdb",99999999]];c["field_provenance"]=[]
        cases[0]["derived"]["target"]["external_ids"]=[["tvdb",1]]
        cases[0]["derived"]["target"]["field_provenance"]=[]
        self.assertEqual(resolve_series(cases)["plans"]["182:1"].outcome,"needs_review")

    def review_cases(self,series):
        from src.v4.application_adapters import SnapshotEnricher
        snapshot=json.loads(PHASE16_REVIEW_FIXTURE.read_text(encoding="utf-8"))
        rebuilt=SnapshotEnricher().enrich(snapshot)
        return [c for c in rebuilt["cases"] if c["raw"]["sonarr_series"]["id"]==series]

    def test_phase16_zero_numbered_special_does_not_poison_main_coverage(self):
        self.assertEqual(numbers({"raw":{"available_episode_numbers":[0,1,2,3]}}),(1,2,3))

    def test_phase16_darling_exact_identity_year_and_coverage_resolves_bad_first_airdate(self):
        p=resolve_series(self.review_cases(77))["plans"]["77:1"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual(p.segments[0].coverage.episode_count,24)

    def test_phase16_room_of_guilty_pleasure_uses_external_alias_guilty_hole(self):
        p=resolve_series(self.review_cases(128))["plans"]["128:1"]
        self.assertEqual(p.outcome,"matched")
        self.assertIn("guilty hole",p.segments[0].release.title.casefold())

    def test_phase16_high_school_dxd_s4_uses_hero_despite_provider_episode_zero(self):
        p=resolve_series(self.review_cases(141))["plans"]["141:4"]
        self.assertEqual(p.outcome,"matched")
        self.assertIn("hero",p.segments[0].release.title.casefold())

    def test_phase16_fate_s2_uses_verified_scoped_2wei_even_when_provider_year_is_wrong(self):
        p=resolve_series(self.review_cases(166))["plans"]["166:2"]
        self.assertEqual(p.outcome,"matched")
        self.assertIn("2wei",p.segments[0].release.title.casefold())
        self.assertNotIn("herz",p.segments[0].release.title.casefold())

    def test_phase16_jojo_s2_composes_stardust_and_numbered_continuation(self):
        p=resolve_series(self.review_cases(90))["plans"]["90:2"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in p.segments],[24,24])
        self.assertEqual([s.coverage.destination_ranges for s in p.segments],[((2,1,24),),((2,25,48),)])

    def test_phase16_jojo_s5_composes_explicit_stone_ocean_parts_without_broadcast_date_equality(self):
        p=resolve_series(self.review_cases(90))["plans"]["90:5"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in p.segments],[12,12,14])

    def test_phase16_helluva_s2_uses_explicit_provider_ordinal_plus_external_structure(self):
        p=resolve_series(self.review_cases(147))["plans"]["147:2"]
        self.assertEqual(p.outcome,"matched")
        self.assertIn("helluva boss 2",p.segments[0].release.title.casefold())

    def test_phase16_helluva_s1_does_not_guess_which_one_of_eight_source_episodes_is_extra(self):
        p=resolve_series(self.review_cases(147))["plans"]["147:1"]
        self.assertEqual(p.outcome,"needs_review")

    def test_phase16_helluva_s1_projects_verified_tvdb_alternate_order_prefix(self):
        cases=self.review_cases(147)
        names=["Murder Family","Loo Loo Land","Spring Broken","C.H.E.R.U.B","The Harvest Moon Festival","Truth Seekers","Ozzie's"]
        sources=[
            {"source":"tvdb_public_official_order","record_id":"391246:official","ordering":"official","independent_of_sonarr":False,
             "title":"Helluva Boss","aliases":[],"external_ids":{"tvdb":"391246"},"seasons":[{"season_number":1,"episode_count":7,
             "episodes":[{"episode_number":i,"title":name} for i,name in enumerate(names,1)]}]},
            {"source":"tvdb_public_dvd_order","record_id":"391246:dvd","ordering":"dvd","independent_of_sonarr":False,
             "title":"Helluva Boss","aliases":[],"external_ids":{"tvdb":"391246"},"seasons":[{"season_number":1,"episode_count":8,
             "episodes":[{"episode_number":i,"title":name} for i,name in enumerate(names+["Queen Bee"],1)]}]}
        ]
        for case in cases:case["raw"]["external_metadata"]["sources"].extend(sources)
        p=resolve_series(cases)["plans"]["147:1"]
        self.assertEqual(p.outcome,"matched")
        self.assertEqual(p.segments[0].crosswalk.basis,"verified_external_ordering_prefix_projection")
        self.assertEqual(p.segments[0].coverage.source_range,(1,7))
        self.assertEqual(p.segments[0].coverage.destination_ranges,((1,1,7),))

    def test_phase16_crystal_26_release_splits_on_independent_external_14_12_structure(self):
        r=resolve_series(self.review_cases(189))
        first=r["plans"]["189:1"];second=r["plans"]["189:2"]
        self.assertEqual((first.outcome,second.outcome),("matched","matched"))
        self.assertEqual(first.segments[0].coverage.source_range,(1,26))
        self.assertEqual(second.segments[0].coverage.source_range,(1,26))
        self.assertEqual(first.segments[0].coverage.destination_ranges,((1,1,14),))
        self.assertEqual(second.segments[0].coverage.destination_ranges,((2,1,12),))
        self.assertEqual(first.segments[0].crosswalk.basis,"independent_external_season_structure")

    def test_output_path_cannot_escape_v4(self):
        from src.v4.production_sources import local_output
        with self.assertRaises(ValueError):local_output("../../outside.json")

    def test_explicit_incompatible_sonarr_season_hard_reject(self):
        cases=json.loads(json.dumps(self.cases(182)))
        for c in cases[0]["derived"]["candidates"]:
            c["season_number"]=2;c["season_namespace"]="sonarr";c["field_provenance"]=[]
        self.assertEqual(resolve_series(cases)["plans"]["182:1"].outcome,"needs_review")

    def test_one_source_episode_may_cover_multiple_destinations_inside_one_segment(self):
        identity=ReleaseIdentity("r","Combined",("c",),("https://example.invalid",),"2025-01-01",(1,))
        coverage=EpisodeCoverage((1,),(EpisodeLink(1,2,1),EpisodeLink(1,2,2)),"complete")
        segment=ReleaseSegment(identity,coverage,Crosswalk("verified segmented ordering","explicit",("same TVDB content",)))
        plan=MappingPlan(("1:2",),(segment,),"matched")
        self.assertEqual([(x.source_episode,x.sonarr_episode) for x in plan.segments[0].coverage.links],[(1,1),(1,2)])

    def test_source_episode_still_cannot_be_reused_across_plan_segments(self):
        identity=ReleaseIdentity("r","Combined",("c",),("https://example.invalid",),"2025-01-01",(1,))
        first=ReleaseSegment(identity,EpisodeCoverage((1,),(EpisodeLink(1,2,1),),"partial"),Crosswalk("a","explicit",("proof",)))
        second=ReleaseSegment(identity,EpisodeCoverage((1,),(EpisodeLink(1,2,2),),"partial"),Crosswalk("b","explicit",("proof",)))
        with self.assertRaises(ValueError):MappingPlan(("1:2",),(first,second),"matched")

if __name__=="__main__":unittest.main()
