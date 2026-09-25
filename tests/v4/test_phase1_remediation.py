from v4_test_support import repo_tempdir
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from itertools import permutations
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from src.v4.api import create_app
from src.v4.application import ApplicationService
from src.v4.application_store import ApplicationStore
from src.v4.live_dataset import LiveSonarrDataset
from src.v4.provider_validation import ProviderDetailCache, ProviderSourceValidator, ttl_for
from src.v4.release_resolver import best_exact_covers
from src.v4.releases import Crosswalk, EpisodeCoverage, EpisodeLink, ReleaseIdentity, ReleaseSegment
from src.v4.runtime import create_runtime
from src.v4.sonarr_eligibility import eligible_legacy_series
from src.v4.source_manifest import build_manifest
from tools.check_duplicate_tests import duplicates


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests/v4/fixtures/production_metadata_v1.json"


def provider_detail(url, *, title="Release", audio="Giapponese", episodes=(1, 2), status="ok"):
    return {
        "schema_version": 1,
        "source": "animeworld_detail",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "content_sha256": title.casefold(),
        "status": status,
        "raw": {
            "title": title,
            "url": url,
            "fields": [{"label": "Audio:", "value": audio}, {"label": "Episodi:", "value": str(len(episodes))}],
            "structured": [],
            "available_episode_numbers": list(episodes),
        },
    }


class FakeManualValidator:
    def __init__(self):self.fingerprint = "f" * 64
    def validate(self, urls, expected_audio, episode_map):
        maximum = max(row["source_episode"] for row in episode_map)
        return [{"url": url, "canonical_url": url, "fingerprint": self.fingerprint,
            "audio": expected_audio, "available_episode_numbers": list(range(1, maximum + 1)),
            "validated_at": "2026-09-22T12:00:00+00:00"} for url in urls]


class Phase1RemediationTests(unittest.TestCase):
    def request(self, app, method, path, body=None, **headers):
        environ = {}
        setup_testing_defaults(environ)
        route, _, query = path.partition("?")
        environ.update(REQUEST_METHOD=method, PATH_INFO=route, QUERY_STRING=query,
            REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost:6004")
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            environ.update(CONTENT_TYPE="application/json", CONTENT_LENGTH=str(len(raw)), **{"wsgi.input": io.BytesIO(raw)})
        environ.update(headers)
        output = []
        raw = b"".join(app(environ, lambda status, response_headers: output.append((int(status.split()[0]), dict(response_headers)))))
        return output[0][0], json.loads(raw) if output[0][1].get("Content-Type", "").startswith("application/json") else raw.decode()

    def test_av4_001_release_lane_builds_only_v4_with_authoritative_gates(self):
        workflow = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.v4").read_text(encoding="utf-8")
        context = (ROOT / "Dockerfile.v4.dockerignore").read_text(encoding="utf-8")
        self.assertIn("file: Dockerfile.v4", workflow)
        self.assertNotIn("file: ./Dockerfile\n", workflow)
        self.assertIn("unittest discover -s tests/v4", workflow)
        self.assertIn("node --test tests/*.test.mjs", workflow)
        self.assertIn("check_duplicate_tests.py", workflow)
        self.assertIn("push-by-digest=true", workflow)
        self.assertIn("needs: build_by_digest", workflow)
        self.assertIn("contract_version']=='4.1'", workflow)
        self.assertIn("SOURCE-MANIFEST.json", dockerfile)
        self.assertNotIn("!src/main.py", context)
        self.assertNotIn("!src/components/", context)

    def test_av4_019_duplicate_gate_detects_overwritten_tests(self):
        with repo_tempdir() as temporary:
            path = Path(temporary) / "test_duplicate_example.py"
            path.write_text("class TestDuplicate:\n    def test_same(self): pass\n    def test_same(self): pass\n", encoding="utf-8")
            found = duplicates([temporary])
            self.assertEqual([(scope, name) for _, scope, name, _, _ in found],
                [("TestDuplicate", "test_same")])

    def test_av4_004_manifest_is_deterministic_and_excludes_legacy_lane(self):
        first = build_manifest(ROOT, "unverified-local")
        second = build_manifest(ROOT, "unverified-local")
        self.assertEqual(first, second)
        paths = {row["path"] for row in first["files"]}
        self.assertIn("anidown_eligibility.py", paths)
        self.assertIn("src/v4/runtime.py", paths)
        self.assertNotIn("src/main.py", paths)
        self.assertFalse(any(path.startswith("src/components/") for path in paths))

    def test_av4_005_private_peer_cannot_gain_mutation_trust_by_host_spoofing(self):
        with repo_tempdir() as temporary:
            token = Path(temporary) / "token"
            token.write_text("t" * 48, encoding="utf-8")
            app = create_runtime(Path(temporary) / "runtime.sqlite3", {"real": FIXTURE},
                docker_transport=True, trusted_proxy_cidrs=["172.18.0.1/32"], auth_token_file=token)
            try:
                self.assertEqual(self.request(app, "GET", "/api/v4/overview", REMOTE_ADDR="172.18.0.1")[0], 200)
                self.assertEqual(self.request(app, "POST", "/api/v4/scans", {"dataset": "real"},
                    REMOTE_ADDR="172.18.0.1", HTTP_HOST="localhost:6004")[0], 403)
                self.assertEqual(self.request(app, "POST", "/api/v4/scans", {"dataset": "real"},
                    REMOTE_ADDR="192.168.1.50", HTTP_HOST="localhost:6004",
                    HTTP_AUTHORIZATION="Bearer " + "t" * 48)[0], 403)
                self.assertEqual(self.request(app, "POST", "/api/v4/scans", {"dataset": "real"},
                    REMOTE_ADDR="169.254.10.20", HTTP_HOST="localhost:6004",
                    HTTP_AUTHORIZATION="Bearer " + "t" * 48)[0], 403)
                self.assertEqual(self.request(app, "POST", "/api/v4/scans", {"dataset": "real"},
                    REMOTE_ADDR="fd00::5", HTTP_HOST="localhost:6004",
                    HTTP_AUTHORIZATION="Bearer " + "t" * 48)[0], 403)
                self.assertEqual(self.request(app, "POST", "/api/v4/scans", {"dataset": "real"},
                    REMOTE_ADDR="192.0.2.10", HTTP_X_FORWARDED_FOR="127.0.0.1",
                    HTTP_AUTHORIZATION="Bearer " + "t" * 48)[0], 403)
                self.assertEqual(self.request(app, "POST", "/api/v4/scans", {"dataset": "real"},
                    REMOTE_ADDR="172.18.0.1", HTTP_AUTHORIZATION="Bearer " + "t" * 48)[0], 202)
            finally:app.close()

    def test_av4_006_explicit_tag_is_authoritative_with_v3_v4_parity(self):
        rows = [
            ({"seriesType": "standard", "tag_labels": ["animeworld"]}, True),
            ({"seriesType": "standard", "tag_labels": []}, False),
            ({"seriesType": "anime", "tag_labels": ["AnimeWorld"]}, True),
            ({"seriesType": "standard", "tag_labels": ["other"]}, False),
        ]
        self.assertEqual([row for row, _ in rows if row in LiveSonarrDataset._eligible_series([row for row, _ in rows])],
            [row for row, expected in rows if expected])
        for row, expected in rows:
            whitelist = "animeworld" in {tag.casefold() for tag in row["tag_labels"]}
            self.assertEqual(eligible_legacy_series(row, whitelist_active=whitelist), expected)

    def test_av4_007_008_negative_cache_recovers_and_success_revalidates(self):
        url = "https://www.animeworld.ac/play/cache-regression.test"
        self.assertGreater(ttl_for(provider_detail(url)), ttl_for(provider_detail(url, status="metadata_not_found")))
        self.assertGreater(ttl_for(provider_detail(url, status="metadata_not_found")),
            ttl_for(provider_detail(url, status="fetch_failed")))
        with repo_tempdir() as temporary:
            now = [datetime(2026, 9, 22, tzinfo=timezone.utc)]
            responses = [provider_detail(url, status="fetch_failed"), provider_detail(url, title="Recovered")]
            calls = []
            def fetch(_url):
                calls.append(_url)
                value = dict(responses.pop(0));value["fetched_at"] = now[0].isoformat();return value
            cache = ProviderDetailCache(temporary, fetcher=fetch, clock=lambda: now[0])
            self.assertEqual(cache.get(url)["status"], "fetch_failed")
            self.assertEqual(len(calls), 1)
            now[0] += timedelta(minutes=6)
            recovered = cache.get(url)
            self.assertEqual((recovered["status"], recovered["raw"]["title"], len(calls)), ("ok", "Recovered", 2))
            now[0] += timedelta(hours=5)
            self.assertEqual(cache.get(url)["raw"]["title"], "Recovered")
            self.assertEqual(len(calls), 2)

    def test_av4_007_008_stale_success_survives_outage_then_retries(self):
        with repo_tempdir() as temporary:
            now = [datetime(2026, 9, 22, tzinfo=timezone.utc)]
            url = "https://www.animeworld.ac/play/stale-success.test"
            responses = [provider_detail(url, title="Old"), provider_detail(url, status="fetch_failed"), provider_detail(url, title="New")]
            def fetch(_url):
                value = dict(responses.pop(0));value["fetched_at"] = now[0].isoformat();return value
            cache = ProviderDetailCache(temporary, fetcher=fetch, clock=lambda: now[0])
            old = cache.get(url)
            now[0] += timedelta(hours=7)
            stale = cache.get(url)
            self.assertEqual((stale["status"], stale["validation_status"], stale["source_fingerprint"]),
                ("ok", "stale_fetch_failed", old["source_fingerprint"]))
            now[0] += timedelta(minutes=6)
            self.assertEqual(cache.get(url)["raw"]["title"], "New")

    def test_av4_007_validation_budget_marks_overflow_pending(self):
        with repo_tempdir() as temporary:
            dataset = LiveSonarrDataset("http://sonarr.invalid", Path(temporary) / "key", FIXTURE,
                Path(temporary) / "cache", validation_budget=3)
            groups = [{"item_id": f"item-{index}", "source_fingerprints": {
                f"https://www.animeworld.ac/play/{index}.test": "f" * 64}} for index in range(8)]
            selected, pending = dataset._due_provider_validations(groups, {})
            self.assertEqual(len(selected), 3)
            self.assertEqual(pending, {f"item-{index}" for index in range(3, 8)})

    def test_av4_007_pending_validation_is_explicit_and_non_executable(self):
        with repo_tempdir() as temporary:
            app = create_app(Path(temporary) / "app.sqlite3", {"real": FIXTURE})
            app.service.scan("real")
            item = next(row for row in app.service.store.list_items() if row["mapping_state"] == "proposed")
            app.service.store.record_provider_validation_state([item["id"]], "pending", {"reason": "budget"})
            target_id = item["original"]["targets"][0]["target_id"]
            series_id, season_number = map(int, target_id.split(":"))
            season = next(row for row in app.service.series_detail(series_id)["seasons"]
                if row["season"] == season_number)
            self.assertEqual(season["provider_validation_state"], "pending")
            self.assertFalse(season["auto_ready"])
            self.assertFalse(season["can_execute"])

    def test_av4_009_late_stronger_cover_wins_in_every_input_order(self):
        def segment(release_id, destinations, basis):
            links = tuple(EpisodeLink(index + 1, 1, destination) for index, destination in enumerate(destinations))
            return ReleaseSegment(ReleaseIdentity(release_id, release_id, (release_id,),
                (f"https://www.animeworld.ac/play/{release_id}",), None, tuple(range(1, len(links) + 1))),
                EpisodeCoverage(tuple(range(1, len(links) + 1)), links, "complete"),
                Crosswalk(basis, "metadata_supported", ("test evidence",)))
        weak_full = segment("a", (1, 2), "series_prefix_date_count_numbering")
        weak_left = segment("b", (1,), "series_prefix_date_count_numbering")
        weak_right = segment("c", (2,), "series_prefix_date_count_numbering")
        strong_left = segment("d", (1,), "whole_target_identity_date_numbering")
        strong_right = segment("e", (2,), "whole_target_identity_date_numbering")
        candidates = [weak_full, weak_left, weak_right, strong_left, strong_right]
        expected = (("d", "e"), False)
        for ordered in permutations(candidates):
            solutions, overflow = best_exact_covers(ordered, {1, 2})
            self.assertEqual((tuple(segment.release.release_id for segment in solutions[0]), overflow), expected)

    def test_av4_009_tied_best_covers_are_canonical_ambiguity(self):
        def segment(release_id, destinations):
            links = tuple(EpisodeLink(index + 1, 1, destination) for index, destination in enumerate(destinations))
            return ReleaseSegment(ReleaseIdentity(release_id, release_id, (release_id,),
                (f"https://www.animeworld.ac/play/{release_id}",), None, tuple(range(1, len(links) + 1))),
                EpisodeCoverage(tuple(range(1, len(links) + 1)), links, "complete"),
                Crosswalk("whole_target_identity_date_numbering", "metadata_supported", ("test evidence",)))
        full = segment("full", (1, 2))
        left = segment("left", (1,))
        right = segment("right", (2,))
        observed = []
        for ordered in permutations([full, left, right]):
            solutions, overflow = best_exact_covers(ordered, {1, 2})
            observed.append(([[part.release.release_id for part in solution] for solution in solutions], overflow))
        self.assertTrue(all(value == observed[0] for value in observed[1:]))
        self.assertEqual(len(observed[0][0]), 2)
        self.assertFalse(observed[0][1])
        self.assertTrue(best_exact_covers([full, left, right], {1, 2}, state_limit=1)[1])

    def test_av4_010_audio_master_is_durable_and_revisioned(self):
        with repo_tempdir() as temporary:
            path = Path(temporary) / "audio.sqlite3"
            store = ApplicationStore(path)
            first = store.set_series_audio_master("42", "DUB", "42:1", "a" * 64)
            reopened = ApplicationStore(path).series_audio_masters()["42"]
            self.assertEqual((first["audio"], reopened["audio"], reopened["revision"]), ("DUB", "DUB", 0))
            changed = store.set_series_audio_master("42", "SUB", "42:1", "b" * 64)
            self.assertEqual((changed["audio"], changed["revision"]), ("SUB", 1))

    def test_av4_010_master_change_invalidates_frozen_later_season(self):
        group={"item_id":"season-two","target_counts":{"42:2":12},"audio":"DUB","source_fingerprints":{}}
        regular={"42:2":{"episode_count":12}}
        self.assertTrue(LiveSonarrDataset._preservation_valid(group,regular,{"42":"DUB"},{}))
        self.assertFalse(LiveSonarrDataset._preservation_valid(group,regular,{"42":"SUB"},{}))

    def test_av4_010_scan_uses_master_even_when_s1_is_not_preserved(self):
        with repo_tempdir() as temporary:
            app = create_app(Path(temporary) / "app.sqlite3", {"real": FIXTURE})
            app.service.scan("real")
            master = next(iter(app.service.store.series_audio_masters().items()))
            captured = {}
            class Reader:
                def read_incremental(self, **kwargs):
                    captured.update(kwargs)
                    raise RuntimeError("captured")
            service = ApplicationService(app.service.store, {"capture": Reader()})
            with self.assertRaisesRegex(RuntimeError, "captured"):
                service.scan("capture")
            self.assertEqual(captured["series_audio_master"][master[0]], master[1]["audio"])

    def test_av4_011_partial_manual_mapping_cannot_be_approved_or_executed(self):
        with repo_tempdir() as temporary:
            validator=FakeManualValidator()
            app = create_runtime(Path(temporary) / "app.sqlite3", {"real": FIXTURE},
                manual_source_validator=validator)
            self.addCleanup(app.close)
            app.service.scan("real")
            rows = app.service.series()["items"]
            choices = [(series, season) for series in rows for season in series["seasons"]
                if season["episode_count"] and app.service.store.get_item(season["mapping_id"])["mapping_state"] in {"proposed", "needs_review"}]
            series, season = min(choices, key=lambda pair: pair[1]["episode_count"])
            url = "https://www.animeworld.ac/play/manual-approval.test"
            with self.assertRaisesRegex(ValueError, "exact destination coverage"):
                app.service.update_season_override(series["series_id"], season["season"], {
                    "source_urls": [url], "audio": "SUB", "manual_approved": True,
                    "episode_map": [{"sonarr_episode": 1, "source_index": 0, "source_episode": 1}]})
            episode_map = [{"sonarr_episode": number, "source_index": 0, "source_episode": number}
                for number in range(1, season["episode_count"] + 1)]
            saved = app.service.update_season_override(series["series_id"], season["season"], {
                "source_urls": [url], "audio": "SUB", "manual_approved": True, "episode_map": episode_map})
            self.assertTrue(saved["approval"]["sources"])
            current = next(row for row in app.service.series_detail(series["series_id"])["seasons"]
                if row["season"] == season["season"])
            self.assertTrue(current["can_execute"])
            self.assertTrue(app.service.manual_override_executable(series["series_id"],season["season"]))
            validator.fingerprint="e"*64
            self.assertFalse(app.service.manual_override_executable(series["series_id"],season["season"]))
            validator.fingerprint="f"*64
            item = app.service.store.get_item(current["mapping_id"])
            action = "approve" if item["mapping_state"] == "proposed" else "dismiss"
            app.service.store.transition(item["id"], action, item["revision"], "revision change", {})
            refreshed = next(row for row in app.service.series_detail(series["series_id"])["seasons"]
                if row["season"] == season["season"])
            self.assertFalse(refreshed["can_execute"])

    def test_av4_011_live_source_audio_and_episode_are_mandatory(self):
        url = "https://www.animeworld.ac/play/manual-live-validation.test"
        validator = ProviderSourceValidator(fetcher=lambda _: provider_detail(
            url, audio="Italiano", episodes=(1,)))
        row = [{"sonarr_episode": 1, "source_index": 0, "source_episode": 1}]
        with self.assertRaisesRegex(ValueError, "audio"):
            validator.validate([url], "SUB", row)
        missing = [{"sonarr_episode": 1, "source_index": 0, "source_episode": 2}]
        with self.assertRaisesRegex(ValueError, "not currently available"):
            validator.validate([url], "DUB", missing)


if __name__ == "__main__":
    unittest.main()
