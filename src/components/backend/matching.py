import pathlib
import re
import unicodedata
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable

from .mapping import normalize_title, source_title_from_url
try:
	from .atomic_json import read_json_durable, write_json_durable
except ImportError:  # Legacy isolated-module test harness.
	import importlib.util
	_atomic_spec = importlib.util.spec_from_file_location("anidown_legacy_atomic_json", pathlib.Path(__file__).with_name("atomic_json.py"))
	_atomic_json = importlib.util.module_from_spec(_atomic_spec)
	_atomic_spec.loader.exec_module(_atomic_json)
	read_json_durable = _atomic_json.read_json_durable
	write_json_durable = _atomic_json.write_json_durable


def empty_metadata() -> dict[str, Any]:
	return {"aliases": {}}


def empty_cache() -> dict[str, Any]:
	return {"searches": {}}


class JsonStore:
	def __init__(self, path: pathlib.Path, default_factory: Callable[[], dict[str, Any]]) -> None:
		self.path = path
		self.default_factory = default_factory
		self.path.parent.mkdir(parents=True, exist_ok=True)
		if not self.path.exists() or self.path.stat().st_size == 0:
			self.write(default_factory())
		self.data = self.read()
		self.fix()

	def read(self) -> dict[str, Any]:
		return read_json_durable(self.path)

	def write(self, data: dict[str, Any]) -> None:
		write_json_durable(self.path, data)

	def sync(self) -> None:
		self.write(self.data)

	def fix(self) -> None:
		changed = False
		defaults = self.default_factory()
		for key, value in defaults.items():
			if key not in self.data:
				self.data[key] = value
				changed = True
		if changed:
			self.sync()


class MappingMetadata(JsonStore):
	def __init__(self, path: pathlib.Path) -> None:
		super().__init__(path, empty_metadata)

	def aliases_for(self, title: str) -> list[str]:
		return list(self.data.get("aliases", {}).get(title, []))

	def set_aliases(self, title: str, aliases: list[str]) -> None:
		clean = unique([alias.strip() for alias in aliases if alias and alias.strip()])
		aliases_map = self.data.setdefault("aliases", {})
		if clean:
			aliases_map[title] = clean
		else:
			aliases_map.pop(title, None)
		self.sync()

	def add_alias(self, title: str, alias: str) -> None:
		aliases = self.aliases_for(title)
		if alias and alias not in aliases:
			aliases.append(alias)
			self.set_aliases(title, aliases)


class SearchCache(JsonStore):
	def __init__(self, path: pathlib.Path) -> None:
		super().__init__(path, empty_cache)

	def get(self, key: str) -> dict[str, Any] | None:
		return deepcopy(self.data.get("searches", {}).get(key))

	def set(self, key: str, value: dict[str, Any]) -> None:
		self.data.setdefault("searches", {})[key] = value
		self.sync()


def unique(values: list[str]) -> list[str]:
	seen = set()
	result = []
	for value in values:
		if not value:
			continue
		key = normalize_title(value)
		if key and key not in seen:
			seen.add(key)
			result.append(value)
	return result


def ascii_variant(title: str) -> str:
	return unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")


def strip_subtitle(title: str) -> str:
	return re.split(r"\s*[:;|]\s*", title, maxsplit=1)[0].strip()


def strip_suffixes(title: str) -> str:
	value = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
	value = re.sub(r"\s*[-–—]\s*(season|part)\s*\d+\s*$", "", value, flags=re.I).strip()
	value = re.sub(r"\s+\d+(st|nd|rd|th)?\s+season\s*$", "", value, flags=re.I).strip()
	return value


def without_leading_article(title: str) -> str:
	return re.sub(r"^(the|a|an)\s+", "", title, flags=re.I).strip()


def without_punctuation(title: str) -> str:
	return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title, flags=re.UNICODE)).strip()


def collect_sonarr_titles(series: dict[str, Any], metadata: MappingMetadata | None = None) -> tuple[list[str], list[str]]:
	title = series.get("title", "")
	values = [
		title,
		series.get("cleanTitle", ""),
		series.get("sortTitle", ""),
		series.get("originalTitle", ""),
	]
	for alt in series.get("alternateTitles", []) or []:
		if isinstance(alt, dict):
			values.append(alt.get("title", ""))
		else:
			values.append(str(alt))
	aliases = metadata.aliases_for(title) if metadata else []
	values.extend(aliases)
	return unique(values), aliases


def generate_title_queries(series_or_title: dict[str, Any] | str, metadata: MappingMetadata | None = None) -> dict[str, Any]:
	if isinstance(series_or_title, str):
		series = {"title": series_or_title}
	else:
		series = series_or_title
	titles, aliases = collect_sonarr_titles(series, metadata)
	candidates: list[str] = []
	for title in titles:
		candidates.extend([
			title,
			without_punctuation(title),
			strip_subtitle(title),
			strip_suffixes(title),
			without_leading_article(strip_suffixes(strip_subtitle(title))),
			normalize_title(title),
			ascii_variant(title),
		])
	return {
		"title": series.get("title", ""),
		"aliases": aliases,
		"queries": unique(candidates),
	}


def score_result(query: str, result: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
	name = result.get("name", "") or source_title_from_url(result.get("url", result.get("link", "")))
	normalized_name = normalize_title(name)
	normalized_query = normalize_title(query)
	alias_keys = [normalize_title(alias) for alias in aliases]
	if normalized_name == normalized_query:
		confidence = 0.98
		match_type = "exact"
	elif normalized_name in alias_keys or normalized_query in alias_keys:
		confidence = 0.95
		match_type = "alias"
	else:
		ratio = SequenceMatcher(None, normalized_query, normalized_name).ratio()
		contains = normalized_query in normalized_name or normalized_name in normalized_query
		confidence = max(ratio, 0.74 if contains else 0)
		match_type = "partial" if confidence >= 0.7 else "weak"
	status = "auto_matched" if confidence >= 0.9 else "needs_review" if confidence >= 0.62 else "low_confidence"
	return {
		"name": name,
		"url": result.get("url", result.get("link", "")),
		"query": query,
		"confidence": round(confidence, 3),
		"match_type": match_type,
		"status": status,
	}


def summarize_search(title: str, queries: list[str], aliases: list[str], matches: list[dict[str, Any]], from_cache: bool = False) -> dict[str, Any]:
	best = matches[0] if matches else None
	status = "no_result"
	reason = "OLD_SEARCH_PATH_USED: legacy matcher had no candidates; inspect Search V3 diagnostics."
	if len(matches) > 1 and best and best["confidence"] < 0.9:
		status = "multiple_possible_matches"
		reason = "Multiple possible matches require review."
	elif best and best["confidence"] >= 0.9:
		status = "auto_matched"
		reason = "High confidence normalized or alias match."
	elif best:
		status = "needs_review"
		reason = "Best match is not high-confidence enough to auto-select."
	return {
		"title": title,
		"queries": queries,
		"aliases": aliases,
		"matches": matches,
		"best": best,
		"status": status,
		"reason": reason,
		"from_cache": from_cache,
		"searched_at": datetime.now().isoformat(timespec="seconds"),
	}
