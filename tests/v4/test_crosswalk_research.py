import unittest

from src.v4.crosswalk_research import MALCrosswalkResearcher, CompositeMetadataResearcher


class FakeMALResearcher(MALCrosswalkResearcher):
    def __init__(self):pass
    def _fribb(self):
        return [{"type":"TV","tvdb_id":100,"mal_id":7,
            "season":{"tvdb":1},"episode_offset":{"tvdb":0},"anime-planet_id":"provider-show"}]
    def _mal_meta(self,mid):
        self.assert_mid=mid
        return (["Provider Show"],12,[],2020)
    def _search_aw(self,query):
        return [{"id":"aw7","malId":7,"name":"Provider Show","episodes":"12",
            "language":"jp","year":"2020"}]


class FakePublic:
    def enrich(self,snapshot,series_ids):
        import copy
        value=copy.deepcopy(snapshot)
        for case in value["cases"]:
            if case["raw"]["sonarr_series"]["id"] in set(series_ids):
                current=case["raw"].get("external_metadata") or {"schema_version":1,"sources":[]}
                current["sources"].append({"source":"tvmaze","record_id":"1","independent_of_sonarr":True,
                    "external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":12}]})
                case["raw"]["external_metadata"]=current
        return value,True


class CrosswalkResearchTests(unittest.TestCase):
    def snapshot(self):
        return {"schema_version":1,"provider_catalog":{"complete":True,
            "entries":[{"title":"Provider Show","aliases":[]}]},
            "cases":[{"raw":{"sonarr_series":{"id":1,"title":"Example","tvdbId":100,
                "alternateTitles":[]},"external_metadata":{"schema_version":1,"sources":[]}},
                "derived":{"target":{"season_number":1}}}]}

    def test_mal_crosswalk_is_merged_for_requested_series(self):
        result,changed=FakeMALResearcher().enrich(self.snapshot(),{1})
        self.assertTrue(changed)
        sources=result["cases"][0]["raw"]["external_metadata"]["sources"]
        source=next(x for x in sources if x["source"]=="fribb_mal_crosswalk")
        self.assertEqual(source["record_id"],"100:1")
        self.assertEqual(source["expected_mal_entries"][0]["episode_count"],12)
        self.assertEqual(source["provider_mal_hits"][0]["mal_id"],7)
        self.assertTrue(source["provider_catalog_complete"])

    def test_unrequested_series_is_untouched(self):
        result,changed=FakeMALResearcher().enrich(self.snapshot(),{2})
        self.assertFalse(changed)
        self.assertEqual(result["cases"][0]["raw"]["external_metadata"]["sources"],[])

    def test_composite_applies_public_then_mal(self):
        result,changed=CompositeMetadataResearcher(FakePublic(),FakeMALResearcher()).enrich(self.snapshot(),{1})
        self.assertTrue(changed)
        names={x["source"] for x in result["cases"][0]["raw"]["external_metadata"]["sources"]}
        self.assertEqual(names,{"tvmaze","fribb_mal_crosswalk"})


if __name__=="__main__":
    unittest.main()
