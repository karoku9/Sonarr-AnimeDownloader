"""Phase 13 production-shaped integration, still isolated and disabled by default."""
from v4_test_support import repo_tempdir
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from threading import Thread
import json
import sqlite3
import unittest

from src.v4.application import ApplicationService
from src.v4.application_adapters import SnapshotAdapter
from src.v4.application_store import ApplicationStore, digest
from src.v4.api import V4API
from src.v4.cutover import OperatorExecutionControl
from src.v4.execution import (
    DownstreamFailure,
    ExecutionCoordinator,
    FakeDownstreamAdapter,
    ProductionEffectAuthorization,
)
from src.v4.precutover import CutoverReadiness, SQLiteBackupVerifier
from src.v4.production_adapter import (
    IsolatedTestPermit,
    AnimeWorldEpisodeDownloader,
    ProductionAdapterConfig,
    ProductionDownstreamAdapter,
    ProductionEffectPermit,
    SonarrV3Client,
    build_production_adapter,
)


FIXTURE = "tests/v4/fixtures/production_metadata_v1.json"


class FakeDownloader:
    def __init__(self, fail=False):self.calls=[];self.fail=fail
    def fetch(self, source_url, source_episode, title, destination):
        self.calls.append((source_url,source_episode,title))
        if self.fail:raise DownstreamFailure("download_failed")
        path=Path(destination)/f"episode-{source_episode}.mkv"
        path.write_bytes(b"isolated-fixture")
        return path


class Phase13Tests(unittest.TestCase):
    def setUp(self):
        self.temp=repo_tempdir();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/"app.sqlite3"
        self.store=ApplicationStore(self.db)
        self.service=ApplicationService(self.store,{"fixture":SnapshotAdapter(FIXTURE)})
        self.service.scan("fixture")

    def approve(self,target="182:1"):
        item=next(i for i in self.store.list_items() if i["mapping_state"]!="superseded" and target in {t["target_id"] for t in i["original"]["targets"]})
        self.service.action(item["id"],"approve",{"expected_revision":item["revision"],"reason":"Phase13 isolated approval"})
        return self.store.get_item(item["id"])

    def sonarr(self,library,fail_command=False):
        state={"commands":[],"headers":[]}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,value):
                raw=json.dumps(value).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                state["headers"].append(self.headers.get("X-Api-Key"))
                if self.path=="/api/v3/system/status":self.reply(200,{"version":"test"})
                elif self.path.startswith("/api/v3/series/"):self.reply(200,{"id":int(self.path.rsplit("/",1)[-1]),"path":str(library)})
                else:self.reply(404,{"error":"not found"})
            def do_POST(self):
                state["headers"].append(self.headers.get("X-Api-Key"));length=int(self.headers["Content-Length"]);body=json.loads(self.rfile.read(length));state["commands"].append(body)
                self.reply(500,{"error":"fixture"}) if fail_command else self.reply(200,{"id":len(state["commands"]),"status":"queued"})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler);Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server,state

    def adapter(self,server,library,downloader=None):
        staging=self.root/"staging";journal=self.root/"journal";staging.mkdir()
        permit=IsolatedTestPermit(staging_root=staging,library_roots=(library,),sonarr_origin=f"http://127.0.0.1:{server.server_port}")
        client=SonarrV3Client(permit.sonarr_origin,"fixture-secret",permit=permit)
        return ProductionDownstreamAdapter(permit,client,downloader or FakeDownloader(),journal_root=journal)

    def test_default_coordinator_rejects_production_receipt_and_authorization_is_explicit(self):
        class Adapter(FakeDownstreamAdapter):
            def execute(self,envelope):return {"adapter":"sonarr-download-v1","receipt_id":envelope["execution_key"],"mode":"production","production_effects":True}
        item=self.approve()
        with self.assertRaises(DownstreamFailure) as caught:
            ExecutionCoordinator(self.store,Adapter(),owner_token="v4").execute(item["id"],expected_revision=item["revision"])
        self.assertEqual(caught.exception.category,"production_effect_forbidden")
        with self.assertRaises(ValueError):ProductionEffectAuthorization.from_manifest({},"ENABLE PRODUCTION EFFECTS")
        manifest={"schema_version":1,"approval_id":"approval-123","environment":"production","production_writes":True,"production_downloads":True,"adapter":"sonarr-download-v1","execution_key":"0"*64}
        authorization=ProductionEffectAuthorization.from_manifest(manifest,"ENABLE PRODUCTION EFFECTS")
        accepted=ExecutionCoordinator(self.store,Adapter(),owner_token="v4",production_authorization=authorization)._receipt({"adapter":"sonarr-download-v1","receipt_id":"0"*64,"mode":"production","production_effects":True})
        self.assertTrue(accepted["production_effects"])
        with self.assertRaises(DownstreamFailure):ExecutionCoordinator(self.store,Adapter(),owner_token="v4",production_authorization=authorization)._receipt({**accepted,"receipt_id":"1"*64})
        with self.assertRaises(DownstreamFailure):ExecutionCoordinator(self.store,Adapter(),owner_token="v4")._receipt({"adapter":"fake","receipt_id":"receipt","mode":"test","production_effects":1})

    def test_production_authorization_is_enforced_before_reservation_or_journal(self):
        manifest={"schema_version":1,"approval_id":"approval-123","environment":"production","production_writes":True,"production_downloads":True,"adapter":"sonarr-download-v1","execution_key":"0"*64}
        authorization=ProductionEffectAuthorization.from_manifest(manifest,"ENABLE PRODUCTION EFFECTS")
        class EffectAdapter:
            production_effects=True
            def __init__(self,required):self.calls=[];self.production_authorization=required
            def execute(self,envelope):
                self.calls.append(envelope)
                return {"adapter":"sonarr-download-v1","receipt_id":envelope["execution_key"],"mode":"production","production_effects":True}
        item=self.approve();effect=EffectAdapter(authorization)
        with self.assertRaises(DownstreamFailure) as caught:
            ExecutionCoordinator(self.store,effect,owner_token="v4").execute(item["id"],expected_revision=item["revision"])
        self.assertEqual(caught.exception.category,"production_effect_forbidden")
        self.assertEqual(effect.calls,[]);self.assertEqual(self.store.execution_attempts(),[])

        actual_key=digest({"item_id":item["id"],"snapshot_digest":item["digest"],
            "approved_revision":item["revision"]})
        actual_manifest={**manifest,"execution_key":actual_key};actual=ProductionEffectAuthorization.from_manifest(actual_manifest,"ENABLE PRODUCTION EFFECTS")
        authorized_effect=EffectAdapter(actual)
        completed=ExecutionCoordinator(self.store,authorized_effect,owner_token="v4",production_authorization=actual).execute(item["id"],expected_revision=item["revision"])
        self.assertEqual(completed["status"],"completed");self.assertEqual(len(authorized_effect.calls),1)

        staging=self.root/"guard-staging";library=self.root/"guard-library";journal=self.root/"guard-journal"
        staging.mkdir();library.mkdir();permit=ProductionEffectPermit.from_authorization(authorization,staging_root=staging,library_roots=(library,),sonarr_origin="http://127.0.0.1:18989")
        direct=ProductionDownstreamAdapter(permit,object(),FakeDownloader(),journal_root=journal)
        with self.assertRaises(DownstreamFailure) as direct_error:
            direct.execute({"execution_key":"1"*64,"target_ids":[],"segments":[]})
        self.assertEqual(direct_error.exception.category,"production_effect_forbidden")
        self.assertFalse(journal.exists())

    def test_isolated_adapter_downloads_imports_rescans_and_is_idempotent(self):
        library=self.root/"library";library.mkdir();server,state=self.sonarr(library);downloader=FakeDownloader();adapter=self.adapter(server,library,downloader)
        self.assertEqual(adapter.probe()["ready"],True);self.assertEqual(state["commands"],[])
        item=self.approve();result=ExecutionCoordinator(self.store,adapter,owner_token="isolated-v4").execute(item["id"],expected_revision=item["revision"])
        self.assertEqual(result["receipt"]["mode"],"isolated-test");self.assertFalse(result["receipt"]["production_effects"])
        self.assertEqual(len(downloader.calls),len(adapter.last_manifest["artifacts"]))
        self.assertTrue(all(Path(a["destination"]).parent==library for a in adapter.last_manifest["artifacts"]))
        self.assertEqual({c["name"] for c in state["commands"]},{"RescanSeries"})
        self.assertTrue(state["headers"] and all(v=="fixture-secret" for v in state["headers"]))
        lookup=adapter.lookup(result["execution_key"]);self.assertEqual(lookup["status"],"completed")
        self.assertNotIn("fixture-secret",json.dumps(lookup))

    def test_post_effect_failure_is_unknown_and_never_resubmitted(self):
        library=self.root/"library";library.mkdir();server,state=self.sonarr(library,fail_command=True);adapter=self.adapter(server,library)
        item=self.approve();coordinator=ExecutionCoordinator(self.store,adapter,owner_token="isolated-v4")
        with self.assertRaises(DownstreamFailure):coordinator.execute(item["id"],expected_revision=item["revision"])
        key=self.store.execution_attempts()[0]["execution_key"]
        self.assertEqual(adapter.lookup(key)["status"],"unknown");count=len(state["commands"])
        with self.assertRaises(DownstreamFailure):adapter.execute(self.store.reserved_execution(key)["envelope"])
        self.assertEqual(len(state["commands"]),count)

    def test_pre_effect_journal_resumes_same_envelope_but_corrupt_is_unknown(self):
        library=self.root/"library";library.mkdir();server,state=self.sonarr(library);downloader=FakeDownloader();adapter=self.adapter(server,library,downloader)
        item=self.approve();coordinator=ExecutionCoordinator(self.store,adapter,owner_token="isolated-v4")
        envelope=coordinator.reserve(item["id"],expected_revision=item["revision"]);key=envelope["execution_key"]
        adapter.journal_root.mkdir();(adapter.journal_root/f"{key}.json").write_text(json.dumps({"schema_version":1,"execution_key":key,"state":"preparing"}),encoding="utf-8")
        result=coordinator.reconcile(key)
        self.assertEqual(result["status"],"completed");self.assertTrue(downloader.calls);self.assertTrue(state["commands"])
        corrupt="f"*64;(adapter.journal_root/f"{corrupt}.json").write_text("not-json",encoding="utf-8")
        self.assertEqual(adapter.lookup(corrupt)["status"],"unknown")

    def test_reconciliation_control_requires_separate_confirmation(self):
        item=self.approve();adapter=FakeDownstreamAdapter(lookup_status="unknown");coordinator=ExecutionCoordinator(self.store,adapter,owner_token="v4")
        envelope=coordinator.reserve(item["id"],expected_revision=item["revision"]);control=OperatorExecutionControl(coordinator,"secret")
        with self.assertRaises(ValueError):control.reconcile(envelope["execution_key"],{"confirmation":"EXECUTE APPROVED PLAN"},"Bearer secret")
        with self.assertRaises(PermissionError):control.reconcile(envelope["execution_key"],{"confirmation":"RECONCILE EXECUTION"},None)
        with self.assertRaises(DownstreamFailure):control.reconcile(envelope["execution_key"],{"confirmation":"RECONCILE EXECUTION"},"Bearer secret")
        api=V4API(self.service,execution_control=control)
        status,payload=api.dispatch("GET",f"/api/v4/operations/executions/{envelope['execution_key']}/status",{}, {},"Bearer secret")
        self.assertEqual(status,200);self.assertEqual(payload["status"],"reserved");self.assertNotIn("secret",json.dumps(payload))
        with self.assertRaises(KeyError):V4API(self.service).dispatch("POST",f"/api/v4/operations/reconciliations/{envelope['execution_key']}",{"confirmation":"RECONCILE EXECUTION"},{},"Bearer secret")

    def test_backup_integrity_and_readiness_are_fail_closed(self):
        backup=SQLiteBackupVerifier.create(self.db,self.root/"backup.sqlite3")
        self.assertRegex(backup["logical_sha256"],r"^[0-9a-f]{64}$")
        self.assertTrue(SQLiteBackupVerifier.verify(self.root/"backup.sqlite3",backup))
        restored=SQLiteBackupVerifier.restore_to_new_database(self.root/"backup.sqlite3",self.root/"restored.sqlite3",backup)
        self.assertTrue(restored["verified"]);self.assertEqual(restored["logical_sha256"],backup["logical_sha256"])
        ready=CutoverReadiness.evaluate(self.store,backup_verified=True,adapter_ready=True,v3_quiescence={"v3_stopped":True,"verification_method":"service_stopped"})
        self.assertTrue(ready["ready"]);self.assertEqual(ready["blockers"],[])
        item=self.approve();ExecutionCoordinator(self.store,FakeDownstreamAdapter(),owner_token="v4").reserve(item["id"],expected_revision=item["revision"])
        blocked=CutoverReadiness.evaluate(self.store,backup_verified=True,adapter_ready=True,v3_quiescence={"v3_stopped":True,"verification_method":"service_stopped"})
        self.assertFalse(blocked["ready"]);self.assertIn("open_execution_reservations",blocked["blockers"])
        with closing(sqlite3.connect(self.root/"backup.sqlite3")) as db:db.execute("PRAGMA user_version=99");db.commit()
        self.assertFalse(SQLiteBackupVerifier.verify(self.root/"backup.sqlite3",backup))

    def test_animeworld_port_selects_exact_episode_and_contains_artifact(self):
        class Episode:
            number="7"
            def download(self,title,destination):
                path=Path(destination)/"fixture.mkv";path.write_bytes(b"episode");return path.name
        class Anime:
            def getEpisodes(self):return [Episode()]
        port=AnimeWorldEpisodeDownloader(lambda url:Anime());destination=self.root/"source";destination.mkdir()
        artifact=port.fetch("https://source.invalid/show",7,"Fixture",destination)
        self.assertEqual(artifact,destination.resolve()/"fixture.mkv")
        with self.assertRaises(DownstreamFailure):port.fetch("https://source.invalid/show",8,"Fixture",destination)

    def test_canary_preflight_requires_one_valid_envelope_and_bound_authorization(self):
        readiness={"ready":True,"blockers":[],"production_enabled":False}
        manifest={"schema_version":1,"approval_id":"approval-123","environment":"production","production_writes":True,"production_downloads":True,"adapter":"sonarr-download-v1","execution_key":"0"*64}
        authorization=ProductionEffectAuthorization.from_manifest(manifest,"ENABLE PRODUCTION EFFECTS")
        envelope={"schema_version":1,"execution_key":"0"*64,"target_ids":["182:1"],"segments":[{"release_id":"fixture"}]}
        result=CutoverReadiness.validate_single_canary(readiness,envelope,authorization)
        self.assertTrue(result["canary_ready"]);self.assertEqual(result["execution_key"],"0"*64)
        with self.assertRaises(ValueError):CutoverReadiness.validate_single_canary(readiness,{**envelope,"execution_key":"short"},authorization)
        other=ProductionEffectAuthorization.from_manifest({**manifest,"execution_key":"1"*64},"ENABLE PRODUCTION EFFECTS")
        with self.assertRaises(ValueError):CutoverReadiness.validate_single_canary(readiness,envelope,other)

    def test_production_builder_requires_explicit_secret_file_and_authorization_but_performs_no_effect(self):
        staging=self.root/"prod-staging";library=self.root/"prod-library";journal=self.root/"prod-journal"
        staging.mkdir();library.mkdir();secret=self.root/"sonarr-api-key";secret.write_text("fixture_key_1234567890\n",encoding="utf-8")
        manifest={"schema_version":1,"approval_id":"approval-123","environment":"production","production_writes":True,"production_downloads":True,"adapter":"sonarr-download-v1","execution_key":"0"*64}
        authorization=ProductionEffectAuthorization.from_manifest(manifest,"ENABLE PRODUCTION EFFECTS")
        config=ProductionAdapterConfig("http://127.0.0.1:18989",secret,staging,(library,),journal)
        adapter=build_production_adapter(config,authorization,downloader=FakeDownloader())
        self.assertTrue(adapter.permit.production_effects);self.assertFalse(journal.exists())
        self.assertNotIn("fixture_key_1234567890",repr(adapter))
        secret.write_text("short",encoding="utf-8")
        with self.assertRaises(ValueError):build_production_adapter(config,authorization,downloader=FakeDownloader())


    def test_adapter_downloads_multi_episode_source_only_once(self):
        library=self.root/"multi-library";library.mkdir();server,state=self.sonarr(library);downloader=FakeDownloader();adapter=self.adapter(server,library,downloader)
        envelope={"schema_version":1,"execution_key":"a"*64,"target_ids":["1:2"],"segments":[{"release_id":"combined","selected_candidate_id":"c","source_url":"https://source.invalid/show","episode_links":[
            {"target_id":"1:2","sonarr_series_id":1,"source_episode":2,"sonarr_season":2,"sonarr_episode":2,"absolute_episode":None},
            {"target_id":"1:2","sonarr_series_id":1,"source_episode":2,"sonarr_season":2,"sonarr_episode":3,"absolute_episode":None},
        ]}]}
        receipt=adapter.execute(envelope)
        self.assertEqual(receipt["mode"],"isolated-test")
        self.assertEqual(len(downloader.calls),1)
        self.assertEqual(downloader.calls[0][1:],(2,"1 - S02E02-E03"))
        self.assertEqual(adapter.last_manifest["artifact_count"],1)
        self.assertEqual({c["name"] for c in state["commands"]},{"RescanSeries"})



if __name__=="__main__":unittest.main()
