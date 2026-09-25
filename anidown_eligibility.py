"""Dependency-free Sonarr eligibility policy shared by legacy and V4 runtimes."""


def normalized_tags(values):
    return {str(value).strip().casefold() for value in (values or []) if str(value).strip()}


def has_authoritative_animeworld_tag(series):
    return "animeworld" in normalized_tags(series.get("tag_labels"))


def eligible_live_series(series):
    """V4 admits only the explicit operator tag, regardless of Sonarr type."""
    return has_authoritative_animeworld_tag(series)


def eligible_legacy_series(series, *, whitelist_active):
    """Retain legacy anime behavior while honoring its explicit whitelist."""
    series_type = str(series.get("seriesType", series.get("type", "")) or "").strip().casefold()
    return series_type == "anime" or bool(whitelist_active)
