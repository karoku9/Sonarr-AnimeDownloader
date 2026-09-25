"""Observable production replay metrics, explicitly without ground-truth accuracy claims."""
from collections import Counter
import json
from .models import Candidate,Target
from .matching import match,canonical_url
from .normalization import normalize_title

def report(snapshot):
    records=[];cause_counts=Counter();codes=Counter();categories=Counter()
    complete=0;native_complete=0;rejected=0;evaluations=0
    for case in snapshot["cases"]:
        target=Target.from_dict(case["derived"]["target"])
        candidates=tuple(Candidate.from_dict(c) for c in case["derived"]["candidates"])
        decision=match(target,candidates)
        by_id={c.id:c for c in candidates}
        chosen=by_id.get(decision.selected_candidate_id)
        observed=case["expected_v3_decision"]["urls"]
        expected={canonical_url(u) for u in observed}
        agreement=bool(chosen and expected=={canonical_url(chosen.url)})
        relevant=[e for e in decision.evaluations if e.title_method!="none" and not e.hard_rejected]
        eligible=[e for e in relevant if not {r.value for r in e.reason_codes}&{"release_date_conflict","episode_count_conflict","duplicate_url_across_seasons"}]
        pool=eligible or relevant
        best=max(pool,key=lambda e:e.rank) if pool else None
        target_complete=bool(target.season_number is not None and (target.air_date or target.release_year) and target.episode_count and target.trusted("episode_count"))
        complete+=target_complete
        full_candidate=any(e.season_verified and by_id[e.candidate_id].premiere_date and by_id[e.candidate_id].episode_count is not None and by_id[e.candidate_id].source.name=="animeworld_detail" for e in relevant)
        native_complete+=bool(full_candidate)
        rejected+=sum(e.hard_rejected for e in decision.evaluations);evaluations+=len(decision.evaluations)
        causes=set()
        if decision.outcome=="needs_review":
            values={r.value for r in decision.reason_codes};codes.update(values)
            if not candidates or not any(c.source.name=="animeworld_detail" for c in candidates):causes.add("metadata_missing")
            if not best or not best.season_verified:causes.add("crosswalk_missing")
            if "multiple_equivalent_candidates" in values:causes.add("multiple_equivalent_candidates")
            if not (target.air_date or target.release_year) or not any(c.premiere_date or c.release_year for c in candidates):causes.add("dates_missing")
            if target.episode_count is None or not best or by_id[best.candidate_id].episode_count is None or values&{"duplicate_url_across_seasons","episode_count_conflict"} or (best and by_id[best.candidate_id].episode_count!=target.episode_count):causes.add("episode_coverage_missing")
            if not relevant or best.title_method=="fuzzy":causes.add("fuzzy_insufficient")
            if values&{"season_conflict","release_date_conflict","episode_count_conflict","duplicate_url_across_seasons","identity_conflict"}:causes.add("structural_or_metadata_conflict")
            if not causes:causes.add("other")
            cause_counts.update(causes)
        mapped_evaluations=[e for e in decision.evaluations if canonical_url(by_id[e.candidate_id].url) in expected]
        target_keys=[normalize_title(target.canonical_title)]+[normalize_title(a.text) for a in target.alternate_titles]
        def related_release(c):
            release_keys=[normalize_title(c.canonical_title)]+[normalize_title(a.text) for a in c.alternate_titles]
            return any(min(len(a),len(b))>=5 and (a in b or b in a) for a in target_keys for b in release_keys)
        probable_v3=bool(mapped_evaluations and any(e.title_method=="none" and not related_release(by_id[e.candidate_id]) and "release_date_conflict" in {r.value for r in e.reason_codes} and by_id[e.candidate_id].source.name=="animeworld_detail" for e in mapped_evaluations) and any(e.title_method=="exact" and not e.hard_rejected and "release_date_conflict" not in {r.value for r in e.reason_codes} for e in decision.evaluations))
        probable_v4=bool(chosen and target.episodes_complete and chosen.episodes_complete and target.episode_count is not None and chosen.episode_count is not None and target.episode_count!=chosen.episode_count)
        if probable_v4:category="probable_v4_error"
        elif probable_v3:category="probable_v3_error"
        elif chosen and observed and not agreement:category="confident_disagreement_with_v3"
        elif chosen:category="metadata_supported_match_unverified"  # never call V3 an oracle
        elif causes&{"multiple_equivalent_candidates","structural_or_metadata_conflict"}:category="needs_review_justified"
        else:category="insufficient_source_metadata"
        categories[category]+=1
        records.append({"target_id":target.target_id,"title":target.canonical_title,"season":target.season_number,"target_metadata_complete":target_complete,"candidate_metadata_complete":bool(full_candidate),"classification":category,"v3_existing_urls":observed,"v4_selected":{"id":chosen.id,"title":chosen.canonical_title,"url":chosen.url} if chosen else None,"agreement":agreement,"review_causes":sorted(causes),"decision":decision.to_dict(),"candidates":[c.to_dict() for c in candidates],"manual_verifiable":bool(candidates and any(c.source.name=="animeworld_detail" for c in candidates)),"disagreement_evidence":[e.__dict__ for evaluation in mapped_evaluations for e in evaluation.evidence]})
    for category in ("confident_correct","confident_disagreement_with_v3","needs_review_justified","insufficient_source_metadata","probable_v3_error","probable_v4_error"):
        categories.setdefault(category,0)
    total=len(records);matched=sum(r["decision"]["outcome"]=="matched" for r in records);with_v3=sum(bool(r["v3_existing_urls"]) for r in records);paired=sum(bool(r["v3_existing_urls"] and r["v4_selected"]) for r in records);agreed=sum(r["agreement"] for r in records)
    ratio=lambda n,d:round(n/d,6) if d else None
    return {"schema_version":1,"ground_truth":None,"policy":{"fuzzy_threshold":.88,"date_tolerance_days":1,"changed":False},"counts":{"series":len({r["title"] for r in records}),"targets":total,"unique_candidates":len({c["url"] for r in records for c in r["candidates"]}),"targets_complete":complete,"targets_with_complete_candidate_identity_date_count":native_complete,"auto_matches":matched,"reviews":total-matched,"structural_rejects":rejected,"candidate_evaluations":evaluations,"targets_with_v3_mapping":with_v3,"paired_auto_decisions":paired,"agreed":agreed,"manual_verifiable":sum(r["manual_verifiable"] for r in records)},"rates":{"auto_match_rate":ratio(matched,total),"review_rate":ratio(total-matched,total),"structural_reject_rate":ratio(rejected,evaluations),"v3_v4_agreement_all_mapped_targets":ratio(agreed,with_v3),"v3_v4_agreement_paired_auto_decisions":ratio(agreed,paired),"insufficient_source_metadata_rate":ratio(codes["insufficient_metadata"],total),"insufficient_metadata_primary_category_rate":ratio(categories["insufficient_source_metadata"],total)},"categories":dict(categories),"review_reason_codes":dict(codes),"review_causes_multilabel":dict(cause_counts),"limitations":["No independent human ground truth; confident correct is not assigned. Probable V4 error flags an auto-choice with incompatible complete provider/target counts and unresolved coverage, not a proven false positive.","Agreement requires exact URL-set equality; a chosen URL from a multi-URL mapping is not full agreement.","Review causes overlap. Missing dates alone do not block a strong title+season match.","Provider release year/episode count do not certify Sonarr season identity.","Candidate pools are bounded retrieval, not an exhaustive production search replay."],"targets":records}

def markdown(result):
    lines=["# Production metadata replay","", "Policy unchanged; no accuracy/precision/recall claims.","",json.dumps(result["counts"],indent=2),"",json.dumps(result["rates"],indent=2),"", "## Review reason codes",json.dumps(result["review_reason_codes"],indent=2),"", "## Review causes (overlapping)",json.dumps(result["review_causes_multilabel"],indent=2),""]
    lines.extend("- "+s for s in result["limitations"])
    for row in result["targets"]:
        lines.extend(["",f"## {row['title']} / S{row['season']}","",f"Classification: {row['classification']}",f"V3 existing: {row['v3_existing_urls']}",f"V4: {row['decision']['outcome']} / {row['v4_selected']}",f"Reasons: {row['decision']['reason_codes']}",f"Causes: {row['review_causes']}"])
        for evaluation in row["decision"]["evaluations"]:
            candidate=next(c for c in row["candidates"] if c["id"]==evaluation["candidate_id"])
            lines.append(f"- {candidate['canonical_title']} / {candidate['url']}: hard reject={evaluation['hard_rejected']}; {evaluation['reason_codes']}")
            for evidence in evaluation["evidence"]:lines.append("  - "+json.dumps(evidence,ensure_ascii=False))
    return "\n".join(lines)+"\n"
