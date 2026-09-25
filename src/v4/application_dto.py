"""Versioned JSON presentation contract; frontend consumes these DTOs only."""
from collections import defaultdict
from .application_store import digest
from .composite_replay import choose_plan_variants
from .models import Candidate,Audio

CONTRACT_VERSION="4.0"
MATCHER_VERSION="provenance-2"
RESOLVER_VERSION="release-resolver-phase5"

MESSAGES={
 "crosswalk_missing":"Season/release correspondence cannot yet be verified.",
 "insufficient_metadata":"Available metadata does not establish a complete mapping plan.",
 "multiple_equivalent_candidates":"More than one supported plan remains equivalent.",
 "season_conflict":"Release season identity conflicts with the requested season.",
 "release_date_conflict":"Release date/year conflicts with the requested season.",
 "episode_count_conflict":"Episode metadata conflicts with the requested coverage.",
 "duplicate_url_across_seasons":"The same source episodes would be assigned to different seasons.",
 "language_mismatch":"The release audio differs from the requested language preference.",
 "low_confidence_title_match":"Title identity remains uncertain.",
 "mapping_plan_unavailable":"Title matching succeeded, but complete episode coverage is not verified.",
 "episode_boundary_unverified":"Episode boundary between the season targets cannot be verified.",
 "mixed_audio_release_plan":"The proposed season would mix dubbed and subtitled source releases.",
 "gemini_verification_failed":"Gemini did not fully confirm this automatic mapping.",
 "candidate_overflow":"The indexed candidate set exceeded its safe review budget.",
 "provider_request_budget":"Provider detail requests exceeded the scan budget; review is pending.",
 "catalog_generation_unavailable":"No complete published provider catalog generation is available."
}


def plan_id(plan):
    return digest({k:v for k,v in plan.items() if k not in {"outcome","reason_codes"}})[:24]


def plan_dto(plan,original,variant_choices=None):
    variant_choices=variant_choices or {}
    candidates={c["id"]:c for c in original["candidates"]}
    target_by_season={t["season_number"]:t for t in original["targets"]}
    segments=[]
    for segment in plan.get("segments",[]):
        release=segment["release"];coverage=segment["coverage"];destinations=defaultdict(list)
        for link in coverage["links"]:destinations[link["sonarr_season"]].append(link["sonarr_episode"])
        segments.append({"release_id":release["release_id"],"title":release["title"],
            "source_episode_range":coverage.get("source_range"),"source_episode_count":coverage.get("episode_count"),
            "coverage_status":coverage["status"],"reliability":segment["crosswalk"]["reliability"],
            "destinations":[{"target_id":target_by_season[s]["target_id"],"season":s,"episode_range":[min(ns),max(ns)],"episode_count":len(ns)} for s,ns in sorted(destinations.items())],
            "variants":[{"candidate_id":id,"title":candidates[id]["canonical_title"],"audio":candidates[id]["audio"],
                "audio_language":candidates[id].get("audio_language"),"subtitle_language":candidates[id].get("subtitle_language"),"selected":variant_choices.get(release["release_id"])==id} for id in release["candidate_ids"] if id in candidates]})
    count=sum(len(s["coverage"]["links"]) for s in plan.get("segments",[]))
    expected=sum(t.get("episode_count") or 0 for t in original["targets"])
    return {"id":plan_id(plan),"coverage_status":"complete" if count and count==expected else "partial" if count else "unknown","targets":[{"target_id":t["target_id"],"season":t["season_number"],"episode_count":t.get("episode_count")} for t in original["targets"]],
        "segments":segments,"episode_count":sum(len(s["coverage"]["links"]) for s in plan.get("segments",[])),
        "requires_uncertainty_acknowledgement":any(s["crosswalk"]["reliability"]=="inferred" for s in plan.get("segments",[]))}


def explanations(original):
    result=[{"code":r,"message":MESSAGES.get(r,"Additional source evidence is required.")} for r in original["reason_codes"]]
    plan=original["proposed_plan"]
    if any(s["crosswalk"]["basis"]=="explicit_scene_coordinates_with_release_anchor" for s in plan.get("segments",[])):
        result.append({"code":"scene_identity_supported","message":"Season identity confirmed by Sonarr scene numbering and release metadata."})
    if len(plan.get("segments",[]))>1:
        count=sum(len(s["coverage"]["links"]) for s in plan["segments"])
        result.append({"code":"composite_coverage","message":f"{len(plan['segments'])} releases together cover all {count} episodes."})
    if any(s["crosswalk"]["reliability"]=="inferred" for s in plan.get("segments",[])):
        result.append({"code":"episode_boundary_unverified","message":MESSAGES["episode_boundary_unverified"]})
    if not result and plan.get("segments"):
        result.append({"code":"metadata_supported","message":"Title, release date and observed episode numbering support this proposal."})
    unique=[];seen=set()
    for item in result:
        if item["code"] not in seen:unique.append(item);seen.add(item["code"])
    return unique


def evidence_summary(original,plan=None):
    plan=plan or original["proposed_plan"]
    if plan.get("segments"):
        candidates={c["id"]:c for c in original["candidates"]}
        results=[]
        for segment in plan["segments"]:
            crosswalk=segment["crosswalk"]
            if crosswalk["reliability"]=="inferred":
                messages=["Observed numbering supports the proposed combined coverage.","The internal season boundary cannot independently be verified."]
            elif crosswalk["basis"]=="explicit_scene_coordinates_with_release_anchor":
                messages=["Season identity supported by Sonarr scene numbering and release metadata.","Observed source episodes agree with the mapped scene segment."]
            else:
                messages=["Title, premiere date and observed episode numbering support this target."]
            results.extend({"candidate_id":id,"title":candidates[id]["canonical_title"],"messages":messages} for id in segment["release"]["candidate_ids"])
        return results
    ids={id for s in original["proposed_plan"].get("segments",[]) for id in s["release"]["candidate_ids"]}
    evaluations=[e for d in original["foundation_decisions"] for e in d["evaluations"] if e["candidate_id"] in ids or (not ids and e["title_method"]!="none")]
    candidates={c["id"]:c for c in original["candidates"]}
    results=[]
    for e in evaluations:
        messages=[]
        for evidence in e["evidence"]:
            if evidence["signal"] in {"release_date","release_year"}:
                if evidence["outcome"]=="conflict":messages.append("Release date/year conflicts with the requested season.")
                elif evidence["outcome"]=="match":messages.append("Release date agrees with the requested target.")
        if messages:results.append({"candidate_id":e["candidate_id"],"title":candidates[e["candidate_id"]]["canonical_title"],"messages":list(dict.fromkeys(messages))})
    return results


def item_dto(item,detail=False):
    original=item["original"];human=item["human_decision"]
    plans={plan_id(p):p for p in [original["proposed_plan"]]+original["alternate_plans"]}
    selected=plans.get(human.get("plan_id")) if human and human.get("plan_id") else original["proposed_plan"]
    can_change=item["mapping_state"] in {"proposed","needs_review"} and item["review_state"]!="dismissed"
    verification=original.get("llm_verification") or {}
    result={"id":item["id"],"title":original["targets"][0]["canonical_title"],"mapping_state":item["mapping_state"],
        "review_state":item["review_state"],"revision":item["revision"],"created_at":item["created_at"],"updated_at":item["updated_at"],
        "review_summary":verification.get("summary"),"gemini_verification":verification or None,
        "audio_preference":original.get("audio_preference","SUB"),
        "targets":[{"target_id":t["target_id"],"season":t["season_number"],"episode_count":t.get("episode_count")} for t in original["targets"]],
        "reasons":explanations(original),"actions":{"approve":can_change and bool(original["proposed_plan"].get("segments")),
            "choose":can_change and any(p.get("segments") for p in plans.values()),"reject":can_change,"dismiss":can_change,
            "reopen":item["review_state"] in {"resolved","rejected","dismissed"}},"approved_by_human":item["mapping_state"]=="approved"}
    if detail:result.update(plan=plan_dto(selected,original,human.get("variant_choices",{}) if human else {}),evidence_summary=evidence_summary(original,selected),alternate_plans=[plan_dto(p,original) for p in original["alternate_plans"]],human_decision=human)
    return result


def item_list_dto(item):
    """Build the complete lightweight mapping-list contract at write/migration time."""
    result=item_dto(item);original=item["original"];human=item["human_decision"] or {}
    plans={plan_id(plan):plan for plan in [original["proposed_plan"],*original["alternate_plans"]]}
    selected=plans.get(human.get("plan_id"),original["proposed_plan"])
    by_id={candidate["id"]:Candidate.from_dict(candidate) for candidate in original["candidates"]}
    groups=[[by_id[value] for value in segment["release"]["candidate_ids"] if value in by_id]
        for segment in selected.get("segments",[])]
    preference=Audio(original.get("audio_preference","SUB")) \
        if original.get("audio_preference") in {"SUB","DUB"} else Audio.SUB
    _,defaults=choose_plan_variants(groups,preference)
    defaults_by_release={segment["release"]["release_id"]:candidate.id
        for segment,candidate in zip(selected.get("segments",[]),defaults)}
    choices={**defaults_by_release,**(human.get("variant_choices") or {})}
    selected_variants=[]
    for segment in selected.get("segments",[]):
        candidate=by_id.get(choices.get(segment["release"]["release_id"]))
        if candidate:
            selected_variants.append({"audio":candidate.audio.value,
                "audio_language":candidate.audio_language})
    mapped=sum(len(segment.get("coverage",{}).get("links",[])) for segment in selected.get("segments",[]))
    expected=sum(target.get("episode_count") or 0 for target in original["targets"])
    result.update(coverage_status="complete" if mapped and mapped==expected else "partial" if mapped else "unknown",
        episode_count=mapped,selected_variants=selected_variants)
    return result
