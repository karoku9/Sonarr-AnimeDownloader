from v4_test_support import repo_tempdir
import unittest
from pathlib import Path
from src.v4.models import Target, Candidate, Review
from src.v4.matching import match
from src.v4.review_store import ReviewRepository

class ReviewStoreTests(unittest.TestCase):
    def test_lifecycle_and_original_snapshot(self):
        with repo_tempdir() as folder:
            path=Path(folder)/"reviews.sqlite3"
            target=Target("Unknown",season_number=1)
            candidates=(Candidate("c","Unknown","https://catalog.example/c"),)
            review=Review.from_decision("r",target,candidates,match(target,candidates))
            repo=ReviewRepository(path)
            repo.create(review)
            repo.update("r","Checked metadata",expected_revision=0)
            with self.assertRaises(ValueError):
                repo.dismiss("r","stale",expected_revision=0)
            repo.resolve("r",None,"No valid release",expected_revision=1)
            saved=ReviewRepository(path).get("r")
            self.assertEqual(saved["original"],review.to_dict())
            self.assertEqual(saved["state"],"resolved")
            self.assertEqual(saved["resolution"]["reason"],"No valid release")
            self.assertEqual(repo.list_open(),[])
            with self.assertRaises(ValueError):
                repo.update("r","overwrite",expected_revision=2)

    def test_dismiss_and_invalid_resolution(self):
        with repo_tempdir() as folder:
            repo=ReviewRepository(Path(folder)/"r.sqlite3")
            t=Target("Title",season_number=1)
            c=(Candidate("wrong","Title", "https://catalog.example/wrong",season_number=2),)
            r=Review.from_decision("r",t,c,match(t,c))
            repo.create(r)
            with self.assertRaises(ValueError):
                repo.resolve("r","wrong","unsafe",expected_revision=0)
            repo.dismiss("r","Manually dismissed",expected_revision=0)
            self.assertEqual(repo.get("r")["original"],r.to_dict())
            self.assertEqual(repo.get("r")["state"],"dismissed")

    def test_external_storage_rejected(self):
        with self.assertRaises(ValueError):
            ReviewRepository(Path(__file__).resolve().parents[3]/"outside.sqlite3")
