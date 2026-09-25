import importlib
import json
import pathlib
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor


SEARCH_V3_PATH = pathlib.Path(__file__).parents[1].joinpath("src", "components", "backend", "search_v3")
package = types.ModuleType("search_v3_testpkg")
package.__path__ = [str(SEARCH_V3_PATH)]
sys.modules.setdefault("search_v3_testpkg", package)

variants = importlib.import_module("search_v3_testpkg.variants")
language = importlib.import_module("search_v3_testpkg.language")
service_module = importlib.import_module("search_v3_testpkg.service")

TitleVariantBuilder = variants.TitleVariantBuilder
normalize_title = variants.normalize_title
SearchV3Service = service_module.SearchV3Service


class FakeTable:
	def __init__(self, entries):
		self.entries = entries

	def getData(self):
		return self.entries


class TestSearchV3(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = pathlib.Path(self.tmp.name)
		self.table = [
			{"title": "Kill la Kill", "absolute": False, "seasons": {"1": ["https://www.animeworld.ac/play/kill-la-kill.non7B/ldVoQ-"]}},
			{"title": "Heavenly Delusion", "absolute": False, "seasons": {"1": ["https://www.animeworld.ac/play/tengoku-daimakyou.ssLA5/Fi-rR6"]}},
			{"title": "One-Punch Man", "absolute": False, "seasons": {"1": ["https://www.animeworld.ac/play/one-punch-man.abc/episode"]}},
			{"title": "High School D\u00d7D", "absolute": False, "seasons": {"1": ["https://www.animeworld.ac/play/high-school-dxd.abc/episode"]}},
		]

	def tearDown(self):
		self.tmp.cleanup()

	def service(self):
		return SearchV3Service(self.root, "https://www.animeworld.ac", FakeTable(self.table))

	def test_title_variants_include_readable_and_normalized_searches(self):
		result = TitleVariantBuilder().build({
			"title": "Gals Can't Be Kind to Otaku!?",
			"alternateTitles": [{"title": "Gals Cannot Be Kind to Otaku"}],
		})
		second = TitleVariantBuilder().build("Agents of the Four Seasons: Dance of Spring")

		self.assertIn("Gals Can't Be Kind to Otaku!?", result["raw_variants"])
		self.assertIn("Gals Cant Be Kind to Otaku", result["raw_variants"])
		self.assertIn("Gals Cannot Be Kind to Otaku", result["raw_variants"])
		self.assertIn("gals cant be kind to otaku", result["normalized_variants"])
		self.assertIn("Agents of the Four Seasons", second["raw_variants"])

	def test_title_variants_only_use_aliases_for_the_requested_season(self):
		series = {
			"title": "Black Lagoon",
			"alternateTitles": [
				{"title": "Burakku Ragun", "sceneSeasonNumber": -1},
				{"title": "Black Lagoon: The Second Barrage", "sceneSeasonNumber": 2},
			],
		}

		season_one = TitleVariantBuilder().build(series, season=1)
		season_two = TitleVariantBuilder().build(series, season="2")

		self.assertIn("Burakku Ragun", season_one["sonarr_aliases"])
		self.assertNotIn("Black Lagoon: The Second Barrage", season_one["sonarr_aliases"])
		self.assertIn("Black Lagoon: The Second Barrage", season_two["season_aliases"])

	def test_season_specific_alias_wins_an_exact_title_tie(self):
		service = self.service()
		index = service.index_store.load()
		index["entries"].extend([
			service.catalog_builder.entry("Black Lagoon", "https://example.test/black-lagoon", "animeworld_catalog"),
			service.catalog_builder.entry("Black Lagoon: The Second Barrage", "https://example.test/second-barrage", "animeworld_catalog"),
		])
		service.index_store.save_atomic(index)
		series = {
			"title": "Black Lagoon",
			"alternateTitles": [{"title": "Black Lagoon: The Second Barrage", "sceneSeasonNumber": 2}],
		}

		result = service.search(series, season=2, debug=True, auto_refresh=False)

		self.assertEqual("matched", result["status"])
		self.assertEqual("https://example.test/second-barrage", result["selected_candidate"]["url"])
		self.assertTrue(result["selected_candidate"]["season_match"])

	def test_table_seeded_index_preserves_table_and_matches_known_titles(self):
		before = json.dumps(self.table, sort_keys=True)
		service = self.service()

		for title in ("Kill la Kill", "Heavenly Delusion", "One-Punch Man", "High School DxD"):
			with self.subTest(title=title):
				result = service.search(title, debug=True, auto_refresh=False)
				matches = [candidate for candidate in result["top_candidates"] if candidate["score"] >= 0.50]
				self.assertTrue(matches)
				self.assertEqual("table", matches[0]["source"])

		self.assertEqual(before, json.dumps(self.table, sort_keys=True))

	def test_catalog_candidates_are_found_for_acceptance_titles_when_indexed(self):
		service = self.service()
		titles = [
			"The Ramparts of Ice",
			"Go For It, Nakamura-kun!!",
			"Gals Can't Be Kind to Otaku!?",
			"Witch Hat Atelier",
			"NEEDY GIRL OVERDOSE",
		]
		index = service.index_store.load()
		index["generated_at"] = "2026-05-25T08:00:00+00:00"
		index["entries"].extend([{
			"title": title,
			"normalized_title": normalize_title(title),
			"url": f"https://www.animeworld.ac/play/{normalize_title(title).replace(' ', '-')}.v3",
			"slug": normalize_title(title).replace(" ", "-"),
			"normalized_slug": normalize_title(title),
			"audio": "SUB",
			"year": 2026,
			"source": "animeworld_catalog",
			"aliases": [],
		} for title in titles])
		service.index_store.save_atomic(index)

		for title in titles:
			with self.subTest(title=title):
				result = service.search(title, debug=True, auto_refresh=False)
				self.assertGreaterEqual(result["top_candidates"][0]["score"], 0.90)

	def test_language_preference_selects_dub_after_title_matching(self):
		service = self.service()
		index = service.index_store.load()
		index["entries"].extend([
			{"title": "Witch Hat Atelier", "normalized_title": "witch hat atelier", "url": "https://example.test/sub", "slug": "witch", "normalized_slug": "witch", "audio": "SUB", "source": "animeworld_catalog", "aliases": []},
			{"title": "Witch Hat Atelier (ITA)", "normalized_title": "witch hat atelier", "url": "https://example.test/dub", "slug": "witch-ita", "normalized_slug": "witch ita", "audio": "DUB", "source": "animeworld_catalog", "aliases": []},
		])
		service.index_store.save_atomic(index)

		result = service.search("Witch Hat Atelier", language_preference="DUB_FIRST", debug=True, auto_refresh=False)

		self.assertEqual("matched", result["status"])
		self.assertEqual("DUB", result["selected_candidate"]["audio"])

	def test_language_preference_does_not_outrank_a_better_title_match(self):
		service = self.service()
		index = service.index_store.load()
		index["entries"].extend([
			service.catalog_builder.entry("Radiant", "https://example.test/radiant", "animeworld_catalog"),
			service.catalog_builder.entry("Nadia - Il mistero della pietra azzurra (ITA)", "https://example.test/nadia", "animeworld_catalog"),
		])
		service.index_store.save_atomic(index)

		result = service.search("Nadia: The Secret of Blue Water", language_preference="SUB_FIRST", debug=True, auto_refresh=False)

		self.assertEqual("Nadia - Il mistero della pietra azzurra (ITA)", result["top_candidates"][0]["title"])
		self.assertGreater(result["top_candidates"][0]["score"], result["top_candidates"][1]["score"])

	def test_catalog_collapses_duplicate_public_links_by_slug_and_audio(self):
		service = self.service()
		entries = [
			service.catalog_builder.entry("Witch Hat Atelier", "https://www.animeworld.ac/play/witch-hat-atelier.code", "animeworld_catalog"),
			service.catalog_builder.entry("Witch Hat Atelier", "https://www.animeworld.ac/play/witch-hat-atelier.code/", "animeworld_catalog"),
			service.catalog_builder.entry("Witch Hat Atelier", "https://www.animeworld.ac/play/witch-hat-atelier.code/episode", "animeworld_catalog"),
		]

		merged = service.catalog_builder.merge_entries(entries)

		self.assertEqual(1, len(merged))
		self.assertEqual("SUB", merged[0]["audio"])
		self.assertEqual("https://www.animeworld.ac/play/witch-hat-atelier.code", merged[0]["url"])

	def test_table_metadata_does_not_overwrite_catalog_title_and_is_season_scoped(self):
		service = self.service()
		remote = service.catalog_builder.entry("The Helpful Fox Senko-san", "https://example.test/senko", "animeworld_catalog")
		table = service.catalog_builder.entry("Ranma 1/2 (2024)", "https://example.test/senko", "table")
		table["table_titles"] = ["Ranma 1/2 (2024)"]
		table["table_seasons"] = ["1"]

		merged = service.catalog_builder.merge_entries([remote], [table])

		self.assertEqual("The Helpful Fox Senko-san", merged[0]["title"])
		self.assertEqual("animeworld_catalog", merged[0]["source"])
		self.assertIn("Ranma 1/2 (2024)", merged[0]["aliases"])
		self.assertEqual(["1"], merged[0]["table_seasons"])

		service.index_store.save_atomic({"version": 3, "generated_at": "2026-08-17T00:00:00+00:00", "source_base_url": "https://www.animeworld.ac", "entries": merged})
		result = service.search("Ranma 1/2 (2024)", season=2, debug=True, auto_refresh=False)

		self.assertEqual("no_result", result["status"])
		self.assertEqual(1, len(result["excluded_candidates"]))
		self.assertIn("not season 2", result["excluded_candidates"][0]["excluded_reason"])

	def test_unrelated_debug_results_report_no_result_even_with_weak_diagnostics(self):
		result = self.service().search("Completely Unknown Show", debug=True, auto_refresh=False)

		self.assertEqual("no_result", result["status"])
		self.assertTrue(result["top_candidates"])

	def test_concurrent_searches_write_diagnostics_and_cache_safely(self):
		service = self.service()
		titles = ["Kill la Kill", "Heavenly Delusion"] * 8

		with ThreadPoolExecutor(max_workers=8) as executor:
			results = list(executor.map(lambda title: service.search(title, debug=True, auto_refresh=False), titles))

		self.assertTrue(all(result["status"] == "matched" for result in results))
		self.assertTrue(service.alias_provider.cache_path.exists())
		self.assertTrue(service.diagnostics.path.exists())


if __name__ == "__main__":
	unittest.main()
