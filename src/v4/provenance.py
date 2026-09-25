"""Per-field origin and explicitly verified season/cour correspondence."""
from dataclasses import asdict, dataclass
import json
from typing import Any

@dataclass(frozen=True)
class FieldProvenance:
    field: str
    value_json: str
    source: Any
    confidence: float | None = None
    derived: bool = False
    implicit: bool = False

    def __post_init__(self):
        json.loads(self.value_json)
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("Confidence must be between zero and one")

    @property
    def value(self):
        return json.loads(self.value_json)

    @property
    def reliable(self):
        return self.source.reliable and not self.derived

    @classmethod
    def create(cls, field, value, source, confidence=None, derived=False, implicit=False):
        from .models import encode
        return cls(field,json.dumps(value,default=encode,sort_keys=True),source,confidence,derived,implicit)

    def to_dict(self):
        return {"field":self.field,"value":self.value,"source":asdict(self.source),"confidence":self.confidence,"derived":self.derived,"implicit":self.implicit}

    @classmethod
    def from_dict(cls, data):
        from .models import source_from_dict
        return cls.create(data["field"],data["value"],source_from_dict(data["source"]),data.get("confidence"),data.get("derived",False),data.get("implicit",False))

@dataclass(frozen=True)
class SeasonCrosswalk:
    id: str
    sonarr_season: int
    candidate_namespace: str = "catalog"
    candidate_season: int | None = None
    scene_season: int | None = None
    cour: int | None = None
    part: int | None = None
    season_title: str | None = None
    candidate_release_id: str | None = None
    verified: bool = False
    source: Any = None
    reason: str = ""

    def __post_init__(self):
        if self.sonarr_season < 0:
            raise ValueError("Invalid crosswalk season")
        if self.verified and (not self.reason.strip() or self.source is None):
            raise ValueError("Verified crosswalk requires source and explanation")

    def applicable(self, candidate):
        from .normalization import normalize_title
        if not self.verified or self.source is None or not self.source.reliable:
            return False
        if self.candidate_namespace != candidate.season_namespace or not candidate.trusted("season_namespace"):
            return False
        anchored = False
        for field,observed in [("candidate_season",candidate.season_number),("scene_season",candidate.scene_season_number),("cour",candidate.cour),("part",candidate.part),("candidate_release_id",candidate.release_id)]:
            expected=getattr(self,field)
            if expected is not None:
                metadata_field={"candidate_season":"season_number","scene_season":"scene_season_number","candidate_release_id":"release_id"}.get(field,field)
                if expected != observed or not candidate.trusted(metadata_field):
                    return False
                anchored = True
        if self.season_title is not None:
            if normalize_title(self.season_title)!=normalize_title(candidate.canonical_title) or not candidate.trusted("canonical_title"):
                return False
            anchored=True
        return anchored

    @classmethod
    def from_dict(cls, data):
        from .models import source_from_dict
        data=dict(data)
        data["source"]=source_from_dict(data["source"]) if data.get("source") else None
        return cls(**data)

@dataclass(frozen=True)
class MatcherPolicy:
    fuzzy_threshold: float = .88
    date_tolerance_days: int = 1

    def __post_init__(self):
        if not 0 <= self.fuzzy_threshold <= 1:
            raise ValueError("Invalid fuzzy threshold")
        if not isinstance(self.date_tolerance_days,int) or not 0 <= self.date_tolerance_days <= 7:
            raise ValueError("Date tolerance must be zero to seven days")
