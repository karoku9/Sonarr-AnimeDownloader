import json
import logging
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any

from .io import write_json_atomic


class SearchDiagnostics:
	def __init__(self, path: pathlib.Path, logger: logging.Logger | None = None, events=None) -> None:
		self.path = path
		self.log = logger or logging.getLogger(__name__)
		self.events = events
		self._lock = threading.Lock()
		self.path.parent.mkdir(parents=True, exist_ok=True)

	def record(self, result: dict[str, Any], season: str | int | None, language_preference: str) -> None:
		index = result["index"]
		self.log.info("SEARCH_V3_START")
		self.log.info(f"  title={result['query']}")
		self.log.info(f"  season={season}")
		self.log.info(f"  language_preference={language_preference}")
		self.log.info(f"  index_size={index['size']}")
		self.log.info(f"  index_age_hours={index['age_hours']}")
		self.log.info("SEARCH_V3_VARIANTS")
		self.log.info(f"  generated_variants={result['title_variants']}")
		self.log.info("SEARCH_V3_CANDIDATES")
		for candidate in result["top_candidates"][:5]:
			self.log.info(
				f"  {candidate['title']} score={candidate['score']} "
				f"audio={candidate['audio']} reason={candidate['reason']} "
				f"field={candidate['matched_field']}"
			)
		self.log.info("SEARCH_V3_RESULT")
		self.log.info(f"  status={result['status']}")
		self.log.info(f"  selected_candidate={result.get('selected_candidate')}")
		self.log.info(f"  errors={result.get('errors', [])}")
		if self.events:
			candidate = result.get("selected_candidate") or {}
			status = result.get("status", "no_result")
			title_season = f"{result['query']} S{season}" if season is not None else result["query"]
			if status == "matched":
				compact = f"{title_season} | matched {candidate.get('audio', 'UNKNOWN')} | score {float(candidate.get('score', 0)):.2f}"
				event_status = "success"
			elif status == "needs_review":
				compact = f"{title_season} | needs review | multiple or uncertain candidates"
				event_status = "warning"
			else:
				compact = f"{title_season} | no AnimeWorld match found"
				event_status = "error"
			self.events.upsert(result.get("operation_id") or self.events.operation_id("search_v3", result["query"], season), {
				"level": "INFO" if event_status != "error" else "WARNING",
				"type": "SEARCH_V3",
				"event": "SEARCH_V3_RESULT",
				"status": event_status,
				"title": result["query"],
				"season": season,
				"compact": compact,
				"message": compact,
				"details": {
					"language_preference": language_preference,
					"index": index,
					"generated_variants": result.get("title_variants", []),
					"candidates": result.get("top_candidates", [])[:10],
					"selected_candidate": result.get("selected_candidate"),
					"errors": result.get("errors", []),
				},
			})
		self.save(result)

	def save(self, result: dict[str, Any]) -> None:
		data = {"search_engine": "v3", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "last_search": result}
		with self._lock:
			write_json_atomic(self.path, data)
