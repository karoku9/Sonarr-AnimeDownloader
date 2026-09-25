"""Production snapshots: raw/derived/provenance separated, matcher policy unchanged."""
from datetime import date,datetime,timezone
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
import re
from .models import Alias,Audio,Candidate,Target,SourceMetadata
from .provenance import FieldProvenance,SeasonCrosswalk
from .normalization import normalize_title
from .matching import canonical_url
from .title_aliases import equivalents

MONTHS={name:index for index,name in enumerate(("gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"),1)}

def parse_date(value):
    if not value:return None
    try:return date.fromisoformat(value[:10])
    except ValueError:pass
    parts=value.casefold().split()
    try:return date(int(parts[2]),MONTHS[parts[1]],int(parts[0])) if len(parts)==3 else None
    except (KeyError,ValueError):return None

_key=lru_cache(maxsize=20000)(normalize_title)

def trusted_external_sources(row, metadata):
    """Yield external metadata only when it cross-checks a Sonarr external ID."""
    expected={}
    for local,external in (("tvdbId","tvdb"),("tvMazeId","tvmaze"),("imdbId","imdb")):
        value=row.get(local)
        if value not in (None,"",0,"0"):expected[external]=str(value)
    for source in (metadata or {}).get("sources",[]):
        ids={str(k).casefold():str(v) for k,v in source.get("external_ids",{}).items() if v not in (None,"")}
        shared=set(expected)&set(ids)
        if shared and all(ids[k]==expected[k] for k in shared):
            yield source

def external_aliases(row, metadata):
    result=[]
    for source in trusted_external_sources(row,metadata):
        for value in [source.get("title"),*source.get("aliases",[])]:
            if isinstance(value,str) and value and value not in result:result.append(value)
        for item in source.get("season_aliases",[]):
            value=item.get("title") if isinstance(item,dict) else None
            if isinstance(value,str) and value and value not in result:result.append(value)
    return result

def independent_season_counts(row, metadata, season_number):
    """Return independently observed total episode counts for one Sonarr season."""
    result=[]
    for source in trusted_external_sources(row,metadata):
        if source.get("independent_of_sonarr") is not True:continue
        count=None
        season=next((x for x in source.get("seasons",[]) if x.get("season_number")==season_number),None)
        if season and isinstance(season.get("episode_count"),int):count=season["episode_count"]
        expected=source.get("expected_mal_entries") or []
        if expected:
            spans=[]
            for item in expected:
                n=item.get("episode_count");offset=item.get("episode_offset") or 0
                if not isinstance(n,int) or not isinstance(offset,int):spans=[];break
                spans.append(offset+n)
            if spans:count=max(spans)
        if isinstance(count,int) and count>0:result.append((str(source.get("source") or "external"),count))
    return result


def reconciled_episode_count(row, season, rows, metadata):
    stats=season.get("statistics",{});total=stats.get("totalEpisodeCount");current=stats.get("episodeCount")
    if not isinstance(total,int):return total,None
    if not (isinstance(current,int) and 0<current<total):return total,None
    trailing=[e for e in rows if isinstance(e.get("episodeNumber"),int) and e["episodeNumber"]>current]
    if not trailing or any(e.get("airDate") or e.get("absoluteEpisodeNumber") is not None for e in trailing):return total,None
    agreeing={name for name,count in independent_season_counts(row,metadata,season.get("seasonNumber")) if count==current}
    if len(agreeing)<2:return total,None
    origin=SourceMetadata("external_completed_count_consensus",f"{row.get('id')}:{season.get('seasonNumber')}",attributes=tuple(("source",name) for name in sorted(agreeing)))
    return current,origin


def verified_provider_absence(row, metadata, target):
    for source in trusted_external_sources(row,metadata):
        if source.get("provider_catalog_complete") is not True:continue
        expected=source.get("expected_mal_entries") or []
        missing=source.get("missing_mal_ids_verified") or []
        if not expected or not missing:continue
        spans=[]
        for item in expected:
            count=item.get("episode_count");offset=item.get("episode_offset") or 0
            if not isinstance(count,int) or not isinstance(offset,int):spans=[];break
            spans.append(offset+count)
        if spans and target.episode_count==max(spans):
            return {"state":"unavailable","verified":True,"method":"fribb_mal_complete_animeworld_catalog_audit",
                "missing_mal_ids":sorted({int(v) for v in missing if isinstance(v,int)})}
    return None


def similarity(a,b):
    if a==b:return 1.0
    scorer=SequenceMatcher(None,a,b)
    if scorer.real_quick_ratio()<.5 or scorer.quick_ratio()<.5:return 0.0
    return scorer.ratio()

def retrieve(row,catalog,mappings,external_metadata=None):
    observed_titles={row["title"]}|{a["title"] for a in row.get("alternateTitles",[])}|set(external_aliases(row,external_metadata))
    keys={_key(alias) for title in observed_titles for alias in equivalents(title)}
    series_keys={_key(alias) for alias in equivalents(row["title"])}
    ranked=[]
    for entry in catalog:
        family_prefix=False
        if entry.get("source")=="table":
            # A saved mapping label is not a release title; retrieve by observation only.
            ratio=0
        else:
            entry_titles={entry["title"]}|set(entry.get("aliases",[]))
            entry_keys={_key(alias) for title in entry_titles for alias in equivalents(title)}
            ratio=max(similarity(k,key) for k in keys for key in entry_keys)
            # Providers often publish later arcs under "Series: Arc Name"; preserve
            # those candidates without lowering the global fuzzy threshold.
            family_prefix=any(len(base)>=5 and any(value.startswith(base+" ") for value in entry_keys)
                for base in series_keys if base)
        ranked.append((ratio,family_prefix,entry["url"],entry))
    ranked.sort(key=lambda v:(-v[0],not v[1],v[2]))
    selected={url:entry for ratio,prefix,url,entry in ranked if ratio>=.62 or prefix}
    # Keep all exact-title ties, including parallel language releases.
    for ratio,prefix,url,entry in ranked:
        if ratio==1:selected[url]=entry
    return list(selected.values())

def tvdb_relocated_specials(row, metadata):
    """Return one proven TVDB relation where alternate-order tail equals all official specials."""
    sources=list(trusted_external_sources(row,metadata))
    official=next((source for source in sources if source.get("ordering")=="official" and str(source.get("source","")).startswith("tvdb_public_")),None)
    if not official:return None
    special=next((season for season in official.get("seasons",[]) if season.get("season_number")==0),None)
    special_eps=sorted((special or {}).get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
    if not special_eps or tuple(e.get("episode_number") for e in special_eps)!=tuple(range(1,len(special_eps)+1)):return None
    special_titles=tuple(_key(e.get("title") or "") for e in special_eps)
    if any(not title for title in special_titles):return None
    proposals=[]
    for alternate in sources:
        if alternate.get("ordering") not in {"alternate","absolute","dvd"} or not str(alternate.get("source","")).startswith("tvdb_public_"):continue
        for alt_season in alternate.get("seasons",[]):
            number=alt_season.get("season_number")
            if not isinstance(number,int) or number<=0:continue
            base=next((season for season in official.get("seasons",[]) if season.get("season_number")==number),None)
            if not base:continue
            base_eps=sorted(base.get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
            alt_eps=sorted(alt_season.get("episodes") or [],key=lambda e:e.get("episode_number") or 0)
            if not base_eps or len(alt_eps)!=len(base_eps)+len(special_eps):continue
            base_titles=tuple(_key(e.get("title") or "") for e in base_eps);alt_titles=tuple(_key(e.get("title") or "") for e in alt_eps)
            if any(not title for title in (*base_titles,*alt_titles)):continue
            if alt_titles[:len(base_titles)]==base_titles and alt_titles[len(base_titles):]==special_titles:
                proposals.append({"regular_season":number,"regular_count":len(base_eps),"special_count":len(special_eps),"combined_count":len(alt_eps),"ordering":alternate.get("ordering")})
    unique={(p["regular_season"],p["regular_count"],p["special_count"],p["combined_count"]):p for p in proposals}
    return next(iter(unique.values())) if len(unique)==1 else None


def targets(row,episodes,manual_aliases=(),external_metadata=None):
    source=SourceMetadata("sonarr_series",str(row["id"]))
    aliases=tuple(Alias(a["title"],kind="sonarr_alternate",season_number=a.get("seasonNumber") if a.get("seasonNumber",-1)>=0 else None,scene_season_number=a.get("sceneSeasonNumber"),scene_namespace="scene",source=source) for a in row.get("alternateTitles",[]))
    aliases+=tuple(Alias(a,kind="manual",source=SourceMetadata("manual_alias",row["title"])) for a in manual_aliases)
    for ext in trusted_external_sources(row,external_metadata):
        ext_source=SourceMetadata(ext.get("source","external_metadata"),str(ext.get("record_id") or ""),
            attributes=tuple(("external_id",f"{k}:{v}") for k,v in sorted(ext.get("external_ids",{}).items())))
        aliases+=tuple(Alias(a,kind="external_series_alias",source=ext_source) for a in [ext.get("title"),*ext.get("aliases",[])] if isinstance(a,str) and a)
        aliases+=tuple(Alias(a.get("title"),kind="external_season_alias",season_number=a.get("season_number"),source=ext_source)
            for a in ext.get("season_aliases",[]) if isinstance(a,dict) and isinstance(a.get("title"),str) and a.get("title") and isinstance(a.get("season_number"),int))
    crosswalks=[]
    mapped={}
    for episode in episodes:
        scene=episode.get("sceneSeasonNumber")
        real=episode.get("seasonNumber")
        if scene is not None and scene>0 and real is not None and real>0:
            mapped.setdefault(scene,[]).append(episode)
    verified_aliases=[]
    for alias in aliases:
        scene=alias.scene_season_number
        proof=mapped.get(scene,[])
        real={e["seasonNumber"] for e in proof}
        if alias.season_number is None and len(real)==1:
            sonarr=next(iter(real))
            origin=SourceMetadata("verified_sonarr_scene_crosswalk",f"{row['id']}:{scene}",attributes=(("scene_season",str(scene)),("episode_rows",str(len(proof))),))
            explanation=f"{len(proof)} explicit Sonarr episode rows link scene S{scene} only to Sonarr S{sonarr}; title anchored to scoped Sonarr alias"
            verified_aliases.append(Alias(alias.text,kind="verified_scene_alias",season_number=sonarr,scene_season_number=scene,scene_namespace="scene",source=origin))
            crosswalks.append(SeasonCrosswalk(f"{row['id']}:{scene}:{len(crosswalks)}",sonarr,season_title=alias.text,verified=True,source=origin,reason=explanation))
    aliases+=tuple(verified_aliases)
    result=[]
    relocated=tvdb_relocated_specials(row,external_metadata)
    for season in row.get("seasons",[]):
        number=season["seasonNumber"]
        rows=[e for e in episodes if e.get("seasonNumber")==number]
        if number==0:
            if not relocated or len(rows)!=relocated["special_count"]:continue
            if tuple(sorted(e.get("episodeNumber") for e in rows))!=tuple(range(1,len(rows)+1)):continue
        first=next((e for e in rows if e.get("episodeNumber")==1 and e.get("airDate")),None)
        air=parse_date(first["airDate"]) if first else None
        fields=[]
        if air:fields.append(FieldProvenance.create("air_date",air,SourceMetadata("sonarr_episode",f"{row['id']}:{number}:1")))
        if number==0:
            count=relocated["special_count"]
            count_source=SourceMetadata("verified_tvdb_relocated_specials",f"{row['id']}:0",attributes=(("regular_season",str(relocated["regular_season"])),("ordering",str(relocated["ordering"])),))
        else:
            count,count_source=reconciled_episode_count(row,season,rows,external_metadata)
        if count_source:fields.append(FieldProvenance.create("episode_count",count,count_source))
        complete=row.get("ended",False)
        result.append(Target(row["title"],season_number=number,alternate_titles=aliases,air_date=air,release_year=row.get("year") if number==1 else None,episode_count=count,episodes_complete=complete,source=source,field_provenance=tuple(fields),external_ids=tuple((k,str(row[k])) for k in ("tvdbId","imdbId","tvMazeId") if row.get(k)),target_id=f"{row['id']}:{number}",crosswalks=tuple(crosswalks)))
    return result

def inferred_scene_season(title):
    text=str(title or "").strip()
    explicit=re.search(r"(?:\b(?:season|stagione)\s+(\d+)|\b(\d+)(?:st|nd|rd|th)\s+season\b)",text,re.I)
    if explicit:return int(next(v for v in explicit.groups() if v))
    multipart=re.search(r"\b(\d+)\s+(?:part|parte|cour)\s+\d+\b",text,re.I)
    if multipart:return int(multipart.group(1))
    suffix=re.search(r"\s(\d+)\s*(?:\(ITA\))?$",text,re.I)
    if not suffix:return None
    prefix=text[:suffix.start()].rstrip()
    if re.search(r"\b(?:part|parte|cour|hen)$",prefix,re.I):return None
    return int(suffix.group(1))


def candidate(entry,detail,mapped_seasons=None):
    url=entry["url"]
    id=hashlib.sha256(url.encode()).hexdigest()[:16]
    native=detail and detail.get("status")=="ok"
    raw=detail.get("raw",{}) if native else {}
    source=SourceMetadata("animeworld_detail" if native else entry.get("source","legacy_index"),id,reliable=bool(native or entry.get("source")=="animeworld_catalog"))
    fields={p["label"]:p["value"] for p in raw.get("fields",[])}
    title=raw.get("title") or entry["title"]
    aliases=[]
    for block in raw.get("structured",[]):
        series=block.get("partOfSeries",{}) if block.get("@type")=="TVEpisode" else block
        if not isinstance(series,dict):continue
        for key in ("name","alternateName"):
            values=series.get(key,[])
            if isinstance(values,str):values=[values]
            aliases.extend(Alias(v,kind="provider_"+key,source=source) for v in values if isinstance(v,str) and v)
    # V3 aliases may contain saved mapping labels merged into the catalog: keep raw only.
    audio=fields.get("Audio:","").casefold()
    native_audio=Audio.DUB if audio=="italiano" else Audio.SUB if audio in ("giapponese","japanese") else Audio.UNKNOWN
    assigned=native_audio if native_audio!=Audio.UNKNOWN else Audio(entry.get("audio","UNKNOWN"))
    year=int(fields["Anno:"]) if fields.get("Anno:","").isdigit() else entry.get("year")
    premiere=parse_date(fields.get("Data di Uscita:"))
    count=int(fields["Episodi:"]) if fields.get("Episodi:","").isdigit() else None
    complete=fields.get("Stato:")=="Finito" and count is not None
    mapped=tuple(int(s) for s in entry.get("table_seasons",[]) if str(s).isdigit()) if mapped_seasons is None else tuple(sorted(mapped_seasons))
    provenance=[]
    if native_audio==Audio.UNKNOWN:
        provenance.append(FieldProvenance.create("audio",assigned,SourceMetadata("legacy_audio_detection",id,reliable=False),derived=True))
    if not fields.get("Anno:") and year is not None:
        provenance.append(FieldProvenance.create("release_year",year,SourceMetadata("legacy_title_year",id,reliable=False),derived=True))
    if mapped:provenance.append(FieldProvenance.create("mapped_seasons",mapped,SourceMetadata("v3_mapping_observation",id,reliable=False)))
    scene=inferred_scene_season(title)
    if scene is not None:provenance.append(FieldProvenance.create("scene_season_number",scene,SourceMetadata("inferred_title",id,reliable=False),derived=True))
    return Candidate(id,title,url,alternate_titles=tuple(aliases),season_namespace="catalog",scene_season_number=scene,premiere_date=premiere,release_year=year,episode_count=count,episodes_complete=complete,episode_scope="catalog_release",audio=assigned,audio_language="it" if native_audio==Audio.DUB else "ja" if native_audio==Audio.SUB else None,mapped_seasons=mapped,source=source,field_provenance=tuple(provenance))


def catalog_entry_from_detail(detail):
    """Reconstruct a native catalog observation from captured provider metadata."""
    if not isinstance(detail,dict) or detail.get("status")!="ok":return None
    raw=detail.get("raw",{});url=raw.get("url");title=raw.get("title")
    if not url or not title:return None
    if url.count("/")>4:url=url.rsplit("/",1)[0]
    aliases=[]
    for block in raw.get("structured",[]):
        series=block.get("partOfSeries",{}) if block.get("@type")=="TVEpisode" else block
        if isinstance(series,dict):
            for name in (series.get("name"),series.get("alternateName")):
                if isinstance(name,str) and name:aliases.append(name)
    return {"title":title,"url":url,"aliases":list(dict.fromkeys(aliases)),"audio":"UNKNOWN","source":"animeworld_detail_index"}

def snapshot(series,episodes,catalog,mappings,details,manual=None,pools=None,availability=None,external=None):
    cases=[]
    excluded=[]
    tag_metadata_present=any("tag_labels" in row for row in series)
    for row in series:
        if row.get("seriesType")!="anime":continue
        if tag_metadata_present and "animeworld" not in {str(v).strip().casefold() for v in row.get("tag_labels",[])}:
            excluded.append({"id":row["id"],"title":row["title"],"reason":"missing_animeworld_tag"});continue
        metadata=(external or {}).get(str(row["id"]))
        entries=pools[str(row["id"])] if pools is not None and str(row["id"]) in pools else retrieve(row,catalog,mappings,metadata)
        scoped_mappings=[r for r in mappings if normalize_title(r["title"])==normalize_title(row["title"])]
        usage={}
        for observed in scoped_mappings:
            for season,urls in observed.get("seasons",{}).items():
                if str(season).isdigit():
                    for url in urls:usage.setdefault(canonical_url(url),set()).add(int(season))
        native=[candidate(e,details.get(e["url"]),mapped_seasons=usage.get(canonical_url(e["url"]),())) for e in entries if e.get("source")!="table" or details.get(e["url"],{}).get("status")=="ok"]
        for target in targets(row,episodes.get(str(row["id"]),[]),(manual or {}).get(row["title"],[]),metadata):
            observed=[r for r in mappings if normalize_title(r["title"])==normalize_title(row["title"])]
            existing=[u for r in observed for u in r.get("seasons",{}).get(str(target.season_number),[])]
            absence=(availability or {}).get(target.target_id) or verified_provider_absence(row,metadata,target)
            cases.append({"cohort":"production","raw":{"sonarr_series":row,"sonarr_season":next(s for s in row["seasons"] if s["seasonNumber"]==target.season_number),"sonarr_episodes":[e for e in episodes.get(str(row["id"]),[]) if e.get("seasonNumber")==target.season_number],"sonarr_special_episodes":[e for e in episodes.get(str(row["id"]),[]) if e.get("seasonNumber")==0],"sonarr_special_season":next((s for s in row["seasons"] if s.get("seasonNumber")==0),None),"catalog_entries":entries,"details":{e["url"]:details.get(e["url"]) for e in entries},"manual_aliases":(manual or {}).get(row["title"],[]),"source_absence":absence,"external_metadata":metadata},"derived":{"target":target.to_dict(),"candidates":[c.to_dict() for c in native]},"expected_v3_decision":{"kind":"persisted_mapping_observation","urls":existing,"ground_truth":False,"raw_mapping_rows":observed}})
    return {"schema_version":1,"kind":"production_metadata_replay","captured_at":datetime.now(timezone.utc).isoformat(),"ground_truth":None,
        "eligibility":{"required_tag":"animeworld" if tag_metadata_present else None,"excluded_series":excluded},
        "retrieval":{"method":"all native title/alias lexical >=.62 plus exact ties; no V3 mapping seeds","full_catalog_entries":len(catalog)},"cases":cases}
