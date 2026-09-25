import pathlib
from datetime import datetime, timezone
from typing import Any

from .io import read_json_atomic, write_json_atomic


class SearchIndexStore:
	def __init__(self, path: pathlib.Path, base_url: str) -> None:
		self.path = path
		self.base_url = base_url.rstrip("/")
		self.path.parent.mkdir(parents=True, exist_ok=True)

	def empty(self) -> dict[str, Any]:
		return {
			"version": 3,
			"generated_at": None,
			"source_base_url": self.base_url,
			"entries": [],
		}

	def exists(self) -> bool:
		return self.path.exists() and self.path.stat().st_size > 0

	def load(self) -> dict[str, Any]:
		if not self.exists():
			return self.empty()
		try:
			data = read_json_atomic(self.path, self.empty())
			if data.get("version") != 3 or not isinstance(data.get("entries"), list):
				return self.empty()
			return data
		except (OSError, ValueError, AttributeError):
			return self.empty()

	def save_atomic(self, data: dict[str, Any]) -> None:
		write_json_atomic(self.path, data)

	def age_hours(self, data: dict[str, Any] | None = None) -> float | None:
		data = data or self.load()
		generated_at = data.get("generated_at")
		if not generated_at:
			return None
		try:
			generated = datetime.fromisoformat(generated_at)
			if generated.tzinfo is None:
				generated = generated.replace(tzinfo=timezone.utc)
			return round((datetime.now(timezone.utc) - generated).total_seconds() / 3600, 2)
		except ValueError:
			return None

	def status(self) -> dict[str, Any]:
		data = self.load()
		return {
			"exists": self.exists(),
			"size": len(data.get("entries", [])),
			"generated_at": data.get("generated_at"),
			"age_hours": self.age_hours(data),
			"source_base_url": data.get("source_base_url", self.base_url),
		}
