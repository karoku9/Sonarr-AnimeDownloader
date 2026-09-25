"""Immutable metadata, explainable decisions and self-contained review snapshots."""
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
import json
from typing import Any

class Audio(str, Enum):
    UNKNOWN = "UNKNOWN"
    SUB = "SUB"
    DUB = "DUB"

class ReasonCode(str, Enum):
    MULTIPLE_EQUIVALENT_CANDIDATES = "multiple_equivalent_candidates"
    SEASON_CONFLICT = "season_conflict"
    RELEASE_DATE_CONFLICT = "release_date_conflict"
    EPISODE_COUNT_CONFLICT = "episode_count_conflict"
    DUPLICATE_URL_ACROSS_SEASONS = "duplicate_url_across_seasons"
    LANGUAGE_MISMATCH = "language_mismatch"
    INSUFFICIENT_METADATA = "insufficient_metadata"
    LOW_CONFIDENCE_TITLE_MATCH = "low_confidence_title_match"
    IDENTITY_CONFLICT = "identity_conflict"
    NO_TITLE_MATCH = "no_title_match"

@dataclass(frozen=True)
class SourceMetadata:
    name: str = "catalog"
    record_id: str | None = None
    reliable: bool = True
    fetched_at: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", tuple(tuple(v) for v in self.attributes))

@dataclass(frozen=True)
class Alias:
    text: str
    language: str | None = None
    kind: str = "alternate"
    season_number: int | None = None
    scene_season_number: int | None = None
    scene_namespace: str = "sonarr"
    source: SourceMetadata = field(default_factory=SourceMetadata)

    def scope(self) -> int | None:
        # Sonarr's real season number takes precedence over scene numbering.
        if self.season_number not in (None, -1):
            return self.season_number
        if self.scene_namespace == "sonarr" and self.scene_season_number not in (None, -1):
            return self.scene_season_number
        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alias":
        data = dict(data)
        data["source"] = source_from_dict(data.get("source"))
        return cls(**data)

@dataclass(frozen=True, kw_only=True)
class Metadata:
    alternate_titles: tuple[Alias, ...] = ()
    season_number: int | None = None
    scene_season_number: int | None = None
    season_namespace: str = "sonarr"
    target_seasons: tuple[int, ...] = ()
    premiere_date: date | None = None
    air_date: date | None = None
    release_year: int | None = None
    date_kind: str = "original_release"
    episode_count: int | None = None
    episodes_complete: bool = False
    episode_scope: str = "season"
    external_ids: tuple[tuple[str, str], ...] = ()
    source: SourceMetadata = field(default_factory=SourceMetadata)
    field_provenance: tuple[Any, ...] = ()
    crosswalks: tuple[Any, ...] = ()
    cour: int | None = None
    part: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self,"alternate_titles",tuple(self.alternate_titles))
        object.__setattr__(self,"target_seasons",tuple(self.target_seasons))
        object.__setattr__(self,"external_ids",tuple(tuple(v) for v in self.external_ids))
        if hasattr(self,"mapped_seasons"):
            object.__setattr__(self,"mapped_seasons",tuple(self.mapped_seasons))
            object.__setattr__(self,"season_coverage",tuple(tuple(v) for v in self.season_coverage))
        from .provenance import FieldProvenance
        provided = {p.field:p for p in self.field_provenance if not p.implicit}
        if len({p.field for p in self.field_provenance})!=len(self.field_provenance):
            raise ValueError("Duplicate field provenance")
        important=("canonical_title","season_number","scene_season_number","season_namespace","target_seasons","cour","part","premiere_date","air_date","release_year","date_kind","episode_count","episodes_complete","episode_scope","external_ids","audio","audio_language","subtitle_language","release_id","mapped_seasons","season_coverage")
        for name in important:
            if not hasattr(self,name):
                continue
            value=getattr(self,name)
            if value is None:
                continue
            current=FieldProvenance.create(name,value,self.source,implicit=True)
            if name in provided and provided[name].value!=current.value:
                raise ValueError("Provenance value differs from metadata field: "+name)
            provided.setdefault(name,current)
        object.__setattr__(self,"field_provenance",tuple(provided[k] for k in sorted(provided)))
        object.__setattr__(self,"crosswalks",tuple(self.crosswalks))
        if self.season_number is not None and self.season_number < 0:
            raise ValueError("Release season number cannot be negative")
        if self.episode_count is not None and self.episode_count < 0:
            raise ValueError("Episode count cannot be negative")
        if self.release_year is not None and not 1 <= self.release_year <= 9999:
            raise ValueError("Invalid release year")

    def provenance(self, name):
        return next((p for p in self.field_provenance if p.field==name),None)

    def trusted(self, name):
        p=self.provenance(name)
        return bool(p and p.reliable)

    def to_dict(self) -> dict[str, Any]:
        data=json.loads(json.dumps(asdict(self),default=encode))
        data["field_provenance"]=json.loads(json.dumps([p.to_dict() for p in self.field_provenance],default=encode))
        return data

@dataclass(frozen=True)
class Target(Metadata):
    canonical_title: str
    audio_preference: Audio = Audio.UNKNOWN
    target_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Target":
        return cls(**metadata_from_dict(data))

@dataclass(frozen=True)
class Candidate(Metadata):
    id: str
    canonical_title: str
    url: str
    audio: Audio = Audio.UNKNOWN
    audio_language: str | None = None
    subtitle_language: str | None = None
    release_id: str | None = None
    mapped_seasons: tuple[int, ...] = ()
    season_coverage: tuple[tuple[int, int, int], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        return cls(**metadata_from_dict(data))

@dataclass(frozen=True)
class Evidence:
    signal: str
    outcome: str
    expected: str | None
    observed: str | None
    source: str
    detail: str

@dataclass(frozen=True)
class Evaluation:
    candidate_id: str
    hard_rejected: bool
    reason_codes: tuple[ReasonCode, ...]
    evidence: tuple[Evidence, ...]
    rank: tuple[float, ...]
    title_method: str
    season_verified: bool

@dataclass(frozen=True)
class Decision:
    outcome: str
    selected_candidate_id: str | None
    reason_codes: tuple[ReasonCode, ...]
    evaluations: tuple[Evaluation, ...]
    matcher_version: str = "provenance-2"
    fuzzy_threshold: float = .88
    date_tolerance_days: int = 1

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=encode))

@dataclass(frozen=True)
class Review:
    id: str
    target_snapshot: Target
    candidate_snapshots: tuple[Candidate, ...]
    decision_snapshot: Decision
    state: str = "open"
    schema_version: int = 1

    @classmethod
    def from_decision(cls, id: str, target: Target, candidates: tuple[Candidate, ...], decision: Decision) -> "Review":
        if decision.outcome == "matched":
            raise ValueError("A matched decision does not require a review")
        return cls(id, target, tuple(candidates), decision)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "schema_version": self.schema_version, "state": self.state,
                "target_snapshot": self.target_snapshot.to_dict(),
                "candidate_snapshots": [c.to_dict() for c in self.candidate_snapshots],
                "decision_snapshot": self.decision_snapshot.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Review":
        if data["schema_version"] != 1:
            raise ValueError("Unsupported review snapshot schema")
        dec = dict(data["decision_snapshot"])
        dec["reason_codes"] = tuple(ReasonCode(c) for c in dec["reason_codes"])
        evaluations = []
        for item in dec["evaluations"]:
            item = dict(item)
            item["reason_codes"] = tuple(ReasonCode(c) for c in item["reason_codes"])
            item["evidence"] = tuple(Evidence(**e) for e in item["evidence"])
            item["rank"] = tuple(item["rank"])
            evaluations.append(Evaluation(**item))
        dec["evaluations"] = tuple(evaluations)
        return cls(data["id"], Target.from_dict(data["target_snapshot"]),
                   tuple(Candidate.from_dict(c) for c in data["candidate_snapshots"]),
                   Decision(**dec), data["state"], data["schema_version"])

def encode(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(type(value).__name__)

def source_from_dict(data: Any) -> SourceMetadata:
    if data is None:
        return SourceMetadata()
    if isinstance(data, str):
        return SourceMetadata(data)
    data = dict(data)
    data["attributes"] = tuple(tuple(v) for v in data.get("attributes", ()))
    return SourceMetadata(**data)

def metadata_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    from .provenance import FieldProvenance, SeasonCrosswalk
    data["field_provenance"]=tuple(FieldProvenance.from_dict(p) for p in data.get("field_provenance",()))
    data["crosswalks"]=tuple(SeasonCrosswalk.from_dict(p) for p in data.get("crosswalks",()))
    data["source"] = source_from_dict(data.get("source"))
    data["alternate_titles"] = tuple(Alias.from_dict(a) for a in data.get("alternate_titles", ()))
    for key in ("premiere_date", "air_date"):
        if data.get(key):
            data[key] = date.fromisoformat(data[key])
    for key in ("external_ids", "season_coverage"):
        if key in data:
            values = data[key].items() if isinstance(data[key], dict) else data[key]
            data[key] = tuple(tuple(v) for v in values)
    for key in ("target_seasons", "mapped_seasons"):
        if key in data:
            data[key] = tuple(data[key])
    for key in ("audio", "audio_preference"):
        if key in data:
            data[key] = Audio(data[key])
    return data
