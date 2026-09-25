from typing import Any

from anidown_eligibility import eligible_legacy_series


def active_tag_ids(tags) -> set[int]:
	return {tag["id"] for tag in tags if tags.isActive(tag["id"])}


def matching_active_tag_ids(series: dict[str, Any], tags) -> set[int]:
	return set(series.get("tags", []) or []).intersection(active_tag_ids(tags))


def is_anime_series(series: dict[str, Any], settings, tags, *, respect_tags: bool = True) -> tuple[bool, bool]:
	"""Return (eligible, tag_override) for a Sonarr series.

	Sonarr occasionally classifies an anime as ``standard``. In whitelist mode an
	explicit active AniDown tag is a stronger user signal than that classification,
	so the tagged series remains eligible without admitting unrelated TV series.
	"""
	whitelist = bool(respect_tags and str(settings["TagsMode"] or "").strip().upper() == "WHITELIST"
		and matching_active_tag_ids(series, tags))
	eligible = eligible_legacy_series(series, whitelist_active=whitelist)
	return eligible, bool(eligible and whitelist and str(series.get("seriesType", series.get("type", "")) or "").strip().lower() != "anime")
