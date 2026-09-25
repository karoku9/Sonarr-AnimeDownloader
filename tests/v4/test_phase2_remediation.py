"""Phase 2 runtime/execution safety regressions.

Every test is hermetic: effect adapters are fake or isolated and all persistence
lives below ``work/``.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import re
import threading
import unittest

from src.v4.animeworld_index import AnimeWorldIndex
from src.v4.api import create_app
from src.v4.application_store import (ApplicationStore,Conflict,approved_execution_key,
    digest)
from src.v4.download_manager import CoordinatorDownloadQueue
from src.v4.execution import (DownstreamFailure,ExecutionCoordinator,FakeDownstreamAdapter,
    ProductionEffectAuthorization,approved_execution_scopes)
from src.v4.notifications import NotificationWorker, TelegramRelay, notification_fingerprint
from src.v4.production_adapter import (IsolatedTestPermit, ProductionDownstreamAdapter,
    ProductionEffectPermit)
from src.v4.runtime import create_runtime


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests/v4/fixtures/production_metadata_v1.json"


class Phase2RemediationTests(unittest.TestCase):
    def temporary(self):
        return TemporaryDirectory(dir=ROOT / "work")

    @staticmethod
    def current_item(service, target_id="182:1"):
        return next(
            item for item in service.store.list_items()
            if item["mapping_state"] != "superseded"
            and target_id in {target["target_id"] for target in item["original"]["targets"]}
        )

    def test_av4_012_execution_key_binds_approved_revision(self):
        with self.temporary() as temporary:
            app = create_app(Path(temporary) / "app.sqlite3", {"fixture": FIXTURE})
            app.service.scan("fixture")
            item = self.current_item(app.service)
            app.service.action(item["id"], "approve", {
                "expected_revision": item["revision"], "reason": "phase 2 revision binding",
            })
            approved = app.service.store.get_item(item["id"])
            envelope = ExecutionCoordinator(
                app.service.store, FakeDownstreamAdapter(), owner_token="phase2-test"
            ).reserve(approved["id"], expected_revision=approved["revision"])
            expected = digest({
                "item_id": approved["id"],
                "snapshot_digest": approved["digest"],
                "approved_revision": approved["revision"],
            })
            self.assertEqual(envelope["execution_key"], expected)

    def test_av4_012_provider_invalidation_race_blocks_reservation(self):
        with self.temporary() as temporary:
            app = create_app(Path(temporary) / "app.sqlite3", {"fixture": FIXTURE})
            app.service.scan("fixture")
            item = next(row for row in app.service.store.list_items()
                if row["mapping_state"] == "proposed" and not row["original"]["reason_codes"])
            app.service.store.record_provider_validation_state([item["id"]], "invalidated", {"reason": "changed"})
            coordinator = ExecutionCoordinator(app.service.store, FakeDownstreamAdapter(), owner_token="phase2-test")
            with self.assertRaises(Conflict):
                coordinator.reserve_automatic(item["id"], expected_revision=item["revision"])

    def test_av4_012_provider_invalidation_blocks_human_approved_execution(self):
        with self.temporary() as temporary:
            app=create_app(Path(temporary)/"app.sqlite3",{"fixture":FIXTURE});app.service.scan("fixture")
            item=self.current_item(app.service)
            app.service.action(item["id"],"approve",{
                "expected_revision":item["revision"],"reason":"provider race"})
            approved=app.service.store.get_item(item["id"])
            app.service.store.record_provider_validation_state([item["id"]],"stale_retry",{})
            adapter=FakeDownstreamAdapter()
            with self.assertRaises(Conflict):
                ExecutionCoordinator(app.service.store,adapter,owner_token="provider-race").execute(
                    approved["id"],expected_revision=approved["revision"])
            self.assertEqual(adapter.calls,[])
            self.assertEqual(app.service.store.execution_attempts(),[])

    def test_av4_012_runtime_has_no_parallel_effect_executor(self):
        runtime_source = (ROOT / "src/v4/runtime.py").read_text(encoding="utf-8")
        queue_source = (ROOT / "src/v4/download_manager.py").read_text(encoding="utf-8")
        self.assertNotIn("from .download_manager import LiveSonarrClient,DownloadQueue", runtime_source)
        self.assertNotIn("shutil.move", queue_source)
        self.assertNotIn("import animeworld", queue_source)
        self.assertIn("ExecutionCoordinator", runtime_source)

    def test_av4_012_startup_reconciliation_dispatches_absent_once_and_blocks_unknown(self):
        with self.temporary() as temporary:
            app = create_app(Path(temporary) / "app.sqlite3", {"fixture": FIXTURE})
            app.service.scan("fixture")
            item = self.current_item(app.service)
            app.service.action(item["id"], "approve", {
                "expected_revision": item["revision"], "reason": "startup reconciliation",
            })
            approved = app.service.store.get_item(item["id"])
            adapter = FakeDownstreamAdapter()
            first = ExecutionCoordinator(app.service.store, adapter, owner_token="startup-test")
            first.reserve(approved["id"], expected_revision=approved["revision"])
            restarted = ExecutionCoordinator(ApplicationStore(Path(temporary) / "app.sqlite3"),
                adapter, owner_token="startup-test")
            recovered = restarted.reconcile_startup()
            self.assertTrue(recovered["ready"])
            self.assertEqual(len(adapter.calls), 1)

        with self.temporary() as temporary:
            app = create_app(Path(temporary) / "app.sqlite3", {"fixture": FIXTURE})
            app.service.scan("fixture")
            item = self.current_item(app.service)
            app.service.action(item["id"], "approve", {
                "expected_revision": item["revision"], "reason": "unknown reconciliation",
            })
            approved = app.service.store.get_item(item["id"])
            adapter = FakeDownstreamAdapter(lookup_status="unknown")
            coordinator = ExecutionCoordinator(app.service.store, adapter, owner_token="startup-unknown")
            coordinator.reserve(approved["id"], expected_revision=approved["revision"])
            recovery = coordinator.reconcile_startup()
            self.assertFalse(recovery["ready"])
            self.assertEqual(adapter.calls, [])
            self.assertEqual(app.service.store.execution_attempts()[0]["status"], "reserved")

    def test_av4_012_startup_readiness_requires_no_remaining_reservations(self):
        with self.temporary() as temporary:
            app=create_app(Path(temporary)/"app.sqlite3",{"fixture":FIXTURE});app.service.scan("fixture")
            item=self.current_item(app.service)
            app.service.action(item["id"],"approve",{
                "expected_revision":item["revision"],"reason":"invalid reconciliation receipt"})
            approved=app.service.store.get_item(item["id"])
            class InvalidCompletedAdapter(FakeDownstreamAdapter):
                def lookup(self,_execution_key):return {"status":"completed","receipt":{"invalid":True}}
            coordinator=ExecutionCoordinator(app.service.store,InvalidCompletedAdapter(),
                owner_token="startup-invalid-receipt")
            coordinator.reserve(approved["id"],expected_revision=approved["revision"])
            recovery=coordinator.reconcile_startup()
            self.assertFalse(recovery["ready"])
            self.assertEqual(len(recovery["unknown"]),1)
            self.assertEqual(app.service.store.execution_attempts()[0]["status"],"reserved")

    def test_av4_012_scheduler_survives_and_records_initial_sync_failure(self):
        with self.temporary() as temporary:
            app=create_app(Path(temporary)/"app.sqlite3",{"fixture":FIXTURE})
            coordinator=ExecutionCoordinator(app.service.store,FakeDownstreamAdapter(),
                owner_token="scheduler-recovery")
            queue=CoordinatorDownloadQueue(app.service,object(),coordinator,enabled=True,poll_seconds=0)
            calls=[]
            def sync_missing():
                calls.append(len(calls)+1)
                if len(calls)==1:raise OSError("transient fixture failure")
                queue.stop_event.set()
            queue.sync_missing=sync_missing
            try:queue._loop()
            finally:queue.close()
            self.assertEqual(calls,[1,2])
            events=app.service.store.activity()
            self.assertTrue(any(event["action"]=="runtime_error" for event in events))

    def test_av4_012_post_rescan_unknown_never_redispatches(self):
        with self.temporary() as temporary:
            root=Path(temporary);database=root/"app.sqlite3";library=root/"library"
            staging=root/"staging";journal=root/"journal"
            library.mkdir();staging.mkdir()
            app=create_app(database,{"fixture":FIXTURE});app.service.scan("fixture")
            item=self.current_item(app.service)
            app.service.action(item["id"],"approve",{
                "expected_revision":item["revision"],"reason":"restart boundary"})
            approved=app.service.store.get_item(item["id"])
            class Downloader:
                def __init__(self):self.calls=0
                def fetch(self,_url,source_episode,_title,destination):
                    self.calls+=1;path=Path(destination)/f"{source_episode}.mkv";path.write_bytes(b"fixture");return path
            class Sonarr:
                def __init__(self,fail):self.fail=fail;self.rescans=0
                def status(self):return {"version":"fixture"}
                def series(self,series_id):return {"id":series_id,"path":str(library)}
                def rescan_series(self,_series_id):
                    self.rescans+=1
                    if self.fail:raise DownstreamFailure("sonarr_unavailable")
                    return {"id":self.rescans}
            permit=IsolatedTestPermit(staging,(library,),"http://127.0.0.1:18989")
            downloader=Downloader();failed=ProductionDownstreamAdapter(permit,Sonarr(True),downloader,journal_root=journal)
            coordinator=ExecutionCoordinator(app.service.store,failed,owner_token="restart-test")
            with self.assertRaises(DownstreamFailure):
                coordinator.execute(approved["id"],expected_revision=approved["revision"])
            key=app.service.store.execution_attempts()[0]["execution_key"];calls=downloader.calls
            healthy=Sonarr(False)
            restarted=ProductionDownstreamAdapter(permit,healthy,downloader,journal_root=journal)
            with self.assertRaises(DownstreamFailure):
                ExecutionCoordinator(ApplicationStore(database),restarted,
                    owner_token="restart-test").reconcile(key)
            self.assertEqual(downloader.calls,calls)
            self.assertEqual(healthy.rescans,0)
            self.assertEqual(ApplicationStore(database).execution_attempts()[0]["status"],"reserved")
            self.assertTrue(list(library.glob("*.mkv")))

    def test_av4_012_manual_approval_is_revalidated_and_bound_to_envelope(self):
        with self.temporary() as temporary:
            class Validator:
                def __init__(self):self.fingerprint="a"*64
                def validate(self,urls,audio,episode_map):
                    return [{"url":url,"canonical_url":url,"fingerprint":self.fingerprint,
                        "audio":audio,"available_episode_numbers":sorted({row["source_episode"]
                            for row in episode_map if row["source_index"]==index}),
                        "validated_at":"2026-09-23T00:00:00+00:00"}
                        for index,url in enumerate(urls)]
            validator=Validator();app=create_app(Path(temporary)/"app.sqlite3",{"fixture":FIXTURE},
                manual_source_validator=validator);app.service.scan("fixture")
            series,season=min(((series,season) for series in app.service.series()["items"]
                for season in series["seasons"] if season.get("episode_count")),
                key=lambda pair:pair[1]["episode_count"])
            url="https://www.animeworld.ac/play/manual-phase2.test"
            episode_map=[{"sonarr_episode":number,"source_index":0,"source_episode":number}
                for number in range(1,season["episode_count"]+1)]
            app.service.update_season_override(series["series_id"],season["season"],{
                "source_urls":[url],"audio":"SUB","manual_approved":True,"episode_map":episode_map})
            item=app.service.store.get_item(season["mapping_id"]);adapter=FakeDownstreamAdapter()
            coordinator=ExecutionCoordinator(app.service.store,adapter,owner_token="manual-test",
                manual_source_validator=validator)
            validator.fingerprint="b"*64
            with self.assertRaises(Conflict):
                coordinator.reserve_manual(item["id"],season["target_id"],expected_revision=item["revision"])
            self.assertEqual(app.service.store.execution_attempts(),[])
            validator.fingerprint="a"*64
            result=coordinator.execute_manual(item["id"],season["target_id"],expected_revision=item["revision"])
            self.assertEqual(result["status"],"completed")
            envelope=adapter.calls[0]
            self.assertEqual(envelope["manual_target_id"],season["target_id"])
            self.assertEqual(envelope["approved_revision"],item["revision"])
            self.assertTrue(all(segment["source_fingerprint"]=="a"*64 for segment in envelope["segments"]))

    def test_av4_012_manual_multi_target_snapshot_reserves_each_episode_independently(self):
        with self.temporary() as temporary:
            class Validator:
                def validate(self,urls,audio,episode_map):
                    return [{"url":url,"canonical_url":url,"fingerprint":str(index+1)*64,
                        "audio":audio,"available_episode_numbers":sorted({row["source_episode"]
                            for row in episode_map if row["source_index"]==index}),
                        "validated_at":"2026-09-23T00:00:00+00:00"}
                        for index,url in enumerate(urls)]
            validator=Validator();app=create_app(Path(temporary)/"app.sqlite3",{"fixture":FIXTURE},
                manual_source_validator=validator);app.service.scan("fixture")
            series=next(value for value in app.service.series()["items"] if value["series_id"]==189)
            multi_mapping_id=next(item["id"] for item in app.service.store.list_items()
                if len(item["original"]["targets"])>1 and any(
                    target["target_id"].startswith("189:") for target in item["original"]["targets"]))
            multi_seasons=[season for season in series["seasons"]
                if season["mapping_id"]==multi_mapping_id]
            self.assertEqual(len(multi_seasons),2)
            for season in multi_seasons:
                count=season["episode_count"];url=f"https://www.animeworld.ac/play/manual-{season['season']}.test"
                episode_map=[{"sonarr_episode":number,"source_index":0,"source_episode":number}
                    for number in range(1,count+1)]
                app.service.update_season_override(189,season["season"],{
                    "source_urls":[url],"audio":"SUB","manual_approved":True,
                    "episode_map":episode_map})
            adapter=FakeDownstreamAdapter();coordinator=ExecutionCoordinator(app.service.store,adapter,
                owner_token="multi-manual",manual_source_validator=validator)
            current=app.service.series()["items"]
            seasons=[season for season in next(value for value in current
                if value["series_id"]==189)["seasons"] if season["mapping_id"]==multi_mapping_id]
            for season in seasons:
                item=app.service.store.get_item(season["mapping_id"])
                coordinator.execute_manual(item["id"],season["target_id"],
                    expected_revision=item["revision"],sonarr_episode=1)
            self.assertEqual(len(adapter.calls),2)
            self.assertEqual({tuple(call["target_ids"]) for call in adapter.calls},
                {(seasons[0]["target_id"],),(seasons[1]["target_id"],)})
            self.assertTrue(all(sum(len(segment["episode_links"]) for segment in call["segments"])==1
                for call in adapter.calls))
            self.assertEqual(len({call["execution_key"] for call in adapter.calls}),2)
            self.assertEqual(len(app.service.store.execution_attempts()),2)

    def test_av4_013_flag_alone_is_truthfully_unavailable(self):
        with self.temporary() as temporary, patch.dict(os.environ, {
            "ANIDOWN_V4_DOWNLOADS_ENABLED": "1",
            "ANIDOWN_V4_SONARR_ORIGIN": "",
            "ANIDOWN_V4_SONARR_KEY_FILE": "",
        }, clear=False):
            runtime = create_runtime(Path(temporary) / "app.sqlite3", {"fixture": FIXTURE})
            self.addCleanup(runtime.close)
            capability = runtime.service.store.settings()["download_capability"]
            self.assertFalse(capability["ready"])
            self.assertEqual(capability["status"], "configuration_error")
            self.assertTrue(capability["diagnostics"])

    def test_av4_013_preflight_enables_only_complete_current_capability(self):
        with self.temporary() as temporary:
            root=Path(temporary);database=root/"app.sqlite3"
            app=create_app(database,{"fixture":FIXTURE});app.service.scan("fixture")
            item=next(row for row in app.service.store.list_items()
                if row["mapping_state"]=="proposed" and not row["original"]["reason_codes"])
            target_id,episode=approved_execution_scopes(item,automatic=True)[0]
            key=approved_execution_key(item,target_id=target_id,sonarr_episode=episode)
            manifest={"schema_version":1,"approval_id":"phase2-capability","environment":"production",
                "production_writes":True,"production_downloads":True,"adapter":"sonarr-download-v1",
                "execution_key":key}
            authorization=ProductionEffectAuthorization.from_manifest(manifest,"ENABLE PRODUCTION EFFECTS")
            targets=[target_id]
            app.service.store.claim_writer(targets,"v4","phase2-owner")
            staging=root/"staging";library=root/"library";journal=root/"journal"
            staging.mkdir();library.mkdir();journal.mkdir()
            class Sonarr:
                def status(self):return {"version":"fixture"}
                def wanted_missing(self):return []
                def queue_episode_ids(self):return set()
            class Downloader:
                def fetch(self,*_args):raise AssertionError("preflight must not download")
            permit=ProductionEffectPermit.from_authorization(authorization,staging_root=staging,
                library_roots=(library,),sonarr_origin="https://sonarr.invalid:8989")
            sonarr=Sonarr();adapter=ProductionDownstreamAdapter(permit,sonarr,Downloader(),journal_root=journal)
            self.assertTrue(adapter.probe()["production_effects"])
            with patch.dict(os.environ,{"ANIDOWN_V4_DOWNLOADS_ENABLED":"1",
                "ANIDOWN_V4_V3_QUIESCENCE":"service_stopped"},clear=False):
                runtime=create_runtime(database,{"fixture":FIXTURE},download_adapter=adapter,
                    production_authorization=authorization,execution_owner_token="phase2-owner",
                    download_sonarr=sonarr)
            self.addCleanup(runtime.close)
            settings=runtime.service.store.settings()
            self.assertTrue(settings["download_capability"]["ready"])
            self.assertTrue(settings["downloads_enabled"])
            self.assertTrue(settings["external_writes_enabled"])

    def test_av4_014_distinct_episode_source_and_stage_have_distinct_identity(self):
        base = {"topic": "error", "item_id": "mapping", "payload": {
            "action": "execution_failed", "error_category": "download_failed",
            "target_id": "42:1", "episode": 1, "source_fingerprint": "a" * 64,
            "stage": "download",
        }}
        variants = [
            base,
            {**base, "payload": {**base["payload"], "episode": 2}},
            {**base, "payload": {**base["payload"], "source_fingerprint": "b" * 64}},
            {**base, "payload": {**base["payload"], "stage": "rescan"}},
        ]
        self.assertEqual(len({notification_fingerprint(value) for value in variants}), len(variants))

    def test_av4_014_failure_preserves_cursor_and_persists_bounded_backoff(self):
        with self.temporary() as temporary:
            store = ApplicationStore(Path(temporary) / "app.sqlite3")
            store.update_settings({"notify_error": True})
            with store._connect() as db:
                store._event(db, None, "execution_failed", {
                    "target_id": "42:1", "episode": 3, "error_category": "download_failed",
                    "stage": "download",
                }, "test")
            class FailingRelay:
                configured = True
                def send(self, _event):
                    raise RuntimeError("telegram_relay_unavailable")
            now = datetime(2026, 9, 23, tzinfo=timezone.utc)
            worker = NotificationWorker(store, FailingRelay(), clock=lambda: now)
            worker.cursor = 0
            self.assertEqual(worker.run_once(), 0)
            retry = store.notification_retry_state("telegram")
            self.assertEqual(store.notification_cursor("telegram"), 0)
            self.assertEqual(retry["attempt_count"], 1)
            due = datetime.fromisoformat(retry["next_attempt_at"])
            self.assertGreaterEqual((due - now).total_seconds(), 15)
            self.assertLessEqual((due - now).total_seconds(), 3600)

    def test_av4_017_wal_reader_writer_stress_has_no_lock_failures(self):
        with self.temporary() as temporary:
            store = ApplicationStore(Path(temporary) / "app.sqlite3")
            def writer(index):
                for value in range(20):
                    store.advance_notification_cursor(f"worker-{index}", value)
                return store.notification_cursor(f"worker-{index}")
            def reader(_index):
                for _ in range(40):
                    store.settings();store.notification_events()
                return True
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(writer, range(4))) + list(pool.map(reader, range(4)))
            self.assertEqual(results[:4], [19] * 4)
            diagnostics = store.sqlite_diagnostics()
            self.assertEqual(diagnostics["journal_mode"], "wal")
            self.assertEqual(diagnostics["lock_failures"], 0)

    def test_av4_017_scanner_api_notification_execution_concurrency(self):
        with self.temporary() as temporary:
            app=create_app(Path(temporary)/"app.sqlite3",{"fixture":FIXTURE});app.service.scan("fixture")
            item=self.current_item(app.service)
            app.service.action(item["id"],"approve",{
                "expected_revision":item["revision"],"reason":"concurrency execution"})
            approved=app.service.store.get_item(item["id"])
            def scanner():
                for _ in range(3):app.service.scan("fixture")
                return "scan"
            def api_writer():
                for value in ("en","it","en"):app.service.store.update_settings({"display_language":value})
                return "api"
            def notification_writer():
                for sequence in range(30):app.service.store.advance_notification_cursor("stress",sequence)
                return "notification"
            def execution_writer():
                return ExecutionCoordinator(app.service.store,FakeDownstreamAdapter(),
                    owner_token="concurrency-test").execute(approved["id"],
                        expected_revision=approved["revision"])["status"]
            def reader():
                for _ in range(30):app.service.overview();app.service.series()
                return "read"
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures=[pool.submit(operation) for operation in
                    (scanner,api_writer,notification_writer,execution_writer,reader)]
                results=[future.result(timeout=20) for future in futures]
            self.assertEqual(set(results),{"scan","api","notification","completed","read"})
            self.assertEqual(app.service.store.notification_cursor("stress"),29)
            self.assertEqual(app.service.store.execution_attempts()[0]["status"],"completed")

    def test_av4_017_bounded_busy_retry_and_wal_restart(self):
        with self.temporary() as temporary:
            import sqlite3
            database=Path(temporary)/"app.sqlite3";store=ApplicationStore(database)
            lock=sqlite3.connect(database,timeout=1);lock.execute("BEGIN IMMEDIATE")
            first_attempt=threading.Event();retry_attempt=threading.Event();attempts=[]
            def contended_write(db):
                db.execute("PRAGMA busy_timeout=100")
                attempts.append(True)
                (first_attempt if len(attempts)==1 else retry_attempt).set()
                db.execute("""INSERT INTO v4_notification_cursors(backend,sequence,updated_at)
                    VALUES (?,?,?) ON CONFLICT(backend) DO UPDATE SET
                    sequence=MAX(v4_notification_cursors.sequence,excluded.sequence),
                    updated_at=excluded.updated_at""",("forced-lock",7,store._now()))
                return db.execute("SELECT sequence FROM v4_notification_cursors WHERE backend=?",
                    ("forced-lock",)).fetchone()[0]
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(store._idempotent_write,"forced-lock",contended_write)
                self.assertTrue(first_attempt.wait(1))
                self.assertTrue(retry_attempt.wait(2));lock.rollback();lock.close()
                self.assertEqual(future.result(timeout=3),7)
            diagnostics=store.sqlite_diagnostics()
            self.assertGreaterEqual(diagnostics["lock_retries"],1)
            restarted=ApplicationStore(database)
            self.assertEqual(restarted.notification_cursor("forced-lock"),7)
            self.assertEqual(restarted.sqlite_diagnostics()["journal_mode"],"wal")
            calls=[]
            def logic_conflict(_db):
                calls.append(True);raise Conflict("fixture logic conflict")
            with self.assertRaises(Conflict):
                restarted._idempotent_write("logic-conflict",logic_conflict)
            self.assertEqual(len(calls),1)

    def test_av4_018_atomic_catalog_publish_and_last_known_good_recovery(self):
        with self.temporary() as temporary:
            path = Path(temporary) / "catalog.json"
            index = AnimeWorldIndex(path, "https://www.animeworld.ac")
            first = {"generated_at": "2026-09-23T00:00:00", "source_base_url": index.base_url,
                "catalog_complete": True, "entries": [{"title": "First", "url": "https://www.animeworld.ac/play/first"}]}
            second = {**first, "entries": [{"title": "Second", "url": "https://www.animeworld.ac/play/second"}]}
            index.write(first)
            with self.assertRaises(ValueError):
                index.write({"generated_at":"2026-09-23T00:00:00",
                    "source_base_url":index.base_url,"entries":[]})
            with patch("src.v4.animeworld_index.os.replace", side_effect=OSError("simulated interruption")):
                with self.assertRaises(OSError):
                    index.write(second)
            self.assertEqual(AnimeWorldIndex(path, index.base_url).data["entries"], first["entries"])
            path.write_text("{truncated", encoding="utf-8")
            recovered = AnimeWorldIndex(path, index.base_url)
            self.assertEqual(recovered.data["entries"], first["entries"])
            path.write_bytes(b"")
            recovered_zero = AnimeWorldIndex(path, index.base_url)
            self.assertEqual(recovered_zero.data["entries"], first["entries"])
            observed=[]
            def publish_versions():
                for number in range(20):index.write(first if number%2==0 else second)
            def read_versions():
                for _ in range(60):
                    document=index.read()
                    self.assertTrue(document["catalog_complete"])
                    observed.append(document["entries"][0]["title"])
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures=[pool.submit(publish_versions),*(pool.submit(read_versions) for _ in range(4))]
                for future in futures:future.result(timeout=15)
            self.assertTrue(observed)
            self.assertLessEqual(set(observed),{"First","Second"})

    def test_av4_020_legacy_live_tests_require_disposable_opt_in(self):
        dev = (ROOT / "src/dev_api.py").read_text(encoding="utf-8")
        tests = (ROOT / "tests/test_components.py").read_text(encoding="utf-8")
        combined = dev + tests
        self.assertTrue("ANIDOWN_LEGACY_INTEGRATION_OPT_IN" in combined, "missing legacy opt-in guard")
        self.assertTrue("ANIDOWN_LEGACY_SONARR_URL" in combined, "missing disposable endpoint input")
        self.assertFalse("netvault" in combined.casefold(), "fixed live-like endpoint remains")
        self.assertFalse("host='0.0.0.0'" in combined, "all-interface legacy bind remains")
        self.assertFalse('host="0.0.0.0"' in combined, "all-interface legacy bind remains")
        example_paths=[ROOT/".vscode/tasks.json",ROOT/"docs/usage/quickstart.md",
            *(ROOT/"docs/static/examples/connections"/name for name in
                ("ntfy.sh","pushbullet.sh","pushover.sh","telegram.sh")),
            ROOT/"src/script/telegram.sh",ROOT/"src/script/pushbullet.sh",
            ROOT/"src/script/pushover.sh",ROOT/"tests/script/telegram.sh"]
        material="\n".join(path.read_text(encoding="utf-8-sig") for path in example_paths)
        literal=re.compile(r'''(?i)(api[_-]?key|token|secret)["']?\s*[:=]\s*["'][A-Za-z0-9_-]{16,}''')
        self.assertIsNone(literal.search(material),"credential-shaped development literal remains")
        self.assertNotIn("192.168.",material)
        self.assertIn("ANIDOWN_LEGACY_SONARR_API_KEY",material)

    def test_av4_024_credentialed_non_loopback_http_relay_is_rejected(self):
        with self.assertRaises(ValueError):
            TelegramRelay("http://192.0.2.10:8780/notify", "fixture-key")
        self.assertTrue(TelegramRelay("http://127.0.0.1:8780/notify", "fixture-key").configured)
        self.assertTrue(TelegramRelay("https://relay.invalid/notify", "fixture-key").configured)

    def test_av4_002_003_v4_release_artifact_excludes_legacy_runtime(self):
        context = (ROOT / "Dockerfile.v4.dockerignore").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.v4").read_text(encoding="utf-8")
        self.assertNotIn("!src/main.py", context)
        self.assertNotIn("!src/components/", context)
        self.assertNotIn("start.c", dockerfile)
        self.assertNotIn("src/main.py", dockerfile)
        self.assertNotIn("frontend_OLD", dockerfile)


if __name__ == "__main__":
    unittest.main()
