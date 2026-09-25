import pathlib
import threading
from datetime import datetime, timezone
import httpx

from .io import read_json_atomic, write_json_atomic
from .variants import unique_text


class AliasProvider:
	def __init__(self, aliases_path: pathlib.Path, cache_path: pathlib.Path) -> None:
		self.aliases_path = aliases_path
		self.cache_path = cache_path
		self.aliases_path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = threading.Lock()
		self._ensure_file(self.aliases_path, {})
		self._ensure_file(self.cache_path, {"external_aliases": {}})

	def _ensure_file(self, path: pathlib.Path, default: dict) -> None:
		if not path.exists() or path.stat().st_size == 0:
			self._save(path, default)

	def _load(self, path: pathlib.Path, default: dict) -> dict:
		try:
			return read_json_atomic(path, default)
		except (OSError, ValueError):
			return default

	def _save(self, path: pathlib.Path, data: dict) -> None:
		write_json_atomic(path, data)

	def manual_aliases(self, title: str) -> list[str]:
		return unique_text(self._load(self.aliases_path, {}).get(title, []))

	def set_manual_aliases(self, title: str, aliases: list[str]) -> list[str]:
		clean = unique_text(aliases)
		with self._lock:
			data = self._load(self.aliases_path, {})
			if clean:
				data[title] = clean
			else:
				data.pop(title, None)
			self._save(self.aliases_path, data)
		return clean

	def external_aliases(self, title: str, fetch: bool = False) -> list[str]:
		with self._lock:
			cache = self._load(self.cache_path, {"external_aliases": {}})
		cached = cache.setdefault("external_aliases", {}).get(title)
		if cached and not fetch:
			return unique_text(cached.get("aliases", []))
		if not fetch:
			return []
		aliases = self._fetch_jikan_aliases(title)
		if aliases:
			with self._lock:
				cache = self._load(self.cache_path, {"external_aliases": {}})
				cache.setdefault("external_aliases", {})[title] = {
					"aliases": aliases,
					"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
					"provider": "jikan",
				}
				self._save(self.cache_path, cache)
		return aliases

	def _fetch_jikan_aliases(self, title: str) -> list[str]:
		try:
			response = httpx.get(
				"https://api.jikan.moe/v4/anime",
				params={"q": title, "limit": 3},
				timeout=5,
				headers={"User-Agent": "AniDown-v3 Search-V3"},
			)
			response.raise_for_status()
			aliases = []
			for item in response.json().get("data", []):
				aliases.extend([
					item.get("title", ""),
					item.get("title_english", ""),
					item.get("title_japanese", ""),
				])
				aliases.extend(item.get("title_synonyms", []) or [])
			return unique_text(aliases)
		except Exception:
			return []
