from typing import Any

from .mapping import valid_url
from .search_v3.variants import TitleVariantBuilder, normalize_title


class MappingResolver:
	"""Resolves an existing table mapping before any automatic title search."""

	def __init__(self, table, alias_provider=None, preferences=None, logger=None, events=None) -> None:
		self.table = table
		self.alias_provider = alias_provider
		self.preferences = preferences
		self.log = logger
		self.events = events
		self.variant_builder = TitleVariantBuilder()

	def _requested_titles(self, series_or_title: dict[str, Any] | str) -> list[str]:
		title, sonarr_aliases = self.variant_builder.collect_titles(series_or_title)
		titles = [title] + sonarr_aliases
		if self.alias_provider and title:
			titles.extend(self.alias_provider.manual_aliases(title))
		metadata = self.preferences.data.get("series", {}).get(title, {}) if self.preferences else {}
		titles.extend(metadata.get("aliases", []) or [])
		for key in ("canonical_sonarr_title", "sonarr_title"):
			if metadata.get(key):
				titles.append(str(metadata[key]))
		return [value for value in titles if str(value).strip()]

	def _entry_titles(self, entry: dict[str, Any]) -> list[str]:
		title = str(entry.get("title", "")).strip()
		titles = [title]
		if self.alias_provider and title:
			titles.extend(self.alias_provider.manual_aliases(title))
		metadata = self.preferences.data.get("series", {}).get(title, {}) if self.preferences else {}
		titles.extend(metadata.get("aliases", []) or [])
		for key in ("canonical_sonarr_title", "sonarr_title"):
			if metadata.get(key):
				titles.append(str(metadata[key]))
		return [value for value in titles if str(value).strip()]

	def matching_entries(self, series_or_title: dict[str, Any] | str, data: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
		entries = data if data is not None else self.table.getData()
		requested = self._requested_titles(series_or_title)
		title = series_or_title if isinstance(series_or_title, str) else str(series_or_title.get("title", ""))
		exact = [entry for entry in entries if entry.get("title") == title]
		keys = {normalize_title(value) for value in requested if normalize_title(value)}
		others = [
			entry for entry in entries
			if entry not in exact and keys.intersection({normalize_title(value) for value in self._entry_titles(entry)})
		]
		return exact + others

	def find_entry(self, series_or_title: dict[str, Any] | str, data: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
		entries = self.matching_entries(series_or_title, data)
		return entries[0] if entries else None

	def resolve_existing_mapping(
		self,
		series_or_title: dict[str, Any] | str,
		season: str | int,
		*,
		force_rematch: bool = False,
		log_skip: bool = False,
	) -> dict[str, Any]:
		title = series_or_title if isinstance(series_or_title, str) else str(series_or_title.get("title", ""))
		season_key = str(season)
		if force_rematch:
			return {"mapped": False, "should_search": True, "reason": "force_rematch", "title": title, "season": season_key, "urls": []}
		entries = self.matching_entries(series_or_title)
		if not entries:
			return {"mapped": False, "should_search": True, "reason": "series_missing", "title": title, "season": season_key, "urls": []}
		for entry in entries:
			metadata = self.preferences.data.get("series", {}).get(str(entry.get("title", "")), {}) if self.preferences else {}
			if metadata.get("needs_review") or metadata.get("invalid"):
				return {"mapped": False, "should_search": True, "reason": "needs_review", "title": title, "season": season_key, "entry": entry, "urls": []}
			seasons = entry.get("seasons", {}) if isinstance(entry.get("seasons", {}), dict) else {}
			if season_key not in seasons:
				continue
			urls = [str(url).strip() for url in (seasons.get(season_key) or []) if str(url).strip() and valid_url(str(url).strip())]
			if urls:
				result = {
					"mapped": True,
					"should_search": False,
					"reason": "existing_valid_mapping",
					"title": title,
					"canonical_title": entry.get("title"),
					"season": season_key,
					"entry": entry,
					"urls": urls,
					"urls_count": len(urls),
				}
				if log_skip:
					self._log_skip(result)
				return result
		reason = "season_missing" if not any(season_key in (entry.get("seasons", {}) or {}) for entry in entries) else "empty_or_invalid_urls"
		return {"mapped": False, "should_search": True, "reason": reason, "title": title, "season": season_key, "entry": entries[0], "urls": []}

	def _log_skip(self, result: dict[str, Any]) -> None:
		if self.log:
			self.log.info(
				"MAPPING_ALREADY_EXISTS_SKIP_SEARCH\n"
				f"  title={result['title']}\n"
				f"  season={result['season']}\n"
				f"  urls_count={result['urls_count']}\n"
				"  reason=existing_valid_mapping"
			)
		if self.events:
			self.events.record({
				"level": "INFO",
				"type": "MAPPING",
				"event": "MAPPING_ALREADY_EXISTS_SKIP_SEARCH",
				"status": "info",
				"title": result["title"],
				"season": result["season"],
				"compact": f"{result['title']} S{result['season']} | already mapped | Search V3 skipped",
				"message": "Existing valid mapping used; automatic search was skipped.",
				"details": result,
			})
