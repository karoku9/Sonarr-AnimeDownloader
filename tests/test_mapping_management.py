import json
import pathlib
import tempfile
import unittest
import importlib.util
import sys
import types
from copy import deepcopy

MAPPING_PATH = pathlib.Path(__file__).parents[1].joinpath("src", "components", "backend", "mapping.py")
MATCHING_PATH = pathlib.Path(__file__).parents[1].joinpath("src", "components", "backend", "matching.py")
INDEX_PATH = pathlib.Path(__file__).parents[1].joinpath("src", "components", "backend", "animeworld_index.py")
spec = importlib.util.spec_from_file_location("mapping", MAPPING_PATH)
mapping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapping)
sys.modules.setdefault("src", types.ModuleType("src"))
sys.modules.setdefault("src.components", types.ModuleType("src.components"))
sys.modules.setdefault("src.components.backend", types.ModuleType("src.components.backend"))
sys.modules["src.components.backend.mapping"] = mapping
matching_spec = importlib.util.spec_from_file_location("src.components.backend.matching", MATCHING_PATH)
matching = importlib.util.module_from_spec(matching_spec)
matching_spec.loader.exec_module(matching)
index_spec = importlib.util.spec_from_file_location("src.components.backend.animeworld_index", INDEX_PATH)
animeworld_index = importlib.util.module_from_spec(index_spec)
index_spec.loader.exec_module(animeworld_index)

MappingPreferences = mapping.MappingPreferences
create_backup = mapping.create_backup
flatten_table = mapping.flatten_table
rows_from_csv = mapping.rows_from_csv
rows_from_json = mapping.rows_from_json
rows_to_csv = mapping.rows_to_csv
table_from_rows = mapping.table_from_rows
validate_rows = mapping.validate_rows
normalize_url = mapping.normalize_url
preview_flatten_order = mapping.preview_flatten_order
reconcile_import_rows = mapping.reconcile_import_rows
CSV_COLUMNS = mapping.CSV_COLUMNS
sort_part_urls = mapping.sort_part_urls
sort_part_urls_in_rows = mapping.sort_part_urls_in_rows
url_order_warnings = mapping.url_order_warnings
MappingMetadata = matching.MappingMetadata
SearchCache = matching.SearchCache
generate_title_queries = matching.generate_title_queries
score_result = matching.score_result
AnimeWorldIndex = animeworld_index.AnimeWorldIndex
normalize_aw_title = animeworld_index.normalize_aw_title


class TestMappingManagement(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = pathlib.Path(self.tmp.name)
		self.table_path = self.root.joinpath("table.json")
		self.table_data = [
			{
				"title": "Example Anime",
				"absolute": False,
				"seasons": {"1": ["https://example.test/play/example-anime.abc"]},
			}
		]
		self.table_path.write_text(json.dumps(self.table_data), encoding="utf-8")

	def tearDown(self):
		self.tmp.cleanup()

	def test_table_load_save_compatibility(self):
		rows = flatten_table(json.loads(self.table_path.read_text(encoding="utf-8")))
		rows[0]["notes"] = "stored outside table"
		saved = table_from_rows(rows)
		self.assertEqual(saved, self.table_data)
		self.assertNotIn("notes", saved[0])

	def test_csv_import_export(self):
		rows = flatten_table(self.table_data)
		rows[0]["language_preference"] = "SUB_FIRST"
		rows[0]["notes"] = "prefer subs"

		csv_content = rows_to_csv(rows)
		imported, issues = rows_from_csv(csv_content)

		self.assertEqual(["sonarr_title", "season", "source_url", "absolute", "language_preference", "notes"], list(CSV_COLUMNS))
		self.assertEqual([], [issue for issue in issues if issue["level"] == "error"])
		self.assertEqual("Example Anime", imported[0]["sonarr_title"])
		self.assertEqual("SUB_FIRST", imported[0]["language_preference"])

	def test_csv_multi_season_rows_do_not_report_duplicate_title(self):
		content = "sonarr_title,season,source_url,absolute,language_preference,notes\nExample,1,https://example.test/one,false,,\nExample,2,https://example.test/two,false,,\n"

		_, issues = rows_from_csv(content)

		self.assertFalse(any(issue["message"] == "Duplicate title appears in this batch." for issue in issues))

	def test_json_import_export_table_shape(self):
		imported, issues = rows_from_json(json.dumps(self.table_data))

		self.assertEqual([], [issue for issue in issues if issue["level"] == "error"])
		self.assertEqual("Example Anime", imported[0]["sonarr_title"])
		self.assertEqual("1", imported[0]["season"])

	def test_language_preference_fallback(self):
		prefs = MappingPreferences(self.root.joinpath("mapping_preferences.json"))
		prefs.set_global_language("DUB_FIRST")

		self.assertEqual("DUB_FIRST", prefs.language_for("Example Anime"))
		prefs.set_series_metadata("Example Anime", {"language_preference": "SUB_ONLY"})
		self.assertEqual("SUB_ONLY", prefs.language_for("Example Anime"))

	def test_validation_logic(self):
		rows = [
			{"sonarr_title": "", "season": "one", "source_url": "ftp://bad", "absolute": False, "language_preference": "NOPE"},
			{"sonarr_title": "Dupe", "season": "1", "source_url": "https://example.test/a", "absolute": False, "language_preference": "AUTO"},
			{"sonarr_title": "dupe", "season": "1", "source_url": "https://example.test/a", "absolute": False, "language_preference": "AUTO"},
			{"sonarr_title": "Incomplete", "season": "1", "source_url": "", "absolute": False, "language_preference": "AUTO"},
		]

		issues = validate_rows(rows)
		messages = [issue["message"] for issue in issues]

		self.assertIn("Title cannot be empty.", messages)
		self.assertIn("Season must be numeric, or absolute for absolute mappings.", messages)
		self.assertIn("URL must be a valid http(s) URL.", messages)
		self.assertIn("Invalid language preference.", messages)
		self.assertIn("URL is empty; this mapping is incomplete.", messages)
		self.assertTrue(any(issue["level"] == "warning" for issue in issues))

	def test_duplicate_titles_can_round_trip_by_entry_id(self):
		data = [
			{"title": "Duplicate", "absolute": False, "seasons": {"1": ["https://example.test/a"]}},
			{"title": "Duplicate", "absolute": False, "seasons": {"2": ["https://example.test/b"]}},
		]

		rows = flatten_table(data)
		saved = table_from_rows(rows)

		self.assertEqual(2, len(saved))
		self.assertEqual({"1": ["https://example.test/a"]}, saved[0]["seasons"])
		self.assertEqual({"2": ["https://example.test/b"]}, saved[1]["seasons"])
		self.assertEqual(["duplicate::0", "duplicate::1"], [row["_mapping_id"] for row in rows])

	def test_unique_mapping_identity_survives_unrelated_sorting_changes(self):
		first = flatten_table([
			{"title": "Beta", "absolute": False, "seasons": {"1": ["https://example.test/b"]}},
			{"title": "Alpha", "absolute": False, "seasons": {"1": ["https://example.test/a"]}},
		])
		second = flatten_table([
			{"title": "Alpha", "absolute": False, "seasons": {"1": ["https://example.test/a"]}},
			{"title": "Beta", "absolute": False, "seasons": {"1": ["https://example.test/b"]}},
		])

		self.assertEqual(
			{row["sonarr_title"]: row["_mapping_id"] for row in first},
			{row["sonarr_title"]: row["_mapping_id"] for row in second},
		)

	def test_seasons_are_flattened_and_saved_in_numeric_order(self):
		data = [{"title": "Ordered", "absolute": False, "seasons": {"2": ["https://example.test/2"], "10": ["https://example.test/10"], "1": ["https://example.test/1"]}}]

		rows = flatten_table(data)
		saved = table_from_rows(rows)

		self.assertEqual(["1", "2", "10"], [row["season"] for row in rows])
		self.assertEqual(["1", "2", "10"], list(saved[0]["seasons"].keys()))

	def test_duplicate_url_across_all_seasons_is_consistently_flagged(self):
		rows = [
			{"_entry_id": "alya", "sonarr_title": "Alya", "season": "1", "source_url": "https://example.test/shared/", "absolute": False, "language_preference": "AUTO"},
			{"_entry_id": "alya", "sonarr_title": "Alya", "season": "2", "source_url": "https://example.test/shared#episode", "absolute": False, "language_preference": "AUTO"},
			{"_entry_id": "frieren", "sonarr_title": "Frieren", "season": "1", "source_url": "https://example.test/frieren", "absolute": False, "language_preference": "AUTO"},
			{"_entry_id": "frieren", "sonarr_title": "Frieren", "season": "2", "source_url": "https://example.test/frieren/", "absolute": False, "language_preference": "AUTO"},
			{"_entry_id": "frieren", "sonarr_title": "Frieren", "season": "3", "source_url": "https://example.test/frieren#x", "absolute": False, "language_preference": "AUTO"},
		]

		issues = validate_rows(rows)
		duplicates = [issue for issue in issues if issue.get("code") == "duplicate_url_across_seasons"]

		self.assertEqual("https://example.test/shared", normalize_url(" https://example.test/shared/#episode "))
		self.assertEqual(2, len(duplicates))
		self.assertEqual({"alya", "frieren"}, {issue["series"] for issue in duplicates})

	def test_cleared_manual_review_stays_clear_while_duplicate_warning_remains(self):
		prefs = MappingPreferences(self.root.joinpath("mapping_preferences.json"))
		rows = [
			{"_entry_id": "show", "sonarr_title": "Review Show", "season": "1", "source_url": "https://example.test/shared", "absolute": False, "language_preference": "", "notes": "", "needs_review": False},
			{"_entry_id": "show", "sonarr_title": "Review Show", "season": "2", "source_url": "https://example.test/shared", "absolute": False, "language_preference": "", "notes": "", "needs_review": False},
		]

		metadata = mapping.metadata_from_rows(rows, prefs)
		issues = validate_rows(rows)

		self.assertFalse(metadata["series"]["Review Show"]["needs_review"])
		self.assertTrue(any(issue.get("code") == "duplicate_url_across_seasons" for issue in issues))

	def test_import_add_only_does_not_modify_existing_season(self):
		existing = flatten_table(self.table_data)
		incoming = [{"sonarr_title": "example anime", "season": "1", "source_url": "https://example.test/new", "absolute": False, "language_preference": "", "notes": ""}]

		final_rows, summary = reconcile_import_rows(existing, incoming, "add_only")
		saved = table_from_rows(final_rows)

		self.assertEqual(self.table_data, saved)
		self.assertEqual(1, summary["unchanged"])
		self.assertEqual(0, summary["updated"])

	def test_import_update_matches_normalized_title_and_keeps_imported_display_title(self):
		existing = flatten_table(self.table_data)
		incoming = [{"sonarr_title": "EXAMPLE ANIME", "season": "1", "source_url": "https://example.test/replaced", "absolute": False, "language_preference": "", "notes": ""}]

		final_rows, summary = reconcile_import_rows(existing, incoming, "update")
		saved = table_from_rows(final_rows)

		self.assertEqual("EXAMPLE ANIME", saved[0]["title"])
		self.assertEqual(["https://example.test/replaced"], saved[0]["seasons"]["1"])
		self.assertEqual(1, summary["updated"])

	def test_import_overwrite_removes_rows_missing_from_source(self):
		existing = flatten_table(self.table_data + [{"title": "Delete Me", "absolute": False, "seasons": {"1": ["https://example.test/delete"]}}])
		incoming = [{"sonarr_title": "Clean Source", "season": "1", "source_url": "https://example.test/clean", "absolute": False, "language_preference": "", "notes": ""}]

		final_rows, summary = reconcile_import_rows(existing, incoming, "overwrite")
		saved = table_from_rows(final_rows)

		self.assertEqual(["Clean Source"], [entry["title"] for entry in saved])
		self.assertEqual(2, summary["deleted"])
		self.assertEqual(1, summary["added"])

	def test_overwrite_json_preserves_explicit_duplicate_table_entries(self):
		duplicates = [
			{"title": "Duplicate", "absolute": False, "seasons": {"1": ["https://example.test/a"]}},
			{"title": "Duplicate", "absolute": False, "seasons": {"2": ["https://example.test/b"]}},
		]
		incoming, _ = rows_from_json(json.dumps(duplicates))

		final_rows, _ = reconcile_import_rows([], incoming, "overwrite")
		saved = table_from_rows(final_rows)

		self.assertEqual(2, len(saved))
		self.assertEqual({"1": ["https://example.test/a"]}, saved[0]["seasons"])
		self.assertEqual({"2": ["https://example.test/b"]}, saved[1]["seasons"])

	def test_backup_creation(self):
		prefs_path = self.root.joinpath("mapping_preferences.json")
		prefs_path.write_text(json.dumps({"global_language_preference": "AUTO", "series": {}, "drafts": []}), encoding="utf-8")

		backups = create_backup([self.table_path, prefs_path], self.root.joinpath("backups"))

		self.assertEqual(2, len(backups))
		for backup in backups:
			self.assertTrue(pathlib.Path(backup).exists())
			self.assertRegex(pathlib.Path(backup).name, r"^(table|mapping_preferences)_\d{4}-\d{2}-\d{2}_\d{4}\.json$")

	def test_sort_part_urls_orders_base_before_later_parts(self):
		urls = [
			"https://www.animeworld.ac/play/dr-stone-4-part-2.DeE4h",
			"https://www.animeworld.ac/play/dr-stone-4-part-3.Y2JYK",
			"https://www.animeworld.ac/play/dr-stone-4.UZxi8",
		]

		self.assertEqual([urls[2], urls[0], urls[1]], sort_part_urls(urls))

	def test_url_order_warning_detects_part_before_base(self):
		warnings = url_order_warnings("Dr. STONE", 4, [
			"https://www.animeworld.ac/play/dr-stone-4-part-2.DeE4h",
			"https://www.animeworld.ac/play/dr-stone-4.UZxi8",
		])

		self.assertTrue(any(warning["code"] == "url_order_warning" for warning in warnings))

	def test_preview_flatten_order_builds_cumulative_ranges(self):
		preview = preview_flatten_order("Dr. STONE", 4, ["base", "part-2", "part-3"], lambda url: {"base": 12, "part-2": 12, "part-3": 4}[url])

		self.assertEqual((1, 12), (preview["urls"][0]["flattened_start"], preview["urls"][0]["flattened_end"]))
		self.assertEqual((25, 28), (preview["urls"][2]["flattened_start"], preview["urls"][2]["flattened_end"]))

	def test_import_preserves_order_by_default_and_sorts_when_requested(self):
		rows = [
			{"sonarr_title": "Dr. STONE", "season": "4", "source_url": "https://www.animeworld.ac/play/dr-stone-4-part-2.DeE4h", "absolute": False},
			{"sonarr_title": "Dr. STONE", "season": "4", "source_url": "https://www.animeworld.ac/play/dr-stone-4.UZxi8", "absolute": False},
		]

		preserved = table_from_rows(rows)
		sorted_table = table_from_rows(sort_part_urls_in_rows(deepcopy(rows)))

		self.assertEqual(rows[0]["source_url"], preserved[0]["seasons"]["4"][0])
		self.assertEqual(rows[1]["source_url"], sorted_table[0]["seasons"]["4"][0])

	def test_per_series_language_override_persists_after_reload(self):
		path = self.root.joinpath("mapping_preferences.json")
		preferences = MappingPreferences(path)
		preferences.set_series_metadata("Persisted Show", {"language_preference": "SUB_FIRST"})

		reloaded = MappingPreferences(path)

		self.assertEqual("SUB_FIRST", reloaded.language_for("Persisted Show"))

	def test_title_query_generation_uses_aliases_and_variants(self):
		metadata = MappingMetadata(self.root.joinpath("mapping_metadata.json"))
		metadata.set_aliases("The Ramparts of Ice", ["Koori no Jouheki"])

		result = generate_title_queries({
			"title": "The Ramparts of Ice",
			"sortTitle": "Ramparts of Ice",
			"alternateTitles": [{"title": "Koori no Jouheki"}],
		}, metadata)

		self.assertIn("The Ramparts of Ice", result["queries"])
		self.assertIn("Ramparts of Ice", result["queries"])
		self.assertIn("Koori no Jouheki", result["queries"])
		self.assertIn("Koori no Jouheki", result["aliases"])

	def test_search_cache_round_trip(self):
		cache = SearchCache(self.root.joinpath("search_cache.json"))
		cache.set("key", {"status": "no_result", "matches": []})

		self.assertEqual("no_result", cache.get("key")["status"])

	def test_alias_match_scores_high_confidence(self):
		scored = score_result("Ganbare! Nakamura-kun!!", {"name": "Ganbare! Nakamura-kun!!", "url": "https://example.test/n"}, ["Ganbare! Nakamura-kun!!"])

		self.assertEqual("auto_matched", scored["status"])
		self.assertGreaterEqual(scored["confidence"], 0.9)

	def test_animeworld_index_finds_known_present_titles(self):
		index = AnimeWorldIndex(self.root.joinpath("animeworld_index.json"), "https://www.animeworld.ac")
		titles = [
			"The Ramparts of Ice",
			"Go For It, Nakamura-kun!!",
			"Witch Hat Atelier",
			"Gals Can't Be Kind to Otaku!?",
			"NEEDY GIRL OVERDOSE",
		]
		index.data = {
			"generated_at": "2026-05-21T15:30:00",
			"source_base_url": "https://www.animeworld.ac",
			"entries": [{
				"title": title,
				"normalized_title": normalize_aw_title(title),
				"url": f"https://www.animeworld.ac/play/{normalize_aw_title(title).replace(' ', '-')}.abc",
				"audio": "SUB",
				"year": 2026,
				"aliases": [],
			} for title in titles],
		}

		for title in titles:
			with self.subTest(title=title):
				candidates = index.search([title])
				self.assertGreaterEqual(len(candidates), 1)
				self.assertEqual(title, candidates[0]["title"])
				self.assertEqual("high", candidates[0]["confidence"])


if __name__ == "__main__":
	unittest.main()
