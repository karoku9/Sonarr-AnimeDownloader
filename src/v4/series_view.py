"""Series-first presentation helpers for the Sonarr-style V4 UX."""
from collections import defaultdict
from urllib.parse import urlsplit

from .composite_replay import choose_plan_variants
from .models import Candidate,Audio


def series_id_from_target(target_id):
    try:return int(str(target_id).split(":",1)[0])
    except (TypeError,ValueError):raise ValueError("Invalid target id")


def target_for_season(original,season):
    return next((t for t in original["targets"] if t.get("season_number")==season),None)


def selected_candidates(original,preference=None):
    by_id={c["id"]:Candidate.from_dict(c) for c in original["candidates"]}
    groups=[[by_id[i] for i in segment["release"]["candidate_ids"] if i in by_id]
        for segment in original["proposed_plan"].get("segments",[])]
    wanted=preference or original.get("audio_preference")
    pref=Audio.DUB if wanted=="DUB" else Audio.SUB
    _,chosen=choose_plan_variants(groups,pref)
    return {segment["release"]["release_id"]:candidate
        for segment,candidate in zip(original["proposed_plan"].get("segments",[]),chosen)}
def override_active(override):
    override=override or {}
    return bool((override.get("source_urls") or override.get("source_url"))
        or override.get("ignore_errors") or override.get("manual_approved") or override.get("excluded")
        or override.get("episode_map") or str(override.get("note") or "").strip())

def config_active(override):
    override=override or {}
    return override_active(override) or override.get("audio") in {"SUB","DUB"}


def automation_status(item,target,override=None):
    override=override or {}
    if override.get("excluded"):return "excluded"
    if override_active(override):return "manual"
    state=item["mapping_state"]
    if state!="proposed":return state
    original=item["original"];season=target["season_number"]
    expected=target.get("episode_count")
    links=[link for segment in original["proposed_plan"].get("segments",[])
        for link in segment["coverage"].get("links",[]) if link.get("sonarr_season")==season]
    reliability=[segment["crosswalk"].get("reliability")
        for segment in original["proposed_plan"].get("segments",[])
        if any(link.get("sonarr_season")==season for link in segment["coverage"].get("links",[]))]
    exact=isinstance(expected,int) and sorted(link.get("sonarr_episode") for link in links)==list(range(1,expected+1))
    relevant=[segment for segment in original["proposed_plan"].get("segments",[])
        if any(link.get("sonarr_season")==season for link in segment["coverage"].get("links",[]))]
    existing_file_gap=bool(links) and bool(relevant) and all(
        segment["crosswalk"].get("basis")=="sonarr_absolute_sequence_existing_file_gap" for segment in relevant)
    return "auto" if (exact or existing_file_gap) and reliability and all(value!="inferred" for value in reliability) else "review"


def _manual_url(value):
    if not isinstance(value,str):raise ValueError("Manual source URL must be a string")
    value=value.strip()
    if not value:return None
    parsed=urlsplit(value);host=(parsed.hostname or "").casefold()
    if parsed.scheme!="https" or not (host=="animeworld.ac" or host.endswith(".animeworld.ac")):
        raise ValueError("Manual source must be an AnimeWorld HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:raise ValueError("Unsafe manual source URL")
    return value


def validate_override(values,episode_count=None):
    allowed={"source_url","source_urls","audio","ignore_errors","manual_approved","excluded","episode_map","note"}
    if set(values)-allowed:raise ValueError("Unexpected override fields")
    raw_urls=values.get("source_urls")
    if raw_urls is None:
        legacy=_manual_url(values["source_url"]) if values.get("source_url") is not None else None
        urls=[legacy] if legacy else []
    else:
        if not isinstance(raw_urls,list) or len(raw_urls)>8:raise ValueError("source_urls must be a list of at most 8 URLs")
        urls=[]
        for raw in raw_urls:
            url=_manual_url(raw)
            if url and url not in urls:urls.append(url)
        legacy=values.get("source_url")
        if legacy is not None:
            legacy=_manual_url(legacy)
            if legacy and urls and legacy!=urls[0]:raise ValueError("source_url must match the first source_urls entry")
            if legacy and not urls:urls=[legacy]
    audio=values.get("audio")
    if audio not in {None,"SUB","DUB"}:raise ValueError("audio must be SUB, DUB or null")
    bools={}
    for key in ("ignore_errors","manual_approved","excluded"):
        value=values.get(key,False)
        if not isinstance(value,bool):raise ValueError(f"{key} must be boolean")
        bools[key]=value
    episode_map=values.get("episode_map",[])
    if not isinstance(episode_map,list) or len(episode_map)>2000:raise ValueError("episode_map must be a list")
    normalized=[];destinations=set()
    for row in episode_map:
        if not isinstance(row,dict) or set(row)!={"sonarr_episode","source_episode","source_index"}:
            raise ValueError("Invalid manual episode mapping")
        sonarr,source,index=row["sonarr_episode"],row["source_episode"],row["source_index"]
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (sonarr,source,index)):
            raise ValueError("Manual episode mapping values must be integers")
        if sonarr<1 or source<1 or index<0 or index>=len(urls):raise ValueError("Manual episode mapping is out of range")
        if isinstance(episode_count,int) and sonarr>episode_count:raise ValueError("Sonarr episode exceeds season size")
        if sonarr in destinations:raise ValueError("Duplicate Sonarr episode in manual mapping")
        destinations.add(sonarr);normalized.append({"sonarr_episode":sonarr,"source_episode":source,"source_index":index})
    normalized.sort(key=lambda row:row["sonarr_episode"])
    note=values.get("note","")
    if not isinstance(note,str) or len(note)>500:raise ValueError("note must be <=500 characters")
    if bools["manual_approved"]:
        if not isinstance(episode_count,int) or episode_count<=0:
            raise ValueError("Manual approval requires a known destination episode count")
        if not urls or audio not in {"SUB","DUB"}:
            raise ValueError("Manual approval requires source URLs and explicit audio")
        if destinations!=set(range(1,episode_count+1)):
            raise ValueError("Manual approval requires exact destination coverage")
    return {"source_url":urls[0] if urls else None,"source_urls":urls,"audio":audio,
        **bools,"episode_map":normalized,"approval":None,"note":note.strip()}


def manual_approval_current(item,target,override):
    approval=(override or {}).get("approval") or {}
    if not override.get("manual_approved") or not approval:return False
    expected=target.get("episode_count")
    destinations={row.get("sonarr_episode") for row in override.get("episode_map",[])}
    exact=isinstance(expected,int) and expected>0 and destinations==set(range(1,expected+1))
    fingerprint={
        "target_id":target.get("target_id"),"episode_count":expected,"item_id":item.get("id"),
        "item_digest":item.get("digest"),"item_revision":item.get("revision"),
        "source_urls":override.get("source_urls") or [],"audio":override.get("audio"),
        "episode_map":override.get("episode_map") or [],
    }
    return exact and approval.get("target") == fingerprint and bool(approval.get("sources"))
def season_record(item,target,override=None,validation_state=None):
    original=item["original"];season=target["season_number"];override=override or {}
    chosen=selected_candidates(original,override.get("audio"))
    episodes=[];sources=[]
    for segment in original["proposed_plan"].get("segments",[]):
        links=[link for link in segment["coverage"].get("links",[]) if link.get("sonarr_season")==season]
        if not links:continue
        candidate=chosen.get(segment["release"]["release_id"])
        source={"title":candidate.canonical_title if candidate else segment["release"]["title"],
            "url":candidate.url if candidate else None,"audio":candidate.audio.value if candidate else None,
            "reliability":segment["crosswalk"].get("reliability"),
            "crosswalk_basis":segment["crosswalk"].get("basis"),
            "premiere_date":candidate.premiere_date.isoformat() if candidate and candidate.premiere_date else None,
            "release_year":candidate.release_year if candidate else None,
            "episode_count":candidate.episode_count if candidate else None}
        sources.append(source)
        for link in links:
            episodes.append({"episode":link.get("sonarr_episode"),"source_episode":link.get("source_episode"),
                "source_title":source["title"],"source_url":source["url"]})
    episodes.sort(key=lambda row:(row["episode"] or 0,row["source_episode"] or 0))
    manual_urls=override.get("source_urls") or ([override["source_url"]] if override.get("source_url") else [])
    effective_sources=sources
    effective_episodes=episodes
    mapping_mode="automatic"
    if manual_urls:
        effective_sources=[{"title":f"Manual source {index+1}","url":url,
            "audio":override.get("audio") or original.get("audio_preference","SUB"),"reliability":"manual"}
            for index,url in enumerate(manual_urls)]
        if override.get("episode_map"):
            effective_episodes=[{"episode":row["sonarr_episode"],"source_episode":row["source_episode"],
                "source_title":effective_sources[row["source_index"]]["title"],
                "source_url":manual_urls[row["source_index"]]} for row in override["episode_map"]]
            mapping_mode="forced"
        elif len(manual_urls)==1:
            effective_episodes=[{**row,"source_title":"Manual source 1","source_url":manual_urls[0]} for row in episodes]
            mapping_mode="single_source_inherit"
        else:
            effective_episodes=[];mapping_mode="manual_sources_unmapped"
    detected_audio=next((source.get("audio") for source in effective_sources if source.get("audio")),None)
    effective_audio=(override.get("audio") or detected_audio) if manual_urls else detected_audio
    automatic={"sources":sources,"episodes":episodes,"audio":next((s.get("audio") for s in sources if s.get("audio")),None)}
    approval_current=manual_approval_current(item,target,override)
    effective={"sources":effective_sources,"episodes":effective_episodes,"audio":effective_audio,
        "audio_requested":override.get("audio"),"mapping_mode":mapping_mode,
        "manual_approved":approval_current,"approval_current":approval_current,
        "excluded":bool(override.get("excluded"))}
    automation=automation_status(item,target,override)
    expected=target.get("episode_count") if isinstance(target.get("episode_count"),int) else 0
    mapped=len({row.get("episode") for row in effective_episodes if isinstance(row.get("episode"),int)})
    policy_unavailable=(automation=="unavailable" and "required_audio_unavailable" in set(original.get("reason_codes") or []))
    attention=(automation in {"review","needs_review","unavailable","rejected"}
        and not policy_unavailable and not bool(override.get("ignore_errors")))
    provider_validation_state=(validation_state or {}).get("state")
    validation_blocked=provider_validation_state in {"pending","stale_retry","invalidated"}
    if validation_blocked and automation=="auto":attention=True
    bases=sorted({segment["crosswalk"].get("basis") for segment in original["proposed_plan"].get("segments",[])
        if any(link.get("sonarr_season")==season for link in segment["coverage"].get("links",[]))
        and segment["crosswalk"].get("basis")})
    return {"season":season,"target_id":target.get("target_id"),"episode_count":target.get("episode_count"),"mapped_episode_count":mapped,
        "coverage_percent":round((mapped/expected)*100,1) if expected else 0.0,"mapping_id":item["id"],
        "mapping_state":item["mapping_state"],"review_state":item["review_state"],"updated_at":item.get("updated_at"),
        "automation":automation,"auto_ready":automation=="auto" and not validation_blocked,
        "can_execute":(automation=="auto" and not validation_blocked) or
            (automation=="manual" and approval_current),
        "provider_validation_state":provider_validation_state,
        "attention":attention,"reason_codes":list(original.get("reason_codes") or []),"mapping_bases":bases,
        "audio_preference":override.get("audio") or original.get("audio_preference","SUB"),
        "sources":sources,"episodes":episodes,"automatic":automatic,"effective":effective,"override":override}


def series_list(items,overrides,validation_states=None):
    validation_states=validation_states or {}
    grouped={}
    for item in items:
        if item["mapping_state"]=="superseded":continue
        for target in item["original"]["targets"]:
            sid=series_id_from_target(target["target_id"])
            row=grouped.setdefault(sid,{"series_id":sid,"title":target["canonical_title"],"seasons":[],
                "episode_count":0,"mapped_episode_count":0,"attention_count":0,"counts":defaultdict(int),"updated_at":None})
            override=overrides.get(target["target_id"],{})
            season=season_record(item,target,override,validation_states.get(item["id"]))
            row["seasons"].append(season);row["episode_count"]+=target.get("episode_count") or 0
            row["mapped_episode_count"]+=season["mapped_episode_count"];row["attention_count"]+=1 if season["attention"] else 0
            row["counts"][season["automation"]]+=1
            if season.get("updated_at") and (row["updated_at"] is None or season["updated_at"]>row["updated_at"]):
                row["updated_at"]=season["updated_at"]
    result=[]
    order={"review":0,"needs_review":0,"manual":1,"excluded":2,"waiting":3,"airing":4,"auto":5,"unavailable":6,"rejected":7}
    for row in grouped.values():
        row["seasons"].sort(key=lambda x:x["season"])
        statuses=[s["automation"] for s in row["seasons"]]
        row["status"]=min(statuses,key=lambda value:order.get(value,9)) if statuses else "unknown"
        row["season_count"]=len(row["seasons"]);row["counts"]=dict(row["counts"])
        row["audio_modes"]=sorted({s["effective"].get("audio") for s in row["seasons"] if s["effective"].get("audio")})
        row["coverage_percent"]=round((row["mapped_episode_count"]/row["episode_count"])*100,1) if row["episode_count"] else 0.0
        row["source_names"]=sorted({source.get("title") for season in row["seasons"]
            for source in season.get("effective",{}).get("sources",[]) if source.get("title")})
        row["auto_ready_seasons"]=sum(1 for s in row["seasons"] if s["auto_ready"])
        result.append(row)
    return sorted(result,key=lambda row:row["title"].casefold())


def series_detail(items,overrides,series_id,validation_states=None):
    rows=[row for row in series_list(items,overrides,validation_states) if row["series_id"]==int(series_id)]
    if not rows:raise KeyError("Unknown series")
    return rows[0]
