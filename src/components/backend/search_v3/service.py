import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

from .aliases import AliasProvider
from .catalog import AnimeWorldCatalogBuilder
from .diagnostics import SearchDiagnostics
from .index_store import SearchIndexStore
from .io import read_json_atomic, write_json_atomic
from .language import LanguagePreferenceResolver
from .scorer import CandidateScorer
from .variants import TitleVariantBuilder, normalize_title


class SearchV3Service:
	def __init__(self, database_folder: pathlib.Path, base_url: str, table, logger: logging.Logger | None = None, events=None) -> None:
		self.database_folder = database_folder
		self.log = logger or logging.getLogger(__name__)
		self.variant_builder = TitleVariantBuilder()
		self.alias_provider = AliasProvider(
			database_folder.joinpath("search_v3_aliases.json"),
			database_folder.joinpath("search_v3_cache.json"),
		)
		self.index_store = SearchIndexStore(database_folder.joinpath("search_v3_index.json"), base_url)
		self.catalog_builder = AnimeWorldCatalogBuilder(self.index_store, base_url, table, self.log)
		self.scorer = CandidateScorer()
		self.language_resolver = LanguagePreferenceResolver()
		self.events = events
		self.diagnostics = SearchDiagnostics(database_folder.joinpath("search_v3_debug.json"), self.log, events)
		self.preferences_path = database_folder.joinpath("mapping_preferences.json")
		self._cache_lock = self.alias_provider._lock
		self.catalog_builder.seed_table_entries()

	def index_status(self) -> dict[str, Any]:
		return self.index_store.status()

	def refresh_index(self) -> dict[str, Any]:
		outcome = self.catalog_builder.build()
		return {
			"search_engine": "v3",
			"index": self.index_status(),
			"refreshed": outcome["refreshed"],
			"errors": outcome["errors"],
		}

	def global_language(self) -> str:
		try:
			return read_json_atomic(self.preferences_path, {}).get("global_language_preference", "AUTO")
		except (OSError, ValueError, AttributeError):
			return "AUTO"

	def per_series_language(self, title: str) -> str:
		try:
			data = read_json_atomic(self.preferences_path, {})
			return data.get("series", {}).get(title, {}).get("language_preference", "AUTO")
		except (OSError, ValueError, AttributeError):
			return "AUTO"

	def search(
		self,
		series_or_title: dict[str, Any] | str,
		language_preference: str = "AUTO",
		season: str | int | None = None,
		debug: bool = False,
		refresh_index: bool = False,
		fetch_external_aliases: bool = False,
		auto_refresh: bool = True,
	) -> dict[str, Any]:
		title = series_or_title if isinstance(series_or_title, str) else str(series_or_title.get("title", "") or "")
		operation_id = self.events.operation_id("search_v3", title, season) if self.events else None
		errors = []
		if refresh_index:
			errors.extend(self.refresh_index()["errors"])
		elif auto_refresh:
			status = self.index_status()
			if status["size"] == 0 or status["generated_at"] is None or (status["age_hours"] is not None and status["age_hours"] >= 24):
				try:
					errors.extend(self.refresh_index()["errors"])
				except Exception as error:
					errors.append(f"Index auto-refresh failed: {error}")
		manual_aliases = self.alias_provider.manual_aliases(title)
		external_aliases = self.alias_provider.external_aliases(title, fetch=fetch_external_aliases)
		variants = self.variant_builder.build(series_or_title, manual_aliases + external_aliases, season=season)
		data = self.index_store.load()
		candidates = self.scorer.score_all(
			data.get("entries", []),
			variants["normalized_variants"],
			manual_aliases,
			external_aliases,
			sonarr_aliases=variants["sonarr_aliases"],
			season_aliases=variants["season_aliases"],
			debug=debug,
		)
		candidates, excluded_candidates = self.filter_candidates_for_season(candidates, season)
		requested = language_preference if language_preference != "AUTO" else self.per_series_language(title)
		effective_preference = self.language_resolver.effective(requested, self.global_language())
		ranked = self.language_resolver.apply(candidates, requested, self.global_language())
		top_candidates = ranked[:20 if debug else 12]
		qualified_candidates = [candidate for candidate in top_candidates if candidate["score"] >= 0.50]
		selected, status = self.select_candidate(qualified_candidates, effective_preference)
		if not data.get("entries"):
			status = "index_empty"
			errors.append("Search V3 index is empty. Refresh the index before searching.")
		elif not qualified_candidates:
			status = "no_result"
			errors.append("Index loaded, but no candidate scored at least 0.50 for this query; low-score diagnostic candidates may be shown.")
		result = {
			"search_engine": "v3",
			"operation_id": operation_id,
			"query": title,
			"normalized_query": normalize_title(title),
			"index": self.index_status(),
			"title_variants": variants["raw_variants"],
			"normalized_variants": variants["normalized_variants"],
			"sonarr_aliases": variants["sonarr_aliases"],
			"season_aliases": variants["season_aliases"],
			"manual_aliases": manual_aliases,
			"external_aliases": external_aliases,
			"top_candidates": top_candidates,
			"excluded_candidates": excluded_candidates[:20 if debug else 8],
			"selected_candidate": selected,
			"status": status,
			"errors": errors,
		}
		self.cache_result(title, result)
		self.diagnostics.record(result, season, effective_preference)
		return result

	def filter_candidates_for_season(self, candidates: list[dict[str, Any]], season: str | int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
		if season is None:
			return candidates, []
		season_value = str(season)
		eligible = []
		excluded = []
		for candidate in candidates:
			table_seasons = {str(value) for value in (candidate.get("table_seasons", []) or [])}
			from_table_match = candidate.get("matched_field") in ("table", "table_alias")
			if from_table_match and table_seasons and season_value not in table_seasons:
				item = dict(candidate)
				item["excluded_reason"] = f"table URL belongs to season(s) {', '.join(sorted(table_seasons))}, not season {season_value}"
				excluded.append(item)
				continue
			eligible.append(candidate)
		return eligible, excluded

	def select_candidate(self, candidates: list[dict[str, Any]], preference: str = "AUTO") -> tuple[dict[str, Any] | None, str]:
		if not candidates:
			return None, "no_result"
		best = candidates[0]
		if best["confidence"] != "high":
			return None, "needs_review"
		if len(candidates) > 1 and candidates[1]["confidence"] == "high" and candidates[1]["score"] == best["score"]:
			if best.get("season_match") and not candidates[1].get("season_match"):
				return best, "matched"
			if preference == "DUB_FIRST" and best.get("audio") == "DUB":
				return best, "matched"
			if preference == "SUB_FIRST" and best.get("audio") != "DUB":
				return best, "matched"
			return None, "needs_review"
		return best, "matched"

	def cache_result(self, title: str, result: dict[str, Any]) -> None:
		path = self.alias_provider.cache_path
		with self._cache_lock:
			try:
				cache = read_json_atomic(path, {"external_aliases": {}})
			except (OSError, ValueError):
				cache = {"external_aliases": {}}
			cache.setdefault("searches", {})[title] = {
				"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
				"status": result["status"],
				"top_candidates": result["top_candidates"],
			}
			write_json_atomic(path, cache)
