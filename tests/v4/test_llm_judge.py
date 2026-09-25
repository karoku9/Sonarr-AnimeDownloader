"""Phase 9 judge: suggestions cannot become deterministic mapping decisions."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.v4.application_store import ApplicationStore
from src.v4.llm_judge import Judge, JudgePolicy, MockProvider, DisabledProvider, GeminiProvider, ProviderError,configured_provider
from src.v4.api import create_app


def group():
    target={"target_id":"sample:1","canonical_title":"Sample","season_number":1,"episode_count":12,"alternate_titles":[{"title":"Sample Alias","season_number":1}],"release_year":2024}
    candidates=[{"id":"a","canonical_title":"Sample","season_number":1,"episode_count":12,"audio":"sub","url":"https://private.example/a"},
                {"id":"b","canonical_title":"Sample Alias","season_number":1,"episode_count":12,"audio":"dub","url":"https://private.example/b"},
                {"id":"bad","canonical_title":"Sample 2","season_number":2,"episode_count":12,"url":"https://private.example/bad"}]
    evaluations=[{"candidate_id":"a","hard_rejected":False,"title_method":"exact","reason_codes":[],"evidence":[]},
                 {"candidate_id":"b","hard_rejected":False,"title_method":"alias","reason_codes":[],"evidence":[]},
                 {"candidate_id":"bad","hard_rejected":True,"title_method":"fuzzy","reason_codes":["season_conflict"],"evidence":[]}]
    return {"targets":[target],"candidates":candidates,"foundation_decisions":[{"evaluations":evaluations}],"reason_codes":["multiple_equivalent_candidates"],"initial_state":"needs_review","proposed_plan":{"segments":[]},"alternate_plans":[]}


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path("work"));self.addCleanup(self.tmp.cleanup)
        self.store=ApplicationStore(Path(self.tmp.name)/"reviews.sqlite3")

    def judge(self,response,**policy):
        provider=MockProvider(response)
        return Judge(provider,self.store,JudgePolicy(**policy)),provider

    def test_valid_choice_is_only_suggestion_and_input_is_sanitized(self):
        judge,provider=self.judge({"candidate_id":"a","confidence":0.96,"reason":"Alias and season align","evidence":["same season"]})
        result=judge.evaluate(group())
        self.assertEqual(result["suggested_candidate_id"],"a")
        self.assertEqual(result["status"],"suggested")
        self.assertEqual(provider.calls,1)
        self.assertNotIn("https://",json.dumps(result["input_snapshot"]))
        self.assertEqual({c["id"] for c in result["input_snapshot"]["candidates"]},{"a","b"})

    def test_raw_urls_in_evidence_and_coverage_are_redacted(self):
        value=group()
        value["foundation_decisions"][0]["evaluations"][0]["evidence"]=[
            {"signal":"duplicate_url","expected":"https://private.example/a","observed":"https://private.example/b"},
            {"signal":"season","outcome":"match","expected":"https://private.example/a","observed":1}]
        value["candidates"][0]["season_coverage"]=[{"status":"complete","url":"https://private.example/a"}]
        result=self.judge({"candidate_id":"a","confidence":0.99,"reason":"same","evidence":[]})[0].evaluate(value)
        self.assertNotIn("https://",json.dumps(result["input_snapshot"]))

    def test_url_hidden_in_allowlisted_field_blocks_provider(self):
        value=group();value["candidates"][0]["season_coverage"]=[{"status":"https://private.example/a"}]
        judge,provider=self.judge({"candidate_id":"a","confidence":0.99,"reason":"same","evidence":[]})
        result=judge.evaluate(value)
        self.assertEqual(result["fallback"],"sensitive_input")
        self.assertEqual(provider.calls,0)

    def test_null_low_confidence_invented_and_invalid_json_fallback(self):
        cases=[({"candidate_id":None,"confidence":0.98,"reason":"uncertain","evidence":[]},"candidate_null"),
               ({"candidate_id":"a","confidence":0.3,"reason":"weak","evidence":[]},"low_confidence"),
               ({"candidate_id":"fiction","confidence":0.99,"reason":"guess","evidence":[]},"unknown_candidate"),
               ("not-json","invalid_json")]
        for response,error in cases:
            with self.subTest(error=error):
                # Distinct repositories model independent remote attempts.
                store=ApplicationStore(Path(self.tmp.name)/f"{error}.sqlite3")
                result=Judge(MockProvider(response),store).evaluate(group())
                self.assertEqual(result["status"],"needs_review")
                self.assertEqual(result["fallback"],error)
                self.assertIsNone(result["suggested_candidate_id"])

    def test_timeout_offline_and_hard_reject(self):
        for response,error in [(TimeoutError(),"timeout"),(ProviderError("offline"),"provider_error"),
                               ({"candidate_id":"bad","confidence":0.99,"reason":"wrong","evidence":[]},"hard_constraint")]:
            with self.subTest(error=error):
                result=self.judge(response)[0].evaluate(group())
                self.assertEqual(result["fallback"],error)

    def test_season_conflict_reason_is_hard_even_if_flag_missing(self):
        value=group()
        bad=value["foundation_decisions"][0]["evaluations"][2]
        bad["hard_rejected"]=False
        result=self.judge({"candidate_id":"bad","confidence":0.99,"reason":"guess","evidence":[]})[0].evaluate(value)
        self.assertEqual(result["fallback"],"hard_constraint")

    def test_cache_hit_and_disabled(self):
        judge,provider=self.judge({"candidate_id":"a","confidence":0.95,"reason":"same","evidence":[]})
        first=judge.evaluate(group());second=judge.evaluate(group())
        self.assertEqual(provider.calls,1);self.assertEqual(first,second)
        self.assertIsNone(Judge(DisabledProvider(),self.store).evaluate(group()))

    def test_cache_survives_store_reopen(self):
        first,provider=self.judge({"candidate_id":"a","confidence":0.95,"reason":"same","evidence":[]})
        result=first.evaluate(group());self.assertEqual(provider.calls,1)
        reopened=ApplicationStore(Path(self.tmp.name)/"reviews.sqlite3")
        second_provider=MockProvider("not-json")
        self.assertEqual(Judge(second_provider,reopened).evaluate(group()),result)
        self.assertEqual(second_provider.calls,0)

    def test_no_call_after_deterministic_decision_or_excess_candidates(self):
        judge,provider=self.judge({"candidate_id":"a","confidence":0.99,"reason":"same","evidence":[]})
        matched=group();matched["initial_state"]="proposed"
        self.assertIsNone(judge.evaluate(matched));self.assertEqual(provider.calls,0)
        result=self.judge({"candidate_id":"a","confidence":0.99,"reason":"same","evidence":[]},max_candidates=1)[0].evaluate(group())
        self.assertEqual(result["fallback"],"candidate_limit")

    def test_real_fixture_deterministic_scan_never_calls_llm(self):
        provider=MockProvider({"candidate_id":None,"confidence":0.5,"reason":"Boundary unknown","evidence":[]})
        app=create_app(Path(self.tmp.name)/"integration.sqlite3",
            {"regressions":"tests/v4/fixtures/production_metadata_v1.json"},judge_provider=provider)
        first=app.service.scan("regressions")
        self.assertEqual(provider.calls,0)
        proposal=next(i for i in app.service.store.list_items() if i["mapping_state"]=="proposed")
        self.assertIsNone(app.service.store.judge_attempt(proposal["id"]))
        review=next(i for i in app.service.store.list_items() if i["mapping_state"]=="needs_review")
        original=review["original"];fingerprint=review["digest"]
        self.assertIsNone(app.service.evidence(review["id"])["judge_attempt"])
        second=app.service.scan("regressions")
        self.assertEqual(second["new_items"],0)
        self.assertEqual(app.service.store.get_item(review["id"])["original"],original)
        self.assertEqual(app.service.store.get_item(review["id"])["digest"],fingerprint)
        self.assertEqual(first["item_ids"],second["item_ids"])

    def test_gemini_env_only_and_no_tool_fields(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,limit):return json.dumps({"candidates":[{"content":{"parts":[{"text":json.dumps({"candidate_id":"a","confidence":0.99,"reason":"same","evidence":[]})}]}}]}).encode()
        observed=[]
        def opener(req,timeout):
            observed.append((req,timeout));return Response()
        provider=GeminiProvider(key="test-key",opener=opener)
        self.assertEqual(provider.model,"gemini-3.1-flash-lite")
        answer=Judge(provider,self.store).evaluate(group())
        self.assertEqual(answer["suggested_candidate_id"],"a")
        req,timeout=observed[0];self.assertEqual(timeout,8)
        self.assertIn("/models/gemini-3.1-flash-lite:generateContent",req.full_url)
        self.assertNotIn("test-key",req.full_url)
        self.assertNotIn(b"test-key",req.data)
        body=json.loads(req.data)
        self.assertNotIn("tools",body)
        self.assertEqual(body["generationConfig"]["responseMimeType"],"application/json")
        schema=body["generationConfig"]["responseSchema"]
        self.assertEqual(schema["type"],"OBJECT")
        self.assertEqual(schema["properties"]["candidate_id"],{"type":"STRING","nullable":True})
        self.assertEqual(schema["properties"]["confidence"],{"type":"NUMBER"})
        self.assertEqual(schema["properties"]["reason"],{"type":"STRING"})
        self.assertEqual(schema["properties"]["evidence"],{"type":"ARRAY","items":{"type":"STRING"}})
        self.assertNotIn("additionalProperties",schema)
        with patch.dict("os.environ",{"ANIDOWN_V4_JUDGE_PROVIDER":"gemini"},clear=True):
            offline=configured_provider()
            self.assertEqual(offline.name,"gemini")
            self.assertEqual(offline.model,"gemini-3.1-flash-lite")
            separate=ApplicationStore(Path(self.tmp.name)/"offline.sqlite3")
            self.assertEqual(Judge(offline,separate).evaluate(group())["fallback"],"provider_error")

    def test_gemini_http_body_obeys_payload_limit(self):
        calls=[]
        def opener(req,timeout):calls.append(req);raise AssertionError("must not send")
        provider=GeminiProvider(key="test-key",opener=opener)
        with self.assertRaises(ProviderError) as caught:
            provider.generate({"title":"x"*2000},JudgePolicy(max_payload_bytes=1024))
        self.assertEqual(str(caught.exception),"payload_limit")
        self.assertEqual(calls,[])


    def test_payload_and_output_limits(self):
        too_large=group();too_large["targets"][0]["canonical_title"]="long "*3000
        result=self.judge({"candidate_id":"a","confidence":0.95,"reason":"same","evidence":[]})[0].evaluate(too_large)
        self.assertEqual(result["fallback"],"payload_limit")
        output={"candidate_id":"a","confidence":0.99,"reason":"x"*300,"evidence":[]}
        result=self.judge(output,max_output_bytes=256)[0].evaluate(group())
        self.assertEqual(result["fallback"],"output_limit")


if __name__=="__main__":unittest.main()
