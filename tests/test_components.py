"""Opt-in legacy integration smoke against a disposable loopback Sonarr only.

Normal test discovery performs no network I/O and imports no legacy runtime.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit
import unittest


def _disposable_configuration():
    if os.getenv("ANIDOWN_LEGACY_INTEGRATION_OPT_IN") != "DISPOSABLE_LOOPBACK_ONLY":
        return None
    origin = os.getenv("ANIDOWN_LEGACY_SONARR_URL", "").strip().rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or parsed.port is None:
        raise RuntimeError("Opt-in endpoint must be disposable and loopback-only")
    key_file = Path(os.getenv("ANIDOWN_LEGACY_SONARR_KEY_FILE", "")).resolve()
    if not key_file.is_file():
        raise RuntimeError("Opt-in integration requires a disposable credential file")
    key = key_file.read_text(encoding="utf-8").strip()
    if not 16 <= len(key) <= 512:
        raise RuntimeError("Disposable credential is invalid")
    return origin, key


class LegacyIntegrationSafetyTests(unittest.TestCase):
    def test_default_discovery_has_no_live_configuration(self):
        if os.getenv("ANIDOWN_LEGACY_INTEGRATION_OPT_IN") is None:
            self.assertIsNone(_disposable_configuration())


@unittest.skipUnless(
    os.getenv("ANIDOWN_LEGACY_INTEGRATION_OPT_IN") == "DISPOSABLE_LOOPBACK_ONLY",
    "requires explicit disposable loopback Sonarr opt-in",
)
class DisposableLegacyIntegrationTests(unittest.TestCase):
    def test_disposable_sonarr_status(self):
        origin, key = _disposable_configuration()
        from src.components.backend.core import Constant as ctx
        from src.components.backend.connection.Sonarr import Sonarr

        ctx.SONARR_URL = origin
        ctx.API_KEY = key
        response = Sonarr().systemStatus()
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
