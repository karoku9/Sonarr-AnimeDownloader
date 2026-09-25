"""V4 application boundary: adapters -> enrichment -> foundation -> local lifecycle."""
from .application_adapters import SnapshotEnricher
from .application_store import Conflict
from .application_dto import item_dto,plan_id,MATCHER_VERSION,RESOLVER_VERSION
from .composite_replay import replay,choose_plan_variants
from .models import Candidate,Audio
from .series_view import validate_override,config_active,manual_approval_current
from .provider_validation import detail_fingerprint

class FoundationEngine:
    def evaluate(self,snapshot):
        # Existing pure replay explicitly runs deterministic matcher then release resolver.
        return replay(snapshot)

class ApplicationService:
    def __init__(self,store,datasets,*,enricher=None,engine=None,judge=None,researcher=None,verifier=None,
        manual_source_validator=None):
        self.store=store;self.datasets=dict(datasets)
        self.enricher=enricher or SnapshotEnricher();self.engine=engine or FoundationEngine()
        self.judge=judge;self.researcher=researcher;self.verifier=verifier
        self.manual_source_validator=manual_source_validator

    @staticmethod
    def _plan_covers_targets(original,plan):
        if not isinstance(plan,dict) or not plan.get("segments"):return False
        links=[link for segment in plan.get("segments",[])
            for link in (segment.get("coverage") or {}).get("links",[]) if isinstance(link,dict)]
        for target in original.get("targets",[]):
            season=target.get("season_number");count=target.get("episode_count")
            if not isinstance(season,int) or season<=0 or not isinstance(count,int) or count<=0:return False
            covered={link.get("sonarr_episode") for link in links
                if link.get("sonarr_season")==season and isinstance(link.get("sonarr_episode"),int)}
            if not set(range(1,count+1))<=covered:return False
        return True

    @classmethod
    def _preserve_group(cls,item):
        if item.get("mapping_state") not in {"proposed","approved"}:return None
        original=item.get("original") or {};plans=[original.get("proposed_plan"),*(original.get("alternate_plans") or [])]
        chosen=original.get("proposed_plan")
        decision=item.get("human_decision") or {}
        if item.get("mapping_state")=="approved" and decision.get("plan_id"):
            chosen=next((p for p in plans if isinstance(p,dict) and plan_id(p)==decision["plan_id"]),None)
        if not cls._plan_covers_targets(original,chosen):return None
        target_counts={str(t["target_id"]):t.get("episode_count") for t in original.get("targets",[])
            if t.get("target_id") and isinstance(t.get("season_number"),int) and t.get("season_number")>0}
        if len(target_counts)!=len(original.get("targets",[])):return None
        by_id={c["id"]:Candidate.from_dict(c) for c in original.get("candidates",[])}
        audio=None;selected_ids=set()
        if item.get("mapping_state")=="approved" and decision.get("variant_choices"):
            selected_ids=set(decision["variant_choices"].values())
            modes={by_id[c].audio.value for c in selected_ids if c in by_id and by_id[c].audio in {Audio.SUB,Audio.DUB}}
            if len(modes)==1:audio=next(iter(modes))
        if audio is None:
            segment_groups=[[by_id[c] for c in s["release"]["candidate_ids"] if c in by_id] for s in chosen.get("segments",[])]
            preference=Audio(original.get("audio_preference","SUB")) if original.get("audio_preference") in {"SUB","DUB"} else Audio.SUB
            mode,selected=choose_plan_variants(segment_groups,preference)
            if selected and mode in {Audio.SUB,Audio.DUB}:
                audio=mode.value;selected_ids={candidate.id for candidate in selected}
        observations=original.get("provider_observations") or {}
        source_fingerprints={by_id[c].url:(observations.get(by_id[c].url) or {}).get("fingerprint")
            for c in selected_ids if c in by_id and by_id[c].url}
        return {"item_id":item["id"],"target_counts":target_counts,"audio":audio,
            "source_fingerprints":source_fingerprints}

    def _ensure_audio_masters(self,current_items):
        masters=self.store.series_audio_masters()
        for item,group in zip(current_items,[self._preserve_group(value) for value in current_items]):
            if not group or group.get("audio") not in {"SUB","DUB"}:continue
            season_one=next((target for target in item["original"].get("targets",[])
                if str(target.get("target_id","")).rsplit(":",1)[-1]=="1"),None)
            if not season_one:continue
            sid=str(season_one["target_id"]).split(":",1)[0]
            if sid not in masters:
                self.store.set_series_audio_master(sid,group["audio"],season_one["target_id"],item["digest"])
                masters=self.store.series_audio_masters()
        return {sid:value["audio"] for sid,value in masters.items()}

    def _persist_new_audio_masters(self):
        masters=self.store.series_audio_masters()
        for item in self.store.current_items():
            group=self._preserve_group(item)
            if not group or group.get("audio") not in {"SUB","DUB"}:continue
            season_one=next((target for target in item["original"].get("targets",[])
                if str(target.get("target_id","")).rsplit(":",1)[-1]=="1"),None)
            if not season_one:continue
            sid=str(season_one["target_id"]).split(":",1)[0]
            if sid not in masters:
                self.store.set_series_audio_master(sid,group["audio"],season_one["target_id"],item["digest"])
                masters=self.store.series_audio_masters()

    def scan(self,dataset,progress=None,scan_mode="normal"):
        if dataset not in self.datasets:raise ValueError("Unknown configured dataset")
        if scan_mode not in {"normal","deep_audit"}:raise ValueError("Invalid scan mode")
        current_items=self.store.current_items()
        preserve_groups=[group for item in current_items if (group:=self._preserve_group(item))]
        series_audio_master=self._ensure_audio_masters(current_items)
        current_target_ids=[t["target_id"] for item in current_items for t in item["original"].get("targets",[])]
        reader=self.datasets[dataset]
        arguments={"preserve_groups":preserve_groups,"current_target_ids":current_target_ids,
            "series_audio_master":series_audio_master,"inventory_fingerprints":self.store.target_inventory(),
            "progress":progress}
        if scan_mode=="deep_audit" and hasattr(reader,"read_deep_audit"):
            metadata=reader.read_deep_audit(**arguments)
        elif hasattr(reader,"read_incremental"):
            metadata=reader.read_incremental(**arguments)
        else:
            if progress:progress("inventory_read",5)
            metadata=reader.read()
        incremental=metadata.get("incremental") or {}
        review_targets={
            "candidate_overflow":set(metadata.get("candidate_overflow_target_ids") or []),
            "provider_request_budget":set(metadata.get("provider_request_pending_target_ids") or []),
            "catalog_generation_unavailable":set(metadata.get("catalog_generation_pending_target_ids") or []),
        }
        preserved_item_ids=set(incremental.get("preserved_item_ids") or [])
        frozen=[item["original"] for item in current_items if item["id"] in preserved_item_ids]
        provider_observations=dict(metadata.get("provider_observations") or {})
        for case in metadata.get("cases",[]):
            for url,detail in ((case.get("raw") or {}).get("details") or {}).items():
                if isinstance(detail,dict):
                    provider_observations.setdefault(url,{"status":detail.get("status"),
                        "fingerprint":detail.get("source_fingerprint") or detail_fingerprint(detail),
                        "validated_at":detail.get("fetched_at"),"next_validation_at":detail.get("next_validation_at"),
                        "validation_status":detail.get("validation_status","snapshot")})
        if progress:progress("candidate_enrichment",20)
        if metadata.get("cases"):
            enriched=self.enricher.enrich(metadata)
            if progress:progress("deterministic_matcher_and_release_resolver",40)
            evaluated=self.engine.evaluate(enriched)
        else:
            enriched=metadata
            evaluated={"targets":[],"inferred_shared_plans":[]}
        if self.researcher:
            unresolved={int(r["target_id"].split(":",1)[0]) for r in evaluated["targets"] if r["after"]=="needs_review"}
            if unresolved:
                if progress:progress("public_metadata_research",55)
                researched,changed=self.researcher.enrich(metadata,unresolved)
                if changed:
                    enriched=self.enricher.enrich(researched)
                    evaluated=self.engine.evaluate(enriched)
        records={r["target_id"]:r for r in evaluated["targets"]}
        for reason,target_ids in review_targets.items():
            for target_id in target_ids:
                record=records.get(target_id)
                if record:
                    record["after"]="needs_review"
                    record["reason_codes"]=sorted(set([*record.get("reason_codes",[]),reason]))
        groups=[];consumed=set()
        for record in evaluated["targets"]:
            tid=record["target_id"]
            if tid in consumed:continue
            shared=next((p for p in evaluated["inferred_shared_plans"] if tid in p["target_ids"] and
                all(records[t]["after"]=="needs_review" for t in p["target_ids"])),None)
            tids=list(shared["target_ids"]) if shared else [tid]
            selected=[records[t] for t in tids];consumed.update(tids)
            plan=shared or record["plan"]
            candidates={c["id"]:c for r in selected for c in r["candidate_snapshots"]}
            alternate={plan_id(p):p for r in selected for p in r["alternative_plans"] if p.get("segments")}
            alternate.pop(plan_id(plan),None)
            reasons=set(r for row in selected for r in row["reason_codes"])
            state="proposed" if record["after"]=="matched" and plan.get("segments") else record["after"]
            if shared:reasons.add("episode_boundary_unverified")
            if record["after"]=="matched" and not plan.get("segments"):reasons.add("mapping_plan_unavailable")
            evidence=[e for row in selected for evaluation in row["baseline_decision"]["evaluations"] for e in evaluation["evidence"]]
            evidence += [{"signal":"release_crosswalk","reliability":s["crosswalk"]["reliability"],"details":s["crosswalk"]["evidence"]} for s in plan.get("segments",[])]
            audio_preferences={r.get("audio_preference","SUB") for r in selected}
            audio_preference=next(iter(audio_preferences)) if len(audio_preferences)==1 else "SUB"
            groups.append({"schema_version":1,"targets":[r["target_snapshot"] for r in selected],
                "candidates":list(candidates.values()),"proposed_plan":plan,"alternate_plans":list(alternate.values()),
                "reason_codes":sorted(reasons),"evidence":evidence,"initial_state":state,
                "audio_preference":audio_preference,
                "provider_observations":{c["url"]:provider_observations[c["url"]]
                    for c in candidates.values() if c.get("url") in provider_observations},
                "matcher_version":MATCHER_VERSION,"resolver_version":RESOLVER_VERSION,
                "foundation_decisions":[r["baseline_decision"] for r in selected],
                "v3_observations":[{"target_id":r["target_id"],"urls":r["v3_urls"],"ground_truth":False} for r in selected]})
        verifications=self.verifier.evaluate_many(groups) if self.verifier and getattr(self.verifier,"enabled",False) else [None]*len(groups)
        for group,verification in zip(groups,verifications):
            if verification is None:continue
            group["llm_verification"]=verification
            # Strict verifier is a one-way gate: it may demote an automatic
            # proposal, but it never promotes a deterministic Review.
            if group["initial_state"]=="proposed" and verification.get("status")!="confirmed":
                group["initial_state"]="needs_review"
                group["reason_codes"]=sorted(set([*group.get("reason_codes",[]),"gemini_verification_failed"]))
        work_groups=groups
        work_attempts=[self.judge.evaluate(group) if self.judge else None for group in work_groups]
        groups=[*frozen,*work_groups]
        attempts=[None]*len(frozen)+work_attempts
        if progress:progress("v4_snapshot_storage",90)
        result=self.store.record_scan(groups,dataset,authoritative=True)
        if metadata.get("target_inventory") is not None:
            self.store.record_target_inventory(metadata["target_inventory"])
        self._persist_new_audio_masters()
        for state,key in (("pending","provider_validation_pending_item_ids"),
            ("stale_retry","provider_validation_stale_retry_item_ids"),
            ("invalidated","provider_validation_invalidated_item_ids")):
            if incremental.get(key):self.store.record_provider_validation_state(incremental[key],state,{"scan_id":result["scan_id"]})
        current_validated=(preserved_item_ids
            - set(incremental.get("provider_validation_pending_item_ids") or [])
            - set(incremental.get("provider_validation_stale_retry_item_ids") or []))
        if current_validated:self.store.record_provider_validation_state(current_validated,"current",{"scan_id":result["scan_id"]})
        for id,attempt in zip(result["item_ids"],attempts):
            if attempt:self.store.record_judge_attempt(id,attempt)
        if incremental:
            result["preflight"]=incremental
            result["preserved_items"]=len(frozen)
        result["scan_mode"]=scan_mode
        result["stages"]=["inventory_read","inventory_diff","provider_verification","dirty_mapping"]
        if incremental.get("work_series_count"):
            result["stages"].extend([
                "catalog_refresh" if incremental.get("catalog_refresh_performed") else "catalog_generation_load",
                "candidate_retrieval","provider_detail","candidate_enrichment",
                "deterministic_matcher_and_release_resolver"])
        if self.researcher:result["stages"].append("public_metadata_research")
        if self.verifier and getattr(self.verifier,"enabled",False):result["stages"].append("gemini_verification")
        result["stages"].append("v4_snapshot_storage")
        return result

    def overview(self):
        observed,open_reviews,current_targets=self.store.overview_counts()
        states={state:observed.get(state,0) for state in
            ("proposed","approved","rejected","needs_review","waiting","airing","unavailable","superseded")}
        effects=self.store.settings()["external_writes_enabled"]
        return {"mapping_counts":states,"open_reviews":open_reviews,
            "current_target_count":current_targets,
            "mode":"production" if effects else "shadow","external_writes":effects}

    def items(self,*,reviews=False,state=None,offset=0,limit=50):
        items,total=self.store.list_item_summaries_page(
            reviews=reviews,state=state,offset=offset,limit=limit)
        return {"items":items,"total":total,"offset":offset,"limit":limit}

    def detail(self,id):return item_dto(self.store.get_item(id),detail=True)

    def series(self,*,offset=0,limit=200,query=None,state=None):
        rows,total=self.store.series_summaries(offset=offset,limit=limit,query=query,status=state)
        return {"items":rows,"total":total,"offset":offset,"limit":limit}

    def series_detail(self,series_id):
        return self.store.series_projection(series_id)

    def season_override(self,series_id,season):
        target_id=f"{int(series_id)}:{int(season)}"
        try:self.store.current_item_for_target(target_id)
        except KeyError:
            raise KeyError("Unknown season")
        return {"target_id":target_id,**self.store.season_override(target_id)}

    def update_season_override(self,series_id,season,body):
        target_id=f"{int(series_id)}:{int(season)}"
        try:item=self.store.current_item_for_target(target_id)
        except KeyError:item=None
        target=next((t for t in item["original"]["targets"] if t["target_id"]==target_id),None) if item else None
        if target is None:raise KeyError("Unknown season")
        value=validate_override(body,target.get("episode_count"))
        if value["manual_approved"]:
            if self.manual_source_validator is None:raise Conflict("Live provider validation is required before manual approval")
            sources=self.manual_source_validator.validate(value["source_urls"],value["audio"],value["episode_map"])
            value["approval"]={"target":{"target_id":target_id,"episode_count":target.get("episode_count"),
                "item_id":item["id"],"item_digest":item["digest"],"item_revision":item["revision"],
                "source_urls":value["source_urls"],"audio":value["audio"],"episode_map":value["episode_map"]},
                "sources":sources}
        if not config_active(value):
            self.store.clear_season_override(target_id)
            return {"target_id":target_id,**self.store.season_override(target_id)}
        saved={"target_id":target_id,**self.store.set_season_override(target_id,value)}
        if int(season)==1 and value.get("audio") in {"SUB","DUB"}:
            self.store.set_series_audio_master(str(series_id),value["audio"],target_id,item["digest"],actor="local-user")
        return saved

    def clear_season_override(self,series_id,season):
        target_id=f"{int(series_id)}:{int(season)}"
        return self.store.clear_season_override(target_id)

    def manual_override_executable(self,series_id,season):
        target_id=f"{int(series_id)}:{int(season)}"
        try:item=self.store.current_item_for_target(target_id)
        except KeyError:item=None
        if item is None or self.manual_source_validator is None:return False
        target=next(target for target in item["original"]["targets"] if target.get("target_id")==target_id)
        override=self.store.season_override(target_id)
        if not manual_approval_current(item,target,override):return False
        try:
            current=self.manual_source_validator.validate(override["source_urls"],override["audio"],override["episode_map"])
        except (OSError,ValueError,RuntimeError):return False
        expected=(override.get("approval") or {}).get("sources") or []
        identity=lambda row:(row.get("url"),row.get("canonical_url"),row.get("fingerprint"),row.get("audio"))
        return [identity(row) for row in current]==[identity(row) for row in expected]

    def metadata(self,id):
        original=self.store.get_item(id)["original"]
        fields=("canonical_title","season_number","scene_season_number","air_date","premiere_date","release_year","episode_count","cour","part")
        return {"targets":[{k:t.get(k) for k in fields} for t in original["targets"]],
            "releases":[{"candidate_id":c["id"],**{k:c.get(k) for k in fields},"source":c["source"]["name"]} for c in original["candidates"]]}

    def evidence(self,id):
        item=self.store.get_item(id)
        return {"snapshot_digest":item["digest"],"original_snapshot":item["original"],"human_decision":item["human_decision"],
            "created_at":item["created_at"],"matcher_version":item["original"]["matcher_version"],"resolver_version":item["original"]["resolver_version"],
            "judge_attempt":self.store.judge_attempt(id)}

    def action(self,id,action,body):
        allowed={"expected_revision","reason","plan_id","variant_choices","acknowledge_uncertainty"} if action in {"approve","choose"} else {"expected_revision","reason"}
        if set(body)-allowed:raise ValueError("Unexpected action fields")
        if action not in {"approve","choose","reject","dismiss","reopen"}:raise KeyError("Unknown action")
        if "expected_revision" not in body or "reason" not in body:raise ValueError("expected_revision and reason are required")
        item=self.store.get_item(id);original=item["original"];decision=None
        if action in {"approve","choose"}:
            plans={plan_id(p):p for p in [original["proposed_plan"]]+original["alternate_plans"]}
            chosen_id=body.get("plan_id",plan_id(original["proposed_plan"]))
            if action=="approve" and chosen_id!=plan_id(original["proposed_plan"]):raise ValueError("Use choose for an alternate plan")
            chosen=plans.get(chosen_id)
            if not chosen or not chosen.get("segments"):raise Conflict("No supported original plan; new source evidence requires a scan")
            if any(s["crosswalk"]["reliability"]=="inferred" for s in chosen["segments"]) and body.get("acknowledge_uncertainty") is not True:
                raise Conflict("Explicit human acknowledgement of unresolved boundaries is required")
            variant_choices=body.get("variant_choices",{})
            if not isinstance(variant_choices,dict):raise ValueError("variant_choices must be an object")
            variants={s["release"]["release_id"]:set(s["release"]["candidate_ids"]) for s in chosen["segments"]}
            if any(release not in variants or candidate not in variants[release] for release,candidate in variant_choices.items()):
                raise Conflict("Candidate is not a supported variant of the selected original plan")
            if set(chosen["target_ids"])!={t["target_id"] for t in original["targets"]}:raise Conflict("Alternate plan affects different targets")
            by_id={c["id"]:Candidate.from_dict(c) for c in original["candidates"]}
            segment_groups=[[by_id[c] for c in s["release"]["candidate_ids"] if c in by_id] for s in chosen["segments"]]
            preference=Audio(original.get("audio_preference","SUB")) if original.get("audio_preference") in {"SUB","DUB"} else Audio.SUB
            _,defaults=choose_plan_variants(segment_groups,preference)
            default_by_release={s["release"]["release_id"]:c.id for s,c in zip(chosen["segments"],defaults)}
            if not defaults and not variant_choices:
                raise Conflict("No homogeneous full-season DUB or SUB variant set is available")
            variant_choices={release:variant_choices.get(release,default_by_release.get(release)) for release in variants}
            if any(candidate is None for candidate in variant_choices.values()):
                raise Conflict("Explicit variant choices are required for every release in a mixed-audio plan")
            decision={"plan_id":chosen_id,"variant_choices":variant_choices,"uncertainty_acknowledged":body.get("acknowledge_uncertainty") is True}
        return item_dto(self.store.transition(id,action,body["expected_revision"],body["reason"],decision),detail=True)

    def activity(self,after=0,limit=50):
        events=self.store.activity(after,limit)
        return {"items":[{"sequence":e["sequence"],"entity_id":e["item_id"],"action":e["action"],"actor":e["actor"],"timestamp":e["timestamp"],
            "message":e["action"].replace("_"," "),"reason":e["payload"].get("decision",{}).get("reason")} for e in events],
            "next_cursor":events[-1]["sequence"] if events else after}
