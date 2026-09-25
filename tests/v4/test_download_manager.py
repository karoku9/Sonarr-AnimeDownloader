import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.v4.runtime import create_runtime
from src.v4.download_manager import DownloadQueue
from src.v4.execution import ExecutionCoordinator,FakeDownstreamAdapter


class FakeSonarr:
    def __init__(self,row):
        self.row=dict(row);self.queued=set()
    def wanted_missing(self):return [dict(self.row)]
    def queue_episode_ids(self):return set(self.queued)


class DownloadManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir="work");self.addCleanup(self.temp.cleanup)
        self.app=create_runtime(Path(self.temp.name)/"downloads.sqlite3",
            {"real":"tests/v4/fixtures/production_metadata_v1.json"})
        self.addCleanup(self.app.close)
        self.app.service.scan("real")

    def eligible(self):
        for summary in self.app.service.series()["items"]:
            series=self.app.service.series_detail(summary["series_id"])
            for season in series["seasons"]:
                links=season.get("effective",{}).get("episodes",[])
                if season["automation"] in {"auto","airing"} and links:
                    link=links[0]
                    return series,season,link
        self.fail("No eligible fixture mapping")

    def queue(self,fake,*,max_workers=3,enabled=True):
        coordinator=ExecutionCoordinator(self.app.service.store,FakeDownstreamAdapter(),owner_token="download-test")
        return DownloadQueue(self.app.service,fake,coordinator,max_workers=max_workers,enabled=enabled,auto_sync=False)

    def test_only_sonarr_missing_mapped_episode_is_candidate(self):
        series,season,link=self.eligible()
        fake=FakeSonarr({"id":900001,"seriesId":series["series_id"],"seasonNumber":season["season"],
            "episodeNumber":link["episode"],"monitored":True,"airDate":"2020-01-01"})
        queue=self.queue(fake)
        self.addCleanup(queue.close)
        rows=queue._mapped_missing()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["episode_id"],900001)
        self.assertEqual(rows[0]["source_episode"],link["source_episode"])
        fake.queued.add(900001)
        self.assertEqual(queue._mapped_missing(),[])

    def test_unmonitored_future_and_unmapped_are_not_candidates(self):
        series,season,link=self.eligible()
        fake=FakeSonarr({"id":900002,"seriesId":series["series_id"],"seasonNumber":season["season"],
            "episodeNumber":link["episode"],"monitored":False,"airDate":"2020-01-01"})
        queue=self.queue(fake)
        self.addCleanup(queue.close)
        self.assertEqual(queue._mapped_missing(),[])
        fake.row["monitored"]=True;fake.row["airDate"]="2999-01-01"
        self.assertEqual(queue._mapped_missing(),[])
        fake.row["airDate"]="2020-01-01";fake.row["episodeNumber"]=9999
        self.assertEqual(queue._mapped_missing(),[])

    def test_rejected_mapping_is_never_writer_candidate(self):
        series,season,link=self.eligible()
        with self.app.service.store._connect() as db:
            db.execute("UPDATE v4_items SET mapping_state='rejected' WHERE id=?",(season["mapping_id"],))
        fake=FakeSonarr({"id":900004,"seriesId":series["series_id"],"seasonNumber":season["season"],
            "episodeNumber":link["episode"],"monitored":True,"airDate":"2020-01-01"})
        queue=self.queue(fake)
        self.addCleanup(queue.close)
        self.assertEqual(queue._mapped_missing(),[])

    def test_worker_limit_is_three(self):
        series,season,link=self.eligible()
        fake=FakeSonarr({"id":900003,"seriesId":series["series_id"],"seasonNumber":season["season"],
            "episodeNumber":link["episode"],"monitored":True,"airDate":"2020-01-01"})
        queue=self.queue(fake,max_workers=3,enabled=False)
        self.addCleanup(queue.close)
        self.assertEqual(queue.max_workers,3)

    def test_dispatch_envelope_contains_only_requested_missing_episode(self):
        series,season,link=self.eligible()
        fake=FakeSonarr({"id":900005,"seriesId":series["series_id"],"seasonNumber":season["season"],
            "episodeNumber":link["episode"],"monitored":True,"airDate":"2020-01-01"})
        adapter=FakeDownstreamAdapter();coordinator=ExecutionCoordinator(self.app.service.store,adapter,
            owner_token="download-scope-test")
        queue=DownloadQueue(self.app.service,fake,coordinator,enabled=True,auto_sync=False)
        self.addCleanup(queue.close)
        row=queue._mapped_missing()[0]
        queue._run_operation(queue._operation_key(row),row)
        self.assertEqual(len(adapter.calls),1)
        envelope=adapter.calls[0]
        links=[value for segment in envelope["segments"] for value in segment["episode_links"]]
        self.assertEqual([(value["target_id"],value["sonarr_episode"]) for value in links],
            [(season["target_id"],link["episode"])])


if __name__=="__main__":unittest.main()
