import unittest
from datetime import date

from src.v4.composite_replay import classify_unmatched
from src.v4.models import Alias, Audio, Candidate, SourceMetadata, Target
from src.v4.production_replay import targets
from src.v4.release_resolver import external_exclusion_projection, resolve_series


class Phase16ReviewFixes(unittest.TestCase):
    def test_completed_count_consensus_drops_undated_placeholder(self):
        row={"id":1,"title":"Kingdom","tvdbId":100,"seriesType":"anime","seasons":[{"seasonNumber":6,"statistics":{"episodeCount":13,"totalEpisodeCount":14}}]}
        episodes=[{"seasonNumber":6,"episodeNumber":n,"airDate":f"2025-12-{n:02d}","absoluteEpisodeNumber":n} for n in range(1,14)]
        episodes.append({"seasonNumber":6,"episodeNumber":14,"airDate":None,"absoluteEpisodeNumber":None})
        metadata={"sources":[
            {"source":"tvmaze","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"seasons":[{"season_number":6,"episode_count":13}]},
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"expected_mal_entries":[{"episode_count":13,"episode_offset":None}]},
        ]}
        target=targets(row,episodes,external_metadata=metadata)[0]
        self.assertEqual(target.episode_count,13)
        self.assertEqual(target.provenance("episode_count").source.name,"external_completed_count_consensus")

    def test_external_recap_exclusions_allow_gapped_source_numbering(self):
        source=SourceMetadata("animeworld_detail","x")
        target=Target("Monogatari",season_number=3,episode_count=23,target_id="1:3",external_ids=(("tvdbId","100"),),source=SourceMetadata("sonarr","1"))
        candidate=Candidate("c","Monogatari Series: Second Season","https://www.animeworld.ac/play/x",episode_count=26,source=source)
        case={"raw":{"external_metadata":{"sources":[{
            "source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},
            "season_aliases":[{"season_number":3,"title":"Monogatari Series: Second Season","episode_count":26,
                "identity_verified":True,"excluded_source_episodes":[6,11,16]}]
        }]}}}
        source_numbers=tuple(range(1,27))
        expected=tuple(n for n in source_numbers if n not in {6,11,16})
        proof=external_exclusion_projection(case,target,candidate,source_numbers)
        self.assertIsNotNone(proof)
        self.assertEqual(proof[2],expected)
        self.assertEqual(proof[3],(6,11,16))

    def test_verified_absence_beats_partial_candidate_review(self):
        target=Target("Mushi-Shi",season_number=2,episode_count=20,target_id="1:2")
        candidate=Candidate("c","Mushi-Shi","https://www.animeworld.ac/play/m",episode_count=10,premiere_date=date(2014,4,5))
        case={"raw":{"sonarr_season":{"statistics":{"episodeCount":20,"totalEpisodeCount":20}},"sonarr_episodes":[],
            "source_absence":{"state":"unavailable","verified":True,"method":"complete_catalog"}}}
        self.assertEqual(classify_unmatched(case,target,(candidate,)),"unavailable")

    def test_equivalent_sub_dub_solutions_choose_full_dub(self):
        sonarr=SourceMetadata("sonarr","1"); aw=SourceMetadata("animeworld_detail","x")
        aliases=(Alias("One Punch Man 2",season_number=2,source=sonarr),Alias("One Punch Man 2 (ITA)",season_number=2,source=sonarr))
        target=Target("One-Punch Man",season_number=2,alternate_titles=aliases,episode_count=2,target_id="1:2",source=sonarr)
        sub=Candidate("sub","One Punch Man 2","https://www.animeworld.ac/play/sub",premiere_date=date(2019,4,2),release_year=2019,episode_count=2,episodes_complete=True,audio=Audio.SUB,source=aw)
        dub=Candidate("dub","One Punch Man 2 (ITA)","https://www.animeworld.ac/play/dub",premiere_date=date(2019,4,10),release_year=2019,episode_count=2,episodes_complete=True,audio=Audio.DUB,source=aw)
        rows=[{"seasonNumber":2,"episodeNumber":1,"airDate":"2019-04-10"},{"seasonNumber":2,"episodeNumber":2,"airDate":"2019-04-17"}]
        details={c.url:{"raw":{"available_episode_numbers":[1,2]}} for c in (sub,dub)}
        case={"raw":{"sonarr_series":{"id":1,"title":"One-Punch Man"},"sonarr_episodes":rows,"details":details},"derived":{"target":target.to_dict(),"candidates":[sub.to_dict(),dub.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:2"]
        self.assertEqual(plan.outcome,"matched")
        self.assertIn("ITA",plan.segments[0].release.title)

    def test_verified_provider_series_span_can_project_first_sonarr_season(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Sket Dance",season_number=1,episode_count=51,target_id="1:1",
            external_ids=(("tvdbId","247805"),),source=sonarr)
        candidate=Candidate("c","SKET Dance","https://www.animeworld.ac/play/sket",premiere_date=date(2011,4,7),
            release_year=2011,episode_count=77,episodes_complete=True,audio=Audio.SUB,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":n,"airDate":"2011-04-07" if n==1 else None} for n in range(1,52)]
        metadata={"sources":[
            {"source":"skyhook_tvdb","external_ids":{"tvdb":"247805"},"seasons":[
                {"season_number":1,"episode_count":51},{"season_number":2,"episode_count":26}]},
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"247805"},
                "expected_mal_entries":[{"mal_id":9863,"episode_count":77,"episode_offset":None}],
                "provider_mal_hits":[{"mal_id":9863,"name":"SKET Dance","episodes":"77"}],
                "season_aliases":[{"season_number":1,"title":"SKET Dance","episode_count":77,"identity_verified":True,"mal_id":9863}]}
        ]}
        details={candidate.url:{"raw":{"available_episode_numbers":list(range(1,78))}}}
        case={"raw":{"sonarr_series":{"id":1,"title":"Sket Dance"},"sonarr_episodes":rows,"details":details,
            "external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(len(plan.segments[0].coverage.links),51)
        self.assertEqual(plan.segments[0].coverage.links[-1].source_episode,51)

    def test_provider_series_span_requires_direct_matching_provider_identity(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Sket Dance",season_number=1,episode_count=51,target_id="1:1",external_ids=(("tvdbId","247805"),),source=sonarr)
        candidate=Candidate("c","SKET Dance","https://www.animeworld.ac/play/sket",premiere_date=date(2011,4,7),episode_count=77,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":n,"airDate":"2011-04-07" if n==1 else None} for n in range(1,52)]
        metadata={"sources":[
            {"source":"skyhook_tvdb","external_ids":{"tvdb":"247805"},"seasons":[{"season_number":1,"episode_count":51},{"season_number":2,"episode_count":26}]},
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"247805"},
                "expected_mal_entries":[{"mal_id":9863,"episode_count":77,"episode_offset":None}],
                "provider_mal_hits":[{"mal_id":9863,"name":"SKET Dance","episodes":"76"}],
                "season_aliases":[{"season_number":1,"title":"SKET Dance","episode_count":77,"identity_verified":True,"mal_id":9863}]}
        ]}
        details={candidate.url:{"raw":{"available_episode_numbers":list(range(1,78))}}}
        case={"raw":{"sonarr_series":{"id":1,"title":"Sket Dance"},"sonarr_episodes":rows,"details":details,"external_metadata":metadata},
            "derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:1"].outcome,"needs_review")

    def test_punctuation_only_alias_cannot_override_wrong_season_year(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Kaguya-sama: Love Is War",season_number=2,alternate_titles=(Alias("Kaguya-sama: Love is War?",season_number=2,source=sonarr),),episode_count=2,target_id="1:2",source=sonarr)
        wrong=Candidate("s1","Kaguya-sama wa Kokurasetai","https://www.animeworld.ac/play/s1",alternate_titles=(Alias("Kaguya-sama: Love is War",source=aw),),premiere_date=date(2019,1,12),release_year=2019,episode_count=2,episodes_complete=True,audio=Audio.SUB,source=aw)
        rows=[{"seasonNumber":2,"episodeNumber":1,"airDate":"2020-04-11"},{"seasonNumber":2,"episodeNumber":2,"airDate":"2020-04-18"}]
        case={"raw":{"sonarr_series":{"id":1,"title":"Kaguya-sama: Love Is War"},"sonarr_episodes":rows,"details":{wrong.url:{"raw":{"available_episode_numbers":[1,2]}}}},"derived":{"target":target.to_dict(),"candidates":[wrong.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:2"].outcome,"needs_review")

    def test_verified_prefix_can_ignore_one_trailing_provider_extra(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Durarara!!",season_number=1,alternate_titles=(Alias("Durarara!!",season_number=1,source=sonarr),),
            episode_count=2,target_id="1:1",external_ids=(("tvdbId","100"),),source=sonarr)
        candidate=Candidate("c","Durarara!!","https://www.animeworld.ac/play/drrr",premiere_date=date(2010,1,8),
            release_year=2010,episode_count=2,episodes_complete=True,audio=Audio.SUB,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2010-01-08"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2010-01-15"}]
        metadata={"sources":[{"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},
            "season_aliases":[{"season_number":1,"title":"Durarara!!","episode_count":2,"identity_verified":True,"mal_id":1}]}]}
        case={"raw":{"sonarr_series":{"id":1,"title":"Durarara!!"},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":[1,2,3]}}},"external_metadata":metadata},
            "derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual([x.source_episode for x in plan.segments[0].coverage.links],[1,2])

    def test_verified_external_offsets_compose_multiple_provider_releases(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Black Lagoon",season_number=1,episode_count=4,target_id="1:1",external_ids=(("tvdbId","100"),),source=sonarr)
        first=Candidate("a","Black Lagoon","https://www.animeworld.ac/play/a",premiere_date=date(2006,4,9),episode_count=2,episodes_complete=True,audio=Audio.DUB,source=aw)
        second=Candidate("b","Black Lagoon: The Second Barrage","https://www.animeworld.ac/play/b",premiere_date=date(2006,10,3),episode_count=2,episodes_complete=True,audio=Audio.DUB,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2006-04-09"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2006-04-16"},
            {"seasonNumber":1,"episodeNumber":3,"airDate":"2006-10-03"},{"seasonNumber":1,"episodeNumber":4,"airDate":"2006-10-10"}]
        metadata={"sources":[{"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},
            "provider_catalog_complete":True,
            "expected_mal_entries":[{"mal_id":1,"episode_count":2,"episode_offset":None},{"mal_id":2,"episode_count":2,"episode_offset":2}],
            "provider_mal_hits":[{"mal_id":1,"name":"Black Lagoon","episodes":"2"},{"mal_id":2,"name":"Black Lagoon: The Second Barrage","episodes":"2"}],
            "season_aliases":[{"season_number":1,"title":"Black Lagoon","episode_count":2,"identity_verified":True,"mal_id":1},
                {"season_number":1,"title":"Black Lagoon: The Second Barrage","episode_count":2,"identity_verified":True,"mal_id":2}]}]}
        details={c.url:{"raw":{"available_episode_numbers":[1,2]}} for c in (first,second)}
        case={"raw":{"sonarr_series":{"id":1,"title":"Black Lagoon"},"sonarr_episodes":rows,"details":details,"external_metadata":metadata},
            "derived":{"target":target.to_dict(),"candidates":[first.to_dict(),second.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(len(plan.segments),2)
        self.assertEqual([x.sonarr_episode for x in plan.segments[1].coverage.links],[3,4])

    def test_external_offset_composition_fails_closed_without_provider_hit(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Series",season_number=1,episode_count=4,target_id="1:1",external_ids=(("tvdbId","100"),),source=sonarr)
        first=Candidate("a","Series","https://www.animeworld.ac/play/a",premiere_date=date(2020,1,1),episode_count=2,episodes_complete=True,source=aw)
        second=Candidate("b","Series Part B","https://www.animeworld.ac/play/b",premiere_date=date(2020,2,1),episode_count=2,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2020-01-01"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2020-01-08"},
            {"seasonNumber":1,"episodeNumber":3,"airDate":"2020-02-01"},{"seasonNumber":1,"episodeNumber":4,"airDate":"2020-02-08"}]
        metadata={"sources":[{"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"provider_catalog_complete":True,
            "expected_mal_entries":[{"mal_id":1,"episode_count":2,"episode_offset":None},{"mal_id":2,"episode_count":2,"episode_offset":2}],
            "provider_mal_hits":[{"mal_id":1,"name":"Series","episodes":"2"}],
            "season_aliases":[{"season_number":1,"title":"Series","episode_count":2,"identity_verified":True,"mal_id":1},
                {"season_number":1,"title":"Series Part B","episode_count":2,"identity_verified":True,"mal_id":2}]}]}
        details={c.url:{"raw":{"available_episode_numbers":[1,2]}} for c in (first,second)}
        case={"raw":{"sonarr_series":{"id":1,"title":"Series"},"sonarr_episodes":rows,"details":details,"external_metadata":metadata},
            "derived":{"target":target.to_dict(),"candidates":[first.to_dict(),second.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:1"].outcome,"needs_review")

    def test_verified_provider_mal_identity_can_bridge_title_translation(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Magi: Adventure of Sinbad",season_number=1,episode_count=2,target_id="1:1",
            external_ids=(("tvdbId","100"),),source=sonarr)
        candidate=Candidate("c","Magi: Sinbad no Bouken (TV)","https://www.animeworld.ac/play/magi",
            premiere_date=date(2016,4,16),release_year=2016,episode_count=2,episodes_complete=True,audio=Audio.SUB,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2016-04-16"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2016-04-23"}]
        metadata={"sources":[{"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},
            "expected_mal_entries":[{"mal_id":31741,"episode_count":2,"episode_offset":None,"release_year":2016}],
            "provider_mal_hits":[{"mal_id":31741,"name":"Magi: Sinbad no Bouken (TV)","episodes":"2","year":"2016"}],
            "season_aliases":[{"season_number":1,"title":"Magi: Sinbad no Bouken (TV)","episode_count":2,
                "identity_verified":True,"mal_id":31741}]}]}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":[1,2]}}},"external_metadata":metadata},
            "derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(plan.segments[0].crosswalk.basis,"verified_provider_mal_identity_numbering")

    def test_verified_provider_mal_identity_rejects_episode_or_year_conflict(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Translated Title",season_number=1,episode_count=2,target_id="1:1",
            external_ids=(("tvdbId","100"),),source=sonarr)
        candidate=Candidate("c","Provider Title","https://www.animeworld.ac/play/x",premiere_date=date(2020,1,1),
            release_year=2021,episode_count=2,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2020-01-01"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2020-01-08"}]
        metadata={"sources":[{"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},
            "expected_mal_entries":[{"mal_id":1,"episode_count":2,"episode_offset":None,"release_year":2020}],
            "provider_mal_hits":[{"mal_id":1,"name":"Provider Title","episodes":"2","year":"2020"}],
            "season_aliases":[{"season_number":1,"title":"Provider Title","episode_count":2,"identity_verified":True,"mal_id":1}]}]}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":[1,2]}}},"external_metadata":metadata},
            "derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:1"].outcome,"needs_review")

    def test_article_only_title_difference_can_match_with_exact_date_and_count(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Patlabor: Mobile Police",season_number=1,episode_count=2,target_id="1:1",source=sonarr)
        candidate=Candidate("c","Patlabor: The Mobile Police (ITA)","https://www.animeworld.ac/play/patlabor",
            premiere_date=date(1988,4,25),release_year=1988,episode_count=2,episodes_complete=True,audio=Audio.DUB,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"1988-04-25"},{"seasonNumber":1,"episodeNumber":2,"airDate":"1988-06-25"}]
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":[1,2]}}}},
            "derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(plan.segments[0].crosswalk.basis,"article_normalized_identity_date_numbering")

    def test_article_only_title_difference_still_requires_matching_date(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Example Mobile Police",season_number=1,episode_count=2,target_id="1:1",source=sonarr)
        candidate=Candidate("c","The Example Mobile Police","https://www.animeworld.ac/play/x",premiere_date=date(1990,1,1),
            episode_count=2,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"1988-04-25"},{"seasonNumber":1,"episodeNumber":2,"airDate":"1988-06-25"}]
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":[1,2]}}}},
            "derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:1"].outcome,"needs_review")

    def test_missing_provider_entry_can_mark_first_season_of_verified_span_unavailable(self):
        target=Target("Long Show",season_number=1,episode_count=2,target_id="1:1",external_ids=(("tvdbId","100"),))
        case={"raw":{"sonarr_season":{"statistics":{"episodeCount":2,"totalEpisodeCount":2}},
            "sonarr_episodes":[{"episodeNumber":1,"airDate":"2000-01-01"},{"episodeNumber":2,"airDate":"2000-01-08"}],
            "external_metadata":{"sources":[
                {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},
                 "provider_catalog_complete":True,"missing_mal_ids_verified":[9],
                 "expected_mal_entries":[{"mal_id":9,"episode_count":5}]},
                {"source":"skyhook_tvdb","external_ids":{"tvdb":"100"},"seasons":[
                    {"season_number":1,"episode_count":2},{"season_number":2,"episode_count":3}]}
            ]}}}
        self.assertEqual(classify_unmatched(case,target,()),"unavailable")

    def test_complete_provider_catalog_with_only_shorter_hit_is_unavailable(self):
        target=Target("Complete Show",season_number=1,episode_count=3,target_id="1:1")
        case={"raw":{"sonarr_season":{"statistics":{"episodeCount":3,"totalEpisodeCount":3}},
            "sonarr_episodes":[{"episodeNumber":n,"airDate":f"2000-01-0{n}"} for n in range(1,4)],
            "external_metadata":{"sources":[{"source":"fribb_mal_crosswalk","provider_catalog_complete":True,
                "expected_mal_entries":[{"mal_id":7,"episode_count":3}],
                "provider_mal_hits":[{"mal_id":7,"name":"Complete Show","episodes":"2"}]}]}}}
        self.assertEqual(classify_unmatched(case,target,()),"unavailable")

    def test_supplemental_ova_can_complete_one_sonarr_season_with_boundary_proof(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Master Keaton",season_number=1,episode_count=4,target_id="1:1",source=sonarr)
        base=Candidate("a","Master Keaton","https://www.animeworld.ac/play/a",premiere_date=date(1998,10,6),episode_count=2,episodes_complete=True,audio=Audio.SUB,source=aw)
        ova=Candidate("b","Master Keaton OVA","https://www.animeworld.ac/play/b",premiere_date=date(1999,12,22),episode_count=2,episodes_complete=True,audio=Audio.SUB,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"1998-10-06"},{"seasonNumber":1,"episodeNumber":2,"airDate":"1998-10-13"},
            {"seasonNumber":1,"episodeNumber":3,"airDate":"1999-12-22"},{"seasonNumber":1,"episodeNumber":4,"airDate":"1999-12-22"}]
        details={c.url:{"raw":{"available_episode_numbers":[1,2]}} for c in (base,ova)}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,"details":details},
            "derived":{"target":target.to_dict(),"candidates":[base.to_dict(),ova.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(len(plan.segments),2)
        self.assertTrue(all(s.crosswalk.basis=="supplemental_release_suffix_with_airdate_boundary" for s in plan.segments))

    def test_supplemental_ova_fails_closed_when_boundary_date_disagrees(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Example",season_number=1,episode_count=4,target_id="1:1",source=sonarr)
        base=Candidate("a","Example","https://www.animeworld.ac/play/a",premiere_date=date(2020,1,1),episode_count=2,episodes_complete=True,source=aw)
        ova=Candidate("b","Example OVA","https://www.animeworld.ac/play/b",premiere_date=date(2021,1,1),episode_count=2,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2020-01-01"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2020-01-08"},
            {"seasonNumber":1,"episodeNumber":3,"airDate":"2020-06-01"},{"seasonNumber":1,"episodeNumber":4,"airDate":"2020-06-08"}]
        details={c.url:{"raw":{"available_episode_numbers":[1,2]}} for c in (base,ova)}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,"details":details},
            "derived":{"target":target.to_dict(),"candidates":[base.to_dict(),ova.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:1"].outcome,"needs_review")

    def test_provider_declared_complete_but_detail_gap_is_unavailable(self):
        aw=SourceMetadata("animeworld_detail","x")
        target=Target("Show",season_number=1,episode_count=3,target_id="1:1")
        candidate=Candidate("c","Provider Show","https://www.animeworld.ac/play/show",episode_count=3,source=aw)
        case={"raw":{"sonarr_season":{"statistics":{"episodeCount":3,"totalEpisodeCount":3}},
            "sonarr_episodes":[{"episodeNumber":n,"airDate":f"2000-01-0{n}"} for n in range(1,4)],
            "details":{candidate.url:{"raw":{"available_episode_numbers":[1,3]}}},
            "external_metadata":{"sources":[{"source":"fribb_mal_crosswalk","provider_catalog_complete":True,
                "expected_mal_entries":[{"mal_id":7,"episode_count":3}],
                "provider_mal_hits":[{"mal_id":7,"name":"Provider Show","episodes":"3"}]}]}}}
        self.assertEqual(classify_unmatched(case,target,(candidate,)),"unavailable")

    def test_tvdb_compact_order_maps_one_provider_file_to_multiple_sonarr_segments(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Segmented Show",season_number=2,episode_count=5,target_id="1:2",external_ids=(("tvdbId","100"),),source=sonarr)
        candidate=Candidate("c","Provider Show","https://www.animeworld.ac/play/seg",premiere_date=date(2025,1,1),release_year=2025,episode_count=2,episodes_complete=True,audio=Audio.DUB,source=aw)
        rows=[{"seasonNumber":2,"episodeNumber":n,"airDate":"2025-01-01" if n==1 else None} for n in range(1,6)]
        metadata={"sources":[
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"expected_mal_entries":[{"mal_id":7,"episode_count":2,"episode_offset":None,"release_year":2025}],"provider_mal_hits":[{"mal_id":7,"name":"Provider Show","episodes":"2","year":"2025"}],"season_aliases":[{"season_number":2,"title":"Provider Show","episode_count":2,"identity_verified":True,"mal_id":7}]},
            {"source":"tvdb_public_official_order","ordering":"official","external_ids":{"tvdb":"100"},"seasons":[{"season_number":2,"episode_count":5,"episodes":[{"episode_number":n,"title":x} for n,x in enumerate(["A","B","C","D","E"],1)]}]},
            {"source":"tvdb_public_dvd_order","ordering":"dvd","external_ids":{"tvdb":"100"},"seasons":[{"season_number":2,"episode_count":2,"episodes":[{"episode_number":1,"title":"A / B"},{"episode_number":2,"title":"C / D / E"}]}]},
        ]}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,"details":{candidate.url:{"raw":{"available_episode_numbers":[1,2]}}},"external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:2"]
        self.assertEqual(plan.outcome,"matched");self.assertEqual(plan.segments[0].crosswalk.basis,"verified_tvdb_segmented_ordering_projection")
        self.assertEqual([(x.source_episode,x.sonarr_episode) for x in plan.segments[0].coverage.links],[(1,1),(1,2),(2,3),(2,4),(2,5)])

    def test_tvdb_compact_order_fails_closed_when_titles_do_not_partition_exactly(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("Segmented Show",season_number=1,episode_count=3,target_id="1:1",external_ids=(("tvdbId","100"),),source=sonarr)
        candidate=Candidate("c","Provider Show","https://www.animeworld.ac/play/seg",premiere_date=date(2025,1,1),release_year=2025,episode_count=2,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":n,"airDate":"2025-01-01" if n==1 else None} for n in range(1,4)]
        metadata={"sources":[
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"expected_mal_entries":[{"mal_id":7,"episode_count":2,"episode_offset":None,"release_year":2025}],"provider_mal_hits":[{"mal_id":7,"name":"Provider Show","episodes":"2","year":"2025"}],"season_aliases":[{"season_number":1,"title":"Provider Show","episode_count":2,"identity_verified":True,"mal_id":7}]},
            {"source":"tvdb_public_official_order","ordering":"official","external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":3,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"},{"episode_number":3,"title":"C"}]}]},
            {"source":"tvdb_public_dvd_order","ordering":"dvd","external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":2,"episodes":[{"episode_number":1,"title":"A / WRONG"},{"episode_number":2,"title":"C"}]}]},
        ]}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,"details":{candidate.url:{"raw":{"available_episode_numbers":[1,2]}}},"external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:1"].outcome,"needs_review")

    def test_tvdb_trailing_alternate_episode_projection_requires_verified_provider_identity(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("TRIGUN STAMPEDE",season_number=1,alternate_titles=(Alias("Trigun Stargaze",season_number=1,source=sonarr),),episode_count=2,target_id="1:1",external_ids=(("tvdbId","100"),),source=sonarr)
        good=Candidate("good","Trigun Stampede","https://www.animeworld.ac/play/good",premiere_date=date(2023,1,1),release_year=2023,episode_count=3,episodes_complete=True,source=aw)
        wrong=Candidate("wrong","Trigun Stargaze","https://www.animeworld.ac/play/wrong",premiere_date=date(2026,1,1),release_year=2026,episode_count=3,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":1,"episodeNumber":1,"airDate":"2023-01-01"},{"seasonNumber":1,"episodeNumber":2,"airDate":"2023-01-08"}]
        metadata={"sources":[
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"expected_mal_entries":[{"mal_id":7,"episode_count":3,"episode_offset":None,"release_year":2023}],"provider_mal_hits":[{"mal_id":7,"name":"Trigun Stampede","episodes":"3","year":"2023"}],"season_aliases":[{"season_number":1,"title":"Trigun Stampede","episode_count":3,"identity_verified":True,"mal_id":7}]},
            {"source":"tvdb_public_official_order","ordering":"official","external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":2,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"}]}]},
            {"source":"tvdb_public_alternate_order","ordering":"alternate","external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":3,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"},{"episode_number":3,"title":"C"}]}]},
        ]}
        details={c.url:{"raw":{"available_episode_numbers":[1,2,3]}} for c in (good,wrong)}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,"details":details,"external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[good.to_dict(),wrong.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:1"]
        self.assertEqual(plan.outcome,"matched");self.assertEqual(plan.segments[0].release.candidate_ids,("good",))
        self.assertEqual([x.source_episode for x in plan.segments[0].coverage.links],[1,2])

    def test_tvdb_relocated_regular_tail_maps_to_complete_specials_target(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("TRIGUN STAMPEDE",season_number=0,episode_count=1,target_id="1:0",external_ids=(("tvdbId","100"),),source=sonarr)
        candidate=Candidate("c","Trigun Stampede","https://www.animeworld.ac/play/trigun",premiere_date=date(2023,1,1),release_year=2023,episode_count=3,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":0,"episodeNumber":1,"airDate":"2023-03-25"}]
        metadata={"sources":[
            {"source":"fribb_mal_crosswalk","independent_of_sonarr":True,"external_ids":{"tvdb":"100"},"expected_mal_entries":[{"mal_id":7,"episode_count":3,"episode_offset":None,"release_year":2023}],"provider_mal_hits":[{"mal_id":7,"name":"Trigun Stampede","episodes":"3","year":"2023"}],"season_aliases":[{"season_number":1,"title":"Trigun Stampede","episode_count":3,"identity_verified":True,"mal_id":7}]},
            {"source":"tvdb_public_official_order","ordering":"official","external_ids":{"tvdb":"100"},"seasons":[{"season_number":0,"episode_count":1,"episodes":[{"episode_number":1,"title":"C"}]},{"season_number":1,"episode_count":2,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"}]}]},
            {"source":"tvdb_public_alternate_order","ordering":"alternate","external_ids":{"tvdb":"100"},"seasons":[{"season_number":1,"episode_count":3,"episodes":[{"episode_number":1,"title":"A"},{"episode_number":2,"title":"B"},{"episode_number":3,"title":"C"}]}]},
        ]}
        case={"raw":{"sonarr_series":{"id":1,"title":target.canonical_title},"sonarr_episodes":rows,"details":{candidate.url:{"raw":{"available_episode_numbers":[1,2,3]}}},"external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:0"]
        self.assertEqual(plan.outcome,"matched");self.assertEqual(plan.segments[0].crosswalk.basis,"verified_tvdb_relocated_special_tail")
        self.assertEqual([(x.source_episode,x.sonarr_season,x.sonarr_episode) for x in plan.segments[0].coverage.links],[(3,0,1)])

    def test_independent_named_season_accepts_exact_token_reordering(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("To LOVE-Ru",season_number=2,episode_count=12,target_id="1:2",
            external_ids=(("tvdbId","81831"),),source=sonarr)
        candidate=Candidate("c","To Love-Ru Motto","https://www.animeworld.ac/play/motto",
            premiere_date=date(2010,10,6),release_year=2010,episode_count=12,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":2,"episodeNumber":n,"airDate":"2010-10-06" if n==1 else None} for n in range(1,13)]
        metadata={"sources":[{"source":"tvmaze","independent_of_sonarr":True,
            "external_ids":{"tvdb":"81831"},"seasons":[{"season_number":2,"name":"Motto To LOVE-Ru",
            "episode_count":12,"premiere_date":"2010-10-05"}]}]}
        case={"raw":{"sonarr_series":{"id":1,"title":"To LOVE-Ru"},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":list(range(1,13))}}},
            "external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        plan=resolve_series([case])["plans"]["1:2"]
        self.assertEqual(plan.outcome,"matched")
        self.assertEqual(plan.segments[0].crosswalk.basis,"independent_named_season_identity")

    def test_independent_named_season_fails_closed_when_premiere_disagrees(self):
        sonarr=SourceMetadata("sonarr","1");aw=SourceMetadata("animeworld_detail","x")
        target=Target("To LOVE-Ru",season_number=2,episode_count=12,target_id="1:2",
            external_ids=(("tvdbId","81831"),),source=sonarr)
        candidate=Candidate("c","To Love-Ru Motto","https://www.animeworld.ac/play/motto",
            premiere_date=date(2011,10,6),release_year=2011,episode_count=12,episodes_complete=True,source=aw)
        rows=[{"seasonNumber":2,"episodeNumber":n,"airDate":"2010-10-06" if n==1 else None} for n in range(1,13)]
        metadata={"sources":[{"source":"tvmaze","independent_of_sonarr":True,
            "external_ids":{"tvdb":"81831"},"seasons":[{"season_number":2,"name":"Motto To LOVE-Ru",
            "episode_count":12,"premiere_date":"2010-10-05"}]}]}
        case={"raw":{"sonarr_series":{"id":1,"title":"To LOVE-Ru"},"sonarr_episodes":rows,
            "details":{candidate.url:{"raw":{"available_episode_numbers":list(range(1,13))}}},
            "external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[candidate.to_dict()]}}
        self.assertEqual(resolve_series([case])["plans"]["1:2"].outcome,"needs_review")
