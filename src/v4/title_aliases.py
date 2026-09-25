"""Small reviewed title-equivalence registry; data never creates season evidence."""
import json
from functools import lru_cache
from pathlib import Path
from .normalization import normalize_title


@lru_cache(maxsize=1)
def _sets():
    data=json.loads((Path(__file__).with_name("title_aliases.json")).read_text(encoding="utf-8"))
    if data.get("schema_version")!=1:raise ValueError("Unsupported title alias schema")
    return tuple(tuple(item["titles"]) for item in data["equivalence_sets"])


@lru_cache(maxsize=1)
def _lookup():
    result={}
    for values in _sets():
        frozen=tuple(values)
        for value in values:
            result[normalize_title(value)]=frozen
    return result


def equivalents(title):
    values=_lookup().get(normalize_title(title),())
    return {title,*values}
