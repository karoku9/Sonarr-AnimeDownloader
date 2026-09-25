"""Phase 3 regressions for AV4-004/015/016/021/022/023.

Every fixture is synthetic or a temporary copy.  Nothing in this module contacts
Sonarr, AnimeWorld, a production database, or a downloader.
"""
from v4_test_support import repo_tempdir
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from src.v4.animeworld_index import AnimeWorldIndex, CatalogShortlistIndex
from src.v4.application import ApplicationService
from src.v4.application_store import ApplicationStore
from src.v4.live_dataset import LiveSonarrDataset
from src.v4.series_view import series_list
from src.v4.source_manifest import build_manifest, compare_manifests


ROOT = Path(__file__).resolve().parents[2]


def isolated_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RANGES = isolated_module("phase3_legacy_episode_ranges", ROOT / "src/components/backend/legacy_episode_ranges.py")
_LANGUAGE = isolated_module("phase3_legacy_language", ROOT / "src/components/backend/search_v3/language.py")
_ATOMIC_JSON = isolated_module("phase3_legacy_atomic_json", ROOT / "src/components/backend/atomic_json.py")


def snapshot(series_id, *, revision=0, episodes=24):
    """Return one complete synthetic current mapping with intentionally heavy detail."""
    target_id = f"{series_id}:1"
    candidate_id = f"candidate-{series_id}-{revision}"
    links = [
        {
            "source_episode": episode,
            "sonarr_season": 1,
            "sonarr_episode": episode,
            "absolute_episode": episode,
        }
        for episode in range(1, episodes + 1)
    ]
    return {
        "schema_version": 1,
        "targets": [{
            "target_id": target_id,
            "canonical_title": f"Synthetic Series {series_id:04d}",
            "season_number": 1,
            "episode_count": episodes,
        }],
        "candidates": [{
            "id": candidate_id,
            "canonical_title": f"Synthetic Series {series_id:04d}",
            "url": f"https://www.animeworld.ac/play/synthetic-{series_id}-{revision}",
            "alternate_titles": [],
            "season_number": 1,
            "season_namespace": "catalog",
            "release_id": f"release-{series_id}",
            "episode_count": episodes,
            "episodes_complete": True,
            "episode_scope": "catalog_release",
            "audio": "SUB",
            "source": {"name": "synthetic", "record_id": candidate_id, "reliable": True, "attributes": []},
            "field_provenance": [],
            "crosswalks": [],
            "external_ids": [],
            "mapped_seasons": [],
        }],
        "proposed_plan": {
            "target_ids": [target_id],
            "segments": [{
                "release": {
                    "release_id": f"release-{series_id}",
                    "title": f"Synthetic Series {series_id:04d}",
                    "candidate_ids": [candidate_id],
                },
                "coverage": {"status": "complete", "links": links},
                "crosswalk": {
                    "basis": "whole_target_identity_date_numbering",
                    "reliability": "metadata_supported",
                    "evidence": ["synthetic regression evidence"],
                },
            }],
        },
        "alternate_plans": [],
        "reason_codes": [],
        "evidence": [],
        "initial_state": "proposed",
        "audio_preference": "SUB",
        "provider_observations": {},
        "matcher_version": "phase3-test",
        "resolver_version": "phase3-test",
        "foundation_decisions": [],
        "v3_observations": [],
    }


class CurrentProjectionRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = repo_tempdir()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "application.sqlite3"
        self.store = ApplicationStore(self.path)

    def populate(self, count=113, history_generations=0):
        current = [snapshot(series_id) for series_id in range(1, count + 1)]
        self.store.record_scan(current, "synthetic", authoritative=True)
        for revision in range(1, history_generations + 1):
            current = [snapshot(series_id, revision=revision) for series_id in range(1, count + 1)]
            self.store.record_scan(current, "synthetic", authoritative=True)
        return ApplicationService(self.store, {})

    def test_av4_015_series_is_summary_only_and_under_current_scale_budget(self):
        service = self.populate()
        payload = service.series()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertEqual(payload["total"], 113)
        self.assertLess(len(encoded), 150_000)
        self.assertTrue(all(row["seasons"] for row in payload["items"]))
        self.assertTrue(all("episodes" not in season and "sources" not in season
            and "effective" not in season and "automatic" not in season
            for row in payload["items"] for season in row["seasons"]))
        detail = service.series_detail(payload["items"][0]["series_id"])
        self.assertTrue(detail["seasons"])
        self.assertTrue(detail["seasons"][0]["episodes"])

    def test_av4_015_list_and_overview_do_not_decode_all_history(self):
        service = self.populate(count=20, history_generations=4)
        with patch.object(self.store, "_decode", wraps=self.store._decode) as decode:
            page = service.items(offset=0, limit=5)
            self.assertEqual(len(page["items"]), 5)
            self.assertEqual(decode.call_count, 0)
        with patch.object(self.store, "_decode", wraps=self.store._decode) as decode:
            overview = service.overview()
            self.assertEqual(overview["current_target_count"], 20)
            self.assertEqual(decode.call_count, 0)

    def test_av4_015_projection_migration_is_idempotent_and_query_plans_use_indexes(self):
        service = self.populate(count=12)
        expected = service.series()
        reopened = ApplicationStore(self.path)
        self.assertEqual(ApplicationService(reopened, {}).series(), expected)
        plans = reopened.projection_query_plans()
        self.assertIn("v4_current_series_title", plans["series"])
        self.assertIn("v4_item_summaries_state_created", plans["items"])

    def test_av4_015_projection_matches_the_previous_current_state_semantics(self):
        service = self.populate(count=8)
        legacy = series_list(
            self.store.current_items(),
            self.store.season_overrides(),
            self.store.provider_validation_states(),
        )
        projected = [service.series_detail(row["series_id"])
            for row in service.series(limit=20)["items"]]
        self.assertEqual(projected, legacy)

    def test_av4_015_public_activity_uses_projected_titles(self):
        self.populate(count=3)
        rows = self.store.public_activity(("scan_completed",), before=0, limit=10)
        self.assertEqual(rows[0]["action"], "scan_completed")
        plans = self.store.projection_query_plans()
        self.assertNotIn("json_extract", plans["activity"].casefold())


class ScanArchitectureRegressions(unittest.TestCase):
    class Seed:
        def read(self):
            return {"cases": [], "language_policy": {"default": "SUB", "overrides": {}}}

    class FreshCache:
        def peek(self, _url, _seed):
            return {"status": "ok", "next_validation_at": "2999-01-01T00:00:00+00:00"}

        def get(self, _url, _seed):
            raise AssertionError("no provider validation was due")

    @staticmethod
    def series_row(series_id, count=12):
        return {
            "id": series_id,
            "title": f"Series {series_id}",
            "seriesType": "anime",
            "tags": [1],
            "seasons": [{
                "seasonNumber": 1,
                "statistics": {
                    "totalEpisodeCount": count,
                    "episodeCount": count,
                    "episodeFileCount": 0,
                },
            }],
        }

    def dataset(self, rows, calls, *, detail_request_budget=256):
        value = LiveSonarrDataset.__new__(LiveSonarrDataset)
        value.seed = self.Seed()
        value.detail_cache = self.FreshCache()
        value.validation_budget = 30
        value.detail_request_budget = detail_request_budget
        value.candidate_limit = 128
        value.cache = ROOT / "work" / "phase3-never-used"
        value.catalog_path = value.cache / "catalog.json"
        value._get = lambda route: calls.append(route) or ([{"id": 1, "label": "animeworld"}]
            if route == "tag" else rows if route == "series" else [])
        return value

    def test_av4_016_no_change_inventory_does_no_rematch_or_episode_fetch(self):
        calls = []
        rows = [self.series_row(series_id) for series_id in range(1, 101)]
        dataset = self.dataset(rows, calls)
        groups = [{
            "item_id": f"item-{series_id}",
            "target_counts": {f"{series_id}:1": 12},
            "audio": "SUB",
            "source_fingerprints": {},
        } for series_id in range(1, 101)]
        progress = []
        result = dataset.read_incremental(
            preserve_groups=groups,
            current_target_ids=[f"{series_id}:1" for series_id in range(1, 101)],
            series_audio_master={},
            progress=lambda stage, percent: progress.append((stage, percent)),
        )
        metrics = result["incremental"]
        self.assertEqual(calls, ["tag", "series"])
        self.assertEqual(metrics["work_series_count"], 0)
        self.assertEqual(metrics["episode_fetches"], 0)
        self.assertEqual(metrics["provider_rematches"], 0)
        self.assertEqual(metrics["catalog_shortlists"], 0)
        self.assertEqual([stage for stage, _ in progress],
            ["inventory_read", "inventory_diff", "provider_verification", "dirty_mapping"])

    def test_av4_016_dirty_work_is_proportional_to_changed_series(self):
        calls = []
        rows = [self.series_row(series_id, 13 if series_id == 37 else 12) for series_id in range(1, 101)]
        dataset = self.dataset(rows, calls)
        groups = [{
            "item_id": f"item-{series_id}",
            "target_counts": {f"{series_id}:1": 12},
            "audio": "SUB",
            "source_fingerprints": {},
        } for series_id in range(1, 101)]

        class FakeIndex:
            data = {"catalog_complete": True, "entries": []}

            def __init__(self, *_args, **_kwargs):
                pass

            def is_stale(self, **_kwargs):
                return False

        with patch("src.v4.live_dataset.AnimeWorldIndex", FakeIndex):
            result = dataset.read_incremental(
                preserve_groups=groups,
                current_target_ids=[f"{series_id}:1" for series_id in range(1, 101)],
                series_audio_master={},
            )
        metrics = result["incremental"]
        self.assertEqual(metrics["work_series_count"], 1)
        self.assertEqual(metrics["episode_fetches"], 1)
        self.assertEqual(metrics["provider_rematches"], 1)
        self.assertEqual([route for route in calls if route.startswith("episode?")], ["episode?seriesId=37"])

    def test_av4_016_inventory_fingerprint_tracks_title_and_relocated_specials(self):
        row = self.series_row(7)
        season = row["seasons"][0]
        baseline = LiveSonarrDataset._inventory_fingerprint(row, season)
        renamed = deepcopy(row)
        renamed["title"] = "Renamed Series 7"
        with_specials = LiveSonarrDataset._inventory_fingerprint(
            row,
            season,
            specials=({"season": 0, "statistics": {"totalEpisodeCount": 2}},),
        )
        self.assertNotEqual(baseline, LiveSonarrDataset._inventory_fingerprint(renamed, season))
        self.assertNotEqual(baseline, with_specials)

    def test_av4_016_indexed_shortlist_is_bounded_and_overflow_is_explicit(self):
        catalog = [{
            "title": f"Common Family Arc {index}",
            "aliases": [],
            "url": f"https://www.animeworld.ac/play/common-{index}",
            "source": "animeworld_catalog",
        } for index in range(500)]
        index = CatalogShortlistIndex(catalog, limit=40)
        result = index.retrieve({"title": "Common Family", "alternateTitles": []})
        self.assertTrue(result.overflow)
        self.assertLessEqual(len(result.entries), 40)
        self.assertGreater(result.total_matches, len(result.entries))
        self.assertLess(result.catalog_entries_examined, len(catalog))

    def test_av4_016_index_miss_does_not_iterate_the_full_catalog(self):
        catalog = [{
            "title": f"Unrelated Title {position}",
            "aliases": [],
            "url": f"https://www.animeworld.ac/play/unrelated-{position}",
            "source": "animeworld_catalog",
        } for position in range(10_000)]
        result = CatalogShortlistIndex(catalog, limit=40).retrieve(
            {"title": "Completely Absent Work", "alternateTitles": []})
        self.assertEqual(result.entries, ())
        self.assertTrue(result.overflow)
        self.assertEqual(result.total_matches, 10_000)
        self.assertEqual(result.catalog_entries_examined, 0)

    def test_av4_016_normal_scan_never_refreshes_catalog_but_deep_audit_does(self):
        calls = []
        dataset = self.dataset([self.series_row(1)], calls)
        refreshes = []

        class FakeIndex:
            data = {"generated_at": "2026-01-01T00:00:00", "catalog_complete": True, "entries": []}

            def __init__(self, *_args, **_kwargs):
                self.data = dict(type(self).data)

            def refresh(self):
                refreshes.append("refresh")
                return self.data

        with patch("src.v4.live_dataset.AnimeWorldIndex", FakeIndex):
            dataset.read_incremental(preserve_groups=[], current_target_ids=[], series_audio_master={})
            self.assertEqual(refreshes, [])
            dataset.read_deep_audit(progress=lambda *_args: None)
        self.assertEqual(refreshes, ["refresh"])

    def test_av4_016_provider_detail_budget_returns_explicit_pending_urls(self):
        calls = []
        dataset = self.dataset([], calls, detail_request_budget=2)

        class Cache:
            def peek(self, _url, _seed):
                return None

            def get(self, url, _seed):
                calls.append(url)
                return {"status": "ok", "raw": {"url": url}}

        dataset.detail_cache = Cache()
        urls = [f"https://www.animeworld.ac/play/budget-{index}" for index in range(5)]
        details, pending = dataset._load_candidate_details(urls, {}, max_workers=2)
        self.assertEqual(set(details), set(urls[:2]))
        self.assertEqual(pending, set(urls[2:]))
        self.assertEqual(calls, urls[:2])


class LegacyRegressionTests(unittest.TestCase):
    def test_av4_021_merged_ranges_parse_immutable_endpoints(self):
        self.assertEqual(_RANGES.expand_episode_number("1-2", offset=0), (1, 2))
        self.assertEqual(_RANGES.expand_episode_number("1-2", offset=10), (11, 12))
        self.assertEqual(_RANGES.expand_episode_number("3-2", offset=0), ())
        self.assertEqual(_RANGES.expand_episode_number("not-a-range", offset=0), ())

    def test_av4_022_unknown_never_receives_positive_sub_evidence(self):
        candidates = [
            {"id": "unknown", "audio": "UNKNOWN", "score": 0.8, "season_match": True},
            {"id": "sub", "audio": "SUB", "score": 0.8, "season_match": True},
            {"id": "dub", "audio": "DUB", "score": 0.8, "season_match": True},
        ]
        ranked = _LANGUAGE.LanguagePreferenceResolver().apply(candidates, "SUB_FIRST")
        scores = {row["id"]: row["language_rank_score"] for row in ranked}
        self.assertGreater(scores["sub"], scores["unknown"])
        self.assertEqual(scores["unknown"], scores["dub"])
        self.assertEqual(AnimeWorldIndex.apply_language_preference(None, 0.8, {"audio": "UNKNOWN"}, "SUB_FIRST"), 0.8)
        self.assertGreater(AnimeWorldIndex.apply_language_preference(None, 0.8, {"audio": "SUB"}, "SUB_FIRST"), 0.8)

    def test_av4_023_atomic_json_survives_interruption_and_concurrent_writers(self):
        with repo_tempdir() as temporary:
            path = Path(temporary) / "state.json"
            _ATOMIC_JSON.write_json_durable(path, {"writer": "initial", "values": list(range(20))})
            before = path.read_bytes()
            with patch.object(_ATOMIC_JSON.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    _ATOMIC_JSON.write_json_durable(path, {"writer": "failed"})
            self.assertEqual(path.read_bytes(), before)

            def writer(number):
                _ATOMIC_JSON.write_json_durable(path, {"writer": number, "values": list(range(50))})

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(writer, range(32)))
            value = _ATOMIC_JSON.read_json_durable(path)
            self.assertIn(value["writer"], range(32))
            self.assertEqual(value["values"], list(range(50)))

    def test_av4_004_manifest_comparison_reports_only_verifiable_drift(self):
        local = build_manifest(ROOT, revision=None, tree_state="unverified")
        same = deepcopy(local)
        self.assertEqual(compare_manifests(local, same)["status"], "identical")
        changed = deepcopy(local)
        changed["files"][0]["sha256"] = "0" * 64
        comparison = compare_manifests(local, changed)
        self.assertEqual(comparison["status"], "different")
        self.assertTrue(comparison["changed"])
        self.assertIsNone(local["vcs_revision"])
        self.assertEqual(local["tree_state"], "unverified")


if __name__ == "__main__":
    unittest.main()
