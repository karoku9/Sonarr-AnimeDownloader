import pathlib
import threading
from copy import deepcopy
from typing import Any

from .search_v3.io import read_json_atomic, write_json_atomic


RUNTIME_DEFAULTS = {
	"downloads_paused": False,
	"search_only_mode": False,
	"auto_save_high_confidence_mappings": True,
	"auto_mapping_min_score": 0.95,
	"log_panel_visible": True,
	"compact_mode": False,
	"log_verbosity": "COMPACT",
	"auto_sort_part_urls_when_adding": True,
}


class RuntimeSettings:
	"""Thread-safe runtime switches stored outside table.json."""

	def __init__(self, path: pathlib.Path, logger=None, events=None) -> None:
		self.path = path
		self.log = logger
		self.events = events
		self._lock = threading.RLock()
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._data = self._load()
		self._sync_if_needed()

	def _load(self) -> dict[str, Any]:
		try:
			data = read_json_atomic(self.path, {})
			return data if isinstance(data, dict) else {}
		except (OSError, ValueError):
			return {}

	def _sync_if_needed(self) -> None:
		changed = False
		for key, value in RUNTIME_DEFAULTS.items():
			if key not in self._data:
				self._data[key] = value
				changed = True
		try:
			self._data["auto_mapping_min_score"] = float(self._data["auto_mapping_min_score"])
		except (TypeError, ValueError):
			self._data["auto_mapping_min_score"] = RUNTIME_DEFAULTS["auto_mapping_min_score"]
			changed = True
		if changed or not self.path.exists():
			write_json_atomic(self.path, self._data)

	def status(self) -> dict[str, Any]:
		with self._lock:
			return deepcopy(self._data)

	def update(self, **changes: Any) -> dict[str, Any]:
		with self._lock:
			for key, value in changes.items():
				if key not in RUNTIME_DEFAULTS:
					continue
				if key == "auto_mapping_min_score":
					value = max(0.0, min(1.0, float(value)))
				elif key == "log_verbosity":
					value = str(value).upper()
					if value not in ("COMPACT", "NORMAL", "DEBUG", "RAW"):
						value = RUNTIME_DEFAULTS["log_verbosity"]
				else:
					value = bool(value)
				self._data[key] = value
			write_json_atomic(self.path, self._data)
			if self.log:
				for key in changes:
					if key in self._data:
						self.log.info(f"RUNTIME_SETTING_CHANGED\n  {key}={str(self._data[key]).lower()}")
						if self.events:
							self.events.record({
								"type": "RUNTIME",
								"event": "RUNTIME_SETTING_CHANGED",
								"status": "info",
								"compact": f"Runtime setting changed | {key}={self._data[key]}",
								"details": {"setting": key, "value": self._data[key]},
							})
			return deepcopy(self._data)

	def download_block_reason(self) -> str | None:
		with self._lock:
			if self._data.get("search_only_mode", False):
				return "search_only"
			if self._data.get("downloads_paused", False):
				return "paused"
			return None
