"""Durable JSON persistence for the quarantined legacy runtime.

The V4 release image excludes this package, but local legacy tools still share these
mutable files.  Publication is serialized across threads/processes, validated before
replace, fsynced, and keeps a last-known-good generation.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time


_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path):
	key = str(Path(path).resolve())
	with _THREAD_LOCKS_GUARD:
		return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _file_lock(path):
	lock_path = Path(path).with_name(Path(path).name + ".lock")
	lock_path.parent.mkdir(parents=True, exist_ok=True)
	with _thread_lock(lock_path):
		with lock_path.open("a+b") as handle:
			handle.seek(0, os.SEEK_END)
			if handle.tell() == 0:
				handle.write(b"0")
				handle.flush()
			handle.seek(0)
			if os.name == "nt":
				import msvcrt

				msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
			else:
				import fcntl

				fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
			try:
				yield
			finally:
				handle.seek(0)
				if os.name == "nt":
					msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
				else:
					fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate(value, validator=None):
	if validator is not None:
		validator(value)
	return value


def _read(path, validator=None):
	with Path(path).open("r", encoding="utf-8") as handle:
		return _validate(json.load(handle), validator)


def _fsync_directory(path):
	try:
		descriptor = os.open(Path(path).parent, os.O_RDONLY)
	except (AttributeError, OSError, TypeError):
		return
	try:
		os.fsync(descriptor)
	except OSError:
		pass
	finally:
		os.close(descriptor)


def _replace(source, destination, attempts=8):
	for attempt in range(attempts):
		try:
			os.replace(source, destination)
			return
		except PermissionError:
			if attempt + 1 >= attempts:
				raise
			time.sleep(min(0.05, 0.005 * (2 ** attempt)))


def _publish(path, data, validator=None):
	path = Path(path)
	temporary = None
	try:
		with tempfile.NamedTemporaryFile(
			"w", encoding="utf-8", newline="\n", dir=path.parent,
			prefix=path.name + ".", suffix=".tmp", delete=False,
		) as handle:
			temporary = Path(handle.name)
			json.dump(data, handle, indent=2, ensure_ascii=False)
			handle.write("\n")
			handle.flush()
			os.fsync(handle.fileno())
		_validate(_read(temporary), validator)
		_replace(temporary, path)
		temporary = None
		_fsync_directory(path)
	finally:
		if temporary is not None:
			try:
				temporary.unlink(missing_ok=True)
			except OSError:
				pass


def write_json_durable(path, data, validator=None):
	"""Validate and atomically publish JSON plus a last-known-good generation."""
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	_validate(data, validator)
	with _file_lock(path):
		_publish(path, data, validator)
		_publish(path.with_name(path.name + ".lkg"), data, validator)


def read_json_durable(path, default=None, validator=None):
	"""Read one complete generation, recovering from the LKG copy when possible."""
	path = Path(path)
	with _file_lock(path):
		try:
			return _read(path, validator)
		except (OSError, ValueError, TypeError, json.JSONDecodeError):
			backup = path.with_name(path.name + ".lkg")
			if backup.exists():
				value = _read(backup, validator)
				_publish(path, value, validator)
				return value
			if default is not None:
				return default
			raise
