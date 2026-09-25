"""Provider observation caching and approval-time source validation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from .production_sources import fetch_detail, public_url


POSITIVE_TTL = timedelta(hours=6)
NOT_FOUND_TTL = timedelta(hours=1)
FAILURE_TTL = timedelta(minutes=5)


def _utc(value=None):
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _utc(parsed)
    except ValueError:
        return None


def detail_audio(detail):
    fields = {row.get("label"): row.get("value") for row in detail.get("raw", {}).get("fields", [])}
    value = str(fields.get("Audio:") or "").strip().casefold()
    if value == "italiano":
        return "DUB"
    if value in {"giapponese", "japanese"}:
        return "SUB"
    return "UNKNOWN"


def detail_fingerprint(detail):
    raw = detail.get("raw") or {}
    relevant = {
        "status": detail.get("status"),
        "canonical_url": raw.get("url"),
        "title": raw.get("title"),
        "fields": raw.get("fields") or [],
        "structured": raw.get("structured") or [],
        "available_episode_numbers": raw.get("available_episode_numbers") or [],
        "content_sha256": detail.get("content_sha256"),
    }
    encoded = json.dumps(relevant, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ttl_for(detail):
    if detail.get("status") == "ok":
        return POSITIVE_TTL
    if detail.get("status") == "metadata_not_found" or detail.get("http_status") == 404:
        return NOT_FOUND_TTL
    return FAILURE_TTL


def observed(detail, *, now=None, validation_status="fresh"):
    value = dict(detail)
    current = _utc(now)
    fetched = _parse(value.get("fetched_at")) or current
    value["fetched_at"] = fetched.isoformat()
    value.setdefault("source_fingerprint", detail_fingerprint(value))
    value.setdefault("next_validation_at", (fetched + ttl_for(value)).isoformat())
    value.setdefault("validation_status", validation_status)
    return value


def is_due(detail, *, now=None):
    return (_parse((detail or {}).get("next_validation_at")) or datetime.min.replace(tzinfo=timezone.utc)) <= _utc(now)


class ProviderDetailCache:
    def __init__(self, root, *, fetcher=fetch_detail, clock=None):
        self.root = Path(root).resolve()
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _path(self, url):
        return self.root / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")

    def _read(self, url, seed_details=None):
        path = self._path(url)
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return observed(value, now=self.clock(), validation_status=value.get("validation_status", "cached"))
            except (OSError, ValueError):
                pass
        seed = (seed_details or {}).get(url)
        return observed(seed, now=self.clock(), validation_status="seed") if isinstance(seed, dict) else None

    def peek(self, url, seed_details=None):
        public_url(url)
        return self._read(url, seed_details)

    def _write(self, url, value):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(url)
        temporary = path.with_suffix(path.suffix + ".tmp")
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def get(self, url, seed_details=None, *, force=False, allow_stale_success=True):
        public_url(url)
        current = self._read(url, seed_details)
        now = _utc(self.clock())
        if current and not force and not is_due(current, now=now):
            return current
        fresh = observed(self.fetcher(url), now=now)
        if fresh.get("status") == "fetch_failed" and current and current.get("status") == "ok" and allow_stale_success:
            stale = dict(current)
            stale["validation_status"] = "stale_fetch_failed"
            stale["last_validation_error"] = {
                "fetched_at": fresh["fetched_at"],
                "error_type": fresh.get("error_type"),
                "http_status": fresh.get("http_status"),
            }
            stale["next_validation_at"] = (now + FAILURE_TTL).isoformat()
            self._write(url, stale)
            return stale
        self._write(url, fresh)
        return fresh


class ProviderSourceValidator:
    """Fetch every manually approved source live and return bound evidence."""

    def __init__(self, *, fetcher=fetch_detail, clock=None):
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def validate(self, urls, expected_audio, episode_map):
        if expected_audio not in {"SUB", "DUB"}:
            raise ValueError("Manual approval requires an explicit SUB or DUB audio mode")
        documents = []
        for url in urls:
            public_url(url)
            detail = observed(self.fetcher(url), now=self.clock())
            if detail.get("status") != "ok":
                raise ValueError("Manual source is not currently available")
            canonical = detail.get("raw", {}).get("url")
            if not canonical:
                raise ValueError("Manual source has no canonical provider identity")
            public_url(canonical)
            audio = detail_audio(detail)
            if audio != expected_audio:
                raise ValueError("Manual source audio does not match the approved series audio")
            documents.append({
                "url": url,
                "canonical_url": canonical,
                "fingerprint": detail["source_fingerprint"],
                "audio": audio,
                "available_episode_numbers": sorted(set(detail.get("raw", {}).get("available_episode_numbers") or [])),
                "validated_at": detail["fetched_at"],
            })
        for row in episode_map:
            available = documents[row["source_index"]]["available_episode_numbers"]
            if row["source_episode"] not in available:
                raise ValueError("Manual source episode is not currently available")
        return documents
