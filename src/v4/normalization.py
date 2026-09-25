"""Unicode-preserving title keys. Release semantics are not stripped."""
import re
import unicodedata
from functools import lru_cache

@lru_cache(maxsize=50000)
def normalize_title(value: str) -> str:
    value = value.replace("½", "1/2")
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"(?<!\d)1\s*[/⁄]\s*2(?!\d)", " fractionhalf ", value)
    while True:
        trimmed = re.sub(r"\s*[\[(]\s*(?:ita|sub(?:\s+ita)?|dub|subbed|dubbed|1080p|720p|hd|fhd)\s*[\])]\s*$", " ", value)
        if trimmed == value:
            break
        value = trimmed
    # Remove Latin diacritics only: Japanese dakuten and other scripts remain intact.
    value = "".join("".join(x for x in unicodedata.normalize("NFD",c) if not unicodedata.combining(x)) if "LATIN" in unicodedata.name(c,"") else c for c in value)
    value = re.sub(r"['’‘‛`ʼʻ′]", "", value)
    value = "".join(c if c.isalnum() or c.isspace() else " " for c in value)
    return " ".join(value.split())
