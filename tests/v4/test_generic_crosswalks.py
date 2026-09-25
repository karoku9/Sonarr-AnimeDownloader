import unittest

from src.v4.production_replay import snapshot,retrieve
from src.v4.release_resolver import resolve_series


def detail(title,count,premiere,numbers=None):
    return {"status":"ok","raw":{"title":title,"fields":[
        {"label":"Episodi:","value":str(count)},
        {"label":"Data di Uscita:","value":premiere},
        {"label":"Stato:","value":"Finito"},
        {"label":"Audio:","value":"Giapponese"},
    ],"available_episode_numbers":numbers if numbers is not None else list(range(1,count+1)),"structured":[]}}


def entry(title,url):
    return {"title":title,"url":url,"aliases":[],"audio":"SUB","source":"animeworld_catalog"}


def row(seasons):
    return {"id":900,"title":"Example Saga","seriesType":"anime","ended":True,"year":2020,
        "seasons":[{"seasonNumber":number,"statistics":{"episodeCount":count,"totalEpisodeCount":count}}
            for number,count in seasons]}


def episode(season,number,absolute,air,has_file=False):
    return {"seasonNumber":season,"episodeNumber":number,"absoluteEpisodeNumber":absolute,
        "airDate":air,"hasFile":has_file,"monitored":True}


class GenericCrosswalkTests(unittest.TestCase):
    def test_retrieve_keeps_exact_series_prefix_arc(self):
        catalog=[entry("Example Saga: Final Arc","https://www.animeworld.ac/play/final"),
                 entry("Other Show","https://www.animeworld.ac/play/other")]
        found=retrieve({"title":"Example Saga","alternateTitles":[]},catalog,[])
        self.assertEqual([item["title"] for item in found],["Example Saga: Final Arc"])

    def test_absolute_sequence_splits_one_release_across_sonarr_seasons(self):
        series=row([(1,2),(2,2),(3,1)])
        episodes={"900":[episode(1,1,1,"2020-01-01"),episode(1,2,2,"2020-01-08"),
            episode(2,1,3,"2020-02-01"),episode(2,2,4,"2020-02-08"),episode(3,1,5,"2020-03-01")]}
        source=entry("Example Saga","https://www.animeworld.ac/play/example")
        snap=snapshot([series],episodes,[source],[],{source["url"]:detail(source["title"],5,"2020-01-01")},
            pools={"900":[source]})
        resolved=resolve_series(snap["cases"])
        self.assertTrue(all(resolved["plans"][f"900:{season}"].outcome=="matched" for season in (1,2,3)))

    def test_series_prefix_plus_date_and_count_maps_named_arc(self):
        series=row([(1,3)])
        episodes={"900":[episode(1,1,1,"2024-01-01"),episode(1,2,2,"2024-01-08"),episode(1,3,3,"2024-01-15")]}
        source=entry("Example Saga: Final Arc","https://www.animeworld.ac/play/final")
        snap=snapshot([series],episodes,[source],[],{source["url"]:detail(source["title"],3,"2024-01-01")},pools={"900":[source]})
        plan=resolve_series(snap["cases"])["plans"]["900:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(plan.segments[0].crosswalk.basis,"series_prefix_date_count_numbering")

    def test_plain_numbered_continuation_composes_one_sonarr_season(self):
        series=row([(1,5)])
        episodes={"900":[episode(1,1,1,"2024-01-01"),episode(1,2,2,"2024-01-08"),
            episode(1,3,3,"2024-07-01"),episode(1,4,4,"2024-07-08"),episode(1,5,5,"2024-07-15")]}
        first=entry("Example Saga: Silver Arc","https://www.animeworld.ac/play/silver")
        second=entry("Example Saga: Silver Arc 2","https://www.animeworld.ac/play/silver-2")
        details={first["url"]:detail(first["title"],2,"2024-01-01"),second["url"]:detail(second["title"],3,"2024-07-01")}
        snap=snapshot([series],episodes,[first,second],[],details,pools={"900":[first,second]})
        plan=resolve_series(snap["cases"])["plans"]["900:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual([s.coverage.episode_count for s in plan.segments],[2,3])

    def test_provider_gap_is_allowed_only_when_sonarr_already_has_file(self):
        series=row([(1,2),(2,2),(3,1)])
        episodes={"900":[episode(1,1,1,"2020-01-01"),episode(1,2,2,"2020-01-08",has_file=True),
            episode(2,1,3,"2020-02-01"),episode(2,2,4,"2020-02-08"),episode(3,1,5,"2020-03-01")]}
        source=entry("Example Saga","https://www.animeworld.ac/play/example-gap")
        snap=snapshot([series],episodes,[source],[],{source["url"]:detail(source["title"],5,"2020-01-01",[1,3,4,5])},pools={"900":[source]})
        plan=resolve_series(snap["cases"])["plans"]["900:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(plan.segments[0].crosswalk.basis,"sonarr_absolute_sequence_existing_file_gap")

    def test_provider_gap_stays_review_when_missing_in_sonarr(self):
        series=row([(1,2),(2,2),(3,1)])
        episodes={"900":[episode(1,1,1,"2020-01-01"),episode(1,2,2,"2020-01-08",has_file=False),
            episode(2,1,3,"2020-02-01"),episode(2,2,4,"2020-02-08"),episode(3,1,5,"2020-03-01")]}
        source=entry("Example Saga","https://www.animeworld.ac/play/example-gap-missing")
        snap=snapshot([series],episodes,[source],[],{source["url"]:detail(source["title"],5,"2020-01-01",[1,3,4,5])},pools={"900":[source]})
        plan=resolve_series(snap["cases"])["plans"]["900:1"]
        self.assertEqual(plan.outcome,"needs_review")
        self.assertEqual(plan.reason_codes,("provider_episode_gap",))


if __name__=="__main__":
    unittest.main()
