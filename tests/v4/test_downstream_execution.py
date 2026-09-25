"""Phase 11 approved-only downstream boundary tests using V4-local storage."""
from v4_test_support import repo_tempdir
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import threading
import unittest

from src.v4.application import ApplicationService
from src.v4.application_adapters import SnapshotAdapter
from src.v4.application_store import ApplicationStore, Conflict
from src.v4.execution import (
    DownstreamFailure,
    ExecutionCoordinator,
    FakeDownstreamAdapter,
    SandboxDownstreamAdapter,
)


FIXTURE = "tests/v4/fixtures/production_metadata_v1.json"


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = repo_tempdir()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "app.sqlite3"
        self.store = ApplicationStore(self.path)
        self.application = ApplicationService(
            self.store, {"fixture": SnapshotAdapter(FIXTURE)}
        )
        self.application.scan("fixture")

    def item(self, target_id):
        return next(
            item
            for item in self.store.list_items()
            if item["mapping_state"] != "superseded"
            and target_id in {target["target_id"] for target in item["original"]["targets"]}
        )

    def approve(self, target_id="182:1"):
        item = self.item(target_id)
        self.application.action(
            item["id"],
            "approve",
            {"expected_revision": item["revision"], "reason": "Phase 11 fixture approval"},
        )
        return self.store.get_item(item["id"])

    def coordinator(self, adapter):
        return ExecutionCoordinator(self.store, adapter, owner_token="phase11-test")

    def test_proposed_review_and_rejected_plans_never_reach_adapter(self):
        adapter = FakeDownstreamAdapter()
        coordinator = self.coordinator(adapter)
        rejected = self.item("143:1")
        self.application.action(
            rejected["id"],
            "reject",
            {"expected_revision": rejected["revision"], "reason": "Fixture rejection"},
        )

        cases = {
            "proposed": self.item("182:1"),
            "needs_review": self.item("189:1"),
            "rejected": self.store.get_item(rejected["id"]),
        }
        for name, item in cases.items():
            with self.subTest(name=name), self.assertRaises(Conflict):
                coordinator.execute(item["id"], expected_revision=item["revision"])

        self.assertEqual(adapter.calls, [])
        self.assertEqual(self.store.execution_attempts(), [])

    def test_only_current_approved_plan_reaches_adapter(self):
        item = self.approve()
        adapter = FakeDownstreamAdapter()
        result = self.coordinator(adapter).execute(
            item["id"], expected_revision=item["revision"]
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(adapter.calls), 1)
        envelope = adapter.calls[0]
        self.assertEqual(envelope["item_id"], item["id"])
        self.assertEqual(envelope["snapshot_digest"], item["digest"])
        self.assertEqual(set(envelope["target_ids"]), {"182:1"})
        self.assertTrue(envelope["segments"])
        self.assertTrue(all(segment["selected_candidate_id"] for segment in envelope["segments"]))
        self.assertTrue(all(segment["episode_links"] for segment in envelope["segments"]))

    def test_duplicate_snapshot_execution_is_blocked(self):
        item = self.approve()
        adapter = FakeDownstreamAdapter()
        coordinator = self.coordinator(adapter)
        coordinator.execute(item["id"], expected_revision=item["revision"])

        with self.assertRaises(Conflict):
            coordinator.execute(item["id"], expected_revision=item["revision"])

        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(self.store.execution_attempts()), 1)

    def test_v3_and_v4_cannot_concurrently_claim_the_same_target(self):
        barrier = threading.Barrier(2)

        def claim(owner):
            local = ApplicationStore(self.path)
            barrier.wait()
            try:
                local.claim_writer(("synthetic:1",), owner, f"{owner}-token")
                return owner
            except Conflict:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("v3", "v4")))

        self.assertEqual(results.count("blocked"), 1)
        self.assertEqual(len(set(results) & {"v3", "v4"}), 1)

    def test_v3_owned_target_blocks_approved_v4_before_adapter(self):
        item = self.approve()
        self.store.claim_writer(("182:1",), "v3", "v3-test-owner")
        adapter = FakeDownstreamAdapter()

        with self.assertRaises(Conflict):
            self.coordinator(adapter).execute(
                item["id"], expected_revision=item["revision"]
            )

        self.assertEqual(adapter.calls, [])
        self.assertEqual(self.store.execution_attempts(), [])

    def test_adapter_failure_is_sanitized_auditable_and_does_not_mutate_item(self):
        item = self.approve()
        before = deepcopy(item)
        adapter = FakeDownstreamAdapter(fail_category="sandbox_unavailable")

        with self.assertRaises(DownstreamFailure):
            self.coordinator(adapter).execute(
                item["id"], expected_revision=item["revision"]
            )

        self.assertEqual(self.store.get_item(item["id"]), before)
        attempt = self.store.execution_attempts()[0]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["error_category"], "sandbox_unavailable")
        self.assertNotIn("message", json.dumps(attempt))
        events = self.store.activity(limit=500)
        failure = next(event for event in events if event["action"] == "execution_failed")
        self.assertEqual(failure["payload"]["error_category"], "sandbox_unavailable")
        self.assertNotIn("exception", failure["payload"])

    def test_sandbox_adapter_publishes_one_atomic_receipt_inside_root(self):
        item = self.approve()
        root = Path(self.temp.name) / "sandbox-receipts"
        adapter = SandboxDownstreamAdapter(root)
        result = self.coordinator(adapter).execute(
            item["id"], expected_revision=item["revision"]
        )

        files = list(root.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(list(root.glob("*.tmp")), [])
        receipt = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["execution_key"], result["execution_key"])
        self.assertEqual(receipt["mode"], "sandbox")
        self.assertFalse(receipt["production_effects"])

    def test_finished_execution_ledger_cannot_be_rewritten_or_deleted(self):
        item = self.approve()
        self.coordinator(FakeDownstreamAdapter()).execute(
            item["id"], expected_revision=item["revision"]
        )
        key = self.store.execution_attempts()[0]["execution_key"]
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE v4_execution_attempts SET status='failed' WHERE execution_key=?",
                    (key,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "DELETE FROM v4_execution_attempts WHERE execution_key=?", (key,)
                )


if __name__ == "__main__":
    unittest.main()
