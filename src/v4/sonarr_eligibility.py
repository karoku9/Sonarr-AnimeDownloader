"""V4 import surface for the shared Sonarr eligibility policy."""

from anidown_eligibility import (
    eligible_legacy_series,
    eligible_live_series,
    has_authoritative_animeworld_tag,
    normalized_tags,
)

__all__ = (
    "eligible_legacy_series",
    "eligible_live_series",
    "has_authoritative_animeworld_tag",
    "normalized_tags",
)
