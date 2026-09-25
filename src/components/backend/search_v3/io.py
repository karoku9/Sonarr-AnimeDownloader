import pathlib
from typing import Any
try:
	from ..atomic_json import read_json_durable, write_json_durable
except ImportError:  # Shadow/legacy harness loads search_v3 as a top-level package.
	import importlib.util
	_atomic_spec = importlib.util.spec_from_file_location(
		"anidown_legacy_atomic_json", pathlib.Path(__file__).resolve().parent.parent / "atomic_json.py")
	_atomic_json = importlib.util.module_from_spec(_atomic_spec)
	_atomic_spec.loader.exec_module(_atomic_json)
	read_json_durable = _atomic_json.read_json_durable
	write_json_durable = _atomic_json.write_json_durable


def read_json_atomic(path: pathlib.Path, default: Any = None) -> Any:
	return read_json_durable(path, default=default)


def write_json_atomic(path: pathlib.Path, data: Any) -> None:
	write_json_durable(path, data)
