"""Deterministic, read-only resolver over versioned production snapshots.

Strong whole-season and scene-segment links use exact scoped identity, observed
numbering and matching premiere dates. Absolute concatenation alone is inferred.
No anime-specific knowledge, fuzzy threshold changes or production side effects.
"""
from datetime import date
from hashlib import sha256
from html import unescape
import re
from .models import Candidate, Target, Audio
from .normalization import normalize_title
from .title_aliases import equivalents
from .releases import (SeriesIdentity, SeasonTarget, EpisodeLink, EpisodeCoverage,
                       ReleaseIdentity, Crosswalk, ReleaseSegment, MappingPlan)


_BASIS_RANK={
    "whole_target_identity_date_numbering":6,
    "independent_external_premiere_and_structure":6,
    "sonarr_absolute_sequence_with_series_identity":6,
    "series_prefix_date_count_numbering":5,
    "series_prefix_continuation_with_sonarr_airdate_boundaries":5,
    "verified_scoped_alias_year_numbering":5,
    "provider_ordinal_with_external_season_structure":5,
    "verified_scoped_alias_numbering":4,
    "verified_external_ordering_prefix_projection_with_provider_identity":6,
    "verified_tvdb_segmented_ordering_projection":6,
}


def _segment_key(segment):
    links=tuple((link.source_episode,link.sonarr_season,link.sonarr_episode) for link in segment.coverage.links)
    destination=tuple(sorted((link.sonarr_season,link.sonarr_episode,link.source_episode)
        for link in segment.coverage.links))
    return destination,segment.release.release_id,links,segment.crosswalk.basis


def _solution_key(solution):
    return tuple(_segment_key(segment) for segment in sorted(solution,key=_segment_key))


def _solution_quality(solution):
    return min((_BASIS_RANK.get(segment.crosswalk.basis,3) for segment in solution),default=0)


def best_exact_covers(available,desired,*,state_limit=4096):
    """Return every canonical best exact cover, independent of candidate order."""
    desired=frozenset(desired)
    states={(frozenset(),frozenset()):(10,{():()})}
    unique={_segment_key(segment):segment for segment in available}
    for item in sorted(unique.values(),key=_segment_key):
        item_covered=frozenset(link.sonarr_episode for link in item.coverage.links)
        item_sources=frozenset((item.release.release_id,link.source_episode) for link in item.coverage.links)
        if not item_covered or not item_covered<=desired:continue
        additions={}
        for (covered,used_sources),(score,solutions) in tuple(states.items()):
            if covered&item_covered or used_sources&item_sources:continue
            state=(covered|item_covered,used_sources|item_sources)
            next_score=min(score,_BASIS_RANK.get(item.crosswalk.basis,3))
            current_score,current_solutions=additions.get(state,(-1,{}))
            if next_score>current_score:current_score,current_solutions=next_score,{}
            if next_score==current_score:
                current_solutions=dict(current_solutions)
                for solution in solutions.values():
                    combined=tuple(sorted((*solution,item),key=_segment_key))
                    current_solutions[_solution_key(combined)]=combined
            additions[state]=(current_score,current_solutions)
        for state,(score,solutions) in additions.items():
            existing=states.get(state)
            if existing is None or score>existing[0]:states[state]=(score,solutions)
            elif score==existing[0]:states[state]=(score,{**existing[1],**solutions})
        if sum(len(value[1]) for value in states.values())>state_limit:return [],True
    completed=[solution for (covered,_),(score,solutions) in states.items() if covered==desired
        for solution in solutions.values() if solution]
    if not completed:return [],False
    best=max(_solution_quality(solution) for solution in completed)
    values={_solution_key(solution):solution for solution in completed if _solution_quality(solution)==best}
    return sorted(values.values(),key=_solution_key),False


def key(title):
    value = normalize_title(unescape(title))
    # Normalize equivalent season labels, retaining the ordinal as identity evidence.
    value = re.sub(r"\b(\d+)(?:st|nd|rd|th) season$", r"\1", value)
    value = re.sub(r"\bseason (\d+)$", r"\1", value)
    roman = {"i":1,"ii":2,"iii":3,"iv":4,"v":5}
    value = re.sub(r"\bseason (i{1,3}|iv|v)$", lambda m:str(roman[m[1]]), value)
    return value


def titles(metadata, season=None):
    result = {key(value) for value in equivalents(metadata.canonical_title)}
    for alias in metadata.alternate_titles:
        if (alias.source is None or alias.source.reliable) and (alias.scope() is None or alias.scope() == season):
            result.update(key(value) for value in equivalents(alias.text))
    return result - {""}


def transport_family(title):
    value=key(title)
    return re.sub(r"\s+(?:ita|dub|dubbed)$","",value).strip()


def article_family(title):
    """Normalize only standalone English articles plus transport-language suffixes."""
    return " ".join(token for token in transport_family(title).split() if token not in {"a","an","the"})


def article_titles(metadata,season=None):
    return {article_family(value) for value in titles(metadata,season) if article_family(value)}


def series_prefix_identity(target,candidate):
    """Exact series-root prefix used only with independent date/count/numbering anchors."""
    base=transport_family(target.canonical_title)
    value=transport_family(candidate.canonical_title)
    return bool(base and len(base)>=5 and value.startswith(base+" "))


def title_token_identity(left,right):
    """Conservative exact-token identity for reordered provider/season names."""
    a=key(left);b=key(right)
    if not a or not b:return False
    if a==b:return True
    aa=a.split();bb=b.split()
    return len(aa)>=2 and len(aa)==len(bb) and sorted(aa)==sorted(bb)


def same_date(left, right):
    if not left or not right:
        return False
    try:
        return abs((date.fromisoformat(str(left)[:10])-date.fromisoformat(str(right)[:10])).days) <= 1
    except ValueError:
        return False


def numbers(detail):
    if not isinstance(detail,dict):return ()
    values = detail.get("raw", {}).get("available_episode_numbers", [])
    # Provider episode 0 is a separately numbered special and cannot shift regular
    # 1..N coverage. Fractions, negatives and nonnumeric values remain unsafe.
    if not values or any(not isinstance(x,(int,float)) or isinstance(x,bool) or x < 0 or int(x) != x for x in values):
        return ()
    positive=tuple(sorted({int(x) for x in values if int(x)>0}))
    return positive if positive else ()


def contiguous(values):
    return bool(values) and values == tuple(range(1,len(values)+1))


def group_releases(candidates, details):
    """Clique grouping prevents transitive alias bridges across incompatible releases."""
    groups = []
    def compatible(a, b):
        an, bn = numbers(details.get(a.url, {})), numbers(details.get(b.url, {}))
        return bool(an and an == bn and a.trusted("premiere_date") and b.trusted("premiere_date")
                    and same_date(a.premiere_date,b.premiere_date) and a.episode_count == b.episode_count
                    and a.release_year == b.release_year and a.date_kind == b.date_kind and a.part == b.part and a.cour == b.cour
                    and titles(a) & titles(b))
    for candidate in sorted(candidates,key=lambda c:c.id):
        group = next((g for g in groups if all(compatible(candidate,c) for c in g)),None)
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    result = []
    for group in groups:
        first = group[0]
        identity = ReleaseIdentity(sha256("\n".join(c.id for c in group).encode()).hexdigest()[:20],
            first.canonical_title, tuple(c.id for c in group), tuple(c.url for c in group),
            first.premiere_date.isoformat() if first.premiere_date else None,
            numbers(details.get(first.url, {})))
        result.append((identity,tuple(group)))
    return result


def supplemental_identity(candidate):
    """Return base title for explicit supplemental release suffixes."""
    value=transport_family(candidate.canonical_title)
    match=re.search(r"\s+(ova|ona|special|specials)$",value)
    return (value[:match.start()],match[1]) if match else (None,None)


def part_identity(candidate):
    """Return release-family key and a provider-declared continuation number.

    `Part/Parte N` is explicit structural evidence. Plain trailing numerals are
    handled separately and only with exact two-release/date-boundary proof.
    """
    value=key(candidate.canonical_title)
    explicit=re.search(r"\s+(?:part|parte)\s+(\d+)$",value)
    if explicit:return (value[:explicit.start()],int(explicit[1]),True)
    return (value,1,False)


def external_sources(case,target,*,independent=None):
    expected={str(k).casefold():str(v) for k,v in target.external_ids}
    aliases={"tvdbid":"tvdb","tvmazeid":"tvmaze","imdbid":"imdb"}
    expected={aliases.get(k,k):v for k,v in expected.items()}
    for source in (case.get("raw",{}).get("external_metadata") or {}).get("sources",[]):
        if independent is not None and source.get("independent_of_sonarr") is not independent:continue
        ids={str(k).casefold():str(v) for k,v in source.get("external_ids",{}).items() if v not in (None,"")}
        shared=set(expected)&set(ids)
        if shared and all(expected[k]==ids[k] for k in shared):yield source


def external_season(case,target,*,independent=None):
    for source in external_sources(case,target,independent=independent):
        for season in source.get("seasons",[]):
            if season.get("season_number")==target.season_number and season.get("episode_count")==target.episode_count:
                return source,season
    return None


def independent_named_season_identity(case,target,candidate):
    """Bind a provider release to an independently named season without fuzzy matching."""
    if target.episode_count is None or candidate.episode_count!=target.episode_count:return None
    if not candidate.premiere_date:return None
    for source in external_sources(case,target,independent=True):
        for season in source.get("seasons",[]):
            if season.get("season_number")!=target.season_number or season.get("episode_count")!=target.episode_count:continue
            name=season.get("name");premiere=season.get("premiere_date")
            if not isinstance(name,str) or not name or not premiere:continue
            if title_token_identity(name,candidate.canonical_title) and same_date(candidate.premiere_date,premiere):
                return source,season
    return None


def external_identity_prefix(case,target,candidate):
    """Trusted external identity may prove provider-appended extras are trailing."""
    for source in external_sources(case,target):
        for item in source.get("season_aliases",[]):
            if not isinstance(item,dict) or item.get("season_number")!=target.season_number:continue
            if item.get("identity_verified") is not True or item.get("episode_count")!=target.episode_count:continue
            if key(item.get("title") or "") in titles(candidate):return source,item
    return None


def external_provider_mal_entry(case,target,candidate,season_number=None):
    """Bind a provider release to one independently verified MAL entry."""
    if candidate.episode_count is None:return None
    season_number=target.season_number if season_number is None else season_number
    candidate_titles=titles(candidate)
    for source in external_sources(case,target,independent=True):
        expected={item.get("mal_id"):item for item in source.get("expected_mal_entries",[])
            if isinstance(item,dict) and item.get("episode_count")==candidate.episode_count
            and item.get("episode_offset") in (None,0)}
        if not expected:continue
        for hit in source.get("provider_mal_hits",[]):
            mid=hit.get("mal_id");episodes=str(hit.get("episodes") or "")
            item=expected.get(mid)
            if not item or not episodes.isdigit() or int(episodes)!=candidate.episode_count:continue
            if key(hit.get("name") or "") not in candidate_titles:continue
            expected_year=item.get("release_year");hit_year=str(hit.get("year") or "")
            if isinstance(expected_year,int) and hit_year.isdigit() and int(hit_year)!=expected_year:continue
            if candidate.release_year is not None and isinstance(expected_year,int) and candidate.release_year!=expected_year:continue
            verified=any(isinstance(alias,dict) and alias.get("identity_verified") is True
                and alias.get("season_number")==season_number and alias.get("mal_id")==mid
                and alias.get("episode_count")==candidate.episode_count and key(alias.get("title") or "") in candidate_titles
                for alias in source.get("season_aliases",[]))
            if verified:return source,item,hit
    return None


def external_provider_mal_identity(case,target,candidate):
    """Direct provider MAL identity for one complete Sonarr target season."""
    if target.episode_count is None or candidate.episode_count!=target.episode_count:return None
    return external_provider_mal_entry(case,target,candidate)

def external_provider_series_span_projection(case,target,candidate,source_numbers):
    """Prove that one provider release starts at this Sonarr season and spans later seasons.

    This is deliberately stricter than count arithmetic: an independent MAL/TVDB
    crosswalk must identify the exact provider hit and a second trusted source must
    expose consecutive season counts whose prefix starts at the requested season.
    """
    if target.episode_count is None or candidate.episode_count is None or candidate.episode_count<=target.episode_count:return None
    if tuple(source_numbers)!=tuple(range(1,candidate.episode_count+1)):return None
    candidate_titles=titles(candidate)
    identity=None
    for source in external_sources(case,target,independent=True):
        expected={item.get("mal_id"):item for item in source.get("expected_mal_entries",[])
            if isinstance(item,dict) and item.get("episode_count")==candidate.episode_count and item.get("episode_offset") in (None,0)}
        if not expected:continue
        for hit in source.get("provider_mal_hits",[]):
            mid=hit.get("mal_id");episodes=str(hit.get("episodes") or "")
            if mid not in expected or not episodes.isdigit() or int(episodes)!=candidate.episode_count:continue
            if key(hit.get("name") or "") not in candidate_titles:continue
            verified=any(isinstance(item,dict) and item.get("identity_verified") is True
                and item.get("season_number")==target.season_number and item.get("mal_id")==mid
                and item.get("episode_count")==candidate.episode_count and key(item.get("title") or "") in candidate_titles
                for item in source.get("season_aliases",[]))
            if verified:identity=(source,mid);break
        if identity:break
    if not identity:return None
    for structure in external_sources(case,target):
        seasons=sorted((item for item in structure.get("seasons",[]) if isinstance(item.get("season_number"),int)
            and isinstance(item.get("episode_count"),int)),key=lambda item:item["season_number"])
        seasons=[item for item in seasons if item["season_number"]>=target.season_number]
        if not seasons or seasons[0]["season_number"]!=target.season_number or seasons[0]["episode_count"]!=target.episode_count:continue
        total=0;used=[];expected_number=target.season_number
        for item in seasons:
            if item["season_number"]!=expected_number:break
            total+=item["episode_count"];used.append(item);expected_number+=1
            if total>=candidate.episode_count:break
        if len(used)>=2 and total==candidate.episode_count:
            return identity[0],identity[1],structure,tuple((item["season_number"],item["episode_count"]) for item in used)
    return None


def external_exclusion_projection(case,target,candidate,source_numbers):
    """Project a provider release after independently identified recap/extra slots."""
    if target.episode_count is None:return None
    for source in external_sources(case,target,independent=True):
        for item in source.get("season_aliases",[]):
            if not isinstance(item,dict) or item.get("season_number")!=target.season_number:continue
            declared=item.get("episode_count")
            if item.get("identity_verified") is not True or not isinstance(declared,int) or declared!=candidate.episode_count:continue
            excluded=tuple(sorted({int(v) for v in item.get("excluded_source_episodes",[]) if isinstance(v,int)}))
            if not excluded or any(v<1 or v>declared for v in excluded):continue
            if tuple(source_numbers)!=tuple(range(1,declared+1)):continue
            kept=tuple(v for v in source_numbers if v not in set(excluded))
            if len(kept)!=target.episode_count:continue
            if key(item.get("title") or "") not in titles(candidate):continue
            return source,item,kept,excluded
    return None


def _ordering_title_partition(source_titles,target_titles):
    """Map compact-order episodes to consecutive expanded-order episodes by exact titles."""
    cursor=0;mapping=[]
    for source_episode,source_title in enumerate(source_titles,1):
        matches=[]
        for end in range(cursor+1,len(target_titles)+1):
            if " ".join(target_titles[cursor:end])==source_title:matches.append(end)
        if len(matches)!=1:return None
        end=matches[0]
        mapping.append((source_episode,tuple(range(cursor+1,end+1))))
        cursor=end
    if cursor!=len(target_titles):return None
    return tuple(mapping)


def external_segmented_ordering_projection(case,target,candidate,source_numbers):
    """Prove one provider episode contains multiple Sonarr/TVDB aired segments.

    The provider release must have an independently verified MAL identity and its
    count must exactly match another TVDB ordering. Every compact-order title must
    equal the ordered concatenation of one or more official-order episode titles.
    """
    if target.episode_count is None or candidate.episode_count is None:return None
    if candidate.episode_count>=target.episode_count:return None
    if tuple(source_numbers)!=tuple(range(1,candidate.episode_count+1)):return None
    provider=external_provider_mal_entry(case,target,candidate)
    if not provider:return None
    orders=[]
    for source in external_sources(case,target):
        if not source.get("ordering") or not str(source.get("source","")).startswith("tvdb_public_"):continue
        season=next((x for x in source.get("seasons",[]) if x.get("season_number")==target.season_number),None)
        if not season:continue
        episodes=sorted(season.get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
        if len(episodes)!=season.get("episode_count") or any(e.get("episode_number")!=i for i,e in enumerate(episodes,1)):continue
        episode_titles=tuple(key(e.get("title") or "") for e in episodes)
        if not episode_titles or any(not value for value in episode_titles):continue
        orders.append((source,season,episode_titles))
    bases=[item for item in orders if item[0].get("ordering")=="official" and item[1].get("episode_count")==target.episode_count]
    compact=[item for item in orders if item[0].get("ordering")!="official" and item[1].get("episode_count")==candidate.episode_count]
    proposals={}
    for base in bases:
        for alternate in compact:
            mapping=_ordering_title_partition(alternate[2],base[2])
            if mapping and any(len(destinations)>1 for _,destinations in mapping):
                proposals.setdefault(mapping,(provider,base,alternate,mapping))
    return next(iter(proposals.values())) if len(proposals)==1 else None


def mapped_segment(identity,rows,mapping,basis,reliability,evidence,full_count):
    row_by_episode={r["episodeNumber"]:r for r in rows}
    links=[]
    for source_episode,destinations in mapping:
        for destination in destinations:
            row=row_by_episode.get(destination)
            if row is None:return None
            links.append(EpisodeLink(source_episode,row["seasonNumber"],row["episodeNumber"],row.get("absoluteEpisodeNumber"),
                row.get("sceneSeasonNumber"),row.get("sceneEpisodeNumber")))
    if len({(link.sonarr_season,link.sonarr_episode) for link in links})!=full_count:return None
    coverage=EpisodeCoverage(identity.episode_numbers,tuple(links),"complete")
    return ReleaseSegment(identity,coverage,Crosswalk(basis,reliability,tuple(evidence)))


def external_relocated_special_projection(case,target,candidate,source_numbers):
    """Map provider tail episodes to a Sonarr Specials season relocated by TVDB ordering."""
    if target.season_number!=0 or target.episode_count is None or candidate.episode_count is None:return None
    if tuple(source_numbers)!=tuple(range(1,candidate.episode_count+1)):return None
    orders=[]
    for source in external_sources(case,target):
        if not source.get("ordering") or not str(source.get("source","")).startswith("tvdb_public_"):continue
        orders.append(source)
    official=next((source for source in orders if source.get("ordering")=="official"),None)
    if not official:return None
    specials=next((season for season in official.get("seasons",[]) if season.get("season_number")==0),None)
    special_eps=sorted((specials or {}).get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
    if len(special_eps)!=target.episode_count or tuple(e.get("episode_number") for e in special_eps)!=tuple(range(1,len(special_eps)+1)):return None
    special_titles=tuple(key(e.get("title") or "") for e in special_eps)
    if any(not title for title in special_titles):return None
    proposals=[]
    for alternate in orders:
        if alternate.get("ordering") not in {"alternate","dvd","absolute"}:continue
        for alt_season in alternate.get("seasons",[]):
            number=alt_season.get("season_number")
            if not isinstance(number,int) or number<=0:continue
            base=next((season for season in official.get("seasons",[]) if season.get("season_number")==number),None)
            if not base:continue
            base_eps=sorted(base.get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
            alt_eps=sorted(alt_season.get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
            if len(alt_eps)!=candidate.episode_count or len(alt_eps)!=len(base_eps)+len(special_eps):continue
            base_titles=tuple(key(e.get("title") or "") for e in base_eps);alt_titles=tuple(key(e.get("title") or "") for e in alt_eps)
            if any(not title for title in (*base_titles,*alt_titles)):continue
            if alt_titles[:len(base_titles)]!=base_titles or alt_titles[len(base_titles):]!=special_titles:continue
            provider=external_provider_mal_entry(case,target,candidate,season_number=number)
            if not provider:continue
            tail=tuple(range(len(base_eps)+1,len(alt_eps)+1))
            proposals.append((provider,official,alternate,number,tail))
    signatures={(p[3],p[4]):p for p in proposals}
    return next(iter(signatures.values())) if len(signatures)==1 else None


def external_ordering_projection(case,target,source_count):
    """Prove a target prefix when one TVDB ordering moves only trailing episodes."""
    if target.episode_count is None or source_count<=target.episode_count:return None
    candidates=[]
    for source in external_sources(case,target):
        if not source.get("ordering") or not str(source.get("source","")).startswith("tvdb_public_"):continue
        season=next((x for x in source.get("seasons",[]) if x.get("season_number")==target.season_number),None)
        if not season:continue
        episodes=sorted(season.get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
        if len(episodes)!=season.get("episode_count") or any(e.get("episode_number")!=i for i,e in enumerate(episodes,1)):continue
        titles_=tuple(key(e.get("title") or "") for e in episodes)
        if not titles_ or any(not title for title in titles_):continue
        candidates.append((source,season,titles_))
    target_orders=[item for item in candidates if item[1].get("episode_count")==target.episode_count]
    source_orders=[item for item in candidates if item[1].get("episode_count")==source_count]
    for base in target_orders:
        for alternate in source_orders:
            if base[0].get("ordering")==alternate[0].get("ordering"):continue
            if alternate[2][:target.episode_count]==base[2] and len(alternate[2])==source_count:
                return base,alternate
    return None


def segment(identity, rows, source_numbers, basis, reliability, evidence, full_count, *, observed_numbers=None, status=None):
    links = tuple(EpisodeLink(n,r["seasonNumber"],r["episodeNumber"],r.get("absoluteEpisodeNumber"),
                 r.get("sceneSeasonNumber"),r.get("sceneEpisodeNumber")) for n,r in zip(source_numbers,rows))
    observed=identity.episode_numbers if observed_numbers is None else tuple(observed_numbers)
    coverage = EpisodeCoverage(observed,links,status or ("complete" if len(rows)==full_count else "partial"))
    return ReleaseSegment(identity,coverage,Crosswalk(basis,reliability,tuple(evidence)))


def external_offset_segments(case,target,groups,rows):
    """Build one season from provider releases whose independent crosswalk supplies exact offsets."""
    if target.episode_count is None or len(rows)!=target.episode_count:return ()
    proposals=[]
    for source in external_sources(case,target,independent=True):
        expected=[x for x in source.get("expected_mal_entries",[]) if isinstance(x,dict)]
        if len(expected)<2 or source.get("provider_catalog_complete") is not True:continue
        parts=[];covered=set();valid=True
        for item in expected:
            mid=item.get("mal_id");count=item.get("episode_count");offset=item.get("episode_offset")
            offset=0 if offset is None else offset
            if not isinstance(mid,int) or not isinstance(count,int) or count<=0 or not isinstance(offset,int) or offset<0:
                valid=False;break
            if offset+count>len(rows) or covered & set(range(offset,offset+count)):
                valid=False;break
            aliases={key(x.get("title") or "") for x in source.get("season_aliases",[]) if isinstance(x,dict)
                and x.get("identity_verified") is True and x.get("season_number")==target.season_number
                and x.get("mal_id")==mid and x.get("episode_count")==count}
            hit_ok=any(x.get("mal_id")==mid and str(x.get("episodes") or "").isdigit()
                and int(x.get("episodes"))==count for x in source.get("provider_mal_hits",[]))
            if not aliases or not hit_ok:valid=False;break
            part_rows=rows[offset:offset+count];matches=[]
            for identity,variants in groups:
                c=variants[0];nums=identity.episode_numbers
                if not (aliases & titles(c)):continue
                if not (contiguous(nums) and len(nums)==count and c.episode_count==count and c.episodes_complete
                        and c.trusted("episode_count") and c.trusted("premiere_date") and c.trusted("episodes_complete")):continue
                if not part_rows or not same_date(c.premiere_date,part_rows[0].get("airDate")):continue
                matches.append((identity,c,nums))
            if len(matches)!=1:valid=False;break
            identity,c,nums=matches[0];covered.update(range(offset,offset+count))
            parts.append(segment(identity,part_rows,nums,"verified_external_episode_offset_composition","metadata_supported",
                [f"independent {source.get('source')} binds MAL {mid} to target season {target.season_number}",
                 f"verified provider hit has {count} episodes at target offset {offset}",
                 "provider premiere matches the Sonarr boundary and numbering is contiguous"],len(rows)))
        if valid and covered==set(range(len(rows))):proposals.append(tuple(parts))
    signatures={tuple((p.release.release_id,tuple((l.source_episode,l.sonarr_episode) for l in p.coverage.links)) for p in plan):plan for plan in proposals}
    return next(iter(signatures.values())) if len(signatures)==1 else ()


def aired_prefix(rows):
    today=date.today();result=[]
    for row in rows:
        value=row.get("airDate")
        if not value:break
        try:air=date.fromisoformat(str(value)[:10])
        except ValueError:break
        if air>today:break
        result.append(row)
    return result


def effective_target_rows(case,target):
    rows=sorted(case["raw"]["sonarr_episodes"],key=lambda r:r["episodeNumber"])
    count=target.episode_count
    if isinstance(count,int) and 0<count<len(rows):
        kept=rows[:count];trailing=rows[count:]
        if tuple(r.get("episodeNumber") for r in kept)==tuple(range(1,count+1)) and all(
                not r.get("airDate") and r.get("absoluteEpisodeNumber") is None for r in trailing):
            return kept
    return rows


def resolve_series(cases):
    targets = {c["derived"]["target"]["target_id"]:Target.from_dict(c["derived"]["target"]) for c in cases}
    rows_by_target = {}
    for c in cases:
        target=targets[c["derived"]["target"]["target_id"]]
        rows_by_target[target.target_id]=effective_target_rows(c,target)
    case_by_target = {c["derived"]["target"]["target_id"]:c for c in cases}
    candidates = {c["id"]:Candidate.from_dict(c) for case in cases for c in case["derived"]["candidates"]}
    details = {u:d for case in cases for u,d in case["raw"]["details"].items()}
    groups = group_releases(tuple(candidates.values()),details)
    choices = {tid:[] for tid in targets}
    exclusions = {tid:[] for tid in targets}
    for tid,target in targets.items():
        rows = rows_by_target[tid]
        complete_target = (target.episode_count is not None and len(rows)==target.episode_count
                           and tuple(r["episodeNumber"] for r in rows)==tuple(range(1,len(rows)+1)))
        for identity,variants in groups:
            c = variants[0]; nums = identity.episode_numbers
            exclusion_projection=external_exclusion_projection(case_by_target[tid],target,c,nums)
            prefix_projection=external_identity_prefix(case_by_target[tid],target,c)
            ordinary_coverage=contiguous(nums) and len(nums)==c.episode_count
            trailing_extra_coverage=(contiguous(nums) and target.episode_count is not None
                and len(nums)>target.episode_count and prefix_projection is not None)
            aired=aired_prefix(rows)
            live_identity=bool(titles(target,target.season_number)&titles(c)) or bool(
                article_titles(target,target.season_number)&article_titles(c))
            live_prefix=(isinstance(target.episode_count,int) and target.episode_count>0
                and 0<len(aired)<target.episode_count and c.episode_count==target.episode_count
                and contiguous(nums) and nums==tuple(range(1,len(nums)+1)) and len(nums)<=len(aired)
                and c.trusted("episode_count") and c.trusted("premiere_date") and rows
                and same_date(c.premiere_date,rows[0].get("airDate")) and live_identity)
            if live_prefix:
                choices[tid].append(segment(identity,rows[:len(nums)],nums,
                    "airing_identity_date_numbering_prefix","metadata_supported",
                    ["exact provider identity for an in-progress target",
                     "provider premiere matches Sonarr episode 1",
                     "observed source numbering is a contiguous prefix of already-aired Sonarr episodes"],
                    target.episode_count,observed_numbers=nums,status="partial"))
                continue
            if not (ordinary_coverage or exclusion_projection or trailing_extra_coverage) or not c.trusted("episode_count") or not c.trusted("premiere_date") or not c.episodes_complete or not c.trusted("episodes_complete"):
                exclusions[tid].append({"release_id":identity.release_id,"reason":"insufficient_metadata"});continue
            common_ids=set(dict(target.external_ids))&set(dict(c.external_ids))
            if target.trusted("external_ids") and c.trusted("external_ids") and any(dict(target.external_ids)[name]!=dict(c.external_ids)[name] for name in common_ids):
                exclusions[tid].append({"release_id":identity.release_id,"reason":"identity_conflict"});continue
            relocated_special=external_relocated_special_projection(case_by_target[tid],target,c,nums)
            if complete_target and relocated_special:
                provider,official,alternate,regular_season,tail=relocated_special
                projected=segment(identity,rows,tail,"verified_tvdb_relocated_special_tail","metadata_supported",
                    [f"TVDB official order relocates the complete {len(rows)}-episode tail to Specials",
                     f"TVDB {alternate.get('ordering')} order appends those exact special titles to season {regular_season}",
                     f"independent {provider[0].get('source')} binds the {len(nums)}-episode provider release to MAL {provider[1].get('mal_id')}"],
                    len(rows),observed_numbers=tail,status="complete")
                choices[tid].append(projected);continue
            # Scoped alias conflicts remain structural; a title/date score cannot bypass them.
            exact = bool(titles(target,target.season_number)&titles(c))
            candidate_key=key(c.canonical_title)
            ordinal = re.search(r" (\d+)$",candidate_key)
            suffix_anchor=bool(ordinal and int(ordinal[1]) == target.season_number and candidate_key[:ordinal.start()] in titles(target,target.season_number))
            prefix_anchor=any(candidate_key.startswith(t+" "+str(target.season_number)+" ") for t in titles(target,target.season_number) if t)
            ordinal_anchor = suffix_anchor or prefix_anchor
            # The title-derived ordinal is corroborative only: date and full observed coverage remain mandatory.
            exact = exact or ordinal_anchor
            conflicting = any(a.source.reliable and key(a.text) in titles(c) and a.scope() is not None and a.scope()!=target.season_number for a in target.alternate_titles)
            scoped_compatible = any(a.source.reliable and key(a.text) in titles(c) and a.scope()==target.season_number for a in target.alternate_titles)
            explicit_conflict = c.season_namespace=="sonarr" and c.season_number is not None and c.trusted("season_number") and c.season_number!=target.season_number
            if explicit_conflict or (conflicting and not scoped_compatible):
                exclusions[tid].append({"release_id":identity.release_id,"reason":"season_conflict"});continue
            scoped_exact = any(a.source.reliable and a.scope()==target.season_number and key(a.text) in titles(c)
                for a in target.alternate_titles)
            scoped_canonical_exact = any(a.source.reliable and a.scope()==target.season_number
                and key(a.text)==key(c.canonical_title) for a in target.alternate_titles)
            scoped_structural_exact = any(a.source.reliable and a.scope()==target.season_number and key(a.text) in titles(c)
                and any(any(ch.isdigit() for ch in token) for token in key(a.text).split()) for a in target.alternate_titles)
            scoped_no_date_exact = scoped_canonical_exact or scoped_structural_exact
            if complete_target and scoped_exact and exclusion_projection:
                source,item,used,excluded=exclusion_projection
                choices[tid].append(segment(identity,rows,used,"verified_external_episode_exclusion_projection","metadata_supported",
                    [f"verified external identity {source.get('source')} binds the provider release to Sonarr season {target.season_number}",
                     f"independent metadata identifies provider-only recap/extra slots {list(excluded)}",
                     "observed provider episode links exactly equal the declared sequence with those slots removed"],len(rows),status="complete"))
                continue
            if complete_target and exact and len(nums)==len(rows) and same_date(c.premiere_date,rows[0].get("airDate")):
                choices[tid].append(segment(identity,rows,nums,"whole_target_identity_date_numbering","metadata_supported",
                    ["exact native/manual/scoped alias", "premiere equals Sonarr episode 1 within unchanged one-day tolerance",
                     "observed contiguous source numbering equals complete Sonarr episode vector"],len(rows)))
                continue
            article_exact=bool(article_titles(target,target.season_number)&article_titles(c))
            if complete_target and article_exact and len(nums)==len(rows) and same_date(c.premiere_date,rows[0].get("airDate")):
                choices[tid].append(segment(identity,rows,nums,"article_normalized_identity_date_numbering","metadata_supported",
                    ["provider and target titles differ only by standalone English articles / transport suffix",
                     "premiere equals Sonarr episode 1 within unchanged one-day tolerance",
                     "observed contiguous source numbering equals complete Sonarr episode vector"],len(rows)))
                continue
            segmented_ordering=external_segmented_ordering_projection(case_by_target[tid],target,c,nums)
            if complete_target and segmented_ordering and rows and same_date(c.premiere_date,rows[0].get("airDate")):
                provider,base,alternate,mapping=segmented_ordering
                projected=mapped_segment(identity,rows,mapping,"verified_tvdb_segmented_ordering_projection","metadata_supported",
                    [f"independent {provider[0].get('source')} verifies provider MAL {provider[1].get('mal_id')} with {len(nums)} complete episodes",
                     f"TVDB {alternate[0].get('ordering')} order has the same {len(nums)} full episodes while official order has {len(rows)} segments",
                     "every compact-order title exactly equals the ordered concatenation of its official-order segment titles",
                     "provider premiere matches the Sonarr season start"],len(rows))
                if projected:
                    choices[tid].append(projected);continue
            provider_mal=external_provider_mal_identity(case_by_target[tid],target,c)
            if complete_target and len(nums)==len(rows) and provider_mal:
                source,item,hit=provider_mal
                choices[tid].append(segment(identity,rows,nums,"verified_provider_mal_identity_numbering","metadata_supported",
                    [f"independent {source.get('source')} maps target season {target.season_number} to MAL {item.get('mal_id')}",
                     f"provider directly reports that MAL id with {target.episode_count} episodes as {hit.get('name')}",
                     "provider release year and observed contiguous numbering agree with the verified MAL entry"],len(rows)))
                continue
            prefix=prefix_projection
            if complete_target and scoped_exact and len(nums)>len(rows) and nums==tuple(range(1,len(nums)+1)) and prefix:
                source,item=prefix;used=nums[:len(rows)]
                choices[tid].append(segment(identity,rows,used,"verified_external_identity_prefix_projection","metadata_supported",
                    [f"verified external identity {source.get('source')} binds the provider release to Sonarr season {target.season_number}",
                     f"external regular episode count equals Sonarr target count {len(rows)}",
                     "provider numbering is contiguous and extra provider items are strictly trailing"],len(rows),observed_numbers=used,status="complete"))
                continue
            ordering=external_ordering_projection(case_by_target[tid],target,len(nums))
            ordering_identity=external_provider_mal_entry(case_by_target[tid],target,c)
            if complete_target and exact and nums==tuple(range(1,len(nums)+1)) and ordering:
                base,alternate=ordering;used=nums[:len(rows)]
                basis="verified_external_ordering_prefix_projection_with_provider_identity" if ordering_identity else "verified_external_ordering_prefix_projection"
                evidence=[f"TVDB {base[0].get('ordering')} ordering has exactly the Sonarr target's {len(rows)} episodes",
                     f"TVDB {alternate[0].get('ordering')} ordering has {len(nums)} episodes and preserves the same first {len(rows)} episode titles",
                     f"provider release has the same {len(nums)}-episode contiguous ordering; only the trailing alternate-order episode(s) are excluded"]
                if ordering_identity:evidence.append(f"independent {ordering_identity[0].get('source')} binds this exact provider release to MAL {ordering_identity[1].get('mal_id')}")
                choices[tid].append(segment(identity,rows,used,basis,"metadata_supported",evidence,
                    len(rows),observed_numbers=used,status="complete"))
                continue
            span=external_provider_series_span_projection(case_by_target[tid],target,c,nums)
            if (complete_target and exact and span and rows and same_date(c.premiere_date,rows[0].get("airDate"))):
                crosswalk,mid,structure,seasons=span;used=nums[:len(rows)]
                choices[tid].append(segment(identity,rows,used,"verified_provider_series_span_prefix","metadata_supported",
                    [f"provider catalog directly identifies MAL {mid} with the same complete {len(nums)}-episode release",
                     f"cross-checked {structure.get('source')} structure splits that release as {list(seasons)}",
                     f"requested Sonarr season {target.season_number} is the verified first {len(rows)}-episode prefix",
                     "provider premiere matches the Sonarr season start and source numbering is contiguous"],
                    len(rows),observed_numbers=used,status="complete"))
                continue
            named_support=independent_named_season_identity(case_by_target[tid],target,c)
            if complete_target and len(nums)==len(rows) and named_support:
                source,season=named_support
                choices[tid].append(segment(identity,rows,nums,"independent_named_season_identity","metadata_supported",
                    [f"independent {source.get('source')} names Sonarr season {target.season_number} as {season.get('name')}",
                     f"independent season count equals the complete target count {target.episode_count}",
                     "provider and independent season names contain the exact same normalized token multiset",
                     "provider and independent season premieres agree within the one-day tolerance"],len(rows)))
                continue
            independent_support=external_season(case_by_target[tid],target,independent=True)
            if (complete_target and exact and len(nums)==len(rows) and independent_support
                    and c.premiere_date and independent_support[1].get("premiere_date")
                    and same_date(c.premiere_date,independent_support[1]["premiere_date"])):
                source,season=independent_support
                choices[tid].append(segment(identity,rows,nums,"independent_external_premiere_and_structure","metadata_supported",
                    ["exact provider/target identity", f"independent {source.get('source')} season count equals the target",
                     "independent season premiere matches the provider release premiere",
                     "observed contiguous source numbering equals complete Sonarr episode vector"],len(rows)))
                continue
            # A verified season-scoped Sonarr alias is stronger than a generic title match.
            # If provider and Sonarr disagree on the exact premiere day, retain safety by
            # requiring the same release year plus exact complete observed numbering.
            row_year = str(rows[0].get("airDate") or "")[:4] if rows else ""
            if (complete_target and scoped_exact and len(nums)==len(rows) and c.trusted("release_year")
                    and c.release_year is not None and row_year.isdigit() and c.release_year==int(row_year)):
                choices[tid].append(segment(identity,rows,nums,"verified_scoped_alias_year_numbering","metadata_supported",
                    ["verified Sonarr season-scoped alias exactly identifies the provider release",
                     "provider release year equals Sonarr season start year",
                     "observed contiguous source numbering equals complete Sonarr episode vector"],len(rows)))
                continue
            if complete_target and scoped_no_date_exact and len(nums)==len(rows):
                choices[tid].append(segment(identity,rows,nums,"verified_scoped_alias_numbering","metadata_supported",
                    ["verified Sonarr season-scoped alias exactly identifies the provider release",
                     "provider date/year is not used as season evidence",
                     "observed contiguous source numbering equals complete Sonarr episode vector"],len(rows)))
                continue
            # An explicit provider ordinal can use cross-checked external season
            # structure when the provider's dub/republication date differs.
            support=external_season(case_by_target[tid],target)
            if complete_target and ordinal_anchor and len(nums)==len(rows) and support:
                source,season=support
                choices[tid].append(segment(identity,rows,nums,"provider_ordinal_with_external_season_structure","metadata_supported",
                    [f"provider title explicitly labels season {target.season_number}",
                     f"cross-checked {source.get('source')} season structure reports {target.episode_count} episodes",
                     "observed contiguous source numbering equals complete Sonarr episode vector"],len(rows)))
                continue
            if (complete_target and series_prefix_identity(target,c) and len(nums)==len(rows)
                    and same_date(c.premiere_date,rows[0].get("airDate"))):
                choices[tid].append(segment(identity,rows,nums,"series_prefix_date_count_numbering","metadata_supported",
                    ["provider title is an exact extension of the Sonarr series title",
                     "provider premiere matches Sonarr episode 1",
                     "provider episode count and observed contiguous numbering exactly cover the target season"],len(rows)))
                continue
            # Scene numbering is an explicit Sonarr coordinate system, NOT a season-number synonym.
            scene_values = {r.get("sceneSeasonNumber") for r in rows if r.get("sceneSeasonNumber",0) and r.get("sceneSeasonNumber",0)>0}
            found = False
            for scene in sorted(scene_values):
                part_rows = [r for r in rows if r.get("sceneSeasonNumber")==scene]
                part_rows.sort(key=lambda r:r.get("sceneEpisodeNumber") or 0)
                anchored = any(a.source.reliable and key(a.text) in titles(c) and a.scene_season_number==scene and
                    (a.scope() is None or a.scope()==target.season_number) for a in target.alternate_titles)
                # Canonical release starts at target episode 1; later parts require their scoped scene alias.
                anchored = anchored or (exact and part_rows[0]["episodeNumber"]==1)
                if (complete_target and anchored and tuple(r.get("sceneEpisodeNumber") for r in part_rows)==nums
                        and same_date(c.premiere_date,part_rows[0].get("airDate"))):
                    choices[tid].append(segment(identity,part_rows,nums,"explicit_scene_coordinates_with_release_anchor",
                        "metadata_supported",[f"Sonarr scene season {scene} explicit episode rows",
                        "exact release alias and matching first scene episode air date", "observed source numbering equals scene numbering"],len(rows)))
                    found = True
            if not found:
                exclusions[tid].append({"release_id":identity.release_id,"reason":"crosswalk_missing" if exact else "identity_unresolved"})
        offset_plan=external_offset_segments(case_by_target[tid],target,groups,rows) if complete_target else ()
        if offset_plan:choices[tid].extend(offset_plan)
        # Some Sonarr seasons deliberately append a separately published OVA/ONA
        # or Special. Compose it only when base identity, exact counts and both
        # provider premiere dates independently anchor the Sonarr boundaries.
        if complete_target:
            for base_identity,base_variants in groups:
                base=base_variants[0];base_nums=base_identity.episode_numbers
                if not (titles(target,target.season_number)&titles(base)):continue
                if not (contiguous(base_nums) and len(base_nums)==base.episode_count and base.episodes_complete
                        and base.trusted("episode_count") and base.trusted("premiere_date") and base.trusted("episodes_complete")):continue
                if not rows or not same_date(base.premiere_date,rows[0].get("airDate")):continue
                for extra_identity,extra_variants in groups:
                    if extra_identity.release_id==base_identity.release_id:continue
                    extra=extra_variants[0];extra_nums=extra_identity.episode_numbers;extra_base,suffix=supplemental_identity(extra)
                    if extra_base not in titles(base) or suffix is None:continue
                    if not (contiguous(extra_nums) and len(extra_nums)==extra.episode_count and extra.episodes_complete
                            and extra.trusted("episode_count") and extra.trusted("premiere_date") and extra.trusted("episodes_complete")):continue
                    boundary=len(base_nums)
                    if boundary+len(extra_nums)!=len(rows) or not same_date(extra.premiere_date,rows[boundary].get("airDate")):continue
                    choices[tid].append(segment(base_identity,rows[:boundary],base_nums,
                        "supplemental_release_suffix_with_airdate_boundary","metadata_supported",
                        ["base release exactly identifies the target season", "base premiere matches Sonarr episode 1",
                         f"provider explicitly labels the appended release as {suffix.upper()}"],len(rows)))
                    choices[tid].append(segment(extra_identity,rows[boundary:],extra_nums,
                        "supplemental_release_suffix_with_airdate_boundary","metadata_supported",
                        [f"supplemental {suffix.upper()} title preserves the exact base release identity",
                         "supplemental premiere matches the Sonarr append boundary",
                         "base plus supplemental episode counts exactly cover the target"],len(rows)))
        # Ordinary cours/parts are safe when every reset-numbered release has an
        # explicit sequential label and its premiere independently anchors the
        # corresponding Sonarr boundary. Episode-count arithmetic alone is never enough.
        if complete_target:
            families={}
            for identity,variants in groups:
                c=variants[0];nums=identity.episode_numbers;base,part,explicit_part=part_identity(c)
                if (contiguous(nums) and len(nums)==c.episode_count and c.trusted("episode_count")
                        and c.trusted("premiere_date") and c.episodes_complete and c.trusted("episodes_complete")):
                    families.setdefault(base,{})[part]=(identity,c,nums,explicit_part)
            for base,parts in families.items():
                if len(parts)<2 or set(parts)!=set(range(1,max(parts)+1)):continue
                first=parts[1][1]
                if not (titles(target,target.season_number)&titles(first)):continue
                offset=0;segments=[];valid=True
                for part in range(1,max(parts)+1):
                    identity,c,nums,_=parts[part];part_rows=rows[offset:offset+len(nums)]
                    if len(part_rows)!=len(nums) or not same_date(c.premiere_date,part_rows[0].get("airDate")):
                        valid=False;break
                    segments.append(segment(identity,part_rows,nums,"explicit_part_label_with_sonarr_airdate_boundary",
                        "metadata_supported",[f"explicit sequential release part {part}",
                        "release premiere matches the first Sonarr episode at this boundary",
                        "observed reset source numbering is contiguous and complete"],len(rows)))
                    offset+=len(nums)
                if valid and offset==len(rows):
                    choices[tid].extend(segments)
                    continue
                # Streaming/ONA release dates may differ from later broadcast dates.
                # Only literal Part/Parte labels on every continuation can replace
                # the date anchors; plain trailing numerals never use this fallback.
                later_explicit=all(parts[p][3] for p in range(2,max(parts)+1))
                scoped_first=any(a.source.reliable and a.scope()==target.season_number and key(a.text) in titles(first)
                    for a in target.alternate_titles)
                if later_explicit and scoped_first and sum(len(parts[p][2]) for p in parts)==len(rows):
                    offset=0;segments=[]
                    for part in range(1,max(parts)+1):
                        identity,c,nums,_=parts[part];part_rows=rows[offset:offset+len(nums)]
                        segments.append(segment(identity,part_rows,nums,"explicit_provider_part_labels_with_scoped_identity",
                            "metadata_supported",["verified Sonarr season-scoped identity anchors the release family",
                            f"provider explicitly labels continuation part {part}" if part>1 else "provider base release is part 1",
                            "all reset-numbered provider parts are contiguous, complete, and exactly cover the target"],len(rows)))
                        offset+=len(nums)
                    choices[tid].extend(segments)

    # Sequential provider season labels may form one Sonarr season when an external
    # season-scoped identity independently binds every piece and dates anchor boundaries.
    for tid,target in targets.items():
        rows=rows_by_target[tid]
        if not rows or target.episode_count is None or len(rows)!=target.episode_count:continue
        families={}
        for identity,variants in groups:
            c=variants[0];nums=identity.episode_numbers;m=re.search(r"\s+(\d+)$",key(c.canonical_title))
            scoped=any(a.source.reliable and a.scope()==target.season_number and key(a.text) in titles(c) for a in target.alternate_titles)
            if not (m and scoped and contiguous(nums) and len(nums)==c.episode_count and c.episodes_complete):continue
            families.setdefault(key(c.canonical_title)[:m.start()],{})[int(m[1])]=(identity,c,nums)
        for base,parts in families.items():
            order=sorted(parts)
            if len(order)<2 or order!=list(range(order[0],order[-1]+1)):continue
            if sum(len(parts[n][2]) for n in order)!=len(rows):continue
            offset=0;segments=[];valid=True
            for n in order:
                identity,c,nums=parts[n];part_rows=rows[offset:offset+len(nums)]
                if len(part_rows)!=len(nums) or not same_date(c.premiere_date,part_rows[0].get("airDate")):
                    valid=False;break
                segments.append(segment(identity,part_rows,nums,"verified_scoped_sequential_provider_seasons",
                    "metadata_supported",["trusted season-scoped external identity binds this provider release",
                    f"provider sequential label {n} is contiguous within the family",
                    "provider premiere matches the corresponding Sonarr internal boundary"],len(rows)))
                offset+=len(nums)
            if valid:choices[tid].extend(segments)

    # Some providers label a continuation as plain `Title 2` rather than Part 2.
    # Never interpret that globally: require an observed base `Title`, exact two-piece
    # target coverage, a scoped/base identity anchor, and Sonarr air-date anchors at
    # both boundaries. This distinguishes continuations from ordinary season titles.
    group_by_family={}
    for identity,variants in groups:
        candidate=variants[0]
        group_by_family.setdefault(transport_family(candidate.canonical_title),[]).append((identity,candidate))
    for tid,target in targets.items():
        rows=rows_by_target[tid]
        if not rows or target.episode_count is None or len(rows)!=target.episode_count:continue
        for second_key,second_pairs in list(group_by_family.items()):
            match=re.search(r"\s+2$",second_key)
            if not match:continue
            base=second_key[:match.start()]
            first_pairs=group_by_family.get(base,())
            if not first_pairs:continue
            for second_identity,second in second_pairs:
                for first_identity,first in first_pairs:
                    if first_identity.release_id==second_identity.release_id:continue
                    first_nums,second_nums=first_identity.episode_numbers,second_identity.episode_numbers
                    if not (contiguous(first_nums) and contiguous(second_nums)
                            and len(first_nums)+len(second_nums)==len(rows)):continue
                    boundary=len(first_nums)
                    if boundary<=0 or boundary>=len(rows):continue
                    scoped=bool(titles(target,target.season_number)&titles(first)) or any(
                        a.source.reliable and a.scope()==target.season_number and key(a.text) in titles(first)
                        for a in target.alternate_titles)
                    family=scoped or (series_prefix_identity(target,first) and series_prefix_identity(target,second))
                    if not family:continue
                    if not same_date(first.premiere_date,rows[0].get("airDate")):continue
                    if not same_date(second.premiere_date,rows[boundary].get("airDate")):continue
                    basis="numbered_provider_continuation_with_sonarr_airdate_boundary" if scoped else "series_prefix_continuation_with_sonarr_airdate_boundaries"
                    pair=[
                        segment(first_identity,rows[:boundary],first_nums,basis,"metadata_supported",
                            ["provider release belongs to the exact Sonarr series family",
                             "base release premiere matches Sonarr target start",
                             "observed source numbering is contiguous and complete"],len(rows)),
                        segment(second_identity,rows[boundary:],second_nums,basis,"metadata_supported",
                            ["provider continuation uses a plain trailing 2 label",
                             "continuation premiere matches the Sonarr internal boundary",
                             "two provider releases exactly cover the target"],len(rows))]
                    signatures={(x.release.release_id,tuple((l.source_episode,l.sonarr_season,l.sonarr_episode) for l in x.coverage.links)) for x in choices[tid]}
                    for item in pair:
                        sig=(item.release.release_id,tuple((l.source_episode,l.sonarr_season,l.sonarr_episode) for l in item.coverage.links))
                        if sig not in signatures:choices[tid].append(item);signatures.add(sig)

    # A complete provider release may span consecutive Sonarr seasons. Prefer an
    # independent season structure when available; otherwise Sonarr's own explicit
    # absolute episode coordinates can prove the split for a release that starts at
    # season 1 with exact series identity and matching premiere.
    ordered_ids=sorted(targets,key=lambda t:targets[t].season_number)
    for identity,variants in groups:
        nums=identity.episode_numbers;c=variants[0]
        if not (nums and isinstance(c.episode_count,int) and c.episode_count>0 and c.episodes_complete
                and c.trusted("episode_count") and c.trusted("premiere_date") and c.trusted("episodes_complete")):continue
        declared=tuple(range(1,c.episode_count+1));observed=set(nums)
        if not observed<=set(declared):continue
        provider_gaps=set(declared)-observed
        for start in range(len(ordered_ids)):
            for end in range(start+2,len(ordered_ids)+1):
                tids=ordered_ids[start:end];subset=[targets[t] for t in tids]
                if any(b.season_number!=a.season_number+1 for a,b in zip(subset,subset[1:])):continue
                if any(t.episode_count is None or len(rows_by_target[tid])!=t.episode_count for tid,t in zip(tids,subset)):continue
                rows=[r for t in tids for r in rows_by_target[t]]
                if len(rows)!=len(declared) or tuple(r.get("absoluteEpisodeNumber") for r in rows)!=declared:continue
                exact_series=(transport_family(c.canonical_title)==transport_family(subset[0].canonical_title))
                if not exact_series or not rows or not same_date(c.premiere_date,rows[0].get("airDate")):continue
                independent=[]
                for source in external_sources(case_by_target[tids[0]],subset[0],independent=True):
                    by_season={x.get("season_number"):x for x in source.get("seasons",[])}
                    if all(by_season.get(t.season_number,{}).get("episode_count")==t.episode_count for t in subset):
                        independent.append(source)
                if provider_gaps:
                    if subset[0].season_number!=1 or len(subset)<3:continue
                    basis="sonarr_absolute_sequence_with_provider_gap"
                    evidence=["provider declares the exact combined Sonarr episode count and exact series identity",
                        "observed provider numbering is a subset of Sonarr absoluteEpisodeNumber with explicit holes",
                        f"provider currently omits source episode numbers {sorted(provider_gaps)}"]
                elif independent:
                    source=independent[0]
                    basis="independent_external_season_structure"
                    evidence=[f"cross-checked independent {source.get('source')} season structure confirms the internal boundary",
                        "provider numbering equals the combined Sonarr absolute sequence"]
                else:
                    if subset[0].season_number!=1 or len(subset)<3:continue
                    basis="sonarr_absolute_sequence_with_series_identity"
                    evidence=["provider release has exact Sonarr series identity and matching premiere",
                        "provider numbering exactly equals Sonarr absoluteEpisodeNumber across consecutive seasons",
                        "each Sonarr season has complete explicit episode rows, so internal boundaries are not inferred from count arithmetic"]
                for tid,target in zip(tids,subset):
                    target_rows=rows_by_target[tid]
                    mapped_rows=[row for row in target_rows if row.get("absoluteEpisodeNumber") in observed]
                    gap_rows=[row for row in target_rows if row.get("absoluteEpisodeNumber") not in observed]
                    if not mapped_rows:continue
                    source_numbers=tuple(row.get("absoluteEpisodeNumber") for row in mapped_rows)
                    local_basis=basis
                    local_evidence=list(evidence)
                    if gap_rows:
                        existing=all(row.get("hasFile") is True for row in gap_rows)
                        local_basis="sonarr_absolute_sequence_existing_file_gap" if existing else "sonarr_absolute_sequence_provider_gap"
                        local_evidence.append("provider gap affects only episodes already present in Sonarr" if existing
                            else "provider gap includes at least one episode not already present in Sonarr")
                    choices[tid].append(segment(identity,mapped_rows,source_numbers,local_basis,
                        "metadata_supported",local_evidence+[f"Sonarr season {target.season_number} contributes exactly {len(target_rows)} rows"],
                        len(target_rows),status="partial" if gap_rows else "complete"))
    results = {}; alternatives = {}
    for tid,target in targets.items():
        desired = set(range(1,(target.episode_count or 0)+1))
        solutions = []
        unique_available={}
        for item in choices[tid]:
            signature=(item.release.release_id,tuple((x.source_episode,x.sonarr_season,x.sonarr_episode) for x in item.coverage.links))
            unique_available.setdefault(signature,item)
        available=list(unique_available.values())
        solutions,cover_overflow=best_exact_covers(available,desired)
        if cover_overflow:
            alternatives[tid]=[]
            results[tid]=MappingPlan((tid,),(),"needs_review",("candidate_overflow",))
            continue
        alternatives[tid]=[MappingPlan((tid,),solution,"needs_review",("multiple_equivalent_candidates",)).to_dict() for solution in solutions]
        selected_solution=None
        if len(solutions)==1:selected_solution=solutions[0]
        elif len(solutions)>1:
            audio_by_release={identity.release_id:{c.audio for c in variants} for identity,variants in groups}
            best=max(_solution_quality(solution) for solution in solutions)
            strongest=[solution for solution in solutions if _solution_quality(solution)==best]
            if len(strongest)==1:
                selected_solution=strongest[0]
            else:
                def structure(solution):
                    return tuple((transport_family(seg.release.title),tuple((x.source_episode,x.sonarr_season,x.sonarr_episode) for x in seg.coverage.links)) for seg in solution)
                if len({structure(solution) for solution in strongest})==1:
                    full_dub=[solution for solution in strongest if all(Audio.DUB in audio_by_release.get(seg.release.release_id,set()) for seg in solution)]
                    full_sub=[solution for solution in strongest if all(Audio.SUB in audio_by_release.get(seg.release.release_id,set()) for seg in solution)]
                    if len(full_dub)==1:selected_solution=full_dub[0]
                    elif len(full_sub)==1:selected_solution=full_sub[0]
        if selected_solution is None and not solutions:
            gap_items=[item for item in available if item.crosswalk.basis in {
                "sonarr_absolute_sequence_existing_file_gap","sonarr_absolute_sequence_provider_gap"}]
            row_by_episode={row["episodeNumber"]:row for row in rows_by_target[tid]}
            ready=[];blocked=[]
            for item in gap_items:
                covered={link.sonarr_episode for link in item.coverage.links}
                missing=desired-covered
                if not missing:continue
                if all(row_by_episode.get(ep,{}).get("hasFile") is True for ep in missing):
                    ready.append((item,))
                else:
                    blocked.append(item)
            if len(ready)==1:
                selected_solution=ready[0]
            elif not ready and len(blocked)==1:
                results[tid]=MappingPlan((tid,),(blocked[0],),"needs_review",("provider_episode_gap",))
                continue
        if selected_solution is not None:
            results[tid]=MappingPlan((tid,),selected_solution,"matched")
        else:
            aired_count=len(aired_prefix(rows_by_target[tid]))
            prefix=set(range(1,aired_count+1))
            partial=[item for item in available
                if {link.sonarr_episode for link in item.coverage.links}==prefix]
            if len(partial)==1 and aired_count<(target.episode_count or 0):
                results[tid]=MappingPlan((tid,),(partial[0],),"needs_review",("season_in_progress",))
            else:
                reasons=("multiple_equivalent_candidates",) if len(solutions)>1 else ("crosswalk_missing","insufficient_metadata")
                results[tid]=MappingPlan((tid,),(),"needs_review",reasons)
    # A transport/release episode cannot be assigned to incompatible targets in separate plans.
    owners = {}
    collisions = set()
    for tid,p in results.items():
        for s in p.segments:
            for link in s.coverage.links:
                coordinate=(s.release.release_id,link.source_episode)
                if coordinate in owners and owners[coordinate]!=tid:
                    collisions.update((tid,owners[coordinate]))
                owners[coordinate]=tid
    for tid in collisions:
        results[tid]=MappingPlan((tid,),(),"needs_review",("duplicate_url_across_seasons",))
    # Inverse proposals retain explicit absolute coordinates but mark the release boundary inferred.
    inferred=[]
    ordered=sorted(targets,key=lambda tid:targets[tid].season_number)
    for identity,variants in groups:
        nums=identity.episode_numbers;c=variants[0]
        if not contiguous(nums) or len(nums)!=c.episode_count:continue
        for start in range(len(ordered)):
            for end in range(start+2,len(ordered)+1):
                tids=ordered[start:end];subset=[targets[t] for t in tids]
                if any(b.season_number!=a.season_number+1 for a,b in zip(subset,subset[1:])):continue
                rows=[r for t in tids for r in rows_by_target[t]]
                if not rows or len(rows)!=len(nums) or any(len(rows_by_target[t])!=targets[t].episode_count for t in tids):continue
                absolutes=tuple(r.get("absoluteEpisodeNumber") for r in rows)
                if absolutes!=nums or not same_date(c.premiere_date,rows[0].get("airDate")) or not titles(subset[0],subset[0].season_number)&titles(c):continue
                s=segment(identity,rows,nums,"absolute_sequence_concatenation","inferred",
                    ["observed release numbering equals combined Sonarr absolute sequence", "first premiere and exact identity agree",
                     "NO independent source evidence certifies the internal season boundary"],len(rows))
                inferred.append(MappingPlan(tuple(tids),(s,),"needs_review",("crosswalk_missing",)))
    raw=cases[0]["raw"]["sonarr_series"]
    series=SeriesIdentity(raw["id"],raw["title"],tuple((k,raw[k]) for k in ("tvdbId","imdbId","tvMazeId") if raw.get(k)))
    contracts=tuple(SeasonTarget(series,t,targets[t].season_number,tuple(r["episodeNumber"] for r in rows_by_target[t]),targets[t].episode_count) for t in ordered)
    return {"plans":results,"inferred_plans":inferred,"releases":[g[0] for g in groups],"targets":contracts,"excluded":exclusions,"alternative_plans":alternatives}
