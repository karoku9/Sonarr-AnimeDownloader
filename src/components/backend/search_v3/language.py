from typing import Any


LANGUAGE_VALUES = ("AUTO", "DUB_FIRST", "SUB_FIRST", "DUB_ONLY", "SUB_ONLY")


class LanguagePreferenceResolver:
	def effective(self, requested: str, global_default: str = "AUTO") -> str:
		value = requested if requested in LANGUAGE_VALUES else "AUTO"
		if value == "AUTO" and global_default in LANGUAGE_VALUES and global_default != "AUTO":
			return global_default
		return value

	def apply(self, candidates: list[dict[str, Any]], requested: str, global_default: str = "AUTO") -> list[dict[str, Any]]:
		preference = self.effective(requested, global_default)
		filtered = candidates
		if preference == "DUB_ONLY":
			filtered = [candidate for candidate in candidates if candidate.get("audio") == "DUB"]
		elif preference == "SUB_ONLY":
			filtered = [candidate for candidate in candidates if candidate.get("audio") == "SUB"]
		ranked = []
		for candidate in filtered:
			item = dict(candidate)
			bonus = 0
			if preference == "DUB_FIRST" and item.get("audio") == "DUB":
				bonus = 0.010
			elif preference == "SUB_FIRST" and item.get("audio") == "SUB":
				bonus = 0.010
			item["language_preference"] = preference
			item["language_rank_score"] = round(item["score"] + bonus, 3)
			ranked.append(item)
		ranked.sort(key=lambda item: (bool(item.get("season_match")) and item["score"] >= 0.90, item["language_rank_score"], item["score"]), reverse=True)
		return ranked
