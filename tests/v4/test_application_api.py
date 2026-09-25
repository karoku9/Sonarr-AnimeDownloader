"""WSGI contract/integration tests with real stable snapshots and V4-only SQLite."""
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
from src.v4.api import create_app
from src.v4.application_store import ApplicationStore,Conflict
from src.v4.application_dto import plan_id

FIXTURE="tests/v4/fixtures/production_metadata_v1.json"

class MutableAdapter:
    def __init__(self,value):self.value=deepcopy(value)
    def read(self):return deepcopy(self.value)

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir=Path("work"));self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"app.sqlite3"
        self.app=create_app(self.path,{"regressions":FIXTURE})
        self.network=patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("network forbidden"))
        self.network.start();self.addCleanup(self.network.stop)
        status,_=self.request("POST","/api/v4/scans",{"dataset":"regressions"});self.assertEqual(status,201)

    def request(self,method,path,body=None,**headers):
        environ={};setup_testing_defaults(environ)
        route,_,query=path.partition("?")
        environ.update(REQUEST_METHOD=method,PATH_INFO=route,QUERY_STRING=query,REMOTE_ADDR="127.0.0.1",HTTP_HOST="localhost")
        if body is not None:
            data=json.dumps(body).encode();environ.update(CONTENT_TYPE="application/json",CONTENT_LENGTH=str(len(data)),**{"wsgi.input":io.BytesIO(data)})
        environ.update(headers);result=[]
        payload=b"".join(self.app(environ,lambda status,headers:result.append((int(status.split()[0]),headers))))
        self.assertEqual(dict(result[0][1])["Content-Type"],"application/json; charset=utf-8")
        return result[0][0],json.loads(payload)

    def item(self,target_id):
        return next(i for i in self.app.service.store.list_items() if i["mapping_state"]!="superseded" and target_id in {t["target_id"] for t in i["original"]["targets"]})

    def action(self,item,action,**extra):
        return self.request("POST",f"/api/v4/reviews/{item['id']}/{action}",{"expected_revision":item["revision"],"reason":"Operator checked source evidence",**extra})

    def test_overview_proposal_is_not_approved(self):
        status,result=self.request("GET","/api/v4/overview")
        self.assertEqual(status,200);self.assertEqual(result["meta"]["contract_version"],"4.0")
        self.assertEqual(result["data"]["mapping_counts"]["approved"],0)
        self.assertEqual(result["data"]["current_target_count"],12)
        self.assertFalse(result["data"]["external_writes"])

    def test_black_composite_serialization(self):
        item=self.item("70:1");status,result=self.request("GET",f"/api/v4/mappings/{item['id']}")
        dto=result["data"];self.assertEqual(status,200);self.assertEqual(dto["mapping_state"],"proposed")
        self.assertEqual(dto["plan"]["episode_count"],24);self.assertEqual(len(dto["plan"]["segments"]),2)
        self.assertEqual([s["destinations"][0]["episode_range"] for s in dto["plan"]["segments"]],[[1,12],[13,24]])
        self.assertEqual([s["source_episode_range"] for s in dto["plan"]["segments"]],[[1,12],[1,12]])
        self.assertTrue(any("all 24 episodes" in r["message"] for r in dto["reasons"]))
        self.assertNotIn("https://",json.dumps(dto))
        self.assertFalse(any("conflicts" in message for e in dto["evidence_summary"] for message in e["messages"]))

    def test_crystal_boundary_review_is_single_multi_target_unit(self):
        item=self.item("189:1");self.assertEqual(item["id"],self.item("189:2")["id"])
        _,result=self.request("GET",f"/api/v4/reviews/{item['id']}")
        dto=result["data"];self.assertEqual(dto["mapping_state"],"needs_review")
        self.assertEqual(len(dto["plan"]["targets"]),2);self.assertEqual(dto["plan"]["episode_count"],26)
        self.assertEqual(len(dto["plan"]["segments"]),1)
        self.assertEqual(dto["plan"]["segments"][0]["reliability"],"inferred")
        self.assertTrue(any(r["code"]=="episode_boundary_unverified" for r in dto["reasons"]))
        self.assertEqual(len([r for r in dto["reasons"] if r["code"]=="episode_boundary_unverified"]),1)
        self.assertEqual(self.action(item,"approve")[0],409)

    def test_crystal_human_acknowledgement_does_not_rewrite_evidence(self):
        item=self.item("189:1");original=deepcopy(item["original"])
        status,result=self.action(item,"approve",acknowledge_uncertainty=True)
        self.assertEqual(status,200);self.assertEqual(result["data"]["mapping_state"],"approved")
        self.assertEqual(self.app.service.store.get_item(item["id"])["original"],original)
        self.assertEqual(result["data"]["plan"]["segments"][0]["reliability"],"inferred")

    def test_nadia_approvable_and_auditable(self):
        item=self.item("182:1");original=deepcopy(item["original"])
        status,result=self.action(item,"approve")
        self.assertEqual(status,200);self.assertTrue(result["data"]["approved_by_human"])
        self.assertEqual(result["data"]["plan"]["episode_count"],39)
        self.assertTrue(any(v["selected"] for s in result["data"]["plan"]["segments"] for v in s["variants"]))
        self.assertEqual(self.app.service.store.get_item(item["id"])["original"],original)
        events=self.app.service.store.activity(limit=100)
        self.assertTrue(any(e["item_id"]==item["id"] and e["action"]=="approve" and e["payload"]["decision"]["reason"] for e in events))

    def test_reject_auditable(self):
        item=self.item("182:1");status,result=self.action(item,"reject")
        self.assertEqual(status,200);self.assertEqual(result["data"]["mapping_state"],"rejected")
        self.assertTrue(any(e["action"]=="reject" and e["item_id"]==item["id"] for e in self.app.service.store.activity(limit=100)))

    def test_dismiss_and_reopen_snapshot_immutable(self):
        item=self.item("189:1");digest=item["digest"]
        self.assertEqual(self.action(item,"dismiss")[0],200)
        closed=self.app.service.store.get_item(item["id"])
        self.assertEqual(self.action(closed,"approve",acknowledge_uncertainty=True)[0],409)
        status,result=self.action(closed,"reopen")
        self.assertEqual(status,200);self.assertEqual(result["data"]["review_state"],"open")
        self.assertEqual(self.app.service.store.get_item(item["id"])["digest"],digest)

    def test_approved_can_reopen_withdraws_approval_preserves_audit(self):
        item=self.item("182:1");self.assertEqual(self.action(item,"approve")[0],200)
        item=self.app.service.store.get_item(item["id"])
        status,result=self.action(item,"reopen")
        self.assertEqual(status,200);self.assertFalse(result["data"]["approved_by_human"])
        self.assertEqual(result["data"]["mapping_state"],"needs_review")
        self.assertTrue(any(e["action"]=="approve" for e in self.app.service.store.activity(limit=100)))

    def test_stale_revision_conflict(self):
        item=self.item("182:1");self.assertEqual(self.action(item,"reject")[0],200)
        self.assertEqual(self.action(item,"approve")[0],409)

    def test_ranma_native_identity_not_v3_contamination(self):
        item=self.item("143:1");_,result=self.request("GET",f"/api/v4/mappings/{item['id']}")
        self.assertIn("Ranma",result["data"]["plan"]["segments"][0]["title"])
        senko=next(c for c in item["original"]["candidates"] if "Senko" in c["canonical_title"])
        release=item["original"]["proposed_plan"]["segments"][0]["release"]["release_id"]
        self.assertEqual(self.action(item,"choose",variant_choices={release:senko["id"]})[0],409)
        future=self.item("143:3");_,detail=self.request("GET",f"/api/v4/reviews/{future['id']}")
        self.assertTrue(any("conflicts" in message for e in detail["data"]["evidence_summary"] for message in e["messages"]))

    def test_enricher_preserves_verified_source_absence(self):
        from src.v4.application_adapters import SnapshotEnricher
        value=json.loads(Path(FIXTURE).read_text(encoding="utf-8"))
        case=value["cases"][0];tid=case["derived"]["target"]["target_id"]
        evidence={"state":"unavailable","verified":True,"method":"independent_frozen_catalog_audit"}
        case["raw"]["source_absence"]=evidence
        rebuilt=SnapshotEnricher().enrich(value)
        row=next(c for c in rebuilt["cases"] if c["derived"]["target"]["target_id"]==tid)
        self.assertEqual(row["raw"]["source_absence"],evidence)

    def test_cached_derived_candidate_poison_ignored(self):
        adapter=MutableAdapter(json.loads(Path(FIXTURE).read_text(encoding="utf-8")))
        for case in adapter.value["cases"]:
            for candidate in case["derived"]["candidates"]:candidate["canonical_title"]="Poisoned title"
        self.app.service.datasets["poison"]=adapter
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"poison"})[0],201)
        self.assertNotIn("Poisoned",json.dumps(self.app.service.detail(self.item("143:1")["id"])))

    def test_choose_supported_audio_variant(self):
        item=self.item("143:2");plan=item["original"]["proposed_plan"];release=plan["segments"][0]["release"]
        candidate=release["candidate_ids"][-1]
        status,result=self.action(item,"choose",plan_id=plan_id(plan),variant_choices={release["release_id"]:candidate})
        self.assertEqual(status,200);self.assertEqual(result["data"]["human_decision"]["variant_choices"][release["release_id"]],candidate)

    def test_fabricated_plan_forbidden(self):
        self.assertEqual(self.action(self.item("182:1"),"choose",plan_id="invented")[0],409)

    def test_superseded_keeps_original_and_human_decision(self):
        old=self.item("182:1");self.assertEqual(self.action(old,"approve")[0],200)
        original=deepcopy(old["original"])
        adapter=MutableAdapter(json.loads(Path(FIXTURE).read_text(encoding="utf-8")))
        for case in adapter.value["cases"]:
            if case["raw"]["sonarr_series"]["id"]==182:case["raw"]["manual_aliases"].append("Synthetic changed evidence alias")
        self.app.service.datasets["changed"]=adapter
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"changed"})[0],201)
        retained=self.app.service.store.get_item(old["id"])
        self.assertEqual(retained["mapping_state"],"superseded");self.assertEqual(retained["original"],original)
        self.assertEqual(retained["human_decision"]["action"],"approve")
        self.assertNotEqual(self.item("182:1")["id"],old["id"])
        self.assertEqual(self.action(retained,"reopen")[0],409)

    def test_unchanged_rescan_keeps_approval(self):
        old=self.item("182:1");self.assertEqual(self.action(old,"approve")[0],200)
        _,result=self.request("POST","/api/v4/scans",{"dataset":"regressions"})
        self.assertEqual(result["data"]["new_items"],0);self.assertEqual(self.item("182:1")["mapping_state"],"approved")

    def test_raw_evidence_only_advanced(self):
        item=self.item("182:1");status,result=self.request("GET",f"/api/v4/advanced/reviews/{item['id']}/evidence")
        self.assertEqual(status,200);self.assertTrue(result["data"]["original_snapshot"]["evidence"])
        self.assertIn("matcher_version",result["data"])
        _,simple=self.request("GET",f"/api/v4/reviews/{item['id']}")
        self.assertNotIn("original_snapshot",simple["data"])

    def test_pagination_filters_and_metadata(self):
        status,result=self.request("GET","/api/v4/proposals?limit=2&offset=1")
        self.assertEqual(status,200);self.assertEqual(len(result["data"]["items"]),2)
        self.assertTrue(all(i["mapping_state"]=="proposed" for i in result["data"]["items"]))
        item=self.item("182:1");status,result=self.request("GET",f"/api/v4/mappings/{item['id']}/metadata")
        self.assertEqual(status,200);self.assertEqual(result["data"]["targets"][0]["episode_count"],39)
        self.assertEqual(self.request("GET","/api/v4/reviews?state=wrong")[0],400)

    def test_settings_cannot_enable_external_effects(self):
        self.assertEqual(self.request("PATCH","/api/v4/settings",{"downloads_enabled":True})[0],400)
        self.assertEqual(self.request("PATCH","/api/v4/settings",{"fuzzy_threshold":0})[0],400)
        status,result=self.request("PATCH","/api/v4/settings",{"display_language":"it"})
        self.assertEqual(status,200);self.assertFalse(result["data"]["downloads_enabled"])
        status,result=self.request("PATCH","/api/v4/settings",
            {"auto_scan_enabled":True,"auto_scan_interval_minutes":180})
        self.assertEqual(status,200);self.assertTrue(result["data"]["auto_scan_enabled"])
        self.assertEqual(result["data"]["auto_scan_interval_minutes"],180)
        self.assertEqual(self.request("PATCH","/api/v4/settings",
            {"auto_scan_interval_minutes":17})[0],400)
        self.assertEqual(self.request("GET","/api/v4/settings")[1]["data"]["display_language"],"it")

    def test_unknown_dataset_and_client_path_forbidden(self):
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"C:/V3/.env"})[0],400)
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"regressions","url":"http://localhost:5000"})[0],400)

    def test_same_origin_loopback_json_guard(self):
        self.assertEqual(self.request("GET","/api/v4/overview",REMOTE_ADDR="192.0.2.1")[0],403)
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"regressions"},HTTP_ORIGIN="https://evil.example")[0],403)
        self.assertEqual(self.request("GET","/api/v4/overview",HTTP_HOST="evil.example")[0],403)
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"regressions"},CONTENT_TYPE="text/plain")[0],400)

    def test_unknown_entity_and_action(self):
        self.assertEqual(self.request("GET","/api/v4/reviews/missing")[0],404)
        self.assertEqual(self.request("POST",f"/api/v4/reviews/{self.item('182:1')['id']}/download",{})[0],404)

    def test_sql_snapshot_and_audit_cannot_be_mutated(self):
        import sqlite3
        item=self.item("182:1")
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE v4_items SET original='{}' WHERE id=?",(item["id"],))
            with self.assertRaises(sqlite3.IntegrityError):db.execute("DELETE FROM v4_events")

    def test_existing_review_repository_remains_compatible(self):
        from src.v4.review_store import ReviewRepository
        from src.v4.models import Review,Target,Candidate
        from src.v4.matching import match
        repo=ReviewRepository(self.path);target=Target("Unknown",season_number=1);candidates=(Candidate("legacy","Unknown","https://catalog.example/u"),)
        review=Review.from_decision("old",target,candidates,match(target,candidates));repo.create(review)
        self.assertEqual(ApplicationStore(self.path).get("old")["original"],review.to_dict())

    def test_equivalent_audio_variants_are_one_plan_and_auto_prefers_dub(self):
        from src.v4.application_adapters import SnapshotAdapter
        self.app.service.datasets["alternate"]=SnapshotAdapter("tests/v4/fixtures/application_alternate_v1.json")
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"alternate"})[0],201)
        item=self.item("73:1");self.assertEqual(item["mapping_state"],"proposed")
        self.assertEqual(item["original"]["alternate_plans"],[])
        status,result=self.action(item,"approve")
        self.assertEqual(status,200);self.assertEqual(result["data"]["plan"]["episode_count"],12)
        variants=result["data"]["plan"]["segments"][0]["variants"]
        self.assertEqual([v["audio"] for v in variants if v["selected"]],["DUB"])

    def test_partial_scan_cannot_split_existing_multi_target_plan(self):
        item=self.item("189:1");snapshot=deepcopy(item["original"])
        snapshot["targets"]=snapshot["targets"][:1]
        with self.assertRaises(Conflict):self.app.service.store.record_scan([snapshot],"partial")
        self.assertEqual(self.item("189:1")["id"],item["id"])
        self.assertEqual(self.item("189:2")["id"],item["id"])

    def test_boolean_revision_and_unknown_action_fields_invalid(self):
        item=self.item("182:1")
        self.assertEqual(self.request("POST",f"/api/v4/mappings/{item['id']}/approve",{"expected_revision":True,"reason":"checked"})[0],400)
        self.assertEqual(self.action(item,"reject",plan_id="ignored")[0],400)

    def test_overlapping_scan_batch_is_rejected_atomically(self):
        item=self.item("182:1");before=len(self.app.service.store.list_items())
        with self.assertRaises(ValueError):self.app.service.store.record_scan([deepcopy(item["original"]),deepcopy(item["original"])],"overlap")
        self.assertEqual(len(self.app.service.store.list_items()),before)

    def test_conflicting_raw_series_metadata_fails_without_storage_changes(self):
        adapter=MutableAdapter(json.loads(Path(FIXTURE).read_text(encoding="utf-8")))
        cases=[c for c in adapter.value["cases"] if c["raw"]["sonarr_series"]["id"]==143]
        cases[-1]["raw"]["sonarr_series"]["title"]="Synthetic inconsistent source title"
        self.app.service.datasets["inconsistent"]=adapter
        before=len(self.app.service.store.list_items())
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"inconsistent"})[0],400)
        self.assertEqual(len(self.app.service.store.list_items()),before)

    def test_configured_snapshot_cannot_expose_credential_urls(self):
        from src.v4.application_adapters import SnapshotAdapter
        value=json.loads(Path(FIXTURE).read_text(encoding="utf-8"))
        value["cases"][0]["raw"]["catalog_entries"][0]["url"]="https://user:secret@www.animeworld.ac/play/private"
        path=Path(self.temp.name)/"invalid-snapshot.json";path.write_text(json.dumps(value),encoding="utf-8")
        with self.assertRaises(ValueError):SnapshotAdapter(path).read()

    def test_scan_cannot_create_approved_mapping(self):
        item=self.item("182:1");snapshot=deepcopy(item["original"]);snapshot["initial_state"]="approved"
        with self.assertRaises(ValueError):self.app.service.store.record_scan([snapshot],"bad_scan")
        self.assertEqual(self.item("182:1")["mapping_state"],"proposed")
        self.assertEqual(self.item("182:1")["id"],item["id"])

    def test_factory_has_no_scan_on_startup(self):
        path=Path(self.temp.name)/"fresh.sqlite3"
        app=create_app(path,{"safe":FIXTURE})
        self.assertEqual(app.service.overview()["current_target_count"],0)
        self.assertEqual(app.service.store.activity(),[])

    def test_activity_is_paginated_and_auditable(self):
        item=self.item("182:1");self.action(item,"reject")
        status,result=self.request("GET","/api/v4/activity?limit=100")
        self.assertEqual(status,200);self.assertTrue(any(e["action"]=="reject" and e["reason"] for e in result["data"]["items"]))
        self.assertEqual(self.request("GET",f"/api/v4/activity?after={result['data']['next_cursor']}")[1]["data"]["items"],[])

if __name__=="__main__":unittest.main()
