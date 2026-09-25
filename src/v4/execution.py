"""Approved-only, once-only V4 downstream execution boundary."""
from copy import deepcopy
import hmac
import json
import os
from pathlib import Path
import re
from typing import Protocol

from .application_dto import plan_id
from .application_store import Conflict,digest
from .composite_replay import choose_plan_variants
from .models import Candidate,Audio


_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DownstreamFailure(RuntimeError):
    def __init__(self,category="adapter_error",*,outcome_unknown=False):
        category=category if isinstance(category,str) and _CATEGORY.fullmatch(category) else "adapter_error"
        super().__init__(category)
        self.category=category;self.outcome_unknown=outcome_unknown


class ProductionEffectAuthorization:
    """Explicit, non-environment production authorization; never created by defaults."""
    _FIELDS={"schema_version","approval_id","environment","production_writes","production_downloads","adapter","execution_key"}

    def __init__(self,manifest):self.manifest=deepcopy(manifest)

    @classmethod
    def from_manifest(cls,manifest,confirmation):
        if confirmation!="ENABLE PRODUCTION EFFECTS" or not isinstance(manifest,dict) or set(manifest)!=cls._FIELDS:
            raise ValueError("Exact production authorization is required")
        if manifest.get("schema_version")!=1 or manifest.get("environment")!="production" or manifest.get("adapter")!="sonarr-download-v1":
            raise ValueError("Unsupported production authorization")
        if manifest.get("production_writes") is not True or manifest.get("production_downloads") is not True:
            raise ValueError("Both production effects must be authorized")
        approval=manifest.get("approval_id")
        if not isinstance(approval,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}",approval):
            raise ValueError("A bounded approval identifier is required")
        if not isinstance(manifest.get("execution_key"),str) or not re.fullmatch(r"[0-9a-f]{64}",manifest["execution_key"]):
            raise ValueError("Authorization must bind one execution key")
        return cls(manifest)

    def permits(self,receipt):
        return receipt.get("adapter")==self.manifest["adapter"] and receipt.get("mode")=="production" and receipt.get("receipt_id")==self.manifest["execution_key"]

    def permits_execution(self,execution_key):
        return isinstance(execution_key,str) and hmac.compare_digest(execution_key,self.manifest["execution_key"])

    def same_capability(self,other):
        return isinstance(other,ProductionEffectAuthorization) and self.manifest==other.manifest


class DownstreamAdapter(Protocol):
    def execute(self,envelope:dict) -> dict: ...


def approved_execution_scopes(item, *, automatic):
    """List target/episode coordinates present in the selected immutable plan."""
    original=item["original"]
    if automatic:plan=original.get("proposed_plan") or {}
    else:
        human=item.get("human_decision") or {};selected_id=human.get("plan_id")
        plans={plan_id(value):value for value in [original.get("proposed_plan") or {},
            *(original.get("alternate_plans") or [])]}
        plan=plans.get(selected_id) or {}
    targets={target.get("season_number"):target.get("target_id")
        for target in original.get("targets",[]) if isinstance(target,dict)}
    scopes={(targets.get(link.get("sonarr_season")),link.get("sonarr_episode"))
        for segment in plan.get("segments",[]) if isinstance(segment,dict)
        for link in (segment.get("coverage") or {}).get("links",[]) if isinstance(link,dict)}
    return tuple(sorted((target_id,episode) for target_id,episode in scopes
        if isinstance(target_id,str) and target_id and isinstance(episode,int)
        and not isinstance(episode,bool) and episode>0))


def _execution_envelope(item,execution_key,target_id=None,sonarr_episode=None):
    original=item["original"];human=item["human_decision"] or {}
    plans={plan_id(plan):plan for plan in [original["proposed_plan"]]+original["alternate_plans"]}
    selected_id=human.get("plan_id")
    selected=plans.get(selected_id)
    if not selected or not selected.get("segments"):
        raise Conflict("Approved plan is unavailable from the immutable snapshot")
    all_target_ids=tuple(target["target_id"] for target in original["targets"])
    target_by_season={target["season_number"]:target for target in original["targets"]}
    if len(target_by_season)!=len(original["targets"]):raise Conflict("Execution targets do not have unique Sonarr seasons")
    if tuple(selected.get("target_ids",()))!=all_target_ids:
        raise Conflict("Approved plan target order differs from the immutable snapshot")
    scoped=target_id is not None or sonarr_episode is not None
    if scoped and (target_id not in all_target_ids or not isinstance(sonarr_episode,int)
            or isinstance(sonarr_episode,bool) or sonarr_episode<1):
        raise Conflict("Execution scope is not part of the approved plan")
    target_ids=(target_id,) if scoped else all_target_ids
    candidates={candidate["id"]:candidate for candidate in original["candidates"]}
    choices=human.get("variant_choices")
    if not isinstance(choices,dict):raise Conflict("Approved variants are missing")
    segments=[]
    for segment in selected["segments"]:
        release=segment["release"];release_id=release["release_id"]
        candidate_id=choices.get(release_id)
        if candidate_id not in release["candidate_ids"] or candidate_id not in candidates:
            raise Conflict("Approved variant is not part of the immutable plan")
        links=segment["coverage"].get("links") or []
        if not links:raise Conflict("Approved segment has no executable episode coordinates")
        episode_links=[]
        for link in links:
            target=target_by_season.get(link["sonarr_season"])
            if not target:raise Conflict("Episode coordinate has no immutable Sonarr target")
            if scoped and (target["target_id"]!=target_id or link["sonarr_episode"]!=sonarr_episode):continue
            try:series_id=int(str(target["target_id"]).split(":",1)[0])
            except (ValueError,TypeError):raise Conflict("Target does not contain a Sonarr series identifier") from None
            episode_links.append({
                "target_id":target["target_id"],"sonarr_series_id":series_id,
                "source_episode":link["source_episode"],"sonarr_season":link["sonarr_season"],
                "sonarr_episode":link["sonarr_episode"],"absolute_episode":link.get("absolute_episode"),
            })
        if episode_links:
            segments.append({
                "release_id":release_id,
                "selected_candidate_id":candidate_id,
                "source_url":candidates[candidate_id]["url"],
                "episode_links":episode_links,
            })
    if not segments:raise Conflict("Execution scope has no approved episode coordinate")
    envelope={
        "schema_version":1,
        "execution_key":execution_key,
        "item_id":item["id"],
        "snapshot_digest":item["digest"],
        "approved_revision":item["revision"],
        "plan_id":selected_id,
        "target_ids":target_ids,
        "segments":segments,
    }
    if scoped:envelope["execution_scope"]={"target_id":target_id,"sonarr_episode":sonarr_episode}
    return envelope


def _automatic_execution_envelope(item,execution_key,audio_preference=None,target_id=None,sonarr_episode=None):
    """Build an executable envelope only from a complete, non-inferred deterministic proposal."""
    original=item["original"];plan=original["proposed_plan"]
    if item["mapping_state"]!="proposed" or item["review_state"]!="not_required" or not plan.get("segments"):
        raise Conflict("Automatic execution requires a deterministic proposal")
    if original.get("reason_codes"):
        raise Conflict("Automatic execution refuses unresolved reason codes")
    if any(segment["crosswalk"].get("reliability")=="inferred" for segment in plan["segments"]):
        raise Conflict("Automatic execution refuses inferred boundaries")
    for target in original["targets"]:
        season=target["season_number"];expected=target.get("episode_count")
        links=[link for segment in plan["segments"] for link in segment["coverage"].get("links",[])
            if link.get("sonarr_season")==season]
        if not isinstance(expected,int) or sorted(link.get("sonarr_episode") for link in links)!=list(range(1,expected+1)):
            raise Conflict("Automatic execution requires exact episode coverage")
    candidates={candidate["id"]:Candidate.from_dict(candidate) for candidate in original["candidates"]}
    groups=[[candidates[id] for id in segment["release"]["candidate_ids"] if id in candidates] for segment in plan["segments"]]
    selected_preference=audio_preference or original.get("audio_preference")
    preference=Audio.DUB if selected_preference=="DUB" else Audio.SUB
    _,chosen=choose_plan_variants(groups,preference)
    if len(chosen)!=len(plan["segments"]):
        raise Conflict("Automatic execution requires one homogeneous audio plan")
    automatic=deepcopy(item)
    automatic["human_decision"]={"plan_id":plan_id(plan),"variant_choices":{
        segment["release"]["release_id"]:candidate.id for segment,candidate in zip(plan["segments"],chosen)}}
    envelope=_execution_envelope(automatic,execution_key,target_id,sonarr_episode)
    envelope["execution_policy"]={"audio_preference":selected_preference}
    return envelope


def _manual_execution_envelope(item,target,override,current_sources,execution_key,sonarr_episode=None):
    target_id=target["target_id"]
    try:series_id=int(str(target_id).split(":",1)[0])
    except (TypeError,ValueError):raise Conflict("Manual target has no Sonarr series identifier") from None
    by_source={}
    for row in override["episode_map"]:
        if sonarr_episode is None or row["sonarr_episode"]==sonarr_episode:
            by_source.setdefault(row["source_index"],[]).append(row)
    segments=[]
    for source_index,rows in sorted(by_source.items()):
        source=current_sources[source_index]
        segments.append({
            "release_id":f"manual:{target_id}:{source_index}",
            "selected_candidate_id":f"manual-source-{source_index}",
            "source_url":override["source_urls"][source_index],
            "source_fingerprint":source["fingerprint"],
            "episode_links":[{"target_id":target_id,"sonarr_series_id":series_id,
                "source_episode":row["source_episode"],"sonarr_season":target["season_number"],
                "sonarr_episode":row["sonarr_episode"],"absolute_episode":None} for row in rows],
        })
    if not segments:raise Conflict("Manual approval has no executable coordinates")
    envelope={"schema_version":1,"execution_key":execution_key,"item_id":item["id"],
        "snapshot_digest":item["digest"],"approved_revision":item["revision"],
        "plan_id":"manual:"+digest(override["approval"]),
        "manual_target_id":target_id,"audio":override["audio"],
        "target_ids":(target_id,),
        "segments":segments}
    if sonarr_episode is not None:
        envelope["execution_scope"]={"target_id":target_id,"sonarr_episode":sonarr_episode}
    return envelope


class ExecutionCoordinator:
    def __init__(self,store,adapter:DownstreamAdapter,*,owner_token,require_preclaimed=False,
        production_authorization=None,manual_source_validator=None):
        if not isinstance(owner_token,str) or not owner_token:raise ValueError("owner_token is required")
        if production_authorization is not None and not isinstance(production_authorization,ProductionEffectAuthorization):raise ValueError("Invalid production authorization")
        self.store=store;self.adapter=adapter;self.owner_token=owner_token;self.require_preclaimed=require_preclaimed
        self.production_authorization=production_authorization;self.manual_source_validator=manual_source_validator

    def _authorize_production(self,execution_key):
        if getattr(self.adapter,"production_effects",False) is not True:return
        required=getattr(self.adapter,"production_authorization",None)
        supplied=self.production_authorization
        if not isinstance(required,ProductionEffectAuthorization) or supplied is None or not supplied.same_capability(required) or not supplied.permits_execution(execution_key):
            raise DownstreamFailure("production_effect_forbidden")

    def reserve(self,item_id,*,expected_revision,target_id=None,sonarr_episode=None):
        return self.store.reserve_execution(item_id,expected_revision,self.owner_token,_execution_envelope,
            require_preclaimed=self.require_preclaimed,key_authorizer=self._authorize_production,
            target_id=target_id,sonarr_episode=sonarr_episode)

    def execute(self,item_id,*,expected_revision,target_id=None,sonarr_episode=None):
        return self._dispatch(self.reserve(item_id,expected_revision=expected_revision,
            target_id=target_id,sonarr_episode=sonarr_episode))

    def reserve_automatic(self,item_id,*,expected_revision,target_id=None,sonarr_episode=None):
        return self.store.reserve_execution(item_id,expected_revision,self.owner_token,_automatic_execution_envelope,
            require_preclaimed=self.require_preclaimed,automatic=True,key_authorizer=self._authorize_production,
            target_id=target_id,sonarr_episode=sonarr_episode)

    def execute_automatic(self,item_id,*,expected_revision,target_id=None,sonarr_episode=None):
        return self._dispatch(self.reserve_automatic(item_id,expected_revision=expected_revision,
            target_id=target_id,sonarr_episode=sonarr_episode))

    def reserve_manual(self,item_id,target_id,*,expected_revision,sonarr_episode=None):
        if self.manual_source_validator is None:raise Conflict("Manual execution requires live source validation")
        item=self.store.get_item(item_id);target=next((value for value in item["original"]["targets"]
            if value.get("target_id")==target_id),None)
        if target is None:raise Conflict("Manual target is not in the immutable snapshot")
        override=self.store.season_override(target_id)
        from .series_view import manual_approval_current
        if not manual_approval_current(item,target,override):raise Conflict("Manual approval is stale")
        try:current=self.manual_source_validator.validate(override["source_urls"],override["audio"],override["episode_map"])
        except (OSError,ValueError,RuntimeError):raise Conflict("Manual source revalidation failed") from None
        expected=(override.get("approval") or {}).get("sources") or []
        identity=lambda row:(row.get("url"),row.get("canonical_url"),row.get("fingerprint"),row.get("audio"))
        if [identity(row) for row in current]!=[identity(row) for row in expected]:
            raise Conflict("Manual source evidence changed")
        return self.store.reserve_manual_execution(item_id,target_id,expected_revision,self.owner_token,
            _manual_execution_envelope,override_digest=digest(override),current_sources=current,
            require_preclaimed=self.require_preclaimed,key_authorizer=self._authorize_production,
            sonarr_episode=sonarr_episode)

    def execute_manual(self,item_id,target_id,*,expected_revision,sonarr_episode=None):
        return self._dispatch(self.reserve_manual(item_id,target_id,expected_revision=expected_revision,
            sonarr_episode=sonarr_episode))

    def _dispatch(self,envelope):
        self._authorize_production(envelope["execution_key"])
        try:
            receipt=self.adapter.execute(deepcopy(envelope))
            receipt=self._receipt(receipt)
        except DownstreamFailure as error:
            if error.outcome_unknown:self.store.reconciliation_pending(envelope["execution_key"],error.category)
            else:self.store.finish_execution(envelope["execution_key"],error_category=error.category)
            raise
        except Exception:
            self.store.finish_execution(envelope["execution_key"],error_category="adapter_error")
            raise DownstreamFailure("adapter_error") from None
        self.store.finish_execution(envelope["execution_key"],receipt=receipt)
        return {"execution_key":envelope["execution_key"],"status":"completed","receipt":receipt}

    def reconcile(self,execution_key):
        reserved=self.store.reserved_execution(execution_key);envelope=reserved["envelope"]
        reconcile=getattr(self.adapter,"reconcile",None)
        try:result=reconcile(execution_key) if callable(reconcile) else self.adapter.lookup(execution_key)
        except Exception:result={"status":"unknown"}
        if not isinstance(result,dict) or result.get("status") not in {"completed","absent","unknown","failed"}:
            result={"status":"unknown"}
        if result["status"]=="completed":
            try:receipt=self._receipt(result.get("receipt"))
            except DownstreamFailure:
                self.store.reconciliation_pending(execution_key,"remote_state_unknown")
                raise DownstreamFailure("remote_state_unknown") from None
            self.store.finish_execution(execution_key,receipt=receipt)
            return {"execution_key":execution_key,"status":"completed","receipt":receipt,"reconciled":True}
        if result["status"]=="absent":return self._dispatch(envelope)
        if result["status"]=="failed":
            category=result.get("error_category")
            category=category if isinstance(category,str) and _CATEGORY.fullmatch(category) else "adapter_error"
            self.store.finish_execution(execution_key,error_category=category)
            return {"execution_key":execution_key,"status":"failed","error_category":category,"reconciled":True}
        self.store.reconciliation_pending(execution_key,"remote_state_unknown")
        raise DownstreamFailure("remote_state_unknown")

    def reconcile_startup(self):
        """Reconcile every durable reservation without creating a new identity.

        Unknown outcomes stay reserved and make the writer unavailable. A proven
        absent effect may resume only through the existing immutable envelope.
        """
        results=[]
        for attempt in self.store.reserved_executions():
            key=attempt["execution_key"]
            try:results.append(self.reconcile(key))
            except DownstreamFailure as error:
                results.append({"execution_key":key,"status":"pending","error_category":error.category})
        remaining=[attempt["execution_key"] for attempt in self.store.reserved_executions()]
        return {"ready":not remaining,"reconciled":results,"unknown":remaining}

    def _receipt(self,receipt):
        if not isinstance(receipt,dict):raise DownstreamFailure("invalid_receipt")
        allowed={"adapter","receipt_id","mode","production_effects"}
        if set(receipt)!=allowed or not all(isinstance(receipt[key],str) for key in ("adapter","receipt_id","mode")):
            raise DownstreamFailure("invalid_receipt")
        effect=receipt["production_effects"]
        if effect is True and (self.production_authorization is None or not self.production_authorization.permits(receipt)):
            raise DownstreamFailure("production_effect_forbidden")
        if effect is not False and effect is not True:raise DownstreamFailure("invalid_receipt")
        return receipt


class FakeDownstreamAdapter:
    def __init__(self,*,fail_category=None,lookup_status=None):
        self.fail_category=fail_category;self.lookup_status=lookup_status;self.calls=[];self.receipts={}

    def execute(self,envelope):
        self.calls.append(deepcopy(envelope))
        if self.fail_category:raise DownstreamFailure(self.fail_category)
        receipt={"adapter":"fake","receipt_id":envelope["execution_key"],"mode":"fake","production_effects":False}
        self.receipts[envelope["execution_key"]]=receipt
        return receipt

    def lookup(self,execution_key):
        if self.lookup_status:return {"status":self.lookup_status}
        receipt=self.receipts.get(execution_key)
        return {"status":"completed","receipt":receipt} if receipt else {"status":"absent"}


class SandboxDownstreamAdapter:
    """Atomically publish one intent receipt inside an explicitly supplied root."""
    def __init__(self,root):
        self.root=Path(root).resolve()

    def execute(self,envelope):
        key=envelope.get("execution_key","")
        if not re.fullmatch(r"[0-9a-f]{64}",key):raise DownstreamFailure("invalid_execution_key")
        self.root.mkdir(parents=True,exist_ok=True)
        destination=(self.root/f"{key}.json").resolve()
        if destination.parent!=self.root:raise DownstreamFailure("sandbox_path_violation")
        temporary=(self.root/f"{key}.tmp").resolve()
        if destination.exists():raise DownstreamFailure("duplicate_sandbox_receipt")
        document={"mode":"sandbox","production_effects":False,"execution_key":key,"execution":envelope}
        try:
            with temporary.open("x",encoding="utf-8",newline="\n") as handle:
                json.dump(document,handle,sort_keys=True,ensure_ascii=False,separators=(",",":"))
                handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,destination)
        except DownstreamFailure:
            raise
        except Exception:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
            raise DownstreamFailure("sandbox_write_failed") from None
        return {"adapter":"sandbox-file","receipt_id":key,"mode":"sandbox","production_effects":False}

    def lookup(self,execution_key):
        if not re.fullmatch(r"[0-9a-f]{64}",execution_key):return {"status":"unknown"}
        path=(self.root/f"{execution_key}.json").resolve()
        if path.parent!=self.root:return {"status":"unknown"}
        if not path.exists():return {"status":"absent"}
        try:
            document=json.loads(path.read_text(encoding="utf-8"))
            if document.get("execution_key")!=execution_key:return {"status":"unknown"}
        except (OSError,ValueError):return {"status":"unknown"}
        return {"status":"completed","receipt":{"adapter":"sandbox-file","receipt_id":execution_key,"mode":"sandbox","production_effects":False}}
