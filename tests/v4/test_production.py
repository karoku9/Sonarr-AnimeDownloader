"""Regressions sourced only from stable sanitized production_metadata_v1.json."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from src.v4.models import Candidate,Target
from src.v4.matching import match
from src.v4.production_metrics import report
from src.v4.production_replay import candidate,snapshot,targets,retrieve,catalog_entry_from_detail,inferred_scene_season

FIXTURE=Path(__file__).parent/"fixtures/production_metadata_v1.json"

class ProductionRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads(FIXTURE.read_text(encoding="utf-8"))

    def case(self,title,season):
        return next(c for c in self.data["cases"] if c["derived"]["target"]["canonical_title"]==title and c["derived"]["target"]["season_number"]==season)

    def test_black_lagoon_actual_sonarr_scene_scope_and_partial_coverage(self):
        case=self.case("Black Lagoon",1)
        t=Target.from_dict(case["derived"]["target"])
        self.assertEqual(t.episode_count,24)
        self.assertEqual({e["seasonNumber"] for e in case["raw"]["sonarr_episodes"] if e.get("sceneSeasonNumber")==2},{1})
        rebuilt=targets(case["raw"]["sonarr_series"],case["raw"]["sonarr_episodes"])[0]
        self.assertTrue(any(c.verified and c.sonarr_season==1 and "Second Barrage" in c.season_title for c in rebuilt.crosswalks))
        cs=tuple(Candidate.from_dict(c) for c in case["derived"]["candidates"])
        result=match(t,cs)
        second=next(c for c in cs if "the-second-barrage" in c.url)
        e=next(e for e in result.evaluations if e.candidate_id==second.id)
        self.assertTrue(e.season_verified)
        self.assertFalse(e.hard_rejected)
        self.assertNotIn("episode_count_conflict",{r.value for r in e.reason_codes})
        self.assertEqual(second.episode_scope,"catalog_release")
        self.assertEqual(second.episode_count,12)
        self.assertIsNone(result.selected_candidate_id)

    def test_nadia_native_metadata_and_unscoped_manual_alias(self):
        case=self.case("Nadia: The Secret of Blue Water",1)
        entry=next(e for e in case["raw"]["catalog_entries"] if "nadia-il-mistero" in e["url"])
        c=candidate(entry,case["raw"]["details"][entry["url"]],mapped_seasons=(1,))
        self.assertEqual(c.episode_count,39)
        self.assertEqual(c.premiere_date.isoformat(),"1990-04-13")
        self.assertEqual(c.audio_language,"it")
        self.assertTrue(any("Fushigi" in a.text for a in c.alternate_titles))
        self.assertEqual(case["expected_v3_decision"]["urls"],[entry["url"]])
        t=Target.from_dict(case["derived"]["target"])
        e=match(t,(c,)).evaluations[0]
        self.assertEqual(e.title_method,"exact")
        self.assertFalse(e.season_verified)  # saved mapping is no season proof

    def test_ranma_mapping_label_is_not_native_release_identity(self):
        case=self.case("Ranma ½ (2024)",1)
        mapped=case["expected_v3_decision"]["urls"][0]
        entry=next(e for e in case["raw"]["catalog_entries"] if e["url"]==mapped)
        self.assertEqual(entry["title"],"Ranma ½ (2024)")
        c=candidate(entry,case["raw"]["details"][mapped],mapped_seasons=(1,))
        self.assertEqual(c.canonical_title,"Sewayaki Kitsune no Senko-san")
        self.assertEqual(c.release_year,2019)
        summary=report({"cases":[case]})["targets"][0]
        self.assertEqual(summary["classification"],"probable_v3_error")
        self.assertNotEqual(summary["v4_selected"],mapped)
        self.assertTrue(summary["disagreement_evidence"])

    def test_multi_part_dates_do_not_imply_probable_v3_error(self):
        case=self.case("Attack on Titan",4)
        summary=report({"cases":[case]})["targets"][0]
        self.assertGreater(len(summary["v3_existing_urls"]),1)
        self.assertNotEqual(summary["classification"],"probable_v3_error")
        self.assertIn("episode_coverage_missing",summary["review_causes"])

    def test_real_kobayashi_season_disagreement_preserves_evidence(self):
        case=self.case("Miss Kobayashi's Dragon Maid",2)
        r=report({"cases":[case]})["targets"][0]
        self.assertEqual(r["classification"],"confident_disagreement_with_v3")
        self.assertIn("kobayashi-san-chi-no-maid-dragon-2",r["v4_selected"]["url"])
        self.assertFalse(r["agreement"])
        self.assertTrue(r["disagreement_evidence"])

    def test_catalog_mapping_footprints_stay_scoped_to_current_series(self):
        case=self.case("Rascal Does Not Dream of Bunny Girl Senpai",1)
        raw=case["raw"];row=raw["sonarr_series"]
        rebuilt=snapshot([row],{str(row["id"]):raw["sonarr_episodes"]},raw["catalog_entries"],case["expected_v3_decision"]["raw_mapping_rows"],raw["details"],pools={str(row["id"]):raw["catalog_entries"]})
        candidates=rebuilt["cases"][0]["derived"]["candidates"]
        observed=case["expected_v3_decision"]["raw_mapping_rows"]
        for c in candidates:
            expected={int(s) for r in observed for s,urls in r["seasons"].items() if str(s).isdigit() and c["url"] in urls}
            self.assertEqual(set(c["mapped_seasons"]),expected)
        self.assertTrue(any(e.get("table_seasons")==["1","2"] for e in raw["catalog_entries"]))
        self.assertTrue(any(c["mapped_seasons"]==[2] for c in candidates))

    def test_offline_metrics_never_contact_services_or_claim_ground_truth(self):
        with patch("urllib.request.urlopen",side_effect=AssertionError("offline")):
            result=report(self.data)
        self.assertIsNone(result["ground_truth"])
        self.assertEqual(result["categories"]["confident_correct"],0)
        self.assertFalse(result["policy"]["changed"])
        for r in result["targets"]:
            if r["v4_selected"]:
                e=next(e for e in r["decision"]["evaluations"] if e["candidate_id"]==r["v4_selected"]["id"])
                self.assertFalse(e["hard_rejected"])
        self.assertNotIn("accuracy",result["rates"])

    def test_sanitized_raw_and_provenance_roundtrip(self):
        def visit(value):
            if isinstance(value,dict):
                self.assertFalse(set(value)&{"apiKey","API_KEY","path","rootFolderPath","token","credentials","description"})
                for item in value.values():visit(item)
            elif isinstance(value,list):
                for item in value:visit(item)
        visit(self.data)
        for case in self.data["cases"]:
            target=Target.from_dict(case["derived"]["target"])
            self.assertEqual(target.to_dict(),case["derived"]["target"])
            self.assertEqual(target.canonical_title,case["raw"]["sonarr_series"]["title"])

    def test_real_source_manifest_detects_semantic_drift(self):
        from tempfile import TemporaryDirectory
        import hashlib
        from src.v4.production import verify_capture
        root=Path(__file__).resolve().parents[2]/"work"
        with TemporaryDirectory(dir=root) as temp:
            folder=Path(temp)
            source=self.case("Black Lagoon",1)["raw"]["sonarr_series"]
            payload=json.dumps(source,ensure_ascii=False,indent=2)
            (folder/"sonarr-series.json").write_text(payload,encoding="utf-8")
            (folder/"capture-manifest.json").write_text(json.dumps({"schema_version":1,"files_sha256":{"sonarr-series.json":hashlib.sha256((folder/"sonarr-series.json").read_bytes()).hexdigest()}}),encoding="utf-8")
            verify_capture(folder)
            changed=dict(source)
            changed["year"]=source["year"]+1
            (folder/"sonarr-series.json").write_text(json.dumps(changed),encoding="utf-8")
            with self.assertRaises(ValueError):verify_capture(folder)

    def test_catalog_air_quarter_and_release_scope_are_not_sonarr_season(self):
        case=self.case("Nadia: The Secret of Blue Water",1)
        entry=next(e for e in case["raw"]["catalog_entries"] if "nadia-il-mistero" in e["url"])
        detail=case["raw"]["details"][entry["url"]]
        self.assertTrue(any(p["label"]=="Stagione:" and p["value"]=="Primavera 1990" for p in detail["raw"]["fields"]))
        c=candidate(entry,detail,mapped_seasons=(1,))
        self.assertIsNone(c.season_number)
        self.assertEqual(c.episode_scope,"catalog_release")
        self.assertEqual(c.episode_count,39)

    def test_failed_detail_cannot_promote_a_real_wrong_mapping_label(self):
        case=self.case("Ranma ½ (2024)",1)
        raw=case["raw"];row=raw["sonarr_series"]
        url=case["expected_v3_decision"]["urls"][0]
        entry=next(e for e in raw["catalog_entries"] if e["url"]==url)
        self.assertEqual(entry["source"],"table")
        result=snapshot([row],{str(row["id"]):raw["sonarr_episodes"]},[entry],case["expected_v3_decision"]["raw_mapping_rows"],{},pools={str(row["id"]):[entry]})
        self.assertEqual(result["cases"][0]["derived"]["candidates"],[])
        self.assertEqual(result["cases"][0]["raw"]["catalog_entries"],[entry])

    def test_native_detail_reconstructs_candidate_without_v3_label(self):
        case=self.case("Nadia: The Secret of Blue Water",1)
        detail=next(d for d in case["raw"]["details"].values() if d and "pietra azzurra" in (d.get("raw",{}).get("title") or "").casefold())
        entry=catalog_entry_from_detail(detail)
        self.assertEqual(entry["source"],"animeworld_detail_index")
        self.assertIn("pietra-azzurra",entry["url"])

    def test_animeworld_tag_is_required_when_tag_metadata_is_present(self):
        tagged={"id":1,"title":"Tagged","seriesType":"anime","tag_labels":["animeworld"],"seasons":[]}
        gintama={"id":2,"title":"Gintama","seriesType":"anime","tag_labels":[],"seasons":[]}
        rezero={"id":3,"title":"Re: ZERO, Starting Life in Another World","seriesType":"anime","tag_labels":[],"seasons":[]}
        result=snapshot([tagged,gintama,rezero],{},[],[],{})
        self.assertEqual(result["eligibility"]["excluded_series"],[
            {"id":2,"title":"Gintama","reason":"missing_animeworld_tag"},
            {"id":3,"title":"Re: ZERO, Starting Life in Another World","reason":"missing_animeworld_tag"},
        ])
        gintama["tag_labels"]=["animeworld"]
        result=snapshot([tagged,gintama,rezero],{},[],[],{})
        self.assertEqual(result["eligibility"]["excluded_series"],[
            {"id":3,"title":"Re: ZERO, Starting Life in Another World","reason":"missing_animeworld_tag"},
        ])

    def test_retrieval_uses_native_aliases_and_never_v3_mapping_urls(self):
        row={"title":"UFO Robot Grendizer","alternateTitles":[]}
        goldrake={"title":"UFO Robot Goldrake (ITA)","aliases":["UFO Robo Grendizer"],"url":"https://www.animeworld.ac/play/goldrake","source":"animeworld_catalog"}
        unrelated={"title":"Nothing Similar","aliases":[],"url":"https://www.animeworld.ac/play/unrelated","source":"animeworld_catalog"}
        mapped={"title":"Gintama","aliases":[],"url":"https://www.animeworld.ac/play/legacy-only","source":"animeworld_catalog"}
        mappings=[{"title":"UFO Robot Grendizer","seasons":{"1":[mapped["url"]]}}]
        found=retrieve(row,[goldrake,unrelated,mapped],mappings)
        self.assertIn(goldrake,found)
        self.assertNotIn(mapped,found)

    def test_reviewed_title_aliases_are_exact_not_global_rewrites(self):
        from src.v4.title_aliases import equivalents
        self.assertIn("GTO: Great Teacher Onizuka",equivalents("Great Teacher Onizuka"))
        self.assertIn("Ganbare Douki-chan",equivalents("Ganbare Doukichan"))
        self.assertIn("Nadia - Il mistero della pietra azzurra",equivalents("Nadia: The Secret of Blue Water"))
        self.assertEqual(equivalents("The Great Teacher Onizuka Movie"),{"The Great Teacher Onizuka Movie"})

    def test_scene_season_parser_does_not_confuse_parts_with_seasons(self):
        self.assertEqual(inferred_scene_season("Dr. Stone 3 Part 2 (ITA)"),3)
        self.assertEqual(inferred_scene_season("L'attacco dei Giganti 4 Parte 3"),4)
        self.assertIsNone(inferred_scene_season("Spy x Family Part 2"))
        self.assertIsNone(inferred_scene_season("Gintama: Shirogane no Tamashii-hen 2"))
        self.assertEqual(inferred_scene_season("High Score Girl 2 (ITA)"),2)
        self.assertEqual(inferred_scene_season("Example 2nd Season"),2)

    def test_retrieval_keeps_multipart_family_beyond_eight_results(self):
        row={"title":"Dr. Stone","alternateTitles":[]}
        entries=[{"title":f"Dr. Stone {n}","aliases":[],"url":f"https://www.animeworld.ac/play/dr-{n}","source":"animeworld_catalog"} for n in range(1,10)]
        part={"title":"Dr. Stone 3 Part 2 (ITA)","aliases":["Dr. Stone: New World Part 2"],"url":"https://www.animeworld.ac/play/dr-3-part-2","source":"animeworld_catalog"}
        self.assertIn(part,retrieve(row,entries+[part],[]))

    def test_special_target_is_created_only_when_tvdb_order_proves_relocation(self):
        row={"id":1,"title":"Example","tvdbId":100,"seriesType":"anime","seasons":[{"seasonNumber":0,"statistics":{"episodeCount":1,"totalEpisodeCount":1}},{"seasonNumber":1,"statistics":{"episodeCount":2,"totalEpisodeCount":2}}]}
        episodes=[{"seasonNumber":0,"episodeNumber":1,"airDate":"2023-03-25"},{"seasonNumber":1,"episodeNumber":1,"airDate":"2023-01-01"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2023-01-08"}]
        metadata={"sources":[
            {"source":"tvdb_public_official_order","ordering":"official","external_ids":{"tvdb":"100"},"seasons":[{"season_number":0,"episode_count":1,"episodes":[{"episode_number":1,"title":"C"}]},{"season_number":1,"episode_count":2,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"}]}]},
            {"source":"tvdb_public_alternate_order","ordering":"alternate","external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":3,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"},{"episode_number":3,"title":"C"}]}]},
        ]}
        built=targets(row,episodes,external_metadata=metadata)
        self.assertEqual({t.season_number for t in built},{0,1})
        self.assertEqual(next(t for t in built if t.season_number==0).episode_count,1)
        broken=json.loads(json.dumps(metadata));broken["sources"][1]["seasons"][0]["episodes"][-1]["title"]="WRONG"
        self.assertEqual({t.season_number for t in targets(row,episodes,external_metadata=broken)},{1})
