import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch
from src.v4.shadow import ROOT, candidates_from_catalog, compare, read_local_json, targets_from_sonarr

class ShadowRegression(unittest.TestCase):
    def test_shadow_compare_has_no_network_or_writes(self):
        series={"id":1,"title":"Black Lagoon","seasons":[{"seasonNumber":1}]}
        fixture=json.loads((Path(__file__).parent/"fixtures/black_lagoon.json").read_text(encoding="utf-8"))
        targets=targets_from_sonarr(series)
        candidates=candidates_from_catalog(fixture["candidates"],"v4")
        # Import first: ordinary Python import caches are not domain persistence.
        compare(targets,candidates)
        with patch.object(socket.socket,"connect",side_effect=AssertionError("Network forbidden")), patch.object(Path,"write_text",side_effect=AssertionError("Write forbidden")), patch.object(Path,"write_bytes",side_effect=AssertionError("Write forbidden")):
            result=compare(targets,candidates)
        self.assertEqual(1,result["counts"]["v4_matched"])

    def test_scope_cannot_escape_worktree(self):
        with self.assertRaises(ValueError):
            read_local_json("../outside.json")

    def test_legacy_mapping_is_not_verified_season_identity(self):
        payload=[{"title":"Title","seasons":{"1":["https://catalog.example/a"]}}]
        c=candidates_from_catalog(payload,"legacy-table")[0]
        self.assertFalse(c.source.reliable)
        result=compare(targets_from_sonarr({"title":"Title","seasons":[{"seasonNumber":1}]}),(c,))
        self.assertEqual(0,result["counts"]["v4_matched"])
        self.assertEqual(1,result["counts"]["v4_needs_review"])

    def test_cli_real_local_snapshots_preserves_inputs(self):
        series=ROOT/"tests/dump/serie.json";catalog=ROOT/"src/database/table.json"
        if not series.is_file() or not catalog.is_file():
            self.skipTest("V4 local legacy snapshots not available; do not fetch V3 data")
        paths=[series,catalog]
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        done=subprocess.run([sys.executable,"-B","-m","src.v4.shadow","--sonarr-series","tests/dump/serie.json","--catalog","src/database/table.json","--catalog-format","legacy-table"],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual("read_only_shadow",json.loads(done.stdout)["mode"])
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

if __name__=="__main__":
    unittest.main()
