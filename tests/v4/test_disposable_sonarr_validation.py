"""Phase 15 disposable-Sonarr validation safety and evidence contracts."""
from v4_test_support import repo_tempdir
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import unittest

from src.v4.disposable_validation import (
    CONFIRMATION,
    EXPECTED_INSTANCE_NAME,
    MARKER,
    DisposableSonarrValidation,
    DisposableValidationConfig,
)


class DisposableValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=repo_tempdir();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();(self.root/".anidown-v4-disposable").write_text(MARKER,encoding="utf-8")
        self.staging=self.root/"staging";self.library=self.root/"library";self.journal=self.root/"journal"
        self.staging.mkdir();self.library.mkdir();self.secret=self.root/"sonarr-api-key"
        self.secret.write_text("disposable_fixture_key_1234\n",encoding="utf-8")

    def server(self,version="4.0.20.3014",instance_name=EXPECTED_INSTANCE_NAME):
        state={"commands":[],"headers":[],"series":0}
        library=self.library
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,value):
                raw=json.dumps(value).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                state["headers"].append(self.headers.get("X-Api-Key"))
                if self.path=="/api/v3/system/status":self.reply(200,{"version":version,"instanceName":instance_name})
                elif self.path=="/api/v3/series/42":state["series"]+=1;self.reply(200,{"id":42,"path":str(library)})
                else:self.reply(404,{"error":"not found"})
            def do_POST(self):
                state["headers"].append(self.headers.get("X-Api-Key"));body=json.loads(self.rfile.read(int(self.headers["Content-Length"])));state["commands"].append(body);self.reply(200,{"id":1,"status":"queued"})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join,3);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server,state

    def manifest(self,origin):
        return {
            "schema_version":1,
            "purpose":"disposable-sonarr-validation",
            "sonarr_origin":origin,
            "expected_version":"4.0.20.3014",
            "api_key_file":str(self.secret),
            "validation_root":str(self.root),
            "staging_root":str(self.staging),
            "library_root":str(self.library),
            "journal_root":str(self.journal),
            "series_id":42,
        }

    def test_expected_identity_obeys_real_sonarr_host_config_naming_constraint(self):
        self.assertEqual(EXPECTED_INSTANCE_NAME,"Sonarr AniDown V4 Disposable Validation")
        words=EXPECTED_INSTANCE_NAME.split()
        self.assertTrue(words and (words[0].casefold()=="sonarr" or words[-1].casefold()=="sonarr"))

    def test_config_requires_exact_confirmation_loopback_marker_and_contained_paths(self):
        server,_=self.server();manifest=self.manifest(f"http://127.0.0.1:{server.server_port}")
        with self.assertRaises(ValueError):DisposableValidationConfig.from_manifest(manifest,"yes")
        with self.assertRaises(ValueError):DisposableValidationConfig.from_manifest({**manifest,"sonarr_origin":"https://sonarr.example.test:8989"},CONFIRMATION)
        with self.assertRaises(ValueError):DisposableValidationConfig.from_manifest({**manifest,"library_root":str(self.root.parent)},CONFIRMATION)
        (self.root/".anidown-v4-disposable").write_text("wrong",encoding="utf-8")
        with self.assertRaises(ValueError):DisposableValidationConfig.from_manifest(manifest,CONFIRMATION)

    def test_run_moves_one_fixture_artifact_rescans_once_and_proves_fresh_adapter_lookup(self):
        server,state=self.server();manifest=self.manifest(f"http://127.0.0.1:{server.server_port}")
        config=DisposableValidationConfig.from_manifest(manifest,CONFIRMATION)
        evidence=DisposableSonarrValidation(config).run()

        self.assertEqual(evidence["status"],"completed");self.assertEqual(evidence["sonarr_version"],"4.0.20.3014")
        self.assertEqual(evidence["artifact_count"],1);self.assertEqual(evidence["fresh_adapter_lookup"],"completed")
        self.assertNotIn("restart_lookup",evidence)
        self.assertFalse(evidence["production_effects"]);self.assertEqual(state["series"],1)
        self.assertEqual(state["commands"],[{"name":"RescanSeries","seriesId":42}])
        self.assertEqual(len(list(self.library.iterdir())),1)
        encoded=json.dumps(evidence,sort_keys=True)
        self.assertNotIn("disposable_fixture_key_1234",encoded);self.assertNotIn(str(self.root),encoded)
        self.assertTrue(state["headers"] and all(value=="disposable_fixture_key_1234" for value in state["headers"]))

    def test_version_mismatch_fails_before_series_journal_or_artifact_effects(self):
        server,state=self.server(version="4.0.14.0");manifest=self.manifest(f"http://127.0.0.1:{server.server_port}")
        config=DisposableValidationConfig.from_manifest(manifest,CONFIRMATION)
        with self.assertRaises(ValueError):DisposableSonarrValidation(config).run()
        self.assertEqual(state["series"],0);self.assertEqual(state["commands"],[])
        self.assertFalse(self.journal.exists());self.assertEqual(list(self.library.iterdir()),[])

    def test_wrong_remote_instance_identity_fails_before_every_effect(self):
        server,state=self.server(instance_name="Current Sonarr");manifest=self.manifest(f"http://127.0.0.1:{server.server_port}")
        config=DisposableValidationConfig.from_manifest(manifest,CONFIRMATION)
        with self.assertRaises(ValueError):DisposableSonarrValidation(config).run()
        self.assertEqual(state["series"],0);self.assertEqual(state["commands"],[])
        self.assertFalse(self.journal.exists());self.assertEqual(list(self.library.iterdir()),[])


if __name__=="__main__":unittest.main()
