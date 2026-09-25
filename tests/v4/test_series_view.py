import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.v4.runtime import create_runtime
from src.v4.execution import ExecutionCoordinator,FakeDownstreamAdapter
from src.v4.application_store import Conflict
from src.v4.notifications import NotificationWorker


class FakeManualValidator:
    def validate(self,urls,expected_audio,episode_map):
        return [{"url":url,"canonical_url":url,"fingerprint":"f"*64,"audio":expected_audio,
            "available_episode_numbers":list(range(1,2001)),"validated_at":"2026-09-22T12:00:00+00:00"}
            for url in urls]


class SeriesViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir="work");self.addCleanup(self.temp.cleanup)
        self.app=create_runtime(Path(self.temp.name)/"series.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"},
            manual_source_validator=FakeManualValidator())
        self.addCleanup(self.app.close)
        self.app.service.scan("real")

    def test_series_first_groups_seasons_and_exposes_episode_mapping(self):
        page=self.app.service.series()
        self.assertGreater(page["total"],0)
        black=next(row for row in page["items"] if row["title"]=="Black Lagoon")
        series_id=black["series_id"]
        self.assertGreater(series_id,0)
        self.assertGreaterEqual(black["season_count"],1)
        detail=self.app.service.series_detail(series_id)
        self.assertEqual(detail["title"],"Black Lagoon")
        season=detail["seasons"][0]
        self.assertIn(season["automation"],{"auto","review","waiting","airing"})
        self.assertTrue(season["episodes"])
        self.assertEqual(sorted(e["episode"] for e in season["episodes"]),
            list(range(1,season["episode_count"]+1)))

    def test_manual_override_is_per_season_and_persistent(self):
        series_id=next(row["series_id"] for row in self.app.service.series()["items"] if row["title"]=="Black Lagoon")
        detail=self.app.service.series_detail(series_id)
        season=detail["seasons"][0]["season"]
        result=self.app.service.update_season_override(series_id,season,{
            "source_url":"https://www.animeworld.ac/play/manual-test.example",
            "ignore_errors":True,"note":"Manual operator override"})
        self.assertTrue(result["ignore_errors"])
        refreshed=self.app.service.series_detail(series_id)
        target=next(s for s in refreshed["seasons"] if s["season"]==season)
        self.assertEqual(target["automation"],"manual")
        self.assertEqual(target["override"]["source_url"],"https://www.animeworld.ac/play/manual-test.example")
        cleared=self.app.service.clear_season_override(series_id,season)
        self.assertTrue(cleared["cleared"])
        self.assertEqual(self.app.service.season_override(series_id,season)["source_url"],None)

    def test_manual_override_rejects_non_animeworld_url(self):
        series_id=next(row["series_id"] for row in self.app.service.series()["items"] if row["title"]=="Black Lagoon")
        detail=self.app.service.series_detail(series_id);season=detail["seasons"][0]["season"]
        with self.assertRaises(ValueError):
            self.app.service.update_season_override(series_id,season,{
                "source_url":"https://example.com/nope","ignore_errors":False,"note":""})

    def test_notification_preferences_are_persisted(self):
        updated=self.app.service.store.update_settings({
            "notify_review":False,"notify_error":True,"notify_anomaly":True,"notify_new_season":True})
        self.assertFalse(updated["notify_review"])
        self.assertTrue(updated["notify_new_season"])
        self.assertEqual(updated["telegram_status"],"not_configured")

    def test_rich_override_keeps_automatic_and_effective_results_separate(self):
        series_id=next(row["series_id"] for row in self.app.service.series()["items"] if row["title"]=="Black Lagoon")
        season=self.app.service.series_detail(series_id)["seasons"][0]["season"]
        episode_count=self.app.service.series_detail(series_id)["seasons"][0]["episode_count"]
        urls=["https://www.animeworld.ac/play/manual-p1.example","https://www.animeworld.ac/play/manual-p2.example"]
        episode_map=[{"sonarr_episode":number,"source_index":0 if number<=12 else 1,
            "source_episode":number if number<=12 else number-12} for number in range(1,episode_count+1)]
        saved=self.app.service.update_season_override(series_id,season,{
            "source_urls":urls,"audio":"DUB","ignore_errors":False,"manual_approved":True,"excluded":False,
            "episode_map":episode_map,"note":"operator mapping"})
        self.assertEqual(saved["source_urls"],urls)
        detail=self.app.service.series_detail(series_id)
        current=next(s for s in detail["seasons"] if s["season"]==season)
        self.assertEqual(current["automation"],"manual")
        self.assertEqual(current["effective"]["audio"],"DUB")
        self.assertEqual(current["effective"]["mapping_mode"],"forced")
        self.assertEqual([e["episode"] for e in current["effective"]["episodes"]],list(range(1,episode_count+1)))
        self.assertGreater(len(current["automatic"]["episodes"]),2)
        self.assertTrue(current["override"]["manual_approved"])

    def test_empty_override_returns_to_auto(self):
        row=next(row for row in self.app.service.series()["items"] if row["title"]=="Black Lagoon")
        season=row["seasons"][0]["season"]
        self.app.service.update_season_override(row["series_id"],season,{"note":"temporary"})
        cleared=self.app.service.update_season_override(row["series_id"],season,{})
        self.assertIsNone(cleared["updated_at"])
        current=next(s for s in self.app.service.series_detail(row["series_id"])["seasons"] if s["season"]==season)
        self.assertNotEqual(current["automation"],"manual")

    def test_notification_bus_persists_transport_neutral_events(self):
        store=self.app.service.store
        with store._connect() as db:
            store._event(db,None,"provider_failed",{"provider":"fixture"},"test")
        event=store.notification_events()[-1]
        self.assertEqual(event["topic"],"provider_error")
        self.assertEqual(event["payload"]["action"],"provider_failed")
        settings=store.settings()
        self.assertEqual(settings["notification_bus"],"ready")
        self.assertEqual({b["id"] for b in settings["notification_backends"]},{"outbox","telegram"})

    def test_telegram_worker_only_delivers_new_error_topics(self):
        store=self.app.service.store
        class Relay:
            configured=True
            def __init__(self):self.sent=[]
            def send(self,event):self.sent.append(event)
        relay=Relay();worker=NotificationWorker(store,relay,poll_seconds=.01)
        with store._connect() as db:store._event(db,None,"scan_failed",{"code":"historical"},"test")
        self.assertTrue(worker.start())
        try:
            with store._connect() as db:store._event(db,None,"provider_failed",{"code":"fresh"},"test")
            deadline=time.monotonic()+1
            while not relay.sent and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(len(relay.sent),1)
            self.assertEqual(relay.sent[0]["payload"]["code"],"fresh")
            with store._connect() as db:store._event(db,None,"review_created",{"code":"review"},"test")
            deadline=time.monotonic()+1
            while len(relay.sent)<2 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(len(relay.sent),2)
            self.assertEqual(relay.sent[1]["topic"],"review")
        finally:worker.close()

    def test_manual_override_events_are_transport_neutral_and_off_by_default(self):
        row=next(row for row in self.app.service.series()["items"] if row["title"]=="Black Lagoon")
        season=row["seasons"][0]["season"]
        self.app.service.update_season_override(row["series_id"],season,{"note":"operator audit"})
        event=self.app.service.store.notification_events()[-1]
        self.assertEqual((event["topic"],event["payload"]["action"]),("manual_override","manual_override_applied"))
        settings=self.app.service.store.settings()
        self.assertFalse(settings["notify_manual_override"])
        self.assertFalse(settings["notification_preferences"]["events"]["manual_override_applied"])
        self.app.service.clear_season_override(row["series_id"],season)
        event=self.app.service.store.notification_events()[-1]
        self.assertEqual((event["topic"],event["payload"]["action"]),("manual_override","manual_override_cleared"))

    def test_series_dto_exposes_operational_coverage_and_attention(self):
        row=next(row for row in self.app.service.series()["items"] if row["title"]=="Black Lagoon")
        self.assertIn("mapped_episode_count",row)
        self.assertIn("coverage_percent",row)
        self.assertIn("attention_count",row)
        self.assertIn("source_names",row)
        season=row["seasons"][0]
        for key in ("auto_ready","can_execute","mapped_episode_count","coverage_percent","mapping_bases","attention"):
            self.assertIn(key,season)

    def test_deterministic_proposal_can_execute_without_manual_approval(self):
        item=next(i for i in self.app.service.store.list_items() if i["mapping_state"]=="proposed" and not i["original"]["reason_codes"])
        adapter=FakeDownstreamAdapter()
        coordinator=ExecutionCoordinator(self.app.service.store,adapter,owner_token="auto-test")
        result=coordinator.execute_automatic(item["id"],expected_revision=item["revision"])
        self.assertEqual(result["status"],"completed")
        self.assertEqual(len(adapter.calls),1)

    def test_audio_preference_does_not_turn_automatic_season_manual(self):
        item=next(i for i in self.app.service.store.list_items() if i["mapping_state"]=="proposed" and len(i["original"]["targets"])==1)
        target=item["original"]["targets"][0];series_id,season=map(int,target["target_id"].split(":"))
        before=next(s for s in self.app.service.series_detail(series_id)["seasons"] if s["season"]==season)["automation"]
        saved=self.app.service.update_season_override(series_id,season,{"audio":"DUB"})
        self.assertEqual(saved["audio"],"DUB")
        current=next(s for s in self.app.service.series_detail(series_id)["seasons"] if s["season"]==season)
        self.assertEqual(current["automation"],before)
        self.assertEqual(current["audio_preference"],"DUB")
        coordinator=ExecutionCoordinator(self.app.service.store,FakeDownstreamAdapter(),owner_token="audio-pref-test")
        reserved=coordinator.reserve_automatic(item["id"],expected_revision=item["revision"])
        self.assertEqual(reserved["target_ids"],[target["target_id"]])
        self.assertEqual(reserved["execution_policy"]["audio_preference"],"DUB")

    def test_manual_override_blocks_automatic_execution(self):
        item=next(i for i in self.app.service.store.list_items() if i["mapping_state"]=="proposed" and len(i["original"]["targets"])==1)
        target=item["original"]["targets"][0]
        series_id,season=map(int,target["target_id"].split(":"))
        self.app.service.update_season_override(series_id,season,{
            "source_url":"https://www.animeworld.ac/play/manual-test.example","ignore_errors":True,"note":"force"})
        coordinator=ExecutionCoordinator(self.app.service.store,FakeDownstreamAdapter(),owner_token="auto-test")
        with self.assertRaises(Conflict):
            coordinator.reserve_automatic(item["id"],expected_revision=item["revision"])


if __name__=="__main__":unittest.main()
