import pathlib
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .mapping import MappingPreferences, create_backup, normalize_title, normalize_url, season_sort_key, source_title_from_url, valid_url
from .mapping_resolver import MappingResolver
from .sonarr_eligibility import active_tag_ids, is_anime_series


class MappingAutomation:
	"""Persists confirmed Search V3 mappings and performs metadata-only Sonarr parses."""

	def __init__(self, database_folder: pathlib.Path, table, search_v3, runtime_settings, sonarr, settings, tags, logger, events=None) -> None:
		self.database_folder = database_folder
		self.table = table
		self.search_v3 = search_v3
		self.runtime_settings = runtime_settings
		self.sonarr = sonarr
		self.settings = settings
		self.tags = tags
		self.log = logger
		self.events = events
		self.preferences = MappingPreferences(database_folder.joinpath("mapping_preferences.json"))
		self.resolver = MappingResolver(table, getattr(search_v3, "alias_provider", None), self.preferences, logger, events)
		self.lock = threading.RLock()

	def _entry_for_title(self, title: str, data: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
		return self.resolver.find_entry(title, data)

	def is_mapped(self, title: str, season: str | int) -> bool:
		return not self.resolver.resolve_existing_mapping(title, season)["should_search"]

	def _is_high_confidence(self, result: dict[str, Any]) -> bool:
		candidate = result.get("selected_candidate")
		min_score = self.runtime_settings.status()["auto_mapping_min_score"]
		return bool(
			result.get("status") == "matched"
			and candidate
			and candidate.get("confidence") == "high"
			and float(candidate.get("score", 0)) >= min_score
		)

	def existing_result(self, title: str, season: str | int, resolution: dict[str, Any]) -> dict[str, Any]:
		return {
			"search_engine": "v3",
			"query": title,
			"status": "already_mapped",
			"skipped_search": True,
			"existing_mapping": resolution,
			"selected_candidate": None,
			"top_candidates": [],
			"title_variants": [],
			"errors": [],
			"index": self.search_v3.index_status() if hasattr(self.search_v3, "index_status") else {"size": 0, "age_hours": None},
		}

	def search_mapping(
		self,
		series_or_title: dict[str, Any] | str,
		season: str | int,
		*,
		language_preference: str = "AUTO",
		force_rematch: bool = False,
		debug: bool = False,
		auto_refresh: bool = True,
		refresh_index: bool = False,
		fetch_external_aliases: bool = False,
	) -> dict[str, Any]:
		title = series_or_title if isinstance(series_or_title, str) else str(series_or_title.get("title", ""))
		resolution = self.resolver.resolve_existing_mapping(series_or_title, season, force_rematch=force_rematch, log_skip=True)
		if not resolution["should_search"]:
			return self.existing_result(title, season, resolution)
		return self.search_v3.search(
			series_or_title,
			language_preference=language_preference,
			season=season,
			debug=debug,
			auto_refresh=auto_refresh,
			refresh_index=refresh_index,
			fetch_external_aliases=fetch_external_aliases,
		)

	def _allow_shared_urls(self, title: str, entry: dict[str, Any] | None = None, option: bool = False) -> bool:
		metadata = self.preferences.data.get("series", {}).get(title, {})
		entry_metadata = self.preferences.data.get("series", {}).get(str((entry or {}).get("title", "")), {})
		return bool(option or (entry and entry.get("absolute", False)) or metadata.get("allow_shared_url_across_seasons", False) or entry_metadata.get("allow_shared_url_across_seasons", False))

	def _shared_url_conflict(self, title: str, season: str | int, url: str, option: bool = False) -> dict[str, Any] | None:
		entry = self._entry_for_title(title)
		if not entry or self._allow_shared_urls(title, entry, option):
			return None
		target_url = normalize_url(url)
		if not target_url:
			return None
		matched_seasons = []
		for key, urls in (entry.get("seasons", {}) or {}).items():
			if str(key) == str(season):
				continue
			if any(normalize_url(value) == target_url for value in (urls or [])):
				matched_seasons.append(str(key))
		if not matched_seasons:
			return None
		return {"code": "duplicate_url_across_seasons", "url": target_url, "seasons": sorted(matched_seasons + [str(season)], key=season_sort_key)}

	def _existing_duplicate_for_season(self, title: str, season: str | int, option: bool = False) -> dict[str, Any] | None:
		entry = self._entry_for_title(title)
		if not entry or self._allow_shared_urls(title, entry, option):
			return None
		for url in (entry.get("seasons", {}) or {}).get(str(season), []) or []:
			conflict = self._shared_url_conflict(title, season, url, option)
			if conflict:
				return conflict
		return None

	def _log_duplicate(self, title: str, conflict: dict[str, Any]) -> None:
		self.log.warning(
			"MAPPING_DUPLICATE_URL_DETECTED\n"
			f"  title={title}\n"
			f"  seasons={conflict['seasons']}\n"
			f"  url={conflict['url']}\n"
			"  severity=warning\n"
			"  action=needs_review"
		)
		if self.events:
			self.events.record({
				"level": "WARNING",
				"type": "MAPPING",
				"event": "MAPPING_DUPLICATE_URL_DETECTED",
				"status": "warning",
				"title": title,
				"compact": f"{title} S{'/S'.join(conflict['seasons'])} | duplicate URL across seasons | needs review",
				"details": conflict,
			})

	def save_high_confidence(self, title: str, season: str | int, result: dict[str, Any], backup: bool = True, allow_shared_url_across_seasons: bool = False) -> dict[str, Any]:
		candidate = result.get("selected_candidate") or {}
		if not self._is_high_confidence(result):
			return {"action": "not_eligible", "saved": False}
		if not self.runtime_settings.status()["auto_save_high_confidence_mappings"]:
			return {"action": "auto_save_disabled", "saved": False}
		season = str(season)
		url = str(candidate.get("url", "")).strip()
		conflict = self._shared_url_conflict(title, season, url, allow_shared_url_across_seasons)
		if conflict:
			self._log_duplicate(title, conflict)
			return {"action": "duplicate_url_review", "saved": False, "warning": conflict, "url": url}
		with self.lock:
			data = self.table.getData()
			entry = self._entry_for_title(title, data)
			if entry and bool(entry.get("absolute", False)) and season != "absolute":
				return {"action": "needs_review_absolute_mapping", "saved": False}
			if entry is None:
				entry = {"title": title, "absolute": False, "seasons": {}}
				data.append(entry)
			elif entry.get("title") != title and normalize_title(str(entry.get("title", ""))) == normalize_title(title):
				entry["title"] = title
			entry.setdefault("seasons", {})
			entry["seasons"].setdefault(season, [])
			if url in entry["seasons"][season]:
				return {"action": "already_mapped", "saved": False, "url": url}
			backups = create_backup([self.table.db]) if backup else []
			entry["seasons"][season].append(url)
			self.table.replaceAll(data)
			self.log.warning(
				"AUTO_MAPPING_SAVED\n"
				f"  title={title}\n"
				f"  season={season}\n"
				f"  url={url}\n"
				f"  score={candidate.get('score')}\n"
				f"  confidence={candidate.get('confidence')}\n"
				"  source=Search V3"
			)
			if self.events:
				operation_id = result.get("operation_id") or self.events.operation_id("search_v3", title, season)
				self.events.upsert(operation_id, {
					"type": "MAPPING",
					"event": "AUTO_MAPPING_SAVED",
					"status": "success",
					"title": title,
					"season": season,
					"compact": f"{title} S{season} | {candidate.get('audio', 'UNKNOWN')} | score {float(candidate.get('score', 0)):.2f} | saved",
					"details": {"action": "AUTO_MAPPING_SAVED", "url": url, "candidate": candidate, "backups": backups},
				})
			return {"action": "saved_mapping", "saved": True, "url": url, "backups": backups}

	def add_review_draft(self, title: str, season: str | int, result: dict[str, Any], warning: str | None = None) -> dict[str, Any]:
		candidate = result.get("selected_candidate") or ((result.get("top_candidates") or [{}])[0])
		url = str(candidate.get("url", "")).strip()
		season = str(season)
		draft = {
			"sonarr_title": title,
			"season": season,
			"source_title": candidate.get("title") or source_title_from_url(url) or title,
			"source_url": url,
			"absolute": False,
			"language_preference": "",
			"detected_language": candidate.get("audio", "UNKNOWN"),
			"source": "search_v3",
			"notes": warning or f"Search V3 {result.get('status', 'needs_review')}. Review before saving.",
			"needs_review": True,
			"created_at": datetime.now().isoformat(timespec="seconds"),
			"search_score": candidate.get("score"),
			"search_confidence": candidate.get("confidence"),
		}
		with self.lock:
			drafts = self.preferences.data.setdefault("drafts", [])
			identity = (normalize_title(title), season, url)
			for existing in drafts:
				existing_identity = (normalize_title(str(existing.get("sonarr_title", ""))), str(existing.get("season", "")), str(existing.get("source_url", "")))
				if existing_identity == identity:
					return {"action": "draft_exists", "draft": deepcopy(existing)}
			drafts.append(draft)
			self.preferences.sync()
		return {"action": "created_review_item", "draft": draft}

	def handle_search_result(self, title: str, season: str | int, result: dict[str, Any], *, apply: bool = True, dry_run: bool = False, allow_shared_url_across_seasons: bool = False) -> dict[str, Any]:
		if result.get("status") == "already_mapped":
			return {"action": "skipped_search", "saved": False}
		if self._is_high_confidence(result):
			candidate = result.get("selected_candidate") or {}
			conflict = self._shared_url_conflict(title, season, str(candidate.get("url", "")), allow_shared_url_across_seasons)
			if conflict:
				self._log_duplicate(title, conflict)
				if dry_run:
					return {"action": "would_create_review_duplicate", "saved": False, "warning": conflict}
				outcome = self.add_review_draft(title, season, result, "Possible duplicate season mapping: candidate URL is already used by another season.")
				outcome["action"] = "duplicate_url_review"
				outcome["warning"] = conflict
				return outcome
			if dry_run or not apply:
				return {"action": "would_save" if dry_run else "matched_not_applied", "saved": False}
			if not self.runtime_settings.status()["auto_save_high_confidence_mappings"]:
				outcome = self.add_review_draft(title, season, result)
				outcome["action"] = "auto_save_disabled_review"
				return outcome
			return self.save_high_confidence(title, season, result, allow_shared_url_across_seasons=allow_shared_url_across_seasons)
		if result.get("status") not in ("needs_review", "matched"):
			return {"action": "no_result", "saved": False}
		if dry_run:
			return {"action": "would_create_draft", "saved": False}
		return self.add_review_draft(title, season, result)

	def _passes_tags(self, serie: dict[str, Any]) -> bool:
		active_tags = active_tag_ids(self.tags)
		serie_tags = [tag for tag in serie.get("tags", []) if tag in active_tags]
		if any(serie_tags) and self.settings["TagsMode"] == "BLACKLIST":
			return False
		if not any(serie_tags) and self.settings["TagsMode"] == "WHITELIST":
			return False
		return True

	def _eligible_seasons(self, serie: dict[str, Any], options: dict[str, Any]) -> list[dict[str, Any]]:
		seasons = []
		for season in serie.get("seasons", []):
			number = season.get("seasonNumber", season.get("number"))
			if number in (None, 0, "0"):
				continue
			if options["only_monitored"] and not season.get("monitored", serie.get("monitored", False)):
				continue
			stats = season.get("statistics", {}) or {}
			if options["only_downloaded_in_sonarr"] and int(stats.get("episodeFileCount", 0) or 0) <= 0:
				continue
			seasons.append({"number": int(number), "data": season})
		return sorted(seasons, key=lambda season: season_sort_key(season["number"]))

	def _episodes_for_series(self, serie: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
		if isinstance(serie.get("episodes"), list):
			return serie["episodes"], True
		if not serie.get("id") or not hasattr(self.sonarr, "episodes"):
			return [], False
		try:
			response = self.sonarr.episodes(serie["id"])
			response.raise_for_status()
			data = response.json()
			return data if isinstance(data, list) else [], True
		except Exception as error:
			self.log.warning(f"SONARR_EPISODE_METADATA_UNAVAILABLE\n  title={serie.get('title')}\n  error={error}")
			return [], False

	def _parse_air_date(self, episode: dict[str, Any]) -> datetime | None:
		value = episode.get("airDateUtc") or episode.get("airDate")
		if not value:
			return None
		try:
			parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
			if parsed.tzinfo is None:
				parsed = parsed.replace(tzinfo=timezone.utc)
			return parsed.astimezone(timezone.utc)
		except ValueError:
			return None

	def _season_release_state(self, season_record: dict[str, Any], episodes: list[dict[str, Any]], metadata_known: bool, include_future_tba: bool) -> dict[str, Any]:
		number = season_record["number"]
		data = season_record["data"]
		season_episodes = [episode for episode in episodes if str(episode.get("seasonNumber", episode.get("season", ""))) == str(number)]
		now = datetime.now(timezone.utc)
		released = [episode for episode in season_episodes if episode.get("hasFile") or (self._parse_air_date(episode) and self._parse_air_date(episode) <= now)]
		if not metadata_known:
			stats = data.get("statistics", {}) or {}
			released_count = int(stats.get("episodeFileCount", 0) or 0)
			return {"eligible": True, "status": "unknown", "future_tba": False, "released_episodes": released_count if released_count else None, "reason": "episode_metadata_unavailable"}
		reason = ""
		if not season_episodes:
			reason = "no_episodes"
		elif not released:
			reason = "no_released_episodes"
		future_tba = bool(reason)
		return {
			"eligible": include_future_tba or not future_tba,
			"status": "future/TBA" if future_tba else "released",
			"future_tba": future_tba,
			"released_episodes": len(released),
			"known_episodes": len(season_episodes),
			"reason": reason or "released_episodes_available",
		}

	def _options(self, payload: dict[str, Any]) -> dict[str, Any]:
		return {
			"dry_run": bool(payload.get("dry_run", False)),
			"apply_high_confidence": bool(payload.get("apply_high_confidence", True)),
			"include_already_mapped": bool(payload.get("include_already_mapped", False)),
			"only_unmapped": bool(payload.get("only_unmapped", True)),
			"only_monitored": bool(payload.get("only_monitored", True)),
			"only_downloaded_in_sonarr": bool(payload.get("only_downloaded_in_sonarr", False)),
			"respect_tags": bool(payload.get("respect_tags", True)),
			"force_rematch": bool(payload.get("force_rematch", False)),
			"include_future_tba": bool(payload.get("include_future_tba", False)),
			"allow_shared_url_across_seasons": bool(payload.get("allow_shared_url_across_seasons", False)),
		}

	def parse_unmapped(self, payload: dict[str, Any]) -> dict[str, Any]:
		if self.events and hasattr(self.events, "batch"):
			with self.events.batch():
				return self._parse_unmapped_impl(payload)
		return self._parse_unmapped_impl(payload)

	def _parse_unmapped_impl(self, payload: dict[str, Any]) -> dict[str, Any]:
		options = self._options(payload)
		self.log.info(
			"SONARR_PARSE_START\n"
			f"  dry_run={str(options['dry_run']).lower()}\n"
			f"  apply_high_confidence={str(options['apply_high_confidence']).lower()}\n"
			f"  only_unmapped={str(options['only_unmapped']).lower()}\n"
			f"  include_already_mapped={str(options['include_already_mapped']).lower()}\n"
			f"  force_rematch={str(options['force_rematch']).lower()}\n"
			f"  include_future_tba={str(options['include_future_tba']).lower()}\n"
			f"  allow_shared_url_across_seasons={str(options['allow_shared_url_across_seasons']).lower()}\n"
			f"  respect_tags={str(options['respect_tags']).lower()}\n"
			f"  only_monitored={str(options['only_monitored']).lower()}"
		)
		if self.events:
			self.events.record({"type": "SONARR", "event": "SONARR_PARSE_START", "status": "info", "compact": "Sonarr parse started | only unmapped | monitored only", "details": options})
		summary = {
			"scanned_series": 0,
			"scanned_seasons": 0,
			"already_mapped": 0,
			"skipped_search": 0,
			"skipped_future_tba": 0,
			"new_mappings_saved": 0,
			"needs_review": 0,
			"duplicate_url_warnings": 0,
			"no_result": 0,
			"errors_count": 0,
			"errors": [],
			"items": [],
			"skipped_series_type": 0,
			"series_type_overrides": 0,
		}
		response = self.sonarr.series()
		response.raise_for_status()
		for serie in response.json():
			title = str(serie.get("title", "")).strip()
			if not title:
				continue
			eligible, tag_override = is_anime_series(serie, self.settings, self.tags, respect_tags=options["respect_tags"])
			if not eligible:
				summary["skipped_series_type"] += 1
				continue
			if tag_override:
				summary["series_type_overrides"] += 1
				self.log.warning(f"SONARR_SERIES_TYPE_TAG_OVERRIDE\n  title={title}\n  series_type={serie.get('seriesType', serie.get('type'))}\n  reason=active whitelist tag")
			if options["respect_tags"] and not self._passes_tags(serie):
				continue
			seasons = self._eligible_seasons(serie, options)
			if not seasons:
				continue
			summary["scanned_series"] += 1
			episodes = None
			metadata_known = False
			reported_duplicates = set()
			for season_record in seasons:
				season = season_record["number"]
				summary["scanned_seasons"] += 1
				duplicate = self._existing_duplicate_for_season(title, season, options["allow_shared_url_across_seasons"])
				if duplicate and not options["force_rematch"]:
					key = (duplicate["url"], tuple(duplicate["seasons"]))
					if key not in reported_duplicates:
						reported_duplicates.add(key)
						summary["duplicate_url_warnings"] += 1
						self._log_duplicate(title, duplicate)
					summary["needs_review"] += 1
					item = self._item(title, season, "needs_review", action="skipped_search", reason="duplicate URL across seasons", existing=True, search_used=False, warning=duplicate)
					summary["items"].append(item)
					self._log_item(item)
					continue
				# SONARR_PARSE_ITEM already reports this outcome. Avoid writing one
				# additional structured event per mapped season during a bulk parse;
				# the event store is intentionally persisted as JSON and that duplicate
				# write made large libraries take minutes to parse.
				resolution = self.resolver.resolve_existing_mapping(serie, season, force_rematch=options["force_rematch"], log_skip=False)
				if not resolution["should_search"]:
					summary["already_mapped"] += 1
					summary["skipped_search"] += 1
					item = self._item(title, season, "already_mapped", action="skipped_search", reason="existing valid mapping", existing=True, urls_count=resolution["urls_count"], search_used=False)
					summary["items"].append(item)
					self._log_item(item)
					continue
				if episodes is None:
					episodes, metadata_known = self._episodes_for_series(serie)
				release = self._season_release_state(season_record, episodes, metadata_known, options["include_future_tba"])
				if not release["eligible"]:
					summary["skipped_future_tba"] += 1
					item = self._item(title, season, "skipped_future_tba", action="skipped_search", reason=release["reason"], existing=False, release=release, search_used=False)
					summary["items"].append(item)
					self._log_item(item)
					continue
				try:
					result = self.search_mapping(
						serie,
						season,
						language_preference=self.preferences.language_for(title),
						force_rematch=options["force_rematch"],
						debug=True,
						auto_refresh=False,
					)
					outcome = self.handle_search_result(title, season, result, apply=options["apply_high_confidence"], dry_run=options["dry_run"], allow_shared_url_across_seasons=options["allow_shared_url_across_seasons"])
					candidate = result.get("selected_candidate") or ((result.get("top_candidates") or [{}])[0])
					if outcome.get("warning"):
						summary["duplicate_url_warnings"] += 1
					if outcome.get("saved"):
						status = "auto_mapped"
						summary["new_mappings_saved"] += 1
					elif outcome.get("warning") or (result.get("status") in ("matched", "needs_review") and outcome["action"] not in ("would_save", "matched_not_applied")):
						status = "needs_review"
						summary["needs_review"] += 1
					elif result.get("status") in ("no_result", "index_empty"):
						status = "no_result"
						summary["no_result"] += 1
					else:
						status = "matched" if result.get("status") == "matched" else result.get("status", "no_result")
					item = self._item(title, season, status, candidate, outcome["action"], "duplicate URL across seasons" if outcome.get("warning") else "; ".join(result.get("errors", [])), existing=False, operation_id=result.get("operation_id"), release=release, search_used=True, warning=outcome.get("warning"))
					summary["items"].append(item)
					self._log_item(item)
				except Exception as error:
					item = self._item(title, season, "error", action="none", reason=str(error), existing=False, release=release, search_used=False)
					summary["items"].append(item)
					summary["errors"].append({"title": title, "season": season, "error": str(error)})
					summary["errors_count"] += 1
					self._log_item(item)
		self.log.info(
			"SONARR_PARSE_DONE\n"
			f"  scanned_series={summary['scanned_series']}\n"
			f"  scanned_seasons={summary['scanned_seasons']}\n"
			f"  already_mapped={summary['already_mapped']}\n"
			f"  skipped_search={summary['skipped_search']}\n"
			f"  skipped_future_tba={summary['skipped_future_tba']}\n"
			f"  new_mappings_saved={summary['new_mappings_saved']}\n"
			f"  needs_review={summary['needs_review']}\n"
			f"  duplicate_url_warnings={summary['duplicate_url_warnings']}\n"
			f"  no_result={summary['no_result']}\n"
			f"  skipped_series_type={summary['skipped_series_type']}\n"
			f"  series_type_overrides={summary['series_type_overrides']}\n"
			f"  errors={summary['errors_count']}"
		)
		if self.events:
			self.events.record({
				"type": "SONARR",
				"event": "SONARR_PARSE_DONE",
				"status": "success" if not summary["errors_count"] else "warning",
				"compact": f"Sonarr parse done | scanned {summary['scanned_seasons']} | skipped mapped {summary['skipped_search']} | skipped TBA {summary['skipped_future_tba']} | saved {summary['new_mappings_saved']} | review {summary['needs_review']} | errors {summary['errors_count']}",
				"details": summary,
			})
		return summary

	def refresh_from_sonarr(self, payload: dict[str, Any]) -> dict[str, Any]:
		return self.parse_unmapped(payload)

	def unmapped_summary(self, payload: dict[str, Any] | None = None) -> dict[str, int]:
		options = self._options(payload or {})
		summary = {"total_series": 0, "total_seasons": 0, "already_mapped": 0, "unmapped": 0, "needs_review": 0}
		response = self.sonarr.series()
		response.raise_for_status()
		for serie in response.json():
			eligible, _ = is_anime_series(serie, self.settings, self.tags, respect_tags=options["respect_tags"])
			if not eligible:
				continue
			if options["respect_tags"] and not self._passes_tags(serie):
				continue
			seasons = self._eligible_seasons(serie, options)
			if not seasons:
				continue
			summary["total_series"] += 1
			episodes = None
			metadata_known = False
			for season_record in seasons:
				season = season_record["number"]
				summary["total_seasons"] += 1
				resolution = self.resolver.resolve_existing_mapping(serie, season)
				if not resolution["should_search"]:
					summary["already_mapped"] += 1
					continue
				if episodes is None:
					episodes, metadata_known = self._episodes_for_series(serie)
				release = self._season_release_state(season_record, episodes, metadata_known, options["include_future_tba"])
				if not release["eligible"]:
					continue
				summary["unmapped"] += 1
				if resolution["reason"] == "needs_review":
					summary["needs_review"] += 1
		return summary

	def _item(self, title: str, season: str | int, status: str, candidate: dict[str, Any] | None = None, action: str = "", reason: str = "", existing: bool = False, urls_count: int = 0, operation_id: str | None = None, release: dict[str, Any] | None = None, search_used: bool = False, warning: dict[str, Any] | None = None) -> dict[str, Any]:
		candidate = candidate or {}
		release = release or {}
		return {
			"title": title,
			"season": str(season),
			"status": status,
			"existing_mapping": existing,
			"urls_count": urls_count,
			"selected_candidate": candidate.get("title"),
			"url": candidate.get("url"),
			"score": candidate.get("score"),
			"confidence": candidate.get("confidence"),
			"language": candidate.get("audio"),
			"action": action,
			"action_taken": action,
			"notes": candidate.get("reason") or reason,
			"reason": candidate.get("reason") or reason,
			"operation_id": operation_id,
			"sonarr_season_status": release.get("status", "unknown"),
			"released_episodes": release.get("released_episodes"),
			"future_tba": bool(release.get("future_tba", False)),
			"search_v3_used": search_used,
			"warning": warning,
		}

	def _log_item(self, item: dict[str, Any]) -> None:
		self.log.info(
			"SONARR_PARSE_ITEM\n"
			f"  title={item['title']}\n"
			f"  season={item['season']}\n"
			f"  status={item['status']}\n"
			f"  action={item['action']}\n"
			f"  score={item.get('score')}\n"
			f"  audio={item.get('language')}\n"
			f"  reason={item.get('reason')}"
		)
		if not self.events:
			return
		if item["status"] == "already_mapped":
			return
		status = "success" if item["status"] == "auto_mapped" else "warning" if item["status"] == "needs_review" else "error" if item["status"] in ("no_result", "error") else "info"
		if item["status"] == "skipped_future_tba":
			compact = f"{item['title']} S{item['season']} | skipped | future/TBA season | no released episodes"
			status = "info"
			event_name = "SONARR_SEASON_SKIPPED_TBA"
		elif item["status"] == "auto_mapped":
			compact = f"{item['title']} S{item['season']} | matched {item.get('language') or 'UNKNOWN'} | saved"
		elif item["action"] in ("would_save", "matched_not_applied"):
			compact = f"{item['title']} S{item['season']} | matched {item.get('language') or 'UNKNOWN'} | preview only"
		elif item["status"] == "needs_review":
			compact = f"{item['title']} S{item['season']} | needs review | {item.get('reason') or 'uncertain result'}"
		else:
			compact = f"{item['title']} S{item['season']} | {item['status']} | {item.get('reason') or item['action']}"
		if item["status"] != "skipped_future_tba":
			event_name = "AUTO_MAPPING_SAVED" if item["status"] == "auto_mapped" else "SONARR_PARSE_ITEM"
		event = {"type": "SEARCH_V3" if item.get("operation_id") else "SONARR", "event": event_name, "status": status, "title": item["title"], "season": item["season"], "compact": compact, "details": item}
		if item.get("operation_id"):
			self.events.upsert(item["operation_id"], event)
		else:
			self.events.record(event)
