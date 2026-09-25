import csv
import io
import json
import pathlib
import re
import shutil
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
try:
	from .atomic_json import read_json_durable, write_json_durable
except ImportError:  # Legacy isolated-module test harness.
	import importlib.util
	_atomic_spec = importlib.util.spec_from_file_location("anidown_legacy_atomic_json", pathlib.Path(__file__).with_name("atomic_json.py"))
	_atomic_json = importlib.util.module_from_spec(_atomic_spec)
	_atomic_spec.loader.exec_module(_atomic_json)
	read_json_durable = _atomic_json.read_json_durable
	write_json_durable = _atomic_json.write_json_durable
from urllib.parse import urlparse, urlunparse


LANGUAGE_PREFERENCES = ("AUTO", "DUB_FIRST", "SUB_FIRST", "DUB_ONLY", "SUB_ONLY")
CSV_COLUMNS = (
	"sonarr_title",
	"season",
	"source_url",
	"absolute",
	"language_preference",
	"notes",
)


PART_PATTERNS = (
	re.compile(r"(?:^|[-_ .])part[-_ .]*(\d+)(?:$|[-_ .])", re.I),
	re.compile(r"(?:^|[-_ .])parte[-_ .]*(\d+)(?:$|[-_ .])", re.I),
	re.compile(r"(?:^|[-_ .])cour[-_ .]*(\d+)(?:$|[-_ .])", re.I),
	re.compile(r"(?:^|[-_ .])(\d+)(?:st|nd|rd|th)[-_ .]*cour(?:$|[-_ .])", re.I),
	re.compile(r"(?:^|[-_ .])stagione[-_ .]*\d+[-_ .]*parte[-_ .]*(\d+)(?:$|[-_ .])", re.I),
	re.compile(r"(?:^|[-_ .])season[-_ .]*\d+[-_ .]*part[-_ .]*(\d+)(?:$|[-_ .])", re.I),
)


def normalize_title(title: str) -> str:
	return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", title.lower())).strip()


def valid_url(url: str) -> bool:
	parsed = urlparse(url)
	return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalize_url(url: str) -> str:
	url = str(url or "").strip()
	if not url:
		return ""
	parsed = urlparse(url)
	path = parsed.path.rstrip("/") or "/"
	return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.params, parsed.query, ""))


def season_sort_key(season: Any) -> tuple[int, int | str]:
	value = str(season).strip()
	if value.isdigit():
		return (0, int(value))
	if value == "absolute":
		return (1, value)
	return (2, value.lower())


def source_title_from_url(url: str) -> str:
	if not url:
		return ""
	path = urlparse(url).path.rstrip("/")
	slug = path.split("/")[-1] if path else ""
	slug = slug.split(".")[0]
	return re.sub(r"[-_]+", " ", slug).strip()


def source_slug_from_url(url: str) -> str:
	if not url:
		return ""
	path = urlparse(url).path.rstrip("/")
	slug = path.split("/")[-1] if path else ""
	return slug.split(".")[0].lower()


def url_part_number(url: str) -> int | None:
	slug = source_slug_from_url(url)
	if not slug:
		return None
	for pattern in PART_PATTERNS:
		match = pattern.search(slug)
		if match:
			try:
				return max(2, int(match.group(1)))
			except (TypeError, ValueError):
				return None
	return 1


def sort_part_urls(urls: list[str]) -> list[str]:
	def key(item: tuple[int, str]) -> tuple[int, int, str]:
		index, url = item
		part = url_part_number(url)
		if part is None:
			return (2, index, normalize_url(url))
		return (0, part, normalize_url(url))

	return [url for _, url in sorted(enumerate(urls), key=key)]


def sort_part_urls_in_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
	for row in rows:
		grouped.setdefault((str(row.get("_entry_id") or normalize_title(str(row.get("sonarr_title", "")))), str(row.get("season", ""))), []).append(row)
	for group in grouped.values():
		if len(group) < 2:
			continue
		sorted_urls = sort_part_urls([str(row.get("source_url", "")) for row in group])
		for row, url in zip(group, sorted_urls):
			row["source_url"] = url
			row["source_title"] = source_title_from_url(url)
	return rows


def url_order_warnings(title: str, season: Any, urls: list[str]) -> list[dict[str, Any]]:
	clean_urls = [url for url in urls if str(url or "").strip()]
	parts = [url_part_number(url) for url in clean_urls]
	warnings = []
	if any("/tba" in str(url).lower() for url in clean_urls):
		warnings.append({"code": "url_tba_warning", "message": "URL contains /tba; review this mapping."})
	known = [part for part in parts if part is not None]
	if len(known) < 2:
		return warnings
	if known != sorted(known):
		warnings.append({"code": "url_order_warning", "message": "URL order looks suspicious; part URLs are not in natural order."})
	if 1 not in known and any(part and part > 1 for part in known):
		warnings.append({"code": "missing_base_url", "message": "Base season URL is missing but a later part exists."})
	return [
		{
			"field": "source_url",
			"level": "warning",
			"title": title,
			"season": str(season),
			"seasons": [str(season)],
			**warning,
		}
		for warning in warnings
	]


def preview_flatten_order(title: str, season: Any, urls: list[str], episode_counter=None) -> dict[str, Any]:
	warnings = []
	result_urls = []
	next_episode = 1
	for index, url in enumerate(urls, start=1):
		count = None
		if episode_counter and url:
			try:
				count = episode_counter(url)
			except Exception as error:
				warnings.append(f"{url}: episode count unavailable ({error})")
		if isinstance(count, int) and count > 0:
			start = next_episode
			end = next_episode + count - 1
			next_episode = end + 1
		else:
			start = None
			end = None
			if url:
				warnings.append(f"{url}: episode count unavailable")
		result_urls.append({
			"order": index,
			"url": url,
			"source_title": source_title_from_url(url),
			"source_episode_count": count,
			"flattened_start": start,
			"flattened_end": end,
			"part": url_part_number(url),
		})
	return {"ok": True, "title": title, "season": season, "urls": result_urls, "warnings": list(dict.fromkeys(warnings))}


def is_valid_season(season: Any, absolute: bool = False) -> bool:
	season = str(season).strip()
	if absolute:
		return season == "absolute"
	if season == "absolute":
		return False
	return season.isdigit()


def empty_preferences() -> dict[str, Any]:
	return {
		"global_language_preference": "AUTO",
		"series": {},
		"drafts": [],
	}


class MappingPreferences:
	def __init__(self, path: pathlib.Path) -> None:
		self.path = path
		self.path.parent.mkdir(parents=True, exist_ok=True)
		if not self.path.exists() or self.path.stat().st_size == 0:
			self.write(empty_preferences())
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
		defaults = empty_preferences()
		for key, value in defaults.items():
			if key not in self.data:
				self.data[key] = value
				changed = True
		if self.data["global_language_preference"] not in LANGUAGE_PREFERENCES:
			self.data["global_language_preference"] = "AUTO"
			changed = True
		if changed:
			self.sync()

	def get_global_language(self) -> str:
		return self.data.get("global_language_preference", "AUTO")

	def set_global_language(self, value: str) -> None:
		if value not in LANGUAGE_PREFERENCES:
			raise ValueError("Invalid language preference.")
		self.data["global_language_preference"] = value
		self.sync()

	def get_series(self, title: str) -> dict[str, Any]:
		series = self.data.setdefault("series", {})
		return series.setdefault(title, {})

	def language_for(self, title: str) -> str:
		override = self.data.get("series", {}).get(title, {}).get("language_preference")
		return override if override in LANGUAGE_PREFERENCES else self.get_global_language()

	def set_series_metadata(self, title: str, metadata: dict[str, Any]) -> None:
		current = self.get_series(title)
		current.update({k: v for k, v in metadata.items() if v not in (None, "")})
		if metadata.get("language_preference") == "":
			current.pop("language_preference", None)
		self.sync()

	def add_draft(self, draft: dict[str, Any]) -> None:
		self.data.setdefault("drafts", []).append(draft)
		self.sync()

	def pop_drafts(self) -> list[dict[str, Any]]:
		drafts = self.data.get("drafts", [])
		self.data["drafts"] = []
		self.sync()
		return drafts


def flatten_table(table_data: list[dict[str, Any]], prefs: MappingPreferences | None = None) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	title_occurrences: dict[str, int] = {}
	for entry_index, serie in enumerate(table_data):
		title = serie.get("title", "")
		title_key = normalize_title(str(title)) or f"untitled-{entry_index}"
		occurrence = title_occurrences.get(title_key, 0)
		title_occurrences[title_key] = occurrence + 1
		mapping_id = f"{title_key}::{occurrence}"
		absolute = bool(serie.get("absolute", False))
		metadata = prefs.get_series(title) if prefs else {}
		language = metadata.get("language_preference", "") if prefs else ""
		effective_language = prefs.language_for(title) if prefs else "AUTO"
		notes = metadata.get("notes", "")
		needs_review = bool(metadata.get("needs_review", False))
		allow_shared = bool(metadata.get("allow_shared_url_across_seasons", False))
		seasons = serie.get("seasons", {})
		if not seasons:
			rows.append(row_from_values(title, "", "", "", absolute, language, effective_language, notes, True, entry_index, allow_shared, mapping_id))
			continue
		for season, urls in sorted(seasons.items(), key=lambda item: season_sort_key(item[0])):
			if not urls:
				rows.append(row_from_values(title, season, "", "", absolute, language, effective_language, notes, True, entry_index, allow_shared, mapping_id))
			for url in urls:
				rows.append(row_from_values(title, season, source_title_from_url(url), url, absolute, language, effective_language, notes, needs_review, entry_index, allow_shared, mapping_id))
	return rows


def row_from_values(
	title: str,
	season: str,
	source_title: str,
	source_url: str,
	absolute: bool,
	language_preference: str,
	effective_language: str,
	notes: str,
	needs_review: bool,
	entry_index: int | None = None,
	allow_shared_url_across_seasons: bool = False,
	mapping_id: str | None = None,
) -> dict[str, Any]:
	row = {
		"sonarr_title": title,
		"season": str(season),
		"source_title": source_title,
		"source_url": source_url,
		"absolute": absolute,
		"language_preference": language_preference,
		"effective_language_preference": effective_language,
		"notes": notes,
		"needs_review": needs_review,
		"allow_shared_url_across_seasons": allow_shared_url_across_seasons,
	}
	if entry_index is not None:
		row["_entry_id"] = str(entry_index)
	if mapping_id is not None:
		row["_mapping_id"] = mapping_id
	return row


def table_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	grouped: dict[str, dict[str, Any]] = {}
	for row in sorted(rows, key=lambda item: (str(item.get("_entry_id") or item.get("sonarr_title", "")), season_sort_key(item.get("season", "")))):
		title = str(row.get("sonarr_title", "")).strip()
		if not title:
			continue
		group_key = str(row.get("_entry_id") or title)
		absolute = bool(row.get("absolute", False))
		season = str(row.get("season", "absolute" if absolute else "1")).strip()
		url = str(row.get("source_url", "")).strip()
		serie = grouped.setdefault(group_key, {"title": title, "absolute": absolute, "seasons": {}})
		serie["title"] = title
		serie["absolute"] = absolute
		if season:
			serie["seasons"].setdefault(season, [])
			if url and url not in serie["seasons"][season]:
				serie["seasons"][season].append(url)
	return sorted(grouped.values(), key=lambda entry: entry["title"])


def metadata_from_rows(rows: list[dict[str, Any]], prefs: MappingPreferences) -> dict[str, Any]:
	data = deepcopy(prefs.data)
	data.setdefault("series", {})
	for row in rows:
		title = str(row.get("sonarr_title", "")).strip()
		if not title:
			continue
		metadata = data["series"].setdefault(title, {})
		language = str(row.get("language_preference", "")).strip()
		if language and language != prefs.get_global_language():
			metadata["language_preference"] = language
		elif language == "":
			metadata.pop("language_preference", None)
		metadata["notes"] = str(row.get("notes", "")).strip()
		metadata["needs_review"] = bool(row.get("needs_review", False)) or not str(row.get("source_url", "")).strip()
		metadata["allow_shared_url_across_seasons"] = bool(row.get("allow_shared_url_across_seasons", False))
	return data


def validate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	errors: list[dict[str, Any]] = []
	titles: dict[str, set[str]] = {}
	grouped_urls: dict[str, dict[str, list[tuple[int, str]]]] = {}
	season_groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
	for index, row in enumerate(rows):
		title = str(row.get("sonarr_title", "")).strip()
		absolute = bool(row.get("absolute", False))
		season = str(row.get("season", "")).strip()
		url = str(row.get("source_url", "")).strip()
		language = str(row.get("language_preference", "")).strip()
		if not title:
			errors.append({"row": index, "field": "sonarr_title", "message": "Title cannot be empty.", "level": "error"})
		else:
			key = normalize_title(title)
			titles.setdefault(key, set()).add(str(row.get("_entry_id") or key))
		if not is_valid_season(season, absolute):
			errors.append({"row": index, "field": "season", "message": "Season must be numeric, or absolute for absolute mappings.", "level": "error"})
		if not url:
			errors.append({"row": index, "field": "source_url", "message": "URL is empty; this mapping is incomplete.", "level": "warning"})
		elif not valid_url(url):
			errors.append({"row": index, "field": "source_url", "message": "URL must be a valid http(s) URL.", "level": "error"})
		if url:
			group_key = str(row.get("_entry_id") or normalize_title(title))
			grouped_urls.setdefault(group_key, {}).setdefault(normalize_url(url), []).append((index, season))
		season_group_key = (str(row.get("_entry_id") or normalize_title(title)), season)
		season_groups.setdefault(season_group_key, []).append((index, row))
		if language and language not in LANGUAGE_PREFERENCES:
			errors.append({"row": index, "field": "language_preference", "message": "Invalid language preference.", "level": "error"})
	for index, row in enumerate(rows):
		title = str(row.get("sonarr_title", "")).strip()
		if title and len(titles.get(normalize_title(title), set())) > 1:
			errors.append({"row": index, "field": "sonarr_title", "message": "Duplicate title appears in this batch.", "level": "warning"})
	for series, url_groups in grouped_urls.items():
		for url, occurrences in url_groups.items():
			if not url or len(occurrences) < 2:
				continue
			seasons = sorted({season for _, season in occurrences}, key=season_sort_key)
			first_row = rows[occurrences[0][0]]
			allow_shared = bool(first_row.get("absolute", False) or first_row.get("allow_shared_url_across_seasons", False))
			across_seasons = len(seasons) > 1
			code = "duplicate_url_across_seasons" if across_seasons else "duplicate_url_same_season"
			message = "Duplicate URL across seasons; review this mapping." if across_seasons else "Duplicate URL repeated in the same season."
			level = "info" if across_seasons and allow_shared else "warning"
			errors.append({
				"row": occurrences[0][0],
				"series": series,
				"field": "source_url",
				"code": code,
				"message": message,
				"level": level,
				"url": url,
				"seasons": seasons,
			})
	for (series, season), grouped_rows in season_groups.items():
		urls = [str(row.get("source_url", "")).strip() for _, row in grouped_rows if str(row.get("source_url", "")).strip()]
		for warning in url_order_warnings(str(grouped_rows[0][1].get("sonarr_title", "")), season, urls):
			warning["row"] = grouped_rows[0][0]
			warning["series"] = series
			errors.append(warning)
	return errors


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
	output = io.StringIO()
	writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
	writer.writeheader()
	for row in rows:
		writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
	return output.getvalue()


def rows_from_csv(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	reader = csv.DictReader(io.StringIO(content))
	missing = [column for column in CSV_COLUMNS if column not in (reader.fieldnames or [])]
	if missing:
		return [], [{"row": 0, "field": ",".join(missing), "message": "Missing required CSV columns.", "level": "error"}]
	rows = []
	for row in reader:
		rows.append({
			"sonarr_title": row.get("sonarr_title", "").strip(),
			"season": row.get("season", "").strip(),
			"source_title": source_title_from_url(row.get("source_url", "").strip()),
			"source_url": row.get("source_url", "").strip(),
			"absolute": str(row.get("absolute", "")).lower() in ("true", "1", "yes", "y"),
			"language_preference": row.get("language_preference", "").strip(),
			"notes": row.get("notes", "").strip(),
			"needs_review": False,
		})
	return rows, validate_rows(rows)


def rows_from_json(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	try:
		data = json.loads(content)
	except json.JSONDecodeError:
		return [], [{"row": 0, "field": "json", "message": "Invalid JSON.", "level": "error"}]
	if isinstance(data, dict) and "rows" in data:
		rows = data["rows"]
	elif isinstance(data, list) and all(isinstance(item, dict) and "seasons" in item for item in data):
		rows = flatten_table(data)
	elif isinstance(data, list):
		rows = data
	else:
		return [], [{"row": 0, "field": "json", "message": "JSON must be table.json or exported rows.", "level": "error"}]
	return rows, validate_rows(rows)


def merge_rows(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
	merged = {(row.get("sonarr_title"), row.get("season"), row.get("source_url")): row for row in existing}
	for row in incoming:
		key = (row.get("sonarr_title"), row.get("season"), row.get("source_url"))
		merged[key] = row
	return list(merged.values())


def _row_signature(row: dict[str, Any]) -> tuple[Any, ...]:
	return (
		str(row.get("season", "")).strip(),
		normalize_url(str(row.get("source_url", ""))),
		bool(row.get("absolute", False)),
		str(row.get("language_preference", "")).strip(),
		str(row.get("notes", "")).strip(),
	)


def _coalesce_import_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Assign one stable entry id to imported rows with equivalent titles."""
	canonical: dict[str, str] = {}
	result = []
	for row in deepcopy(rows):
		title = str(row.get("sonarr_title", "")).strip()
		key = normalize_title(title)
		if not key:
			result.append(row)
			continue
		if row.get("_entry_id") is not None:
			row["_entry_id"] = f"import:{row['_entry_id']}"
			result.append(row)
			continue
		canonical.setdefault(key, title)
		row["sonarr_title"] = canonical[key]
		row["_entry_id"] = f"import:{key}"
		result.append(row)
	return result


def reconcile_import_rows(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], mode: str = "add_only") -> tuple[list[dict[str, Any]], dict[str, int]]:
	"""Build an import result without changing the legacy table.json structure."""
	mode = {"merge": "update", "merge_add": "add_only", "merge_update": "update"}.get(mode, mode)
	if mode not in ("add_only", "update", "overwrite"):
		raise ValueError("Invalid import mode.")
	current = deepcopy(existing)
	imported = _coalesce_import_rows(incoming)
	existing_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
	incoming_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
	for row in current:
		existing_groups.setdefault((normalize_title(str(row.get("sonarr_title", ""))), str(row.get("season", "")).strip()), []).append(row)
	for row in imported:
		incoming_groups.setdefault((normalize_title(str(row.get("sonarr_title", ""))), str(row.get("season", "")).strip()), []).append(row)
	summary = {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
	if mode == "overwrite":
		for key, rows in incoming_groups.items():
			old = existing_groups.get(key)
			if old is None:
				summary["added"] += len(rows)
			elif {_row_signature(row) for row in old} == {_row_signature(row) for row in rows}:
				summary["unchanged"] += len(rows)
			else:
				summary["updated"] += len(rows)
		for key, rows in existing_groups.items():
			if key not in incoming_groups:
				summary["deleted"] += len(rows)
		return imported, summary
	existing_series: dict[str, tuple[str, str]] = {}
	for row in current:
		key = normalize_title(str(row.get("sonarr_title", "")))
		if key and key not in existing_series:
			existing_series[key] = (str(row.get("_entry_id") or row.get("sonarr_title", "")), str(row.get("sonarr_title", "")))
	final_rows = deepcopy(current)
	for (title_key, season), rows in incoming_groups.items():
		old = existing_groups.get((title_key, season))
		entry_id, existing_title = existing_series.get(title_key, (rows[0]["_entry_id"], rows[0]["sonarr_title"]))
		if old and mode == "add_only":
			summary["unchanged"] += len(rows)
			continue
		if old:
			if {_row_signature(row) for row in old} == {_row_signature(row) for row in rows}:
				summary["unchanged"] += len(rows)
				continue
			final_rows = [row for row in final_rows if (normalize_title(str(row.get("sonarr_title", ""))), str(row.get("season", "")).strip()) != (title_key, season)]
			summary["updated"] += len(rows)
		else:
			summary["added"] += len(rows)
		display_title = rows[0]["sonarr_title"] if mode == "update" else existing_title
		if mode == "update" and title_key in existing_series:
			for final_row in final_rows:
				if normalize_title(str(final_row.get("sonarr_title", ""))) == title_key:
					final_row["sonarr_title"] = display_title
		for row in rows:
			next_row = deepcopy(row)
			next_row["_entry_id"] = entry_id
			next_row["sonarr_title"] = display_title
			final_rows.append(next_row)
	return final_rows, summary


def create_backup(paths: list[pathlib.Path], backup_root: pathlib.Path | None = None) -> list[str]:
	timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
	if backup_root is None:
		backup_root = paths[0].parent.joinpath("backups")
	backup_root.mkdir(parents=True, exist_ok=True)
	backups = []
	for path in paths:
		if path.exists():
			target = backup_root.joinpath(f"{path.stem}_{timestamp}{path.suffix}")
			suffix = 1
			while target.exists():
				target = backup_root.joinpath(f"{path.stem}_{timestamp}_{suffix}{path.suffix}")
				suffix += 1
			shutil.copy2(path, target)
			backups.append(str(target))
	return backups


def fuzzy_matches(query: str, candidates: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
	normalized_query = normalize_title(query)
	results = []
	for candidate in candidates:
		names = [candidate.get("sonarr_title", ""), candidate.get("source_title", "")]
		names.extend(candidate.get("aliases", []) or [])
		score = max((SequenceMatcher(None, normalized_query, normalize_title(name)).ratio() for name in names if name), default=0)
		if score:
			result = dict(candidate)
			result["confidence"] = round(score, 3)
			result["needs_review"] = score < 0.85
			results.append(result)
	return sorted(results, key=lambda item: item["confidence"], reverse=True)[:limit]
