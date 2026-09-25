"""Strict Gemini verification gate for deterministic V4 proposals.

The verifier can only demote an automatic proposal to Review. It never promotes
an unresolved deterministic result.
"""
import json,os,socket,time
from urllib import request,error
from .application_store import digest

class DisabledVerifier:
    enabled=False
    def evaluate_many(self,groups):return [None for _ in groups]

class GeminiSearchVerifier:
    enabled=True
    def __init__(self,*,key=None,model="gemini-3.1-flash-lite",min_confidence=.95,
            batch_size=6,timeout_seconds=30.0,opener=None):
        if not model.startswith("gemini-"):raise ValueError("Invalid Gemini model")
        if not .5<=min_confidence<=1:raise ValueError("Invalid confidence threshold")
        self.key=key if key is not None else os.getenv("GEMINI_API_KEY")
        self.model=model;self.min_confidence=min_confidence
        self.batch_size=max(1,min(int(batch_size),10));self.timeout=timeout_seconds
        self.opener=opener or request.urlopen
        self._search_available=True

    @staticmethod
    def _summary(value):
        words=str(value or "").strip().split()
        return " ".join(words[:4]) if words else "Gemini non verificato"

    @staticmethod
    def _safe_group(group):
        candidates={c["id"]:c for c in group.get("candidates",[])}
        def plan(value):
            out=[]
            for segment in value.get("segments",[])[:8]:
                release=segment.get("release",{})
                out.append({"title":release.get("title"),
                    "variants":[{"title":candidates[i].get("canonical_title"),
                        "audio":candidates[i].get("audio"),
                        "episodes":candidates[i].get("episode_count"),
                        "date":candidates[i].get("premiere_date")}
                        for i in release.get("candidate_ids",[]) if i in candidates],
                    "coverage":segment.get("coverage",{}).get("destination_ranges"),
                    "source_range":segment.get("coverage",{}).get("source_range"),
                    "basis":segment.get("crosswalk",{}).get("basis"),
                    "reliability":segment.get("crosswalk",{}).get("reliability")})
            return out
        return {"key":"|".join(t["target_id"] for t in group.get("targets",[])),
            "state":group.get("initial_state"),"audio_preference":group.get("audio_preference"),
            "targets":[{"title":t.get("canonical_title"),"season":t.get("season_number"),
                "episodes":t.get("episode_count"),"aliases":[a.get("text") for a in t.get("alternate_titles",[])
                    if isinstance(a,dict) and a.get("text")][:12]} for t in group.get("targets",[])],
            "reason_codes":group.get("reason_codes",[]),
            "proposed_plan":plan(group.get("proposed_plan") or {}),
            "alternate_plans":[plan(p) for p in group.get("alternate_plans",[])[:4]]}
    def _request(self,items,*,use_search):
        if not self.key:raise RuntimeError("key_unavailable")
        schema={"type":"OBJECT","properties":{"audits":{"type":"ARRAY","items":{
            "type":"OBJECT","properties":{
                "key":{"type":"STRING"},
                "verdict":{"type":"STRING","enum":["confirm","review"]},
                "confidence":{"type":"NUMBER"},
                "summary":{"type":"STRING"}},
            "required":["key","verdict","confidence","summary"]}}},
            "required":["audits"]}
        search_text=("Google Search is available: use it only when public aliases, distributor labels, "
            "alternate Italian dubs, or naming conventions materially need verification. "
            if use_search else
            "No external search is available in this pass. Confirm only from supplied evidence; "
            "if external lookup would be needed to remove a real doubt, return review. ")
        prompt=("Audit each AniDown mapping independently. The deterministic resolver remains authoritative for hard constraints. "
            "For state=proposed, return confirm ONLY when title identity, exact season/cour, episode coverage and audio policy "
            "are all clearly correct. audio_preference is FIRST, not ONLY: prefer that homogeneous audio when it fully covers every mapped "
            "segment; otherwise falling back to the other homogeneous audio is valid. Never require the preferred audio when it cannot fully "
            "cover the plan. If there is any meaningful doubt, return review. For state=needs_review you MUST return review and only "
            "summarize the doubt. "+search_text+
            "CR can mean Crunchyroll and AG can mean Anime Generation, but never assume that fact when it affects correctness. "
            "The summary MUST be Italian and at most four words, such as 'doppiaggi italiani multipli', 'alias stagione non verificato', "
            "'copertura episodi ambigua', or 'mapping verificato'. Never invent missing episode boundaries.")
        body={"systemInstruction":{"parts":[{"text":prompt}]},
            "contents":[{"role":"user","parts":[{"text":json.dumps({"items":items},ensure_ascii=False,separators=(",",":"))}]}],
            "generationConfig":{"responseMimeType":"application/json","responseSchema":schema,
                "temperature":0,"maxOutputTokens":4096}}
        if use_search:body["tools"]=[{"google_search":{}}]
        req=request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=json.dumps(body,separators=(",",":")).encode(),method="POST",
            headers={"Content-Type":"application/json","x-goog-api-key":self.key})
        last=None
        for attempt in range(4):
            try:
                with self.opener(req,timeout=self.timeout) as response:env=json.load(response)
                candidate=env["candidates"][0]
                raw="".join(p.get("text","") for p in candidate["content"]["parts"])
                audits=json.loads(raw)["audits"]
                grounding=candidate.get("groundingMetadata") or {}
                return audits,grounding
            except error.HTTPError as exc:
                last=exc
                if exc.code==429 and use_search:raise RuntimeError("search_quota_unavailable") from exc
                if exc.code==429 or 500<=exc.code<600:
                    time.sleep(4*(attempt+1));continue
                raise RuntimeError(f"http_{exc.code}") from exc
            except (error.URLError,socket.timeout,TimeoutError) as exc:
                last=exc;time.sleep(2*(attempt+1))
            except (KeyError,ValueError,TypeError,json.JSONDecodeError) as exc:
                raise RuntimeError("malformed_response") from exc
        raise RuntimeError("remote_unavailable") from last

    def _call(self,items):
        search_mode="grounded"
        if self._search_available:
            try:return (*self._request(items,use_search=True),search_mode,None)
            except RuntimeError as exc:
                if str(exc)!="search_quota_unavailable":raise
                self._search_available=False
        search_mode="evidence_only"
        audits,grounding=self._request(items,use_search=False)
        return audits,grounding,search_mode,"search_quota_unavailable"

    def evaluate_many(self,groups):
        if not groups:return []
        safe=[self._safe_group(g) for g in groups];results=[]
        def append_result(item,a,grounding,search_mode,search_fallback):
            confidence=float(a.get("confidence",0))
            verdict=a.get("verdict") if a.get("verdict") in {"confirm","review"} else "review"
            confirmed=item["state"]=="proposed" and verdict=="confirm" and confidence>=self.min_confidence
            results.append({"schema_version":1,"provider":"gemini","model":self.model,
                "input_hash":digest({"item":item,"model":self.model,"policy":"strict-search-v3"}),
                "status":"confirmed" if confirmed else "review","verdict":verdict,
                "confidence":max(0,min(confidence,1)),"summary":self._summary(a.get("summary")),
                "search_queries":list((grounding or {}).get("webSearchQueries") or [])[:12],
                "search_mode":search_mode,"fallback":search_fallback})
        def append_failure(item,exc):
            results.append({"schema_version":1,"provider":"gemini","model":self.model,
                "input_hash":digest({"item":item,"model":self.model,"policy":"strict-search-v3"}),
                "status":"review","verdict":"review","confidence":0.0,
                "summary":"Gemini non verificato","search_queries":[],"search_mode":"failed",
                "fallback":str(exc)[:64]})
        for start in range(0,len(safe),self.batch_size):
            batch=safe[start:start+self.batch_size];expected={x["key"] for x in batch}
            try:
                audits,grounding,search_mode,search_fallback=self._call(batch)
                by={a.get("key"):a for a in audits if isinstance(a,dict)}
                if set(by)==expected:
                    for item in batch:append_result(item,by[item["key"]],grounding,search_mode,search_fallback)
                else:
                    # A malformed batch must not demote every item. Retry each
                    # target independently and fail closed only for the one that
                    # still cannot be audited.
                    for item in batch:
                        try:
                            one,g,mode,fallback=self._call([item])
                            if len(one)!=1 or one[0].get("key")!=item["key"]:raise RuntimeError("id_mismatch")
                            append_result(item,one[0],g,mode,fallback)
                        except Exception as exc:append_failure(item,exc)
                        time.sleep(.4)
                time.sleep(1.2)
            except Exception:
                for item in batch:
                    try:
                        one,g,mode,fallback=self._call([item])
                        if len(one)!=1 or one[0].get("key")!=item["key"]:raise RuntimeError("id_mismatch")
                        append_result(item,one[0],g,mode,fallback)
                    except Exception as exc:append_failure(item,exc)
                    time.sleep(.4)
        return results

def configured_verifier():
    choice=os.getenv("ANIDOWN_V4_VERIFY_PROVIDER","disabled").strip().lower()
    if choice=="disabled":return DisabledVerifier()
    if choice=="gemini":return GeminiSearchVerifier(model=os.getenv("ANIDOWN_V4_GEMINI_MODEL","gemini-3.1-flash-lite"))
    raise ValueError("Unknown V4 verifier")
