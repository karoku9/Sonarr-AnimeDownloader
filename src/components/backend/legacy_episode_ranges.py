"""Small dependency-free parser for legacy AnimeWorld episode coordinates.

This module intentionally stays below the V4 release boundary.  Dockerfile.v4's
deny-all context does not include ``src/components``.
"""
import re


_WHOLE = re.compile(r"^\d+$")
_MERGED = re.compile(r"^(\d+)-(\d+)$")


def expand_episode_number(value, *, offset=0):
	"""Return immutable integer endpoints for a whole or merged source number.

	Fractional specials and malformed/reversed ranges are deliberately ignored, which
	preserves the legacy caller's behavior without allowing a partial range mutation.
	"""
	raw = str(value or "")
	if _WHOLE.fullmatch(raw):
		return (int(raw) + int(offset),)
	match = _MERGED.fullmatch(raw)
	if not match:
		return ()
	start, end = (int(match.group(1)), int(match.group(2)))
	if start < 1 or end < start:
		return ()
	return tuple(number + int(offset) for number in range(start, end + 1))
