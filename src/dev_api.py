"""Legacy development API, quarantined behind disposable-loopback opt-in.

This module is excluded from every V4 artifact. It deliberately has no defaults
for a Sonarr origin or credential and never binds an all-interface listener.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit


def _legacy_configuration():
    if os.getenv("ANIDOWN_LEGACY_INTEGRATION_OPT_IN") != "DISPOSABLE_LOOPBACK_ONLY":
        raise RuntimeError("Legacy integration requires explicit disposable opt-in")
    origin = os.getenv("ANIDOWN_LEGACY_SONARR_URL", "").strip().rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or parsed.port is None:
        raise RuntimeError("Legacy integration requires an explicit loopback Sonarr endpoint")
    key_file = Path(os.getenv("ANIDOWN_LEGACY_SONARR_KEY_FILE", "")).resolve()
    if not key_file.is_file():
        raise RuntimeError("Legacy integration requires a disposable credential file")
    key = key_file.read_text(encoding="utf-8").strip()
    if not 16 <= len(key) <= 512:
        raise RuntimeError("Legacy integration credential is invalid")
    return origin, key


def main():
    origin, key = _legacy_configuration()
    from components.api import API
    from components.backend.core.Core import Core, ctx

    ctx.DOWNLOAD_FOLDER = Path("./tests/downloads").absolute()
    ctx.DATABASE_FOLDER = Path("./tests/database").absolute()
    ctx.SCRIPT_FOLDER = Path("./tests/script").absolute()
    ctx.SONARR_URL = origin
    ctx.API_KEY = key
    ctx.VERSION = "dev-disposable"
    return API(Core())


if __name__ == "__main__":
    main().run(debug=False, host="127.0.0.1", use_reloader=False)
