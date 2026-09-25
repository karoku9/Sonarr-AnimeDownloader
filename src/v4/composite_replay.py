"""Offline release resolver replay. Writes reports only inside V4, never mappings."""
import argparse
from collections import Counter,defaultdict
from dataclasses import asdict
import hashlib
import json
from datetime import date
from .production_sources import local_output
from .production_metrics import report as baseline_report
from .matching import canonical_url
from .models import Target,Candidate,Audio
from .release_resolver import resolve_series,titles as release_titles,key as release_key


def choose_variant(candidates, preference):
    preferred=[c for c in candidates if preference!=Audio.UNKNOWN and c.audio==preference]
    if preference==Audio.UNKNOWN:
        preferred=[c for c in candidates if c.audio==Audio.DUB] or [c for c in candidates if c.audio==Audio.SUB]
    return min(preferred or candidates,key=lambda c:c.url)


def choose_plan_variants(segment_candidates,preference=Audio.DUB,*,strict=False):
    """Choose one homogeneous audio mode for the entire season plan.

    Normal policy may fall back to the other homogeneous language. Strict
    series-master policy never crosses the S1 audio boundary.
    """
    groups=[list(group) for group in segment_candidates]
    if not groups:return Audio.UNKNOWN,[]
    if strict and preference in {Audio.SUB,Audio.DUB}:order=(preference,)
    elif preference==Audio.SUB:order=(Audio.SUB,Audio.DUB)
    elif preference==Audio.DUB:order=(Audio.DUB,Audio.SUB)
    else:order=(Audio.DUB,Audio.SUB)
    for audio in order:
        if all(any(c.audio==audio for c in group) for group in groups):
            return audio,[choose_variant(group,audio) for group in groups]
    return Audio.UNKNOWN,[]


def target_audio_preference(snapshot,target_id):
    policy=snapshot.get("language_policy") or {}
    if not policy:return Audio.DUB  # legacy frozen fixtures keep their historical behavior
    value=(policy.get("overrides") or {}).get(target_id,policy.get("default","SUB"))
    return Audio.DUB if value=="DUB" else Audio.SUB


def target_audio_strict(snapshot,target_id):
    policy=snapshot.get("language_policy") or {}
    return str(target_id) in {str(v) for v in policy.get("strict_targets",[])}


def preferred_alternative_plan(alternatives,variants,preference,*,strict=False):
    """Resolve an equivalent-plan tie only when audio policy leaves one plan."""
    wanted_order=(preference,) if strict else (preference,Audio.SUB if preference==Audio.DUB else Audio.DUB)
    for wanted in wanted_order:
        supported=[]
        for plan in alternatives:
            groups=[[variants[i] for i in segment["release"]["candidate_ids"] if i in variants]
                for segment in plan.get("segments",[])]
            audio,chosen=choose_plan_variants(groups,wanted,strict=strict)
            if groups and audio==wanted and chosen:supported.append(plan)
        if len(supported)==1:return supported[0]
        if len(supported)>1:return None
    return None


def classify_unmatched(case,target,candidates):
    """Classify non-matches without turning absence or future episodes into review work."""
    stats=case["raw"].get("sonarr_season",{}).get("statistics",{})
    total=stats.get("totalEpisodeCount") or 0;current=stats.get("episodeCount") or 0
    rows=case["raw"].get("sonarr_episodes",[])
    dated=[]
    for row in rows:
        value=row.get("airDate")
        if not value:continue
        try:dated.append(date.fromisoformat(str(value)[:10]))
        except ValueError:pass
    if dated:
        today=date.today();aired=any(d<=today for d in dated);future=any(d>today for d in dated)
        if future:return "airing" if aired else "waiting"
        undated=sum(1 for row in rows if not row.get("airDate"))
        if undated and total and current<total:return "airing"
    elif total:
        # Statistics are only a fallback when episode-level air dates are unavailable.
        if current==0:return "waiting"
        if current<total:return "airing"
    absence=case["raw"].get("source_absence") or {}
    # A verified complete-provider audit outranks a partial candidate: if the
    # source is known not to cover the target, this is Unavailable rather than Review.
    if absence.get("verified") is True and absence.get("state")=="unavailable":return "unavailable"
    metadata_sources=(case["raw"].get("external_metadata") or {}).get("sources",[])
    for source in metadata_sources:
        expected=source.get("expected_mal_entries") or []
        missing=set(source.get("missing_mal_ids_verified") or [])
        counts=[item.get("episode_count") for item in expected if isinstance(item,dict)]
        if (source.get("provider_catalog_complete") is True and missing and counts
                and all(isinstance(v,int) and v>0 for v in counts)
                and sum(counts)==target.episode_count):
            return "unavailable"
        if source.get("provider_catalog_complete") is not True:continue
        # A missing provider MAL entry may span multiple TVDB/Sonarr seasons.
        # Accept that absence only when another trusted structure proves this
        # target is the exact first season of a consecutive span totaling it.
        for item in expected:
            if not isinstance(item,dict) or item.get("mal_id") not in missing:continue
            count=item.get("episode_count")
            if not isinstance(count,int) or count<=target.episode_count:continue
            for structure in metadata_sources:
                seasons=sorted((s for s in structure.get("seasons",[]) if isinstance(s.get("season_number"),int)
                    and isinstance(s.get("episode_count"),int) and s.get("season_number")>=target.season_number),
                    key=lambda s:s["season_number"])
                if not seasons or seasons[0]["season_number"]!=target.season_number or seasons[0]["episode_count"]!=target.episode_count:continue
                total=0;number=target.season_number
                for season in seasons:
                    if season["season_number"]!=number:break
                    total+=season["episode_count"];number+=1
                    if total>=count:break
                if total==count:return "unavailable"
        # Complete provider catalog with only shorter hits for an otherwise
        # exact MAL/season mapping proves the full target is not available.
        for item in expected:
            if not isinstance(item,dict) or item.get("episode_count")!=target.episode_count:continue
            mid=item.get("mal_id")
            hit_counts=[]
            for hit in source.get("provider_mal_hits",[]):
                if hit.get("mal_id")!=mid:continue
                value=str(hit.get("episodes") or "")
                if value.isdigit():hit_counts.append(int(value))
            if hit_counts and max(hit_counts)<target.episode_count:return "unavailable"
        # If the provider declares every expected MAL part but a matching detail
        # page exposes a non-empty strict subset of 1..N, the complete target is
        # unavailable rather than ambiguous. Empty/failed detail fetches stay Review.
        valid_expected=[item for item in expected if isinstance(item,dict) and isinstance(item.get("episode_count"),int)
            and item.get("episode_count")>0]
        if valid_expected and sum(item["episode_count"] for item in valid_expected)==target.episode_count:
            details=case["raw"].get("details",{})
            for item in valid_expected:
                mid=item.get("mal_id");count=item["episode_count"]
                hit_names={release_key(hit.get("name") or "") for hit in source.get("provider_mal_hits",[])
                    if hit.get("mal_id")==mid and str(hit.get("episodes") or "").isdigit()
                    and int(hit.get("episodes"))==count}
                if not hit_names:continue
                for candidate in candidates:
                    if candidate.episode_count!=count or not (hit_names & release_titles(candidate)):continue
                    observed=details.get(candidate.url,{}).get("raw",{}).get("available_episode_numbers",[])
                    observed={int(v) for v in observed if isinstance(v,(int,float)) and not isinstance(v,bool) and int(v)==v and v>0}
                    expected_numbers=set(range(1,count+1))
                    if observed and observed<expected_numbers:return "unavailable"
    dates={str(r.get("airDate"))[:10] for r in rows if r.get("airDate")}
    target_titles=release_titles(target,target.season_number)
    for c in candidates:
        candidate_titles=release_titles(c)
        scoped_conflict=any(a.source.reliable and a.scope() is not None and a.scope()!=target.season_number
            and release_key(a.text) in candidate_titles
            for a in target.alternate_titles)
        scoped_match=any(a.source.reliable and a.scope()==target.season_number
            and release_key(a.text) in candidate_titles
            for a in target.alternate_titles)
        identity=bool(target_titles&candidate_titles) and (not scoped_conflict or scoped_match)
        boundary=not c.premiere_date or c.premiere_date.isoformat() in dates
        coverage=not c.episode_count or not target.episode_count or c.episode_count<=target.episode_count
        if identity and boundary and coverage:return "needs_review"
    return "needs_review"


def replay(snapshot):
    baseline=baseline_report(snapshot)
    old={r["target_id"]:r for r in baseline["targets"]}
    groups=defaultdict(list)
    for case in snapshot["cases"]:groups[case["raw"]["sonarr_series"]["id"]].append(case)
    records=[];inferred=[];releases=[]
    policy_masters=(snapshot.get("language_policy") or {}).get("series_audio_master") or {}
    for series_id,cases in groups.items():
        resolution=resolve_series(cases);releases.extend(asdict(r) for r in resolution["releases"])
        inferred.extend(p.to_dict() for p in resolution["inferred_plans"])
        raw_master=policy_masters.get(str(series_id));series_master=Audio(raw_master) if raw_master in {"SUB","DUB"} else None
        ordered_cases=sorted(cases,key=lambda c:(0 if (c.get("derived",{}).get("target",{}).get("season_number")==1) else 1,
            c.get("derived",{}).get("target",{}).get("season_number") or 0))
        for case in ordered_cases:
            target=Target.from_dict(case["derived"]["target"]);tid=target.target_id;p=resolution["plans"][tid]
            oldrow=old[tid]
            variants={c["id"]:Candidate.from_dict(c) for cs in cases for c in cs["derived"]["candidates"]}
            has_shared_inference=any(tid in proposal.target_ids for proposal in resolution["inferred_plans"])
            preference=target_audio_preference(snapshot,tid)
            strict_audio=target_audio_strict(snapshot,tid)
            if target.season_number>1 and series_master in {Audio.SUB,Audio.DUB}:
                preference=series_master;strict_audio=True
            plan=p.to_dict()
            if p.outcome!="matched" and set(p.reason_codes)=={"multiple_equivalent_candidates"}:
                preferred=preferred_alternative_plan(resolution["alternative_plans"][tid],variants,preference,strict=strict_audio)
                if preferred:
                    plan={**preferred,"outcome":"matched","reason_codes":["language_preference_disambiguated"]}
            outcome="matched" if plan.get("outcome")=="matched" else "needs_review" if has_shared_inference else classify_unmatched(case,target,tuple(variants.values()))
            selected=[]
            segment_candidates=[[variants[i] for i in segment["release"]["candidate_ids"] if i in variants] for segment in plan.get("segments",[])]
            plan_audio,chosen_variants=choose_plan_variants(segment_candidates,preference,strict=strict_audio)
            if plan.get("segments") and not chosen_variants and outcome=="matched":
                if strict_audio:
                    outcome=classify_unmatched(case,target,())
                    if outcome=="needs_review":outcome="unavailable"
                    plan={**plan,"segments":[],"outcome":outcome,
                        "reason_codes":list(dict.fromkeys([*plan.get("reason_codes",[]),"required_audio_unavailable"]))}
                else:
                    outcome="needs_review"
                    plan={**plan,"outcome":"needs_review","reason_codes":list(dict.fromkeys([*plan.get("reason_codes",[]),"mixed_audio_release_plan"]))}
            else:
                for c in chosen_variants:
                    selected.append({"id":c.id,"title":c.canonical_title,"url":c.url,"audio":c.audio.value})
            if target.season_number==1 and outcome=="matched" and plan_audio in {Audio.SUB,Audio.DUB}:
                series_master=plan_audio
            expected={canonical_url(u) for u in oldrow["v3_existing_urls"]}
            chosen={canonical_url(c["url"]) for c in selected}
            variant_sets=[{canonical_url(u) for u in s["release"]["urls"]} for s in plan.get("segments",[])]
            narrative_agreement=bool(expected and selected and len(expected)==len(variant_sets)
                and all(len(expected & options)==1 for options in variant_sets)) if variant_sets else bool(selected and chosen==expected)
            terminal_reasons={"waiting":["awaiting_first_episode"],"airing":["season_in_progress"],
                "unavailable":["required_audio_unavailable"] if strict_audio and not chosen_variants else ["animeworld_source_unavailable"]}
            reasons=list(dict.fromkeys(plan.get("reason_codes",[]))) if outcome=="needs_review" else terminal_reasons.get(outcome,[])
            causes=oldrow["review_causes"] if outcome=="needs_review" else []
            records.append({"target_id":tid,"title":target.canonical_title,"season":target.season_number,
                "before":oldrow["decision"]["outcome"],"after":outcome,"plan":plan,
                "audio_preference":preference.value,
                "legacy_decision_retained":False,"selected":selected,"v3_urls":oldrow["v3_existing_urls"],
                "agreement":bool(selected and chosen==expected),"release_variant_agreement":narrative_agreement,
                "target_snapshot":case["derived"]["target"],"candidate_snapshots":case["derived"]["candidates"],"reason_codes":reasons,
                "review_causes":causes,"excluded":resolution["excluded"][tid],
                "baseline_decision":oldrow["decision"],"alternative_plans":resolution["alternative_plans"][tid],
                "llm_eligible":outcome=="needs_review" and "multiple_equivalent_candidates" in reasons,
                "inferred_shared_plan_ids":[i for i,p2 in enumerate(inferred) if tid in p2["target_ids"]]})
    total=len(records);auto=sum(r["after"]=="matched" for r in records)
    solved=lambda cause:sum(cause in old[r["target_id"]]["review_causes"] and r["after"]=="matched" for r in records)
    causes=Counter(c for r in records for c in r["review_causes"])
    paired=sum(bool(r["selected"] and r["v3_urls"]) for r in records);agreed=sum(r["agreement"] for r in records)
    return {"schema_version":2,"ground_truth":None,"policy_changed":False,"baseline_counts":baseline["counts"],
        "baseline_causes":baseline["review_causes_multilabel"],"counts":{"targets":total,"series":len(groups),
        "auto_matches":auto,"reviews":sum(r["after"]=="needs_review" for r in records),
        "waiting":sum(r["after"]=="waiting" for r in records),"airing":sum(r["after"]=="airing" for r in records),
        "unavailable":sum(r["after"]=="unavailable" for r in records),"missing_crosswalk_resolved":solved("crosswalk_missing"),
        "missing_crosswalk_remaining":causes["crosswalk_missing"],"equivalent_candidate_resolved":solved("multiple_equivalent_candidates"),
        "equivalent_candidate_remaining":causes["multiple_equivalent_candidates"],
        "insufficient_metadata_baseline_cohort_remaining":sum(r["after"]=="needs_review" and "insufficient_metadata" in old[r["target_id"]]["decision"]["reason_codes"] for r in records),
        "insufficient_metadata_remaining":sum("insufficient_metadata" in r["reason_codes"] for r in records),
        "equivalent_candidate_reviews":sum("multiple_equivalent_candidates" in r["reason_codes"] for r in records),
        "composite_auto_plans":sum(r["after"]=="matched" and len(r["plan"]["segments"])>1 for r in records),
        "shared_release_inferred_plans":len(inferred),"shared_release_verified_plans":0,
        "paired_auto_decisions":paired,"v3_v4_agreed":agreed,
        "v3_v4_release_variant_agreed":sum(r["release_variant_agreement"] for r in records),
        "transport_variant_only_disagreements":sum(r["release_variant_agreement"] and not r["agreement"] for r in records),"llm_eligible":sum(r["llm_eligible"] for r in records)},
        "rates":{"auto_match_rate":auto/total,"review_rate":(total-auto)/total,
            "agreement_paired_auto":agreed/paired if paired else None},
        "review_reason_codes":dict(Counter(c for r in records for c in r["reason_codes"])),
        "review_causes_multilabel":dict(causes),"inferred_shared_plans":inferred,"release_identities":releases,"targets":records,
        "limitations":["Offline proposals only; metadata-supported links are not human ground truth.",
            "Absolute-number concatenation without independent source boundaries remains inferred and Needs Review.",
            "Residual cause counts retain baseline causes for direct before/after cohorts; reason_codes describe resolver output.",
            "V3 mappings are audit observations only and never retain or discover an automatic decision."]}


def markdown(result):
    lines=["# Phase 5 release resolver replay","",json.dumps(result["counts"],indent=2),"",json.dumps(result["rates"],indent=2),""]
    lines.extend("- "+s for s in result["limitations"])
    for r in result["targets"]:
        lines.extend(["",f"## {r['title']} / S{r['season']}",f"Before: {r['before']}; after: {r['after']}",
            f"V3: {r['v3_urls']}",f"V4: {r['selected']}",f"Review: {r['reason_codes']}",
            "Plan: "+json.dumps(r['plan'],ensure_ascii=False),"Excluded: "+json.dumps(r['excluded'],ensure_ascii=False)])
    lines.extend(["", "## Inferred shared-release plans",json.dumps(result["inferred_shared_plans"],ensure_ascii=False,indent=2)])
    return "\n".join(lines)+"\n"


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--snapshot",required=True);parser.add_argument("--output-dir",required=True)
    args=parser.parse_args();source=local_output(args.snapshot);output=local_output(args.output_dir)
    data=source.read_bytes();snapshot=json.loads(data)
    if snapshot.get("schema_version")!=1 or snapshot.get("kind")!='production_metadata_replay':raise ValueError("Expected production snapshot v1")
    result=replay(snapshot);result["input_sha256"]=hashlib.sha256(data).hexdigest()
    output.mkdir(parents=True,exist_ok=True)
    (output/"composite-report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    (output/"composite-report.md").write_text(markdown(result),encoding="utf-8")
    print(json.dumps(result["counts"],indent=2));print(json.dumps(result["rates"],indent=2))

if __name__=="__main__":main()
