"""Optional suggestion-only second level for ambiguous V4 reviews.

Only the sanitized shortlist crosses the provider boundary. A judge response never
changes a mapping state or relaxes a deterministic hard constraint.
"""
from dataclasses import dataclass
import json
import os
import socket
from typing import Protocol
from urllib import error,request
from .application_store import digest,encode


class ProviderError(Exception):
    pass


class JudgeProvider(Protocol):
    name:str
    model:str
    enabled:bool
    def generate(self,payload:dict,policy:"JudgePolicy")->str:...


@dataclass(frozen=True)
class JudgePolicy:
    min_confidence:float=0.90
    max_candidates:int=5
    max_payload_bytes:int=8192
    max_output_bytes:int=4096
    timeout_seconds:float=8.0
    max_output_tokens:int=256

    def __post_init__(self):
        if not (0<self.min_confidence<=1 and 1<=self.max_candidates<=8 and
                1024<=self.max_payload_bytes<=16384 and 256<=self.max_output_bytes<=8192 and
                0<self.timeout_seconds<=30 and 32<=self.max_output_tokens<=512):
            raise ValueError("Judge policy outside bounded range")


class DisabledProvider:
    name="disabled";model="none";enabled=False
    def generate(self,payload,policy):raise ProviderError("disabled")


class MockProvider:
    """Explicit deterministic provider for contract tests; never makes network calls."""
    name="mock";model="deterministic-test";enabled=True
    def __init__(self,response):self.response=response;self.calls=0
    def generate(self,payload,policy):
        self.calls+=1
        if isinstance(self.response,Exception):raise self.response
        return self.response if isinstance(self.response,str) else encode(self.response)


class GeminiProvider:
    name="gemini";enabled=True
    def __init__(self,*,model="gemini-3.1-flash-lite",key=None,opener=None):
        if not model.startswith("gemini-") or not all(c.isalnum() or c in ".-_" for c in model):
            raise ValueError("Invalid Gemini model")
        self.model=model;self._key=key if key is not None else os.getenv("GEMINI_API_KEY")
        self._opener=opener or request.urlopen

    def generate(self,payload,policy):
        if not self._key:raise ProviderError("key_unavailable")
        schema={"type":"OBJECT","properties":{"candidate_id":{"type":"STRING","nullable":True},
            "confidence":{"type":"NUMBER"},"reason":{"type":"STRING"},
            "evidence":{"type":"ARRAY","items":{"type":"STRING"}}},
            "required":["candidate_id","confidence","reason","evidence"]}
        body=encode({"systemInstruction":{"parts":[{"text":"Choose only among supplied candidate ids or null. Use only supplied metadata. Never browse, infer missing season boundaries, or override hard constraints. Reply with concise JSON."}]},
            "contents":[{"role":"user","parts":[{"text":encode(payload)}]}],
            "generationConfig":{"responseMimeType":"application/json","responseSchema":schema,
                "maxOutputTokens":policy.max_output_tokens,"temperature":0}}).encode("utf-8")
        if len(body)>policy.max_payload_bytes:raise ProviderError("payload_limit")
        endpoint=f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        req=request.Request(endpoint,data=body,headers={"Content-Type":"application/json","x-goog-api-key":self._key},method="POST")
        try:
            with self._opener(req,timeout=policy.timeout_seconds) as response:
                raw=response.read(65537)
            if len(raw)>65536:raise ProviderError("response_limit")
            envelope=json.loads(raw)
            parts=envelope["candidates"][0]["content"]["parts"]
            answer="".join(p.get("text","") for p in parts)
            if len(answer.encode("utf-8"))>policy.max_output_bytes:raise ProviderError("output_limit")
            return answer
        except (error.URLError,socket.timeout,TimeoutError) as exc:
            if isinstance(exc,(socket.timeout,TimeoutError)) or isinstance(getattr(exc,"reason",None),TimeoutError):
                raise TimeoutError() from None
            raise ProviderError("remote_unavailable") from None
        except (KeyError,IndexError,TypeError,ValueError):raise ProviderError("malformed_response") from None


def configured_provider():
    choice=os.getenv("ANIDOWN_V4_JUDGE_PROVIDER","disabled").strip().lower()
    if choice=="disabled":return DisabledProvider()
    if choice=="gemini":return GeminiProvider(model=os.getenv("ANIDOWN_V4_GEMINI_MODEL","gemini-3.1-flash-lite"))
    raise ValueError("Unknown V4 judge provider")


def _safe_aliases(aliases,season):
    result=[]
    for alias in aliases or []:
        if isinstance(alias,str):result.append({"title":alias});continue
        if not isinstance(alias,dict):continue
        scoped=alias.get("season_number")
        if scoped not in (None,season):continue
        result.append({k:alias[k] for k in ("text","language","kind","season_number","scene_season_number") if k in alias})
    return result[:16]


def _safe_entity(entity,*,target=False):
    fields=("target_id" if target else "id", "canonical_title","season_number","scene_season_number",
        "cour","part","premiere_date","air_date","release_year","episode_count","episode_scope",
        "episodes_complete","mapped_seasons","audio","audio_language","subtitle_language")
    result={k:entity[k] for k in fields if k in entity}
    result["alternate_titles"]=_safe_aliases(entity.get("alternate_titles"),entity.get("season_number"))
    result["season_coverage"]=[{k:v for k,v in coverage.items() if k in ("sonarr_season","scene_season","source_start","source_end","episode_start","episode_end","status")}
        for coverage in entity.get("season_coverage",[])[:8] if isinstance(coverage,dict)]
    # Compact crosswalk claims only; source URLs and arbitrary raw source values stay local.
    result["crosswalks"]=[{k:c[k] for k in ("sonarr_season","scene_season","cour","part","reliability") if k in c}
        for c in entity.get("crosswalks",[])[:8] if isinstance(c,dict)]
    return result


def _safe_plan(plan,eligible):
    segments=[]
    for segment in plan.get("segments",[])[:8]:
        release=segment.get("release",{});coverage=segment.get("coverage",{});crosswalk=segment.get("crosswalk",{})
        if not set(release.get("candidate_ids",[]))&eligible:continue
        segments.append({"candidate_ids":[id for id in release.get("candidate_ids",[]) if id in eligible],
            "coverage":{k:coverage[k] for k in ("status","episode_count","source_range","absolute_range","destination_ranges","offset","observed") if k in coverage},
            "crosswalk":{k:crosswalk[k] for k in ("basis","reliability") if k in crosswalk}})
    return {"target_ids":plan.get("target_ids",[])[:8],"segments":segments}


def _safe_evidence(evidence):
    allowed={"season","scene_season","release_date","release_year","episode_count",
        "exact_title","alias_title","fuzzy_title","language","cour","part"}
    if evidence.get("signal") not in allowed:return None
    result={k:evidence[k] for k in ("signal","outcome") if k in evidence}
    for k in ("expected","observed"):
        value=evidence.get(k)
        if isinstance(value,(str,int,float)) and not isinstance(value,bool):
            if isinstance(value,str) and (len(value)>200 or "://" in value):continue
            result[k]=value
    return result


def _validate_answer(raw,eligible,hard,policy):
    if not isinstance(raw,str) or len(raw.encode("utf-8"))>policy.max_output_bytes:return None,"output_limit"
    try:answer=json.loads(raw)
    except (ValueError,TypeError):return None,"invalid_json"
    if not isinstance(answer,dict) or set(answer)!={"candidate_id","confidence","reason","evidence"}:
        return None,"invalid_schema"
    id=answer["candidate_id"];confidence=answer["confidence"]
    if id is not None and (not isinstance(id,str) or id not in eligible):
        return None,"hard_constraint" if id in hard else "unknown_candidate"
    if not isinstance(confidence,(float,int)) or isinstance(confidence,bool) or not 0<=confidence<=1:
        return None,"invalid_schema"
    if not isinstance(answer["reason"],str) or not 0<len(answer["reason"])<=500:
        return None,"invalid_schema"
    if not isinstance(answer["evidence"],list) or len(answer["evidence"])>8 or any(not isinstance(s,str) or len(s)>200 for s in answer["evidence"]):
        return None,"invalid_schema"
    if id is None:return answer,"candidate_null"
    if confidence<policy.min_confidence:return answer,"low_confidence"
    return answer,None


class Judge:
    def __init__(self,provider:JudgeProvider,store,policy=None):
        self.provider=provider;self.store=store;self.policy=policy or JudgePolicy()

    def evaluate(self,group):
        if not self.provider.enabled or group["initial_state"]!="needs_review":return None
        evaluations=[e for d in group["foundation_decisions"] for e in d["evaluations"]]
        hard_codes={"season_conflict","duplicate_url_across_seasons"}
        hard={e["candidate_id"] for e in evaluations if e["hard_rejected"] or hard_codes&set(e["reason_codes"])}
        eligible={e["candidate_id"] for e in evaluations if e["candidate_id"] not in hard and e["title_method"]!="none"}
        candidates=[c for c in group["candidates"] if c["id"] in eligible]
        if not candidates:return None
        # LLM only arbitrates candidate ambiguity or an uncertain title; it does not
        # fill an unverified season boundary or create a mapping plan.
        if len(candidates)<2 and not ({"multiple_equivalent_candidates","low_confidence_title_match"}&set(group["reason_codes"])):
            return None
        payload={"schema_version":1,"targets":[_safe_entity(t,target=True) for t in group["targets"]],
            "candidates":[_safe_entity(c) for c in candidates],
            "crosswalk_plan":_safe_plan(group.get("proposed_plan",{}),eligible),
            "deterministic":{"reason_codes":group["reason_codes"],
                "evaluations":[{"candidate_id":e["candidate_id"],"reason_codes":e["reason_codes"],
                    "evidence":[safe for evidence in e["evidence"][:12] if (safe:=_safe_evidence(evidence))]
                    } for e in evaluations if e["candidate_id"] in eligible]}}
        key=digest({"input":payload,"provider":self.provider.name,"model":self.provider.model,"policy":self.policy.__dict__})
        base={"judge_version":"suggestion-1","provider":self.provider.name,"model":self.provider.model,"input_hash":key,"input_snapshot":payload,
            "suggested_candidate_id":None,"confidence":None,"reason":None,"evidence":[],"status":"needs_review","fallback":None}
        serialized=encode(payload)
        if "://" in serialized:
            base["input_snapshot"]={"schema_version":1,"redacted":True,"reason_codes":group["reason_codes"]}
            base["fallback"]="sensitive_input"
            return base
        if len(candidates)>self.policy.max_candidates:base["fallback"]="candidate_limit";return base
        if len(serialized.encode("utf-8"))>self.policy.max_payload_bytes:base["fallback"]="payload_limit";return base
        cached=self.store.judge_cache_get(key)
        if cached:return cached
        try:
            raw=self.provider.generate(payload,self.policy)
            answer,fallback=_validate_answer(raw,eligible,hard,self.policy)
            if answer:
                base.update(confidence=answer["confidence"],reason=answer["reason"],evidence=answer["evidence"])
            if fallback:base["fallback"]=fallback
            else:base.update(status="suggested",suggested_candidate_id=answer["candidate_id"])
            # Valid model responses (including null/uncertain) cache across scans.
            if answer or fallback in {"invalid_json","invalid_schema","unknown_candidate","hard_constraint","output_limit"}:
                self.store.judge_cache_put(key,base)
        except TimeoutError:base["fallback"]="timeout"
        except ProviderError as exc:
            base["fallback"]="payload_limit" if str(exc)=="payload_limit" else "provider_error"
            base["error_code"]=str(exc) if str(exc) in {"key_unavailable","remote_unavailable","malformed_response","response_limit","output_limit"} else "provider_error"
        except Exception:base["fallback"]="provider_error"
        return base
