from difflib import SequenceMatcher
from typing import Any

from .variants import normalize_title


class CandidateScorer:
	def score_all(
		self,
		entries: list[dict[str, Any]],
		normalized_variants: list[str],
		manual_aliases: list[str],
		external_aliases: list[str],
		sonarr_aliases: list[str] | None = None,
		season_aliases: list[str] | None = None,
		debug: bool = False,
	) -> list[dict[str, Any]]:
		manual_keys = {normalize_title(alias) for alias in manual_aliases}
		external_keys = {normalize_title(alias) for alias in external_aliases}
		sonarr_keys = {normalize_title(alias) for alias in (sonarr_aliases or [])}
		season_keys = {normalize_title(alias) for alias in (season_aliases or [])}
		query_keys = {query for query in normalized_variants if query}
		exact_entries = []
		for entry in entries:
			keys = {entry.get("normalized_title", ""), entry.get("normalized_slug", "")}
			keys.update(normalize_title(alias) for alias in (entry.get("aliases", []) or []))
			if query_keys.intersection(keys):
				exact_entries.append(entry)
		# Exact matches already include parallel SUB/DUB versions; avoid thousands of
		# fuzzy comparisons during large Sonarr parse runs.
		scored_entries = exact_entries or entries
		results = []
		for entry in scored_entries:
			candidate = self.score_entry(entry, normalized_variants, manual_keys, external_keys, sonarr_keys, season_keys)
			if candidate["score"] >= 0.50 or debug:
				results.append(candidate)
		results.sort(key=lambda result: (bool(result.get("season_match")) and result["score"] >= 0.90, result["score"]), reverse=True)
		return results

	def score_entry(self, entry: dict[str, Any], queries: list[str], manual_keys: set[str], external_keys: set[str], sonarr_keys: set[str], season_keys: set[str] | None = None) -> dict[str, Any]:
		season_keys = season_keys or set()
		table_keys = {normalize_title(title) for title in (entry.get("table_titles", []) or [])}
		fields = [("title", entry.get("normalized_title", ""), 1.0)]
		if entry.get("normalized_slug"):
			fields.append(("slug", entry["normalized_slug"], 0.95))
		for alias in entry.get("aliases", []) or []:
			key = normalize_title(alias)
			source = "manual_alias" if key in manual_keys else "external_alias" if key in external_keys else "table_alias" if key in table_keys else "alias"
			fields.append((source, key, 0.98))
		best = (0.0, "no comparable field", "", "")
		for query in queries:
			for field, value, exact_score in fields:
				if not query or not value:
					continue
				if query == value:
					matched_field = self.query_source(query, field, manual_keys, external_keys, sonarr_keys)
					best = max(best, (exact_score, f"exact normalized {matched_field} match", query, matched_field), key=lambda item: item[0])
					continue
				if query in value or value in query:
					length_ratio = min(len(query), len(value)) / max(len(query), len(value))
					contains_score = 0.70 + 0.12 * length_ratio
					matched_field = self.query_source(query, field, manual_keys, external_keys, sonarr_keys)
					best = max(best, (contains_score, f"contains {matched_field} match", query, matched_field), key=lambda item: item[0])
				ratio = SequenceMatcher(None, query, value).ratio()
				if ratio >= 0.40:
					score = 0.50 + ((ratio - 0.40) / 0.60) * (0.40 if field == "slug" else 0.45)
					matched_field = self.query_source(query, field, manual_keys, external_keys, sonarr_keys)
					best = max(best, (score, f"fuzzy {matched_field} match", query, matched_field), key=lambda item: item[0])
		score, reason, query, field = best
		confidence = "high" if score >= 0.90 else "medium" if score >= 0.70 else "low"
		return {
			"title": entry.get("title", ""),
			"url": entry.get("url", ""),
			"score": round(score, 3),
			"confidence": confidence,
			"audio": entry.get("audio", "UNKNOWN"),
			"reason": reason,
			"matched_query": query,
			"matched_field": "table" if entry.get("source") == "table" and field == "title" else field,
			"season_match": query in season_keys,
			"source": entry.get("source", "animeworld_catalog"),
			"slug": entry.get("slug", ""),
			"table_titles": list(entry.get("table_titles", []) or []),
			"table_seasons": [str(value) for value in (entry.get("table_seasons", []) or [])],
		}

	def query_source(self, query: str, field: str, manual_keys: set[str], external_keys: set[str], sonarr_keys: set[str]) -> str:
		if query in manual_keys:
			return "manual_alias"
		if query in external_keys:
			return "external_alias"
		if query in sonarr_keys:
			return "alias"
		return field
