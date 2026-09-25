import unittest
from src.v4.dataset import build_dataset,public_url
from src.v4.meaningful_shadow import compare_dataset,markdown

class DatasetTests(unittest.TestCase):
    def test_sanitized_significant_dataset(self):
        data=build_dataset()
        self.assertGreaterEqual(len(data["cases"]),90)
        encoded=str(data)
        for forbidden in ("apiKey", "rootFolderPath", "qualityProfileId", "credentials"):
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(sum(c["cohort"]=="synthetic_regression" for c in data["cases"]),7)
        self.assertTrue(all(not c["target"]["source"]["reliable"] for c in data["cases"] if c["cohort"]=="documentation_observation"))

    def test_report_regressions_and_evidence(self):
        report=compare_dataset(build_dataset())
        self.assertEqual(report["counts"]["synthetic_wrong_selections"],0)
        self.assertEqual(report["counts"]["synthetic_abstentions"],0)
        black=next(r for r in report["targets"] if r["target"]=="Black Lagoon")
        wrong=next(c for c in black["excluded"] if c["candidate"]["id"]=="bl2")
        self.assertTrue(wrong["evaluation"]["hard_rejected"])
        self.assertIn("season_conflict",wrong["evaluation"]["reason_codes"])
        black2=next(r for r in report["targets"] if r["target"]=="Black Lagoon" and r["season"]==2)
        self.assertEqual(black2["selected"]["id"],"bl2")
        self.assertTrue(any(e["signal"]=="season_crosswalk" and e["outcome"]=="match" for e in black2["v4"]["evaluations"][1]["evidence"]))
        shared=next(r for r in report["targets"] if any(c["candidate"]["url"].endswith("crystal-shared") for c in r["excluded"]))
        self.assertTrue(shared["review_required"])
        self.assertIn("duplicate_url_across_seasons",shared["v4"]["reason_codes"])
        self.assertIn("source=",markdown(report))

    def test_url_sanitization(self):
        self.assertEqual(public_url("https://www.animeworld.tv/play/test?key=secret#private"),"https://www.animeworld.tv/play/test")
        with self.assertRaises(ValueError):
            public_url("https://user:pass@www.animeworld.tv/play/test")
