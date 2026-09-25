from v4_test_support import repo_tempdir
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.v4.api import create_app
from src.v4.crosswalk_research import ProviderAliasExpansionResearcher
from src.v4.llm_verify import GeminiSearchVerifier


class FakeStrictVerifier:
    enabled=True
    def __init__(self,confirm=True):self.confirm=confirm
    def evaluate_many(self,groups):
        out=[]
        for group in groups:
            if group["initial_state"]=="proposed" and self.confirm:
                out.append({"schema_version":1,"provider":"fake","model":"strict-test","input_hash":"x",
                    "status":"confirmed","verdict":"confirm","confidence":.99,"summary":"mapping verificato",
                    "search_queries":[],"fallback":None})
            else:
                out.append({"schema_version":1,"provider":"fake","model":"strict-test","input_hash":"x",
                    "status":"review","verdict":"review","confidence":.99,"summary":"mapping ancora dubbio",
                    "search_queries":[],"fallback":None})
        return out


class DeploymentGateTests(unittest.TestCase):
    def test_repo_tempdir_creates_its_missing_work_root(self):
        import v4_test_support
        with TemporaryDirectory() as outer:
            missing=Path(outer)/"work"
            self.assertFalse(missing.exists())
            with patch.object(v4_test_support,"WORK_ROOT",missing):
                with v4_test_support.repo_tempdir() as created:
                    self.assertTrue(missing.is_dir())
                    self.assertTrue(Path(created).resolve().is_relative_to(missing.resolve()))

    def test_v4_tests_use_repo_temp_helper_instead_of_raw_work_parent(self):
        offenders=[]
        for path in Path(__file__).resolve().parent.glob("test_*.py"):
            if path.resolve()==Path(__file__).resolve():
                continue
            text=path.read_text(encoding="utf-8")
            if "TemporaryDirectory(dir=" in text and "work" in text:
                offenders.append(path.name)
        self.assertEqual(offenders,[])

    def test_external_alias_requeries_provider_catalog(self):
        snapshot={"schema_version":1,"provider_catalog":{"complete":True,"entries":[{
            "title":"Guilty Hole","url":"https://www.animeworld.ac/play/guilty-hole.test",
            "aliases":[],"audio":"SUB","source":"animeworld_full_type_catalog"}]},
            "cases":[{"raw":{"sonarr_series":{"id":128,"title":"Room of Guilty Pleasure","tvdbId":455169,
                "alternateTitles":[]},"catalog_entries":[],"details":{},
                "external_metadata":{"schema_version":1,"sources":[{
                    "source":"skyhook_tvdb","record_id":"455169","independent_of_sonarr":False,
                    "external_ids":{"tvdb":"455169"},"title":"Room of Guilty Pleasure",
                    "aliases":["Guilty Hole"],"seasons":[{"season_number":1,"episode_count":8}]
                }]}},"derived":{"target":{"season_number":1}}}]}
        detail={"schema_version":1,"source":"animeworld_detail","status":"ok",
            "raw":{"title":"Guilty Hole","url":"https://www.animeworld.ac/play/guilty-hole.test",
                "fields":[{"label":"Episodi:","value":"8"}],"structured":[],"available_episode_numbers":list(range(1,9))}}
        with patch("src.v4.production_sources.fetch_detail",return_value=detail):
            result,changed=ProviderAliasExpansionResearcher().enrich(snapshot,{128})
        self.assertTrue(changed)
        case=result["cases"][0]
        self.assertEqual([e["title"] for e in case["raw"]["catalog_entries"]],["Guilty Hole"])
        self.assertEqual(case["raw"]["details"]["https://www.animeworld.ac/play/guilty-hole.test"]["status"],"ok")

    def test_gemini_gate_demotes_but_never_promotes(self):
        with repo_tempdir() as temp:
            app=create_app(Path(temp)/"x.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"},
                verifier=FakeStrictVerifier(confirm=False))
            try:
                app.service.scan("real")
                items=[i for i in app.service.store.list_items() if i["mapping_state"]!="superseded"]
                self.assertFalse(any(i["mapping_state"]=="proposed" for i in items))
                demoted=next(i for i in items if "gemini_verification_failed" in i["original"]["reason_codes"])
                self.assertEqual(demoted["mapping_state"],"needs_review")
                self.assertEqual(demoted["original"]["llm_verification"]["summary"],"mapping ancora dubbio")
                # A deterministic review must stay a review even if another verifier run would confirm proposals.
                self.assertTrue(any(i["original"]["initial_state"]=="needs_review" for i in items))
            finally:pass

    def test_confirmed_gemini_keeps_proposal(self):
        with repo_tempdir() as temp:
            app=create_app(Path(temp)/"x.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"},
                verifier=FakeStrictVerifier(confirm=True))
            try:
                app.service.scan("real")
                items=[i for i in app.service.store.list_items() if i["mapping_state"]!="superseded"]
                proposed=[i for i in items if i["mapping_state"]=="proposed"]
                self.assertTrue(proposed)
                self.assertTrue(all(i["original"]["llm_verification"]["status"]=="confirmed" for i in proposed))
            finally:pass

    def test_gemini_batch_id_mismatch_retries_individually(self):
        class Verifier(GeminiSearchVerifier):
            def __init__(self):super().__init__(key="x",batch_size=2)
            def _call(self,items):
                if len(items)>1:
                    return ([{"key":"wrong","verdict":"confirm","confidence":1.0,
                        "summary":"mapping verificato"}],{},"evidence_only",None)
                return ([{"key":items[0]["key"],"verdict":"confirm","confidence":1.0,
                    "summary":"mapping verificato"}],{},"evidence_only",None)
        groups=[{"initial_state":"proposed","targets":[{"target_id":"1:1","canonical_title":"A",
            "season_number":1,"episode_count":1,"alternate_titles":[]}],"candidates":[],
            "proposed_plan":{"segments":[]},"alternate_plans":[],"audio_preference":"SUB","reason_codes":[]},
            {"initial_state":"proposed","targets":[{"target_id":"2:1","canonical_title":"B",
            "season_number":1,"episode_count":1,"alternate_titles":[]}],"candidates":[],
            "proposed_plan":{"segments":[]},"alternate_plans":[],"audio_preference":"SUB","reason_codes":[]}]
        result=Verifier().evaluate_many(groups)
        self.assertEqual([x["status"] for x in result],["confirmed","confirmed"])
        self.assertFalse(any(x["fallback"]=="id_mismatch" for x in result))

    def test_gemini_search_quota_falls_back_to_evidence_only(self):
        class Verifier(GeminiSearchVerifier):
            def __init__(self):
                super().__init__(key="x")
                self.calls=[]
            def _request(self,items,*,use_search):
                self.calls.append(use_search)
                if use_search:raise RuntimeError("search_quota_unavailable")
                return ([{"key":items[0]["key"],"verdict":"confirm","confidence":.99,
                    "summary":"mapping verificato"}],{})
        verifier=Verifier()
        audits,grounding,mode,fallback=verifier._call([{"key":"1:1"}])
        self.assertEqual(verifier.calls,[True,False])
        self.assertEqual(mode,"evidence_only")
        self.assertEqual(fallback,"search_quota_unavailable")
        self.assertEqual(audits[0]["verdict"],"confirm")


if __name__=="__main__":unittest.main()
