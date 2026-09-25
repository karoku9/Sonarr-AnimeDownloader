import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.v4.metadata_research import PublicMetadataResearcher,_parse_tvdb_ordering
from src.v4.api import create_app

class Response:
    def __init__(self,url,payload):self.url=url;self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def geturl(self):return self.url
    def read(self,limit):return self.payload[:limit]

class MetadataResearchTests(unittest.TestCase):
    def test_tvdb_ordering_parser_keeps_episode_sequence(self):
        raw=b"""<span class="episode-label">S01E01</span><a>Murder Family</a>
        <span class="episode-label">S01E02</span><a>Loo Loo Land</a>
        <span class="episode-label">S01E03</span><a>Queen &amp; Bee</a>"""
        source=_parse_tvdb_ordering(raw,title="Example",tvdb_id=1,order="dvd")
        self.assertEqual(source["seasons"][0]["episode_count"],3)
        self.assertEqual([e["title"] for e in source["seasons"][0]["episodes"]],["Murder Family","Loo Loo Land","Queen & Bee"])

    def test_tvdb_ordering_parser_keeps_special_coordinates(self):
        raw=b'<small class="text-muted episode-label">SPECIAL 0x1</small><a>HIGH NOON AT JULY</a><span class="episode-label">S01E01</span><a>NOMAN\'S LAND</a>'
        source=_parse_tvdb_ordering(raw,title="Example",tvdb_id=1,order="official")
        seasons={row["season_number"]:row for row in source["seasons"]}
        self.assertEqual(seasons[0]["episodes"],[{"episode_number":1,"title":"HIGH NOON AT JULY"}])
        self.assertEqual(seasons[1]["episodes"],[{"episode_number":1,"title":"NOMAN'S LAND"}])

    def test_public_research_cross_checks_skyhook_tvdb_orders_and_tvmaze(self):
        sky={"tvdbId":391246,"title":"Helluva Boss","slug":"helluva-boss","tvMazeId":45397,"imdbId":"tt10691770","tmdbId":289892,
             "alternativeTitles":[],"episodes":[{"seasonNumber":1,"episodeNumber":i,"airDate":"2020-10-31"} for i in range(1,8)]}
        official=''.join(f'<span class="episode-label">S01E{i:02}</span><a>E{i}</a>' for i in range(1,8)).encode()
        dvd=''.join(f'<span class="episode-label">S01E{i:02}</span><a>E{i}</a>' for i in range(1,9)).encode()
        maze={"id":45397,"name":"Helluva Boss","externals":{"thetvdb":391246,"imdb":"tt10691770"},
              "_embedded":{"seasons":[{"number":1,"episodeOrder":7,"premiereDate":"2020-10-31","endDate":"2021-10-31","name":""}],
              "episodes":[{"season":1} for _ in range(7)]}}
        seen=[]
        def opener(req,timeout):
            seen.append(req.full_url)
            if "skyhook.sonarr.tv" in req.full_url:return Response(req.full_url,json.dumps(sky).encode())
            if req.full_url.endswith("/official"):return Response(req.full_url,official)
            if req.full_url.endswith("/dvd"):return Response(req.full_url,dvd)
            if req.full_url.endswith("/alternate"):return Response(req.full_url,official)
            if req.full_url.endswith("/absolute"):return Response(req.full_url,official)
            if "api.tvmaze.com" in req.full_url:return Response(req.full_url,json.dumps(maze).encode())
            raise AssertionError(req.full_url)
        result=PublicMetadataResearcher(opener=opener).research({"tvdbId":391246,"tvMazeId":45397,"title":"Helluva Boss"})
        sources={s["source"]:s for s in result["sources"]}
        self.assertEqual(sources["tvdb_public_official_order"]["seasons"][0]["episode_count"],7)
        self.assertEqual(sources["tvdb_public_dvd_order"]["seasons"][0]["episode_count"],8)
        self.assertTrue(sources["tvmaze"]["independent_of_sonarr"])
        self.assertEqual(len(seen),6)

    def test_application_researches_only_after_a_review_exists(self):
        class FakeResearcher:
            def __init__(self):self.calls=[]
            def enrich(self,snapshot,series_ids):self.calls.append(set(series_ids));return snapshot,False
        researcher=FakeResearcher()
        with TemporaryDirectory(dir=Path("work")) as tmp:
            app=create_app(Path(tmp)/"app.sqlite3",{"fixture":"tests/v4/fixtures/production_metadata_v1.json"},metadata_researcher=researcher)
            app.service.scan("fixture")
        self.assertEqual(len(researcher.calls),1)
        self.assertTrue(researcher.calls[0])

if __name__=="__main__":unittest.main()
