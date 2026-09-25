import pathlib
import re
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .search_v3.io import read_json_atomic, write_json_atomic


class StructuredEventStore:
	"""Small persistent event buffer for the dashboard log panel."""

	def __init__(self, path: pathlib.Path, max_events: int = 600) -> None:
		self.path = path
		self.max_events = max_events
		self._lock = threading.RLock()
		self._batch_depth = 0
		self._batch_dirty = False
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._events = self._load()

	def _load(self) -> list[dict[str, Any]]:
		try:
			data = read_json_atomic(self.path, [])
			return data if isinstance(data, list) else []
		except (OSError, ValueError):
			return []

	def operation_id(self, prefix: str, title: str = "", season: str | int | None = None) -> str:
		name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32] or "operation"
		season_part = f"_S{season}" if season is not None else ""
		return f"{prefix}_{name}{season_part}_{uuid.uuid4().hex[:10]}"

	def record(self, event: dict[str, Any]) -> dict[str, Any]:
		with self._lock:
			item = self._defaults(event)
			self._events.append(item)
			self._request_sync()
			return deepcopy(item)

	def upsert(self, operation_id: str, event: dict[str, Any]) -> dict[str, Any]:
		with self._lock:
			for index, existing in enumerate(self._events):
				if existing.get("operation_id") != operation_id:
					continue
				merged = dict(existing)
				merged.update(event)
				details = dict(existing.get("details", {}))
				details.update(event.get("details", {}))
				merged["details"] = details
				merged["operation_id"] = operation_id
				self._events[index] = self._defaults(merged)
				self._request_sync()
				return deepcopy(self._events[index])
			item = dict(event)
			item["operation_id"] = operation_id
			return self.record(item)

	def list(self, limit: int = 300) -> list[dict[str, Any]]:
		with self._lock:
			limit = max(1, min(int(limit), self.max_events))
			return deepcopy(self._events[-limit:][::-1])

	def _defaults(self, event: dict[str, Any]) -> dict[str, Any]:
		item = dict(event)
		item.setdefault("id", uuid.uuid4().hex)
		item.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
		item.setdefault("level", "INFO")
		item.setdefault("type", "RUNTIME")
		item.setdefault("event", "ACTIVITY")
		item.setdefault("status", "info")
		item.setdefault("message", item.get("compact", item["event"]))
		item.setdefault("compact", item["message"])
		item.setdefault("details", {})
		return item

	def _sync(self) -> None:
		self._events = self._events[-self.max_events:]
		write_json_atomic(self.path, self._events)

	def _request_sync(self) -> None:
		if self._batch_depth:
			self._batch_dirty = True
			return
		self._sync()

	@contextmanager
	def batch(self):
		"""Keep events in memory and persist them once after a bulk operation."""
		with self._lock:
			self._batch_depth += 1
		try:
			yield self
		finally:
			with self._lock:
				self._batch_depth -= 1
				if self._batch_depth == 0 and self._batch_dirty:
					self._batch_dirty = False
					self._sync()
