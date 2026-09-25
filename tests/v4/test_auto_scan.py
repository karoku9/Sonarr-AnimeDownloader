from v4_test_support import repo_tempdir
from pathlib import Path
import unittest

from src.v4.runtime import create_runtime

FIXTURE="tests/v4/fixtures/production_metadata_v1.json"


class AutoScanTests(unittest.TestCase):
    def test_scheduler_is_dynamic_and_manual_scan_resets_timer(self):
        with repo_tempdir() as temp:
            runtime=create_runtime(Path(temp)/"app.sqlite3",{"production":FIXTURE})
            runtime.auto_scan.close()
            try:
                settings=runtime.service.store.update_settings(
                    {"auto_scan_enabled":True,"auto_scan_interval_minutes":15})
                self.assertTrue(runtime.auto_scan._due(settings))
                runtime.jobs.start("production")
                job=runtime.jobs.list()[0]
                self.assertIn(job["status"],{"queued","running","completed"})
                self.assertFalse(runtime.auto_scan._due(settings))
            finally:
                runtime.close()


if __name__=="__main__":unittest.main()
