import time
from flask import Flask, Response, request
import sys, os, json, threading, uuid
import re
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
import animeworld as aw

from ..backend import Core
from ..backend.core import Constant as ctx
from ..backend.mapping import (
	CSV_COLUMNS,
	LANGUAGE_PREFERENCES,
	MappingPreferences,
	create_backup,
	flatten_table,
	fuzzy_matches,
	merge_rows,
	metadata_from_rows,
	normalize_title,
	normalize_url,
	preview_flatten_order,
	reconcile_import_rows,
	rows_from_csv,
	rows_from_json,
	rows_to_csv,
	season_sort_key,
	sort_part_urls,
	sort_part_urls_in_rows,
	source_title_from_url,
	table_from_rows,
	valid_url,
	validate_rows,
)
from .search_v3_api import loadSearchV3API


def json_response(data, status=200):
	return Response(
		mimetype='application/json',
		status=status,
		response=json.dumps(data),
		headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*", "Cache-Control": "no-store"}
	)

def loadAPI(app:Flask):

	core:Core = app.config['CORE']
	preferences = core.mapping_automation.preferences
	parse_jobs = {}
	parse_jobs_lock = threading.RLock()
	loadSearchV3API(app, core)

	@app.after_request
	def mappingCors(response):
		response.headers.setdefault("Access-Control-Allow-Origin", "*")
		response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
		response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		return response

	def backup_mapping_files():
		return create_backup([
			core.table.db,
			preferences.path,
			core.search_v3.alias_provider.aliases_path,
			core.search_v3.alias_provider.cache_path,
			core.search_v3.index_store.path,
			core.runtime_settings.path,
		])

	def record_event(event, compact, *, status="info", title="", season=None, details=None, event_type="MAPPING"):
		core.events.record({
			"level": "WARNING" if status == "warning" else "ERROR" if status == "error" else "INFO",
			"type": event_type,
			"event": event,
			"status": status,
			"title": title,
			"season": season,
			"compact": compact,
			"details": details or {},
		})

	def detected_audio(url: str, title: str = "") -> str:
		text = f"{title} {url}".lower()
		return "DUB" if re.search(r"(?:\(|[-_. ])ita(?:\)|[-_. /]|$)", text) else ("SUB" if url else "UNKNOWN")

	def extension_candidate(title, rows, aliases, queries):
		aliases = list(aliases or [])
		values = core.search_v3.variant_builder.build(title, aliases).get("normalized_variants", [])
		score = max((SequenceMatcher(None, query, value).ratio() for query in queries for value in values if query and value), default=0.0)
		exact = any(query == value for query in queries for value in values if query and value)
		score = 1.0 if exact else round(score, 3)
		issues = validate_rows(rows) if rows else []
		urls = [row.get("source_url", "") for row in rows if row.get("source_url")]
		audio = detected_audio(urls[0], title) if urls else "UNKNOWN"
		preference = preferences.language_for(title)
		mismatch = (preference.startswith("SUB") and audio == "DUB") or (preference.startswith("DUB") and audio == "SUB")
		manual_review = any(bool(row.get("needs_review")) for row in rows)
		has_error = any(issue["level"] == "error" for issue in issues)
		has_warning = any(issue["level"] == "warning" for issue in issues)
		if has_error:
			status = "invalid"
		elif mismatch:
			status = "language_mismatch"
		elif not urls:
			status = "unmapped"
		elif manual_review or has_warning:
			status = "needs_review"
		else:
			status = "mapped"
		seasons = {}
		for row in rows:
			season = str(row.get("season") or "1")
			item = seasons.setdefault(season, {"season": season, "mapped": False, "urls": [], "audio": "UNKNOWN", "needs_review": manual_review})
			if row.get("source_url") and row["source_url"] not in item["urls"]:
				item["urls"].append(row["source_url"])
				item["mapped"] = True
				item["audio"] = detected_audio(row["source_url"], title)
		if not seasons:
			seasons["1"] = {"season": "1", "mapped": False, "urls": [], "audio": "UNKNOWN", "needs_review": False}
		for season, item in seasons.items():
			season_rows = [row for row in rows if str(row.get("season") or "1") == str(season)]
			season_issues = validate_rows(season_rows) if season_rows else []
			season_issues.extend(issue for issue in issues if str(season) in [str(value) for value in issue.get("seasons", [])])
			codes = [issue.get("code") for issue in season_issues if issue.get("code")]
			item["warning_codes"] = codes
			if any(issue.get("level") == "error" for issue in season_issues):
				item["status"] = "invalid"
			elif "language_mismatch" in codes:
				item["status"] = "language_mismatch"
			elif any(code in ("duplicate_url_across_seasons", "duplicate_url_same_season") for code in codes):
				item["status"] = "duplicate_url"
			elif item["needs_review"]:
				item["status"] = "needs_review"
			else:
				item["status"] = "mapped" if item["mapped"] else "empty"
		return {
			"title": title,
			"score": score,
			"reason": "exact normalized title match" if exact else "fuzzy title match",
			"status": status,
			"audio_preference": preference,
			"warning_codes": [issue.get("code") for issue in issues if issue.get("code")],
			"seasons": sorted(seasons.values(), key=lambda item: season_sort_key(item["season"])),
			"preselected": False,
		}

	def extension_candidates(payload):
		page_title = str(payload.get("detected_title") or payload.get("page_title") or "").strip()
		queries = core.search_v3.variant_builder.build(page_title).get("normalized_variants", [])
		show_mapped = bool(payload.get("show_already_mapped", False))
		rows = flatten_table(core.table.getData(), preferences)
		grouped = {}
		for row in rows:
			grouped.setdefault(str(row.get("_entry_id") or normalize_title(row.get("sonarr_title", ""))), []).append(row)
		candidates = []
		known_titles = set()
		for mapped_rows in grouped.values():
			title = str(mapped_rows[0].get("sonarr_title", ""))
			known_titles.add(normalize_title(title))
			candidate = extension_candidate(title, mapped_rows, core.search_v3.alias_provider.manual_aliases(title), queries)
			if show_mapped or candidate["status"] != "mapped":
				candidates.append(candidate)
		try:
			response = core.sonarr.series()
			response.raise_for_status()
			for serie in response.json():
				title = str(serie.get("title", "")).strip()
				if not title or normalize_title(title) in known_titles:
					continue
				seasons = [
					{
						"_entry_id": f"sonarr:{serie.get('id', title)}",
						"sonarr_title": title,
						"season": str(season.get("seasonNumber")),
						"source_url": "",
						"absolute": False,
						"language_preference": "",
						"needs_review": False,
					}
					for season in serie.get("seasons", [])
					if season.get("seasonNumber") not in (None, 0)
				]
				aliases = [alt.get("title") for alt in serie.get("alternateTitles", []) if isinstance(alt, dict) and alt.get("title")]
				candidates.append(extension_candidate(title, seasons, aliases, queries))
		except Exception as error:
			core.log.debug(f"Extension Sonarr candidate lookup unavailable: {error}")
		candidates.sort(key=lambda item: (item["score"], item["status"] != "mapped"), reverse=True)
		ambiguous = bool(len(candidates) > 1 and candidates[0]["score"] >= 0.90 and candidates[1]["score"] >= candidates[0]["score"] - 0.03)
		if candidates and candidates[0]["score"] >= 0.90 and not ambiguous:
			candidates[0]["preselected"] = True
		for candidate in candidates:
			candidate["multiple_possible_candidates"] = ambiguous
		return candidates[:80]

	@app.route('/api/runtime/status', methods=['GET'])
	def runtimeStatus():
		return json_response({"error": False, "data": core.runtime_settings.status()})

	@app.route('/api/runtime/pause-downloads', methods=['POST'])
	def runtimePauseDownloads():
		return json_response({"error": False, "data": core.runtime_settings.update(downloads_paused=True)})

	@app.route('/api/runtime/resume-downloads', methods=['POST'])
	def runtimeResumeDownloads():
		return json_response({"error": False, "data": core.runtime_settings.update(downloads_paused=False)})

	@app.route('/api/runtime/search-only-mode', methods=['POST'])
	def runtimeSearchOnly():
		enabled = bool((request.json or {}).get("enabled", False))
		return json_response({"error": False, "data": core.runtime_settings.update(search_only_mode=enabled)})

	@app.route('/api/runtime/auto-save-mappings', methods=['POST'])
	def runtimeAutoSaveMappings():
		payload = request.json or {}
		changes = {"auto_save_high_confidence_mappings": bool(payload.get("enabled", True))}
		if "auto_mapping_min_score" in payload:
			try:
				changes["auto_mapping_min_score"] = float(payload["auto_mapping_min_score"])
			except (TypeError, ValueError):
				return json_response({"error": "auto_mapping_min_score must be numeric.", "data": None}, 400)
		return json_response({"error": False, "data": core.runtime_settings.update(**changes)})

	@app.route('/api/runtime/ui-settings', methods=['POST'])
	def runtimeUiSettings():
		payload = request.json or {}
		changes = {key: payload[key] for key in ("log_panel_visible", "compact_mode", "log_verbosity", "auto_sort_part_urls_when_adding") if key in payload}
		return json_response({"error": False, "data": core.runtime_settings.update(**changes)})

	@app.route('/api/runtime/run-scan-now', methods=['POST'])
	def runtimeRunScanNow():
		result = core.runScanNow()
		return json_response({"error": False if result.get("ok") else result.get("message", "A scan is already running"), "data": result}, 200 if result.get("ok") else 409)

	@app.route('/api/mapping/refresh-from-sonarr', methods=['POST'])
	def mappingRefreshFromSonarr():
		try:
			return json_response({"error": False, "data": core.mapping_automation.parse_unmapped(request.json or {})})
		except Exception as e:
			core.log.exception(e)
			return json_response({"error": str(e), "data": None}, 502)

	@app.route('/api/sonarr/unmapped-summary', methods=['GET'])
	def sonarrUnmappedSummary():
		try:
			return json_response({"error": False, "data": core.mapping_automation.unmapped_summary(request.args.to_dict())})
		except Exception as e:
			core.log.exception(e)
			return json_response({"error": str(e), "data": None}, 502)

	@app.route('/api/sonarr/parse-unmapped', methods=['POST'])
	def sonarrParseUnmapped():
		try:
			return json_response({"error": False, "data": core.mapping_automation.parse_unmapped(request.json or {})})
		except Exception as e:
			core.log.exception(e)
			return json_response({"error": str(e), "data": None}, 502)

	@app.route('/api/sonarr/parse-unmapped/start', methods=['POST'])
	def sonarrParseUnmappedStart():
		with parse_jobs_lock:
			for job in parse_jobs.values():
				if job.get("status") in ("queued", "running"):
					return json_response({"error": False, "data": {"job_id": job["job_id"], "status": job["status"], "already_running": True}}, 202)
			job_id = uuid.uuid4().hex
			job = {
				"job_id": job_id,
				"status": "queued",
				"created_at": datetime.now().isoformat(timespec="seconds"),
				"result": None,
				"error": None,
			}
			parse_jobs.clear()
			parse_jobs[job_id] = job
		payload = deepcopy(request.json or {})

		def run_parse():
			with parse_jobs_lock:
				job["status"] = "running"
				job["started_at"] = datetime.now().isoformat(timespec="seconds")
			try:
				result = core.mapping_automation.parse_unmapped(payload)
				with parse_jobs_lock:
					job["result"] = result
					job["status"] = "completed"
			except Exception as error:
				core.log.exception(error)
				with parse_jobs_lock:
					job["error"] = str(error)
					job["status"] = "failed"
			finally:
				with parse_jobs_lock:
					job["finished_at"] = datetime.now().isoformat(timespec="seconds")

		threading.Thread(target=run_parse, name=f"SonarrParse-{job_id[:8]}", daemon=True).start()
		return json_response({"error": False, "data": {"job_id": job_id, "status": "queued", "already_running": False}}, 202)

	@app.route('/api/sonarr/parse-unmapped/status/<job_id>', methods=['GET'])
	def sonarrParseUnmappedStatus(job_id):
		with parse_jobs_lock:
			job = parse_jobs.get(job_id)
			if not job:
				return json_response({"error": "Parse job not found", "data": None}, 404)
			return json_response({"error": False, "data": deepcopy(job)})

	@app.route('/api/rescan', methods=['GET'])
	def rescan():
		result = core.runScanNow()
		return Response(
			mimetype='application/json',
			status=200 if result.get("ok") else 409,
			response=json.dumps({
				"error": False if result.get("ok") else result.get("message", "A scan is already running"),
				"data": result
			}),
			headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}
		)

	@app.route('/api/mapping/manager', methods=['GET'])
	def mappingManager():
		rows = flatten_table(core.table.getData(), preferences)
		drafts = [deepcopy(draft) for draft in preferences.data.get("drafts", [])]
		review_events = {}
		for event in core.events.list(600):
			details = event.get("details") or {}
			if details.get("status") != "needs_review" and event.get("status") != "warning":
				continue
			title = str(event.get("title") or details.get("title") or "")
			season = str(event.get("season") or details.get("season") or "1")
			key = (normalize_title(title), season)
			if not key[0] or key in review_events:
				continue
			candidates = details.get("candidates") or []
			reason = str(details.get("reason") or "").strip()
			if len(candidates) > 1:
				best_score = candidates[0].get("score")
				tied = [candidate for candidate in candidates if candidate.get("score") == best_score and candidate.get("confidence") == candidates[0].get("confidence")]
				if len(tied) > 1:
					names = ", ".join(str(candidate.get("title") or "?") for candidate in tied[:4])
					reason = f"Multiple equally strong candidates ({best_score}): {names}"
			if not reason:
				reason = str(event.get("message") or event.get("compact") or "Review required").strip()
			review_events[key] = {
				"reason": reason,
				"event": event.get("event"),
				"timestamp": event.get("timestamp"),
				"candidates": candidates[:6],
			}
		for draft in drafts:
			key = (normalize_title(str(draft.get("sonarr_title", ""))), str(draft.get("season", "1")))
			context = review_events.get(key)
			if context:
				draft["review_reason"] = context["reason"]
				draft["review_event"] = context["event"]
				draft["review_timestamp"] = context["timestamp"]
				draft["review_candidates"] = context["candidates"]
			else:
				draft["review_reason"] = str(draft.get("notes") or "Review required")
		return json_response({
			"error": False,
			"data": {
				"rows": rows,
				"drafts": drafts,
				"language_preferences": LANGUAGE_PREFERENCES,
				"global_language_preference": preferences.get_global_language(),
				"aliases": {
					row["sonarr_title"]: core.search_v3.alias_provider.manual_aliases(row["sonarr_title"])
					for row in rows
					if row.get("sonarr_title")
				},
				"columns": CSV_COLUMNS,
			}
		})

	@app.route('/api/mapping/preferences', methods=['POST'])
	def mappingPreferences():
		data = request.json or {}
		try:
			preferences.set_global_language(data.get("global_language_preference", "AUTO"))
		except ValueError as e:
			return json_response({"error": str(e), "data": None}, 400)
		return json_response({"error": False, "data": "Preferenze aggiornate."})

	@app.route('/api/mapping/validate', methods=['POST'])
	def mappingValidate():
		rows = (request.json or {}).get("rows", [])
		return json_response({"error": False, "data": validate_rows(rows)})

	@app.route('/api/mapping/bulk-save', methods=['POST'])
	def mappingBulkSave():
		payload = request.json or {}
		rows = payload.get("rows", [])
		errors = validate_rows(rows)
		if any(error["level"] == "error" for error in errors):
			return json_response({"error": "Validation failed.", "data": errors}, 400)
		with core.mapping_automation.lock:
			backups = backup_mapping_files()
			core.table.replaceAll(table_from_rows(rows))
			preferences.data = metadata_from_rows(rows, preferences)
			preferences.data["drafts"] = payload.get("drafts", preferences.data.get("drafts", []))
			preferences.sync()
		return json_response({"error": False, "data": {"message": "Mapping salvata.", "backups": backups, "warnings": errors}})

	@app.route('/api/mapping/bulk-action', methods=['POST'])
	def mappingBulkAction():
		payload = request.json or {}
		action = str(payload.get("action", "")).strip()
		mapping_ids = {str(value) for value in payload.get("mapping_ids", payload.get("entry_ids", []))}
		if not mapping_ids:
			return json_response({"error": "Select at least one mapping.", "data": None}, 400)
		rows = flatten_table(core.table.getData(), preferences)
		selected = [row for row in rows if str(row.get("_mapping_id", row.get("_entry_id"))) in mapping_ids]
		if not selected:
			return json_response({"error": "Selected mappings no longer exist.", "data": None}, 404)
		selected_mapping_ids = {str(row.get("_mapping_id", row.get("_entry_id"))) for row in selected}
		titles = sorted({str(row.get("sonarr_title", "")).strip() for row in selected if row.get("sonarr_title")})
		count = len(selected_mapping_ids)
		with core.mapping_automation.lock:
			if action == "delete":
				if not bool(payload.get("confirm", False)):
					return json_response({"error": "Deletion requires confirmation.", "data": None}, 400)
				backups = backup_mapping_files()
				remaining = [row for row in rows if str(row.get("_mapping_id", row.get("_entry_id"))) not in selected_mapping_ids]
				core.table.replaceAll(table_from_rows(remaining))
				remaining_titles = {normalize_title(str(row.get("sonarr_title", "")).strip()) for row in remaining}
				for title in titles:
					if normalize_title(title) not in remaining_titles:
						preferences.data.get("series", {}).pop(title, None)
				preferences.sync()
				compact = f"Bulk delete | {count} mappings removed"
				event = "BULK_DELETE_MAPPINGS"
			elif action in ("clear_review", "clear_needs_review"):
				backups = backup_mapping_files()
				for title in titles:
					preferences.get_series(title)["needs_review"] = False
				preferences.sync()
				compact = f"Bulk action | cleared manual needs review on {count} mappings"
				event = "BULK_CLEAR_NEEDS_REVIEW"
			elif action == "set_language":
				language = str(payload.get("language_preference", "")).strip()
				if language and language not in LANGUAGE_PREFERENCES:
					return json_response({"error": "Invalid language preference.", "data": None}, 400)
				backups = backup_mapping_files()
				for title in titles:
					metadata = preferences.get_series(title)
					if language:
						metadata["language_preference"] = language
					else:
						metadata.pop("language_preference", None)
				preferences.sync()
				display_language = language or f"global {preferences.get_global_language()}"
				compact = f"Bulk language update | {count} mappings set to {display_language}"
				event = "BULK_LANGUAGE_UPDATE"
			else:
				return json_response({"error": "Unsupported bulk action.", "data": None}, 400)
		record_event(event, compact, status="success", details={"action": action, "count": count, "titles": titles, "language_preference": payload.get("language_preference"), "backup_created": bool(backups), "backups": backups})
		return json_response({"error": False, "data": {"ok": True, "action": action, "message": compact, "updated_count": 0 if action == "delete" else count, "deleted_count": count if action == "delete" else 0, "errors": [], "backup_path": backups[0] if backups else None, "backups": backups}})

	@app.route('/api/mapping/export/json', methods=['GET'])
	def mappingExportJson():
		return Response(
			json.dumps(core.table.getData(), indent=4),
			mimetype="application/json",
			headers={"Content-disposition":"attachment; filename=table.json", "Access-Control-Allow-Origin": "*"},
		)

	@app.route('/api/mapping/export/csv', methods=['GET'])
	def mappingExportCsv():
		return Response(
			rows_to_csv(flatten_table(core.table.getData(), preferences)),
			mimetype="text/csv",
			headers={"Content-disposition":"attachment; filename=mappings.csv", "Access-Control-Allow-Origin": "*"},
		)

	@app.route('/api/mapping/import/preview', methods=['POST'])
	def mappingImportPreview():
		import_type = request.form.get("type", "json")
		uploaded_file = request.files.get("file")
		if not uploaded_file:
			return json_response({"error": "Missing import file.", "data": None}, 400)
		content = uploaded_file.read().decode("utf-8")
		if import_type == "csv" or uploaded_file.filename.lower().endswith(".csv"):
			rows, issues = rows_from_csv(content)
		else:
			rows, issues = rows_from_json(content)
		current = flatten_table(core.table.getData(), preferences)
		summaries = {}
		for mode in ("add_only", "update", "overwrite"):
			_, summaries[mode] = reconcile_import_rows(current, rows, mode)
		record_event("MAPPING_IMPORT_PREVIEW", f"Import preview | {len(rows)} rows", details={"summaries": summaries})
		return json_response({"error": False, "data": {"rows": rows, "issues": issues, "summaries": summaries}})

	@app.route('/api/mapping/import/apply', methods=['POST'])
	def mappingImportApply():
		payload = request.json or {}
		rows = payload.get("rows", [])
		mode = payload.get("mode", "add_only")
		if payload.get("auto_sort_urls", False):
			rows = sort_part_urls_in_rows(deepcopy(rows))
		errors = validate_rows(rows)
		if any(error["level"] == "error" for error in errors):
			return json_response({"error": "Validation failed.", "data": errors}, 400)
		current = flatten_table(core.table.getData(), preferences)
		try:
			final_rows, summary = reconcile_import_rows(current, rows, mode)
		except ValueError as error:
			return json_response({"error": str(error), "data": None}, 400)
		with core.mapping_automation.lock:
			backups = backup_mapping_files()
			core.table.replaceAll(table_from_rows(final_rows))
			preferences.data = metadata_from_rows(final_rows, preferences)
			preferences.sync()
		compact = f"Import complete | added {summary['added']} | updated {summary['updated']} | deleted {summary['deleted']} | unchanged {summary['unchanged']}"
		record_event("MAPPING_IMPORT_COMPLETE", compact, status="success", details={"mode": mode, "summary": summary, "backups": backups})
		return json_response({"error": False, "data": {"message": compact, "backups": backups, "warnings": errors, "summary": summary}})

	@app.route('/api/mapping/preview-season-order', methods=['POST'])
	def mappingPreviewSeasonOrder():
		payload = request.json or {}
		title = str(payload.get("title", "")).strip()
		season = str(payload.get("season", "1")).strip()
		if not title or not season:
			return json_response({"error": "Title and season are required.", "data": None}, 400)
		entry = core.mapping_resolver.find_entry(title, core.table.getData()) if core.mapping_resolver else None
		if not entry:
			return json_response({"error": "Mapping not found.", "data": None}, 404)
		urls = list((entry.get("seasons", {}) or {}).get(season, []) or [])
		def count_episodes(url):
			return len(aw.Anime(link=url).getEpisodes())
		preview = preview_flatten_order(entry.get("title", title), season, urls, count_episodes)
		record_event(
			"MAPPING_FLATTEN_PREVIEW",
			f"{entry.get('title', title)} S{season} | preview episode order | {len(urls)} URLs",
			status="info",
			title=entry.get("title", title),
			season=season,
			details=preview,
		)
		return json_response({"error": False, "data": preview})

	@app.route('/api/mapping/import-url', methods=['POST'])
	def mappingImportUrl():
		data = request.json or {}
		title = (data.get("title") or "").strip()
		url = (data.get("url") or "").strip()
		if not title or not url:
			return json_response({"error": "Title and URL are required.", "data": None}, 400)
		if not valid_url(url):
			return json_response({"error": "URL must be a valid http(s) URL.", "data": None}, 400)
		draft = {
			"sonarr_title": title,
			"season": "1",
			"source_title": source_title_from_url(url) or title,
			"source_url": url,
			"absolute": False,
			"language_preference": "",
			"detected_language": data.get("detected_language", "UNKNOWN"),
			"source": data.get("source", "manual"),
			"notes": "Imported from URL. Review before saving.",
			"needs_review": True,
			"created_at": datetime.now().isoformat(timespec="seconds"),
		}
		with core.mapping_automation.lock:
			preferences.add_draft(draft)
		return json_response({"error": False, "data": {"message": "Draft mapping imported.", "draft": draft}})

	@app.route('/api/extension/status', methods=['GET'])
	def extensionStatus():
		record_event("EXTENSION_CONNECTED", "Extension connected | AnimeWorld mapping helper ready", status="info")
		return json_response({"error": False, "data": {
			"ok": True,
			"version": "AniDown v3",
			"global_language_preference": preferences.get_global_language(),
		}})

	@app.route('/api/extension/current-page-candidates', methods=['POST'])
	def extensionCurrentPageCandidates():
		payload = request.json or {}
		url = str(payload.get("url", "")).strip()
		title = str(payload.get("detected_title") or payload.get("page_title") or "").strip()
		if not title or not valid_url(url):
			return json_response({"error": "A valid page title and URL are required.", "data": None}, 400)
		audio = str(payload.get("detected_audio") or detected_audio(url, title)).upper()
		if audio not in ("SUB", "DUB", "UNKNOWN"):
			audio = "UNKNOWN"
		record_event("EXTENSION_PAGE_DETECTED", f"Extension page detected | {title} | {audio}", status="info", title=title, details={"url": url, "audio": audio})
		candidates = extension_candidates(payload)
		selected = next((candidate for candidate in candidates if candidate.get("preselected")), None)
		best = candidates[0] if candidates else None
		record_event(
			"EXTENSION_CANDIDATES",
			f"Extension candidates | best match {best['title']} | score {best['score']:.2f}" if best else f"Extension candidates | {title} | no match",
			status="success" if selected else "info",
			title=title,
			details={"url": url, "audio": audio, "candidate_count": len(candidates), "selected": selected, "ambiguous": bool(best and best.get("multiple_possible_candidates"))},
			event_type="MAPPING",
		)
		return json_response({"error": False, "data": {
			"current_page": {"title": title, "url": url, "audio": audio},
			"candidates": candidates,
		}})

	@app.route('/api/extension/save-mapping', methods=['POST'])
	def extensionSaveMapping():
		payload = request.json or {}
		title = str(payload.get("series_title", "")).strip()
		season = str(payload.get("season", "")).strip()
		url = normalize_url(str(payload.get("url", "")).strip())
		audio = str(payload.get("audio", "UNKNOWN")).upper()
		mode = str(payload.get("mode", "add_url"))
		confirmed = bool(payload.get("confirmed", False))
		if not title or not season or not season.isdigit() or not valid_url(url):
			return json_response({"error": "Title, numeric season and valid URL are required.", "data": None}, 400)
		if mode not in ("add_url", "replace_season", "replace_all_season_urls") or audio not in ("SUB", "DUB", "UNKNOWN"):
			return json_response({"error": "Invalid save mode or audio.", "data": None}, 400)
		with core.mapping_automation.lock:
			data = deepcopy(core.table.getData())
			entry = core.mapping_resolver.find_entry(title, data)
			existed_before = entry is not None
			if entry is None:
				entry = {"title": title, "absolute": False, "seasons": {}}
				data.append(entry)
			canonical_title = str(entry.get("title") or title)
			existing_urls = list((entry.get("seasons", {}) or {}).get(season, []) or [])
			conflict = core.mapping_automation._shared_url_conflict(canonical_title, season, url)
			if (existing_urls or conflict) and not confirmed:
				record_event(
					"EXTENSION_OVERWRITE_REQUESTED",
					f"Extension overwrite confirmation required | {canonical_title} S{season} already mapped",
					status="warning",
					title=canonical_title,
					season=season,
					details={"existing_urls": existing_urls, "duplicate_warning": conflict, "mode": mode, "url": url},
				)
				return json_response({"error": False, "data": {
					"ok": True, "action": "needs_confirmation",
					"message": "This season already has a URL or the URL is used by another season. Confirm to continue.",
					"mapping": entry,
					"warning": conflict,
				}})
			entry.setdefault("seasons", {})
			urls = entry["seasons"].setdefault(season, [])
			changed = False
			if mode in ("replace_season", "replace_all_season_urls"):
				changed = urls != [url]
				entry["seasons"][season] = [url]
			elif normalize_url(url) not in {normalize_url(existing) for existing in urls}:
				urls.append(url)
				changed = True
				if core.runtime_settings.status().get("auto_sort_part_urls_when_adding", True):
					entry["seasons"][season] = sort_part_urls(urls)
			backups = backup_mapping_files()
			if changed:
				core.table.replaceAll(data)
			metadata = preferences.get_series(canonical_title)
			metadata["detected_audio"] = audio
			metadata["source"] = "chrome_extension"
			if payload.get("clear_needs_review", True):
				metadata["needs_review"] = False
			preferences.sync()
		action = "updated" if changed and (existed_before and existing_urls) else "saved" if changed else "unchanged"
		compact = f"Extension mapping {action} | {canonical_title} S{season} | {audio} | URL saved" if changed else f"Extension mapping unchanged | {canonical_title} S{season} | URL already present"
		record_event("EXTENSION_MAPPING_SAVED", compact, status="warning" if conflict else "success", title=canonical_title, season=season, details={"url": url, "audio": audio, "mode": mode, "backups": backups, "warning": conflict})
		return json_response({"error": False, "data": {"ok": True, "action": action, "message": compact, "mapping": entry, "backups": backups}})

	@app.route('/api/mapping/drafts/clear', methods=['POST'])
	def mappingDraftsClear():
		with core.mapping_automation.lock:
			preferences.data["drafts"] = []
			preferences.sync()
		return json_response({"error": False, "data": "Drafts cleared."})

	@app.route('/api/mapping/fuzzy', methods=['POST'])
	def mappingFuzzy():
		query = (request.json or {}).get("query", "")
		candidates = flatten_table(core.table.getData(), preferences)
		return json_response({"error": False, "data": fuzzy_matches(query, candidates)})

	@app.route('/api/mapping/aliases', methods=['POST'])
	def mappingAliases():
		core.log.error("OLD_SEARCH_PATH_USED: /api/mapping/aliases; use /api/search-v3/aliases")
		data = request.json or {}
		title = (data.get("title") or "").strip()
		aliases = data.get("aliases", [])
		if not title:
			return json_response({"error": "Title is required.", "data": None}, 400)
		saved = core.search_v3.alias_provider.set_manual_aliases(title, aliases)
		return json_response({"error": False, "data": {"title": title, "aliases": saved}})

	@app.route('/api/mapping/search', methods=['POST'])
	def mappingSearch():
		core.log.error("OLD_SEARCH_PATH_USED: /api/mapping/search; use /api/search-v3/search")
		data = request.json or {}
		title = (data.get("title") or "").strip()
		if not title:
			return json_response({"error": "Title is required.", "data": None}, 400)
		try:
			search = core.mapping_automation.search_mapping(
				title,
				data.get("season", 1),
				language_preference=preferences.language_for(title),
				force_rematch=bool(data.get("force_rematch", False)),
				debug=True,
				refresh_index=bool(data.get("refresh", False)),
			)
			return json_response({"error": False, "data": search})
		except Exception as e:
			return json_response({"error": str(e), "data": None}, 502)

	@app.route('/api/animeworld/index/refresh', methods=['POST'])
	def animeworldIndexRefresh():
		core.log.error("OLD_SEARCH_PATH_USED: /api/animeworld/index/refresh; use /api/search-v3/refresh-index")
		try:
			data = core.search_v3.refresh_index()
			return json_response({"error": False, "data": data})
		except Exception as e:
			return json_response({"error": str(e), "data": None}, 502)

	@app.route('/api/animeworld/search-debug', methods=['GET'])
	def animeworldSearchDebug():
		core.log.error("OLD_SEARCH_PATH_USED: /api/animeworld/search-debug; use /api/search-v3/debug")
		query = (request.args.get("query") or "").strip()
		if not query:
			return json_response({"error": "query is required.", "data": None}, 400)
		try:
			return json_response({"error": False, "data": core.search_v3.search(query, debug=True, auto_refresh=False)})
		except Exception as e:
			return json_response({"error": str(e), "data": None}, 502)

	@app.route('/api/mapping/sonarr/series', methods=['GET'])
	def mappingSonarrSeries():
		try:
			res = core.sonarr.series()
			res.raise_for_status()
			series = []
			for serie in res.json():
				title = serie.get("title", "")
				aliases = [alt.get("title") for alt in serie.get("alternateTitles", []) if alt.get("title")]
				aliases.extend(core.search_v3.alias_provider.manual_aliases(title))
				seasons = [season.get("seasonNumber") for season in serie.get("seasons", []) if season.get("seasonNumber") not in (None, 0)]
				mapped_seasons = [season for season in seasons if not core.mapping_resolver.resolve_existing_mapping(serie, season)["should_search"]]
				mapped = bool(seasons) and len(mapped_seasons) == len(seasons)
				series.append({
					"id": serie.get("id"),
					"title": title,
					"sortTitle": serie.get("sortTitle", title),
					"seasonCount": len(serie.get("seasons", [])),
					"aliases": aliases,
					"mapped": mapped,
					"mappedSeasons": len(mapped_seasons),
					"suggestions": [],
					"search_v3_status": "already_mapped" if mapped else "unmapped",
				})
			return json_response({"error": False, "data": series})
		except Exception as e:
			return json_response({"error": str(e), "data": []}, 502)

	@app.route('/api/table', methods=['GET'])
	def getTable():
		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": core.table.getData()
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/table/add', methods=['POST'])
	def addData():
		data = request.json
		title = data["title"]
		absolute = data["absolute"]
		season = "absolute" if absolute else data["season"]
		links = data["links"]

		core.table.appendSerie(title, absolute)
		core.table.appendUrls(title, season, links)

		log = "Informazioni aggiunte."

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/table/remove', methods=['POST'])
	def removeData():
		data = request.json
		title = data["title"]
		season = data["season"] if "season" in data else None
		link = data["link"] if "link" in data else None

		log = ""

		try:
			if link:
				core.table.removeUrl(title, season, link)
				log = "Url rimosso."
			elif season:
				core.table.removeSeason(title, season)
				log = f"Stagione {season} rimossa."
			else:
				core.table.removeSerie(title)
				log = f"Serie {title} rimossa."
		except KeyError as e:
			log = str(e)

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/table/edit', methods=['POST'])
	def editData():

		data = request.json
		title = data["title"]
		season = data["season"] if "season" in data else None
		link = data["link"] if "link" in data else None

		log = ""
		try:
			if link:
				if core.table.renameUrl(title, season, link[0], link[1]):
					log = "Url modificato."
				else:
					log = "Errore nella modifica dell'url."
			elif season:
				if core.table.renameSeason(title, season[0], season[1]):
					log = f"Stagione {season[0]} modificata."
				else:
					log = "Errore nella modifica della stagione."
			else:
				if core.table.renameSerie(title[0], title[1]):
					log = f"Serie {title} modificata."
				else:
					log = "Errore nella modifica della serie." 
		except KeyError as e:
			log = str(e)

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/settings', methods=['GET'])
	def getSettings():
		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": core.settings.getData()
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/settings', methods=['POST'])
	def updateSettings():
		data = request.json

		log = "ERRORE"
		if data["AutoBind"]:
			core.settings["AutoBind"] = data["AutoBind"]
			log = "Auto Ricerca Link aggiornato."
		elif data["LogLevel"]:
			core.settings["LogLevel"] = data["LogLevel"]
			log = "Livello del Log aggiornato."
		elif data["MoveEp"]:
			core.settings["MoveEp"] = data["MoveEp"]
			log = "Sposta Episodi aggiornato."
		elif data["RenameEp"]:
			core.settings["RenameEp"] = data["RenameEp"]
			log = "Rinomina Episodi aggiornato."
		elif data["ScanDelay"]:
			core.settings["ScanDelay"] = data["ScanDelay"]
			log = "Intervallo Scan aggiornato."
		elif data["TagsMode"]:
			core.settings["TagsMode"] = data["TagsMode"]
			log = "Modalità tags aggiornata."

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/log', methods=['GET'])
	@app.route('/api/log/<row>', methods=['GET'])
	def getLog(row:int=0):
		rows = 100
		row = int(row)

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": [
					x for x in (open("log.log", 'r', encoding='utf-8').readlines())
				][ -(rows + row) :]
				# ][ -100 :  - 1]
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	def sanitized_raw_log(lines=300):
		try:
			data = open("log.log", 'r', encoding='utf-8').read().splitlines()
		except OSError:
			data = []
		data = data[-max(1, min(int(lines), 2000)):]
		text = "\n".join(data)
		for secret in (getattr(core.sonarr, "url", ""), getattr(core.sonarr, "api_key", "")):
			if secret:
				text = text.replace(secret, "[redacted]")
		return text

	@app.route('/api/logs/events', methods=['GET'])
	def logEvents():
		try:
			limit = int(request.args.get("limit", 300))
		except ValueError:
			limit = 300
		events = core.events.list(limit)
		return json_response({"error": False, "data": events, "latest_event_id": events[0]["id"] if events else None})

	@app.route('/api/logs/raw', methods=['GET'])
	@app.route('/api/logs/tail', methods=['GET'])
	def logRaw():
		try:
			lines = int(request.args.get("lines", 300))
		except ValueError:
			lines = 300
		text = sanitized_raw_log(lines)
		return json_response({"error": False, "data": {"text": text, "lines": text.splitlines()}})
		
	@app.route('/api/connections', methods=['GET'])
	def getConnections():

		connections = core.connections_db.getData()
		for conn in connections:
			if core.connections_db.getPath(conn['name']).is_file():
				conn["valid"] = True
			else:
				conn["valid"] = False

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": connections
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/connections/toggle', methods=['POST'])
	def toggleConnection():
		data = request.json

		log = ""
		try:
			res = core.connections_db.toggle(data["name"])
			log = f"La Connection {data['name']} è stata {'attivata' if res else 'disattivata'}."
		except KeyError:
			log = f"Non è stato trovato nessuna Connection con il nome {data['name']}."

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/connections/remove', methods=['POST'])
	def removeConnection():
		data = request.json

		log = ""
		try:
			del core.connections_db[data["name"]]
			log = f"La Connection {data['name']} è stata rimossa."
		except KeyError:
			log = f"Non è stato trovato nessuna Connection con il nome {data['name']}."

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/connections/add', methods=['POST'])
	def addConnection():
		data = request.json

		log = ""
		try:
			core.connections_db.append(data["name"], data["script"], data["active"])
			log = f"La Connection {data['name']} è stata aggiunta."
		except ValueError as e:
			log = str(e)
		except FileNotFoundError:
			log = f"il file {data['script']} non esiste."

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	#  TAGS
		
	@app.route('/api/tags', methods=['GET'])
	def getTags():
		
		tags = core.tags.getData()
		for tag in tags:
			tag["valid"] = True

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": tags
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)
		
	@app.route('/api/tags/toggle', methods=['POST'])
	def toggleTag():
		data = request.json

		tag_id = data["id"]

		log = ""
		try:
			res = core.tags.toggle(data["name"])
			log = f"Il tag {data['name']} è stato {'attivato' if res else 'disattivato'}."
		except KeyError:
			log = f"Non è stato trovato nessun Tag con il nome {data['name']}."


		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/tags/remove', methods=['POST'])
	def removeTag():
		data = request.json

		tag_id = data["id"]
		name = data["name"]

		log = ""
		try:
			del core.tags[name]
			log = f"Il tag {data['name']} è stato rimosso."
		except KeyError:
			log = f"Non è stato trovato nessun Tag con il nome {data['name']}."

		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	@app.route('/api/tags/add', methods=['POST'])
	def addTag():
		data = request.json

		name = data["name"]
		active = data["active"]

		res = core.sonarr.tags()
		res.raise_for_status()

		sonarr_tag = next(filter(lambda x:x["label"] == name, res.json()), None)

		log = ""
		if sonarr_tag:
			tag_id = sonarr_tag["id"]
			try:
				core.tags.append(tag_id, name, active)
				log = f"Il tag {name} è stato aggiunto."
			except ValueError as e:
				log = str(e)
		else:
			log = f"Il Tag {name} non esiste su Sonarr."


		return Response(
			mimetype='application/json',
			status=200,
			response=json.dumps({
				"error": False,
				"data": log
			}),
			headers={"Access-Control-Allow-Origin": "*"}
		)

	# IMPORT / EXPORT 
	@app.route('/ie/table', methods=['GET', 'POST'])
	def ieTable():
		if request.method == 'GET':
			return Response(
				json.dumps(core.table.getData(), indent=4),
				mimetype="text/plain",
				headers={"Content-disposition":"attachment; filename=table.json", "Access-Control-Allow-Origin": "*"},
			)
		else:
			uploaded_file = request.files['file']
			if uploaded_file.filename != '':
				data = uploaded_file.read()
				check = True
				try:
					backup_mapping_files()
					check = core.table.setData(json.loads(data.decode('utf-8')))
				except json.decoder.JSONDecodeError:
					check = False
				
				return Response(
					mimetype='application/json',
					status=200,
					response=json.dumps({
						"error": False if check else f"Table invalida."
					}),
					headers={"Access-Control-Allow-Origin": "*"}
				)

	@app.route('/ie/settings', methods=['GET', 'POST'])
	def ieSettings():
		if request.method == 'GET':
			return Response(
				json.dumps(core.settings.getData(), indent=4),
				mimetype="text/plain",
				headers={"Content-disposition":"attachment; filename=settings.json", "Access-Control-Allow-Origin": "*"}
			)
		else:
			uploaded_file = request.files['file']
			if uploaded_file.filename != '':
				data = uploaded_file.read()
				check = True
				try:
					check = core.settings.setData(json.loads(data.decode('utf-8')))
				except json.decoder.JSONDecodeError:
					check = False
				
				return Response(
					mimetype='application/json',
					status=200,
					response=json.dumps({
						"error": False if check else f"Settings invalide."
					}),
					headers={"Access-Control-Allow-Origin": "*"}
				)

	@app.route('/ie/log', methods=['GET'])
	def ieLog():

		SONARR_URL = core.sonarr.url
		API_KEY = core.sonarr.api_key

		data = open("log.log", 'r', encoding='utf-8').read()
		data = data.replace(SONARR_URL, '█'*len(SONARR_URL)).replace(API_KEY, '█'*len(API_KEY))

		return Response(
			data,
			mimetype="text/plain",
			headers={"Content-disposition":"attachment; filename=log.log", "Access-Control-Allow-Origin": "*"}
		)

	@app.route('/ie/connections', methods=['GET', 'POST'])
	def ieConnections():
		if request.method == 'GET':
			return Response(
				json.dumps(core.connections_db.getData(), indent=4),
				mimetype="text/plain",
				headers={"Content-disposition":"attachment; filename=connections.json", "Access-Control-Allow-Origin": "*"}
			)
		else:
			uploaded_file = request.files['file']
			if uploaded_file.filename != '':
				data = uploaded_file.read()

				check = True
				try:
					check = core.connections_db.setData(json.loads(data.decode('utf-8')))
				except json.decoder.JSONDecodeError:
					check = False
				
				return Response(
					mimetype='application/json',
					status=200,
					response=json.dumps({
						"error": False if check else f"Connections invalide."
					}),
					headers={"Access-Control-Allow-Origin": "*"}
				)

	@app.route('/ie/tags', methods=['GET', 'POST'])
	def ieTags():
		if request.method == 'GET':
			return Response(
				json.dumps(core.tags.getData(), indent=4),
				mimetype="text/plain",
				headers={"Content-disposition":"attachment; filename=tags.json", "Access-Control-Allow-Origin": "*"}
			)
		else:
			uploaded_file = request.files['file']
			if uploaded_file.filename != '':
				data = uploaded_file.read()

				check = True
				try:
					check = core.tags.setData(json.loads(data.decode('utf-8')))
				except json.decoder.JSONDecodeError:
					check = False
				
				return Response(
					mimetype='application/json',
					status=200,
					response=json.dumps({
						"error": False if check else f"Tag invalidi."
					}),
					headers={"Access-Control-Allow-Origin": "*"}
				)
