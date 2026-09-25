import json
import importlib
import logging
import pathlib
import tempfile
import types
import unittest
from copy import deepcopy
from unittest.mock import patch

import sys

animeworld = types.ModuleType("animeworld")
animeworld.Anime = object
animeworld.Episodio = object
animeworld.AnimeNotAvailable = type("AnimeNotAvailable", (Exception,), {})
animeworld.ServerNotSupported = type("ServerNotSupported", (Exception,), {})
animeworld.Error404 = type("Error404", (Exception,), {})
animeworld.DeprecatedLibrary = type("DeprecatedLibrary", (Exception,), {})
sys.modules.setdefault("animeworld", animeworld)

BACKEND_PATH = pathlib.Path(__file__).parents[1].joinpath("src", "components", "backend")
package = types.ModuleType("runtime_testpkg")
package.__path__ = [str(BACKEND_PATH)]
sys.modules.setdefault("runtime_testpkg", package)
core_package = types.ModuleType("runtime_testpkg.core")
core_package.__path__ = [str(BACKEND_PATH.joinpath("core"))]
sys.modules.setdefault("runtime_testpkg.core", core_package)
database_package = types.ModuleType("runtime_testpkg.database")
database_package.Settings = object
database_package.Tags = object
database_package.Table = object
sys.modules.setdefault("runtime_testpkg.database", database_package)
connection_package = types.ModuleType("runtime_testpkg.connection")
connection_package.ConnectionsManager = object
connection_package.Sonarr = object
connection_package.ExternalDB = object
sys.modules.setdefault("runtime_testpkg.connection", connection_package)
utility_package = types.ModuleType("runtime_testpkg.utility")
utility_package.ColoredString = types.SimpleNamespace(yellow=lambda value: value)
sys.modules.setdefault("runtime_testpkg.utility", utility_package)
constant_module = types.ModuleType("runtime_testpkg.core.Constant")
constant_module.LOGGER = logging.getLogger("downloader-test")
sys.modules.setdefault("runtime_testpkg.core.Constant", constant_module)

Downloader = importlib.import_module("runtime_testpkg.core.Downloader").Downloader
Processor = importlib.import_module("runtime_testpkg.core.Processor").Processor
MappingAutomation = importlib.import_module("runtime_testpkg.mapping_automation").MappingAutomation
RuntimeSettings = importlib.import_module("runtime_testpkg.runtime").RuntimeSettings
StructuredEventStore = importlib.import_module("runtime_testpkg.event_log").StructuredEventStore


class FakeTable:
	def __init__(self, path, data=None):
		self.db = path
		self._data = data or []
		self.db.write_text(json.dumps(self._data), encoding="utf-8")

	def getData(self):
		return deepcopy(self._data)

	def replaceAll(self, data):
		self._data = deepcopy(data)
		self.db.write_text(json.dumps(self._data), encoding="utf-8")


class FakeResponse:
	def __init__(self, data):
		self.data = data

	def raise_for_status(self):
		return None

	def json(self):
		return self.data


class FakeSonarr:
	def __init__(self, series=None):
		self._series = series or []
		self._episodes = {}
		self.queue_calls = 0
		self.episode_calls = 0
		self._wanted = []

	def series(self):
		return FakeResponse(self._series)

	def queue(self):
		self.queue_calls += 1
		return FakeResponse({"records": []})

	def episodes(self, series_id):
		self.episode_calls += 1
		return FakeResponse(self._episodes.get(series_id, []))

	def wantedMissing(self, n=20, page=1):
		return FakeResponse({"records": self._wanted if page == 1 else []})

	def serie(self, series_id):
		return FakeResponse(next((series for series in self._series if series.get("id") == series_id), {}))


class FakeSearch:
	def __init__(self, result):
		self.result = result
		self.calls = 0

	def search(self, *args, **kwargs):
		self.calls += 1
		return deepcopy(self.result)


class FakeTags:
	def __init__(self, tags=None):
		self.tags = tags or []

	def __iter__(self):
		return iter(self.tags)

	def isActive(self, key):
		return any(tag.get("id") == key and tag.get("active", False) for tag in self.tags)

	def __getitem__(self, key):
		return next(tag for tag in self.tags if tag.get("id") == key)


class FakeLegacyTable:
	def __init__(self, data):
		self.data = data

	def __contains__(self, title):
		return title in self.data

	def __getitem__(self, title):
		return self.data[title]


class FakeConnections:
	def __init__(self):
		self.sent = []

	def send(self, message):
		self.sent.append(message)


class AvailableEpisode:
	number = "1"

	def __init__(self, runtime, folder):
		self.runtime = runtime
		self.folder = folder

	def download(self, title, folder, hook=None):
		self.runtime.update(search_only_mode=True)
		path = folder.joinpath("completed.mkv")
		path.write_text("episode", encoding="utf-8")
		return path.name


class AvailableAnime:
	def __init__(self, runtime, folder, **kwargs):
		self.runtime = runtime
		self.folder = folder

	def getEpisodes(self):
		return [AvailableEpisode(self.runtime, self.folder)]


def high_result():
	return {
		"status": "matched",
		"selected_candidate": {
			"title": "Witch Hat Atelier",
			"url": "https://www.animeworld.ac/play/witch-hat-atelier.test",
			"score": 1.0,
			"confidence": "high",
			"audio": "SUB",
			"reason": "exact normalized title match",
		},
		"top_candidates": [],
		"errors": [],
	}


class TestRuntimeMapping(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.root = pathlib.Path(self.temp.name)
		self.table = FakeTable(self.root.joinpath("table.json"))
		self.runtime = RuntimeSettings(self.root.joinpath("runtime_settings.json"))
		self.sonarr = FakeSonarr()
		self.search = FakeSearch(high_result())
		self.service = MappingAutomation(
			self.root,
			self.table,
			self.search,
			self.runtime,
			self.sonarr,
			{"TagsMode": "WHITELIST"},
			FakeTags(),
			logging.getLogger("mapping-test"),
		)

	def tearDown(self):
		self.temp.cleanup()

	def test_auto_save_high_confidence_creates_backup_and_does_not_duplicate_url(self):
		first = self.service.save_high_confidence("Witch Hat Atelier", 1, high_result())
		second = self.service.save_high_confidence("Witch Hat Atelier", 1, high_result())

		self.assertTrue(first["saved"])
		self.assertEqual("already_mapped", second["action"])
		entry = self.table.getData()[0]
		self.assertEqual({"title": "Witch Hat Atelier", "absolute": False, "seasons": {"1": ["https://www.animeworld.ac/play/witch-hat-atelier.test"]}}, entry)
		self.assertEqual(1, len(list(self.root.joinpath("backups").glob("table_*.json"))))

	def test_refresh_dry_run_never_modifies_table_then_apply_saves_only_high_match(self):
		self.sonarr._series = [{
			"title": "Witch Hat Atelier",
			"seriesType": "anime",
			"seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {"episodeFileCount": 1}}],
			"tags": [],
		}]
		options = {
			"dry_run": True,
			"apply_high_confidence": True,
			"only_unmapped": True,
			"only_monitored": True,
			"respect_tags": False,
		}

		preview = self.service.refresh_from_sonarr(options)
		self.assertEqual([], self.table.getData())
		self.assertEqual("would_save", preview["items"][0]["action_taken"])

		options["dry_run"] = False
		applied = self.service.refresh_from_sonarr(options)
		self.assertEqual(1, applied["new_mappings_saved"])
		self.assertEqual("Witch Hat Atelier", self.table.getData()[0]["title"])

	def test_whitelisted_standard_series_is_parsed_as_explicit_anime_override(self):
		self.service.tags = FakeTags([{"id": 2, "name": "animeworld", "active": True}])
		self.sonarr._series = [{
			"title": "I Made Friends with the Second Prettiest Girl in My Class",
			"seriesType": "standard",
			"seasons": [{"seasonNumber": 1, "monitored": True}],
			"tags": [2],
		}]

		result = self.service.parse_unmapped({"dry_run": True, "respect_tags": True, "only_monitored": True})

		self.assertEqual(1, result["scanned_series"])
		self.assertEqual(1, result["series_type_overrides"])
		self.assertEqual(1, self.search.calls)

	def test_standard_series_without_whitelist_tag_remains_excluded(self):
		self.service.tags = FakeTags([{"id": 2, "name": "animeworld", "active": True}])
		self.sonarr._series = [{"title": "Ordinary TV", "seriesType": "standard", "seasons": [{"seasonNumber": 1, "monitored": True}], "tags": []}]

		result = self.service.parse_unmapped({"dry_run": True, "respect_tags": True, "only_monitored": True})

		self.assertEqual(0, result["scanned_series"])
		self.assertEqual(1, result["skipped_series_type"])
		self.assertEqual(0, self.search.calls)

	def test_missing_scan_keeps_whitelisted_standard_series(self):
		tags = FakeTags([{"id": 2, "name": "animeworld", "active": True}])
		sonarr = FakeSonarr()
		sonarr._wanted = [{
			"series": {
				"title": "I Made Friends with the Second Prettiest Girl in My Class",
				"path": "C:/Anime/Second Prettiest",
				"id": 181,
				"seriesType": "standard",
				"tags": [2],
				"alternateTitles": [{"title": "Class de 2-banme ni Kawaii Onnanoko to Tomodachi ni Natta", "sceneSeasonNumber": -1}],
			},
			"seasonNumber": 1,
			"episodeNumber": 1,
			"title": "Episode 1",
			"id": 1001,
			"absoluteEpisodeNumber": None,
		}]
		title = sonarr._wanted[0]["series"]["title"]
		table = FakeLegacyTable({title: {"title": title, "absolute": False, "seasons": {"1": ["https://example.test/second-prettiest"]}}})
		processor = Processor(sonarr, settings={"AutoBind": False, "TagsMode": "WHITELIST"}, tags=tags, table=table, external=object())

		result = processor.getData()

		self.assertEqual(1, len(result))
		self.assertEqual("standard", result[0]["type"])
		self.assertEqual(["https://example.test/second-prettiest"], result[0]["seasons"][0]["urls"])

	def test_uncertain_refresh_creates_review_draft_without_writing_table(self):
		uncertain = high_result()
		uncertain["status"] = "needs_review"
		uncertain["selected_candidate"] = None
		uncertain["top_candidates"] = [{"title": "Maybe Witch", "url": "https://example.test/maybe", "score": 0.8, "confidence": "medium", "audio": "SUB"}]
		self.service.search_v3 = FakeSearch(uncertain)
		self.sonarr._series = [{
			"title": "Maybe Witch",
			"seriesType": "anime",
			"seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {}}],
			"tags": [],
		}]

		result = self.service.refresh_from_sonarr({"dry_run": False, "apply_high_confidence": True, "only_monitored": True, "respect_tags": False})

		self.assertEqual([], self.table.getData())
		self.assertEqual(1, result["needs_review"])
		self.assertEqual(1, len(self.service.preferences.data["drafts"]))

	def test_existing_valid_mapping_skips_search_v3(self):
		self.table.replaceAll([{"title": "Witch-Hat Atelier!", "absolute": False, "seasons": {"1": ["https://example.test/mapped"]}}])
		self.sonarr._series = [{"title": "Witch Hat Atelier", "seriesType": "anime", "seasons": [{"seasonNumber": 1, "monitored": True}], "tags": []}]

		result = self.service.parse_unmapped({"respect_tags": False, "only_monitored": True})

		self.assertEqual(0, self.search.calls)
		self.assertEqual(1, result["skipped_search"])
		self.assertEqual("skipped_search", result["items"][0]["action"])
		self.assertEqual(0, self.sonarr.episode_calls)

	def test_empty_or_missing_season_mapping_calls_search_v3(self):
		self.table.replaceAll([{"title": "Witch Hat Atelier", "absolute": False, "seasons": {"1": []}}])
		self.sonarr._series = [{"title": "Witch Hat Atelier", "seriesType": "anime", "seasons": [{"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": True}], "tags": []}]

		self.service.parse_unmapped({"dry_run": True, "respect_tags": False, "only_monitored": True})

		self.assertEqual(2, self.search.calls)

	def test_force_rematch_can_search_existing_mapping(self):
		self.table.replaceAll([{"title": "Witch Hat Atelier", "absolute": False, "seasons": {"1": ["https://example.test/mapped"]}}])
		self.sonarr._series = [{"title": "Witch Hat Atelier", "seriesType": "anime", "seasons": [{"seasonNumber": 1, "monitored": True}], "tags": []}]

		result = self.service.parse_unmapped({"dry_run": True, "respect_tags": False, "only_monitored": True, "force_rematch": True})

		self.assertEqual(1, self.search.calls)
		self.assertEqual(0, result["skipped_search"])

	def test_future_tba_season_is_skipped_without_search_or_table_write(self):
		self.sonarr._series = [{"id": 17, "title": "Ranma 1/2 (2024)", "seriesType": "anime", "seasons": [{"seasonNumber": 3, "monitored": True}], "tags": []}]
		self.sonarr._episodes = {17: [{"seasonNumber": 3, "airDateUtc": "2099-01-01T00:00:00Z", "monitored": True}]}

		result = self.service.parse_unmapped({"dry_run": False, "respect_tags": False, "only_monitored": True})

		self.assertEqual(0, self.search.calls)
		self.assertEqual([], self.table.getData())
		self.assertEqual(1, result["skipped_future_tba"])
		self.assertEqual("skipped_future_tba", result["items"][0]["status"])

	def test_duplicate_candidate_across_seasons_becomes_review_in_apply_mode(self):
		events = StructuredEventStore(self.root.joinpath("duplicate_events.json"))
		self.service.events = events
		self.service.resolver.events = events
		self.table.replaceAll([{"title": "Ranma", "absolute": False, "seasons": {"2": ["https://www.animeworld.ac/play/witch-hat-atelier.test"]}}])
		self.sonarr._series = [{"title": "Ranma", "seriesType": "anime", "seasons": [{"seasonNumber": 3, "monitored": True}], "tags": []}]

		result = self.service.parse_unmapped({"dry_run": False, "respect_tags": False, "only_monitored": True})

		self.assertEqual(1, self.search.calls)
		self.assertEqual(0, result["new_mappings_saved"])
		self.assertEqual(1, result["duplicate_url_warnings"])
		self.assertEqual("duplicate_url_review", result["items"][0]["action"])
		self.assertNotIn("3", self.table.getData()[0]["seasons"])
		self.assertIn("MAPPING_DUPLICATE_URL_DETECTED", [event["event"] for event in events.list()])

	def test_tba_and_duplicate_compact_events_are_created(self):
		events = StructuredEventStore(self.root.joinpath("events.json"))
		self.service.events = events
		self.service.resolver.events = events
		self.sonarr._series = [{"id": 23, "title": "Future Show", "seriesType": "anime", "seasons": [{"seasonNumber": 2, "monitored": True}], "tags": []}]
		self.sonarr._episodes = {23: []}

		self.service.parse_unmapped({"dry_run": True, "respect_tags": False, "only_monitored": True})
		event_names = [event["event"] for event in events.list()]

		self.assertIn("SONARR_SEASON_SKIPPED_TBA", event_names)
		self.assertIn("SONARR_PARSE_DONE", event_names)

	def test_structured_log_store_returns_recent_expandable_events(self):
		events = StructuredEventStore(self.root.joinpath("events.json"))
		events.record({"type": "MAPPING", "event": "MAPPING_ALREADY_EXISTS_SKIP_SEARCH", "compact": "Existing mapping skipped", "details": {"season": 1}})

		recent = events.list()

		self.assertEqual("Existing mapping skipped", recent[0]["compact"])
		self.assertEqual({"season": 1}, recent[0]["details"])

	def test_structured_log_store_batches_bulk_writes(self):
		events = StructuredEventStore(self.root.joinpath("batched_events.json"))

		with patch("runtime_testpkg.event_log.write_json_atomic") as write:
			with events.batch():
				events.record({"event": "ONE"})
				events.record({"event": "TWO"})
				self.assertEqual(0, write.call_count)
			self.assertEqual(1, write.call_count)

	def test_ui_contains_dashboard_and_live_log_panel(self):
		javascript = pathlib.Path(__file__).parents[1].joinpath("src", "components", "frontend_OLD", "static", "js", "index", "table.js").read_text(encoding="utf-8")
		extension = pathlib.Path(__file__).parents[1].joinpath("chrome-extension", "content.js").read_text(encoding="utf-8")

		self.assertIn("live-log-dock", javascript)
		self.assertIn("Parse unmapped from Sonarr", javascript)
		self.assertIn("/api/sonarr/parse-unmapped/start", javascript)
		self.assertIn("/api/sonarr/parse-unmapped/status/", javascript)
		self.assertIn("/api/logs/events", javascript)
		self.assertIn("Needs review", javascript)
		self.assertIn("Include future/TBA seasons", javascript)
		self.assertIn("selectedMappings", javascript)
		self.assertIn("Merge and update existing", javascript)
		self.assertIn("/api/extension/current-page-candidates", extension)
		self.assertIn("/api/extension/save-mapping", extension)
		self.assertIn("Save mapping", extension)
		self.assertIn("Add season", extension)
		self.assertIn("Clear needs review after save", extension)
		self.assertIn("Copy debug info", extension)
		self.assertIn("/api/runtime/run-scan-now", javascript)
		self.assertIn("Run scan now", javascript)
		self.assertIn("Sort part URLs", javascript)
		self.assertIn("Preview episode order", javascript)
		self.assertIn("Auto-sort part URLs after import", javascript)

	def test_runtime_settings_persist(self):
		self.runtime.update(downloads_paused=True, search_only_mode=True, auto_save_high_confidence_mappings=False)
		reloaded = RuntimeSettings(self.root.joinpath("runtime_settings.json")).status()

		self.assertTrue(reloaded["downloads_paused"])
		self.assertTrue(reloaded["search_only_mode"])
		self.assertFalse(reloaded["auto_save_high_confidence_mappings"])

	def test_pause_and_search_only_prevent_episode_operations(self):
		settings = {"MoveEp": True, "RenameEp": True}
		sonarr = FakeSonarr()
		connections = FakeConnections()
		downloader = Downloader(settings, sonarr, connections, self.root, runtime_settings=self.runtime)
		serie = {
			"title": "Blocked",
			"path": str(self.root),
			"id": 1,
			"seasons": [{"number": 1, "urls": ["https://example.test/show"], "episodes": [{"episodeNumber": 1, "seasonNumber": 1, "id": 1}]}],
		}

		with patch("runtime_testpkg.core.Downloader.aw.Anime") as anime:
			self.runtime.update(downloads_paused=True)
			downloader.download(serie)
			self.runtime.update(downloads_paused=False, search_only_mode=True)
			downloader.download(serie)

		anime.assert_not_called()
		self.assertEqual(0, sonarr.queue_calls)
		self.assertEqual([], connections.sent)

	def test_search_only_enabled_during_active_download_skips_post_actions(self):
		settings = {"MoveEp": False, "RenameEp": False}
		connections = FakeConnections()
		downloader = Downloader(settings, FakeSonarr(), connections, self.root, runtime_settings=self.runtime)
		serie = {
			"title": "Active",
			"path": str(self.root),
			"id": 1,
			"seasons": [{"number": 1, "urls": ["https://example.test/show"], "episodes": [{"episodeNumber": 1, "seasonNumber": 1, "id": 1}]}],
		}

		with patch("runtime_testpkg.core.Downloader.aw.Anime", side_effect=lambda **kwargs: AvailableAnime(self.runtime, self.root, **kwargs)):
			downloader.download(serie)

		self.assertEqual([], connections.sent)


if __name__ == "__main__":
	unittest.main()
