import re
import unicodedata
from typing import Any


def ascii_text(value: str) -> str:
	value = (value or "").replace("\u00d7", "x").replace("\u2715", "x")
	return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def normalized_apostrophes(value: str) -> str:
	return (value or "").replace("\u2018", "'").replace("\u2019", "'").replace("\u0060", "'")


def normalize_title(value: str) -> str:
	value = normalized_apostrophes(value)
	value = ascii_text(value)
	value = re.sub(r"\(\s*ita\s*\)", " ", value, flags=re.I)
	value = value.replace("'", "")
	value = re.sub(r"[^\w\s]", " ", value.lower())
	return re.sub(r"\s+", " ", value).strip()


def unique_text(values: list[str]) -> list[str]:
	seen = set()
	result = []
	for value in values:
		value = str(value or "").strip()
		key = normalize_title(value)
		if value and key and key not in seen:
			seen.add(key)
			result.append(value)
	return result


def unique_variants(values: list[str]) -> list[str]:
	seen = set()
	result = []
	for value in values:
		value = str(value or "").strip()
		key = normalized_apostrophes(value).casefold()
		if value and key not in seen:
			seen.add(key)
			result.append(value)
	return result


def season_key(value: Any) -> str | None:
	if value is None or value == "":
		return None
	try:
		return str(int(value))
	except (TypeError, ValueError):
		return str(value).strip()


def alternate_title_matches_season(alias: dict[str, Any] | str, season: str | int | None) -> bool:
	"""Whether a Sonarr alternate title applies to the requested season."""
	if season is None or not isinstance(alias, dict):
		return True
	markers = [
		season_key(alias.get(key))
		for key in ("seasonNumber", "sceneSeasonNumber")
		if alias.get(key) not in (None, "")
	]
	if not markers or "-1" in markers:
		return True
	return season_key(season) in markers


def alternate_title_is_season_specific(alias: dict[str, Any] | str, season: str | int | None) -> bool:
	if season is None or not isinstance(alias, dict):
		return False
	markers = [
		season_key(alias.get(key))
		for key in ("seasonNumber", "sceneSeasonNumber")
		if alias.get(key) not in (None, "")
	]
	return bool(markers) and "-1" not in markers and season_key(season) in markers


class TitleVariantBuilder:
	def collect_titles(self, series_or_title: dict[str, Any] | str, season: str | int | None = None) -> tuple[str, list[str]]:
		if isinstance(series_or_title, str):
			return series_or_title, []
		title = str(series_or_title.get("title", "") or "")
		aliases = []
		for key in ("cleanTitle", "sortTitle", "originalTitle"):
			if series_or_title.get(key):
				aliases.append(str(series_or_title[key]))
		for alias in series_or_title.get("alternateTitles", []) or []:
			if isinstance(alias, dict) and alias.get("title") and alternate_title_matches_season(alias, season):
				aliases.append(str(alias["title"]))
			elif isinstance(alias, str):
				aliases.append(alias)
		return title, unique_text(aliases)

	def build(self, series_or_title: dict[str, Any] | str, extra_aliases: list[str] | None = None, season: str | int | None = None) -> dict[str, Any]:
		title, sonarr_aliases = self.collect_titles(series_or_title, season)
		season_aliases = []
		if isinstance(series_or_title, dict):
			season_aliases = unique_text([
				str(alias.get("title", ""))
				for alias in (series_or_title.get("alternateTitles", []) or [])
				if isinstance(alias, dict) and alias.get("title") and alternate_title_is_season_specific(alias, season)
			])
		all_inputs = unique_variants([title] + sonarr_aliases + list(extra_aliases or []))
		raw_variants = []
		for value in all_inputs:
			raw_variants.extend(self.variants_for(value))
		raw_variants = unique_variants(raw_variants)
		return {
			"title": title,
			"sonarr_aliases": sonarr_aliases,
			"season_aliases": season_aliases,
			"raw_variants": raw_variants,
			"normalized_variants": unique_text([normalize_title(value) for value in raw_variants]),
		}

	def variants_for(self, value: str) -> list[str]:
		value = normalized_apostrophes(value)
		no_year = re.sub(r"\s*\(\s*\d{4}\s*\)\s*$", "", value).strip()
		no_brackets = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*", " ", value).strip()
		no_subtitle = re.split(r"\s*[:;|]\s*", value, maxsplit=1)[0].strip()
		no_punctuation = re.sub(r"[^\w\s]", " ", ascii_text(value).replace("'", "")).strip()
		no_suffix = re.sub(
			r"\s*(?:-|:)?\s*(?:season\s+\d+|\d+(?:st|nd|rd|th)?\s+season|part\s+\d+)\s*$",
			"",
			value,
			flags=re.I,
		).strip()
		variants = [
			value,
			no_punctuation,
			no_year,
			no_brackets,
			no_subtitle,
			no_suffix,
			ascii_text(value),
			normalize_title(value),
		]
		if re.search(r"\bcan[' ]?t\b", value, flags=re.I):
			variants.append(re.sub(r"\bcan[' ]?t\b", "Cannot", value, flags=re.I))
		if re.search(r"\bcannot\b", value, flags=re.I):
			variants.append(re.sub(r"\bcannot\b", "Can't", value, flags=re.I))
		if "&" in value:
			variants.append(value.replace("&", "and"))
		if re.search(r"\band\b", value, flags=re.I):
			variants.append(re.sub(r"\band\b", "&", value, flags=re.I))
		return variants
