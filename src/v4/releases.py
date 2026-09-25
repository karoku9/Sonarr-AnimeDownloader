"""Offline release identity and episode crosswalk contracts; never runtime mappings.

Coverage links retain every coordinate rather than assuming a constant offset.
Reliability describes evidence, not a calibrated probability.
"""
from dataclasses import dataclass, asdict
from typing import Optional

@dataclass(frozen=True)
class SeriesIdentity:
    sonarr_id: int
    title: str
    external_ids: tuple = ()
    source: str = "sonarr"

@dataclass(frozen=True)
class SeasonTarget:
    series: SeriesIdentity
    target_id: str
    season: int
    episode_numbers: tuple
    expected_count: Optional[int]
    source: str = "sonarr_episode"

@dataclass(frozen=True)
class EpisodeLink:
    source_episode: int
    sonarr_season: int
    sonarr_episode: int
    absolute_episode: Optional[int] = None
    scene_season: Optional[int] = None
    scene_episode: Optional[int] = None

@dataclass(frozen=True)
class EpisodeCoverage:
    observed_numbers: tuple
    links: tuple = ()
    status: str = "unknown"
    source: str = "animeworld_detail:data-episode-num"
    observed: bool = True

    def __post_init__(self):
        if self.status not in {"complete", "partial", "unknown"}:
            raise ValueError("invalid coverage status")
        if len(set(self.observed_numbers)) != len(self.observed_numbers):
            raise ValueError("duplicate source episode")
        if any(link.source_episode not in self.observed_numbers for link in self.links):
            raise ValueError("unobserved source episode")
        destinations = [(x.sonarr_season, x.sonarr_episode) for x in self.links]
        if len(set(destinations)) != len(destinations):
            raise ValueError("duplicate destination")

    @property
    def episode_count(self):
        return len(self.observed_numbers) if self.observed_numbers else None

    @property
    def source_range(self):
        return (min(self.observed_numbers),max(self.observed_numbers)) if self.observed_numbers else None

    @property
    def absolute_range(self):
        values=[x.absolute_episode for x in self.links]
        return (min(values),max(values)) if values and all(x is not None for x in values) else None

    @property
    def destination_ranges(self):
        seasons=sorted({x.sonarr_season for x in self.links})
        return tuple((s,min(x.sonarr_episode for x in self.links if x.sonarr_season==s),
            max(x.sonarr_episode for x in self.links if x.sonarr_season==s)) for s in seasons)

    @property
    def offset(self):
        offsets = {x.sonarr_episode-x.source_episode for x in self.links}
        return next(iter(offsets)) if len(offsets) == 1 else None

@dataclass(frozen=True)
class ReleaseIdentity:
    release_id: str
    title: str
    candidate_ids: tuple
    urls: tuple
    premiere_date: Optional[str]
    episode_numbers: tuple
    reliability: str = "metadata_supported"

@dataclass(frozen=True)
class Crosswalk:
    basis: str
    reliability: str
    evidence: tuple
    source: str = "derived:sonarr+animeworld"

    def __post_init__(self):
        if self.reliability not in {"explicit","metadata_supported","inferred"}:
            raise ValueError("unknown crosswalk reliability")
        if not self.evidence:
            raise ValueError("crosswalk must explain its evidence")

    @property
    def eligible(self):
        return self.reliability in {"explicit", "metadata_supported"}

@dataclass(frozen=True)
class ReleaseSegment:
    release: ReleaseIdentity
    coverage: EpisodeCoverage
    crosswalk: Crosswalk

@dataclass(frozen=True)
class MappingPlan:
    target_ids: tuple
    segments: tuple
    outcome: str
    reason_codes: tuple = ()

    def __post_init__(self):
        object.__setattr__(self,"reason_codes",tuple(dict.fromkeys(self.reason_codes)))
        coordinates = [(x.sonarr_season,x.sonarr_episode) for s in self.segments for x in s.coverage.links]
        used_source_coordinates=set()
        for segment in self.segments:
            # One physical source episode may legitimately contain multiple Sonarr
            # segments. Reuse is allowed inside that release segment, but never
            # across separate plan segments.
            segment_coordinates={(segment.release.release_id,x.source_episode) for x in segment.coverage.links}
            if used_source_coordinates & segment_coordinates:
                raise ValueError("source episode assigned across plan segments")
            used_source_coordinates.update(segment_coordinates)
        if self.outcome not in {"matched","needs_review"}:
            raise ValueError("invalid plan outcome")
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("overlapping plan segments")
        if self.outcome == "matched" and any(not s.crosswalk.eligible for s in self.segments):
            raise ValueError("inferred crosswalk cannot auto-match")

    def to_dict(self):
        result=asdict(self)
        for item,segment in zip(result["segments"],self.segments):
            item["coverage"].update(episode_count=segment.coverage.episode_count,
                source_range=segment.coverage.source_range,absolute_range=segment.coverage.absolute_range,
                destination_ranges=segment.coverage.destination_ranges,offset=segment.coverage.offset)
        return result
