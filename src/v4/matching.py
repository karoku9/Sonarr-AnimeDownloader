"""Pure deterministic matcher. No network, logging, persistence or legacy imports."""
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Iterable
from .models import SourceMetadata, Audio, Candidate, Decision, Evaluation, Evidence, ReasonCode as R, Target
from .normalization import normalize_title
from .provenance import MatcherPolicy, FieldProvenance

def canonical_url(url: str) -> str:
    # Preserve query/path identifiers; only strip non-identifying fragments.
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))

def titles(metadata: Target | Candidate, season: int | None) -> list[tuple[str, int, str]]:
    result = [(metadata.canonical_title, 2, metadata.source.name)]
    for alias in metadata.alternate_titles:
        if not alias.source.reliable:
            continue
        scope = alias.scope()
        if scope is not None and scope != season:
            continue
        # Scene aliases in unknown numbering systems cannot certify scope.
        if alias.scene_season_number not in (None,-1) and alias.scene_namespace != "sonarr" and alias.season_number is None:
            continue
        result.append((alias.text, 3 if scope is not None else 2, alias.source.name))
    return result

def coverage_allows_shared(c: Candidate, requested: int | None) -> bool:
    needed = set(c.mapped_seasons) | {requested}
    if not c.trusted("season_coverage"):
        return False
    rows = c.season_coverage
    if len({s for s,_,_ in rows}) != len(rows):
        return False
    if not needed.issubset({s for s,_,_ in rows}):
        return False
    ordered = sorted(rows, key=lambda row: row[1])
    return all(start >= 1 and end >= start for _,start,end in rows) and all(a[2] < b[1] for a,b in zip(ordered,ordered[1:]))

def evaluate(target: Target, c: Candidate, policy: MatcherPolicy) -> Evaluation:
    evidence = []
    reasons = []
    hard = False
    def signal(name, outcome, expected, observed, detail, code=None):
        field_name={"season":"season_number","identity":"external_ids","release_date":"premiere_date" if c.premiere_date else "air_date" if c.air_date else "release_year","release_year":"release_year"}.get(name,name)
        origin=c.provenance(field_name)
        source=origin.source if origin else c.source
        evidence.append(Evidence(name,outcome,str(expected) if expected is not None else None,
                                 str(observed) if observed is not None else None,source.name + (":" + source.record_id if source.record_id else ""),detail + (f"; field reliability={origin.reliable}, confidence={origin.confidence}, derived={origin.derived}" if origin else "")))
        if code and code not in reasons:
            reasons.append(code)
    try:
        url = urlsplit(c.url)
        valid_url = url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        valid_url = False
    if not valid_url:
        signal("candidate_url","invalid", "absolute HTTP(S) URL", None, "Missing or invalid candidate URL",R.INSUFFICIENT_METADATA)
        hard = True
    expected_ids = dict(target.external_ids); observed_ids = dict(c.external_ids)
    common = set(expected_ids) & set(observed_ids)
    identity = 0
    if c.trusted("external_ids") and target.trusted("external_ids"):
        for namespace in sorted(common):
            if expected_ids[namespace] != observed_ids[namespace]:
                signal("identity","conflict",expected_ids[namespace],observed_ids[namespace],namespace,R.IDENTITY_CONFLICT)
                hard = True
            else:
                identity = 1
                signal("identity","match",expected_ids[namespace],observed_ids[namespace],namespace)
    season_verified = False
    allowed = c.target_seasons if c.target_seasons else ((c.season_number,) if c.season_namespace == target.season_namespace and c.season_number is not None else ())
    if allowed and c.trusted("target_seasons" if c.target_seasons else "season_number") and c.trusted("season_namespace") and target.trusted("season_namespace") and target.trusted("season_number") and target.season_number is not None:
        if target.season_number not in allowed:
            signal("season","conflict",target.season_number,allowed,"Candidate belongs to another target season",R.SEASON_CONFLICT)
            hard = True
        else:
            season_verified = True
            signal("season","match",target.season_number,allowed,"Comparable season metadata or explicit crosswalk")
    else:
        signal("season","unknown",target.season_number,c.season_number,"No reliable comparable season metadata",R.INSUFFICIENT_METADATA)
    all_walks=(*target.crosswalks,*c.crosswalks)
    verified_walks=[cw for cw in all_walks if cw.applicable(c)]
    for cw in all_walks:
        if not cw.applicable(c):
            evidence.append(Evidence("season_crosswalk","unknown",str(cw.sonarr_season),str(c.season_number),cw.source.name+":"+cw.id if cw.source else "unknown:"+cw.id,"Unverified or missing/conflicting/untrusted anchors; "+cw.reason))
    if verified_walks and target.season_number is not None:
        mapped={cw.sonarr_season for cw in verified_walks}
        for cw in verified_walks:
            evidence.append(Evidence("season_crosswalk","match" if cw.sonarr_season==target.season_number else "conflict",str(target.season_number),str(cw.sonarr_season),cw.source.name+":"+cw.id,cw.reason))
        if mapped!={target.season_number}:
            reasons.append(R.SEASON_CONFLICT)
            hard=True
        elif target.trusted("season_number") and not hard:
            season_verified=True
            reasons=[r for r in reasons if r!=R.INSUFFICIENT_METADATA]
    canonical_key = normalize_title(c.canonical_title)
    # A trusted Sonarr season alias identifying the candidate cannot leak globally.
    excluded_scopes = {a.scope() for a in target.alternate_titles if a.source.reliable and a.scope() is not None and normalize_title(a.text) == canonical_key}
    if excluded_scopes and target.season_number not in excluded_scopes:
        signal("season_alias","conflict",target.season_number,sorted(excluded_scopes),"Canonical title is an alias for another season",R.SEASON_CONFLICT)
        hard = True
    relevant_scopes = {a.scope() for a in target.alternate_titles if a.source.reliable and a.scope() is not None and target.season_number is not None and a.scope() == target.season_number and normalize_title(a.text)==canonical_key}
    if relevant_scopes and not hard:
        season_verified = True
        reasons = [r for r in reasons if r != R.INSUFFICIENT_METADATA]
        signal("season_alias","match",target.season_number,sorted(relevant_scopes),"Trusted target alias identifies candidate season")
    shared = set(c.mapped_seasons)-{target.season_number}
    if shared and not coverage_allows_shared(c,target.season_number):
        signal("url_coverage","conflict",target.season_number,sorted(c.mapped_seasons),"URL belongs to other seasons without disjoint explicit episode ranges",R.DUPLICATE_URL_ACROSS_SEASONS)
    date_rank = 0
    tdate = target.premiere_date or target.air_date
    cdate = c.premiere_date or c.air_date
    tyear = tdate.year if tdate else target.release_year
    cyear = cdate.year if cdate else c.release_year
    if target.date_kind == c.date_kind and target.trusted("date_kind") and c.trusted("date_kind") and target.trusted("premiere_date" if target.premiere_date else "air_date" if target.air_date else "release_year") and c.trusted("premiere_date" if c.premiere_date else "air_date" if c.air_date else "release_year"):
        if tdate and cdate:
            if abs((tdate-cdate).days)>policy.date_tolerance_days:
                signal("release_date","conflict",tdate,cdate,"Comparable first-release dates differ",R.RELEASE_DATE_CONFLICT)
            else:
                date_rank = 2
                signal("release_date","match",tdate,cdate,f"Dates agree within {policy.date_tolerance_days} days")
        elif tyear is not None and cyear is not None:
            if tyear != cyear:
                signal("release_year","conflict",tyear,cyear,"Different release years; requires metadata review",R.RELEASE_DATE_CONFLICT)
            else:
                date_rank = 1
                signal("release_year","match",tyear,cyear,"Coarse release-year support")
        else:
            signal("release_date","unknown",tdate or target.release_year,cdate or c.release_year,"Missing date/year is not a hard rejection")
    else:
        signal("release_date","unknown",target.date_kind,c.date_kind,"Date kinds or source reliability are not comparable")
    episode_rank = 0
    if target.episode_count is not None and c.episode_count is not None and target.trusted("episode_count") and c.trusted("episode_count"):
        if target.episode_scope == c.episode_scope and target.episodes_complete and c.episodes_complete and all(m.trusted(f) for m in (target,c) for f in ("episode_scope","episodes_complete")):
            if target.episode_count != c.episode_count:
                signal("episode_count","conflict",target.episode_count,c.episode_count,"Complete releases with equal coverage scope differ",R.EPISODE_COUNT_CONFLICT)
            else:
                episode_rank=1
                signal("episode_count","match",target.episode_count,c.episode_count,"Complete comparable counts agree")
        else:
            signal("episode_count","unknown",target.episode_count,c.episode_count,"Partial counts or differing coverage scopes")
    else:
        signal("episode_count","unknown",target.episode_count,c.episode_count,"Episode metadata unavailable")
    tier = 0
    for title,tstrength,tsource in titles(target,target.season_number):
        key = normalize_title(title)
        for other,cstrength,csource in titles(c,target.season_number):
            if key and key==normalize_title(other):
                strength=max(tstrength,cstrength)
                tier=max(tier,strength)
                signal("title_alias" if strength==3 or title!=target.canonical_title or other!=c.canonical_title else "exact_title","match",title,other,f"Unicode exact key; alias source {tsource}/{csource}")
    method = "exact" if tier else "none"
    return Evaluation(c.id,hard,tuple(reasons),tuple(evidence),(identity,int(season_verified),int(tier==3),date_rank,episode_rank,tier,0.0),method,season_verified)

def match(target: Target, candidates: Iterable[Candidate], *, fuzzy_threshold: float | None = None, policy: MatcherPolicy | None = None) -> Decision:
    policy=policy or MatcherPolicy()
    fuzzy_threshold=policy.fuzzy_threshold if fuzzy_threshold is None else fuzzy_threshold
    candidates=tuple(sorted(candidates,key=lambda c:c.id))
    if len({c.id for c in candidates})!=len(candidates):
        raise ValueError("Candidate IDs must be unique")
    if not 0 <= fuzzy_threshold <= 1:
        raise ValueError("Invalid fuzzy threshold")
    def decide(*args):
        return Decision(*args,fuzzy_threshold=fuzzy_threshold,date_tolerance_days=policy.date_tolerance_days)
    by_id={c.id:c for c in candidates}
    # Derive URL usage within this snapshot in addition to existing mapping metadata.
    usage = {}
    for c in candidates:
        seasons = set(c.target_seasons) | set(c.mapped_seasons)
        if c.season_namespace == target.season_namespace and c.season_number is not None:
            seasons.add(c.season_number)
        try:
            key = canonical_url(c.url)
        except ValueError:
            key = c.url
        usage.setdefault(key,set()).update(seasons)
    evaluations=[]
    for c in candidates:
        try:
            key = canonical_url(c.url)
        except ValueError:
            key = c.url
        mapped=tuple(sorted(set(c.mapped_seasons)|usage[key]))
        derived=replace(c,mapped_seasons=mapped,field_provenance=tuple(p for p in c.field_provenance if p.field!="mapped_seasons")+(FieldProvenance.create("mapped_seasons",mapped,SourceMetadata("derived_url_usage",reliable=False),derived=True),))
        evaluations.append(evaluate(target,derived,policy))
    blocking={R.RELEASE_DATE_CONFLICT,R.EPISODE_COUNT_CONFLICT,R.DUPLICATE_URL_ACROSS_SEASONS}
    strong=any(not e.hard_rejected and e.season_verified and e.title_method=="exact" and not blocking.intersection(e.reason_codes) for e in evaluations)
    if not strong:
        for i,e in enumerate(evaluations):
            if e.hard_rejected or e.title_method=="exact":
                continue
            c=by_id[e.candidate_id]
            ratio=max((SequenceMatcher(None,normalize_title(a),normalize_title(b)).ratio()
                       for a,_,_ in titles(target,target.season_number) for b,_,_ in titles(c,target.season_number)
                       if min(len(normalize_title(a)),len(normalize_title(b)))>=4),default=0.0)
            if ratio >= fuzzy_threshold:
                evidence=e.evidence+(Evidence("fuzzy_title","weak",target.canonical_title,c.canonical_title,c.source.name,f"Last-resort similarity {ratio:.4f}; review only"),)
                evaluations[i]=replace(e,title_method="fuzzy",rank=e.rank[:-2]+(1,round(ratio,6)),evidence=evidence,reason_codes=e.reason_codes+(R.LOW_CONFIDENCE_TITLE_MATCH,))
    possible=[e for e in evaluations if not e.hard_rejected and e.title_method!="none" and not blocking.intersection(e.reason_codes)]
    if not possible:
        reasons={r for e in evaluations for r in e.reason_codes}
        if not reasons:
            reasons.add(R.INSUFFICIENT_METADATA if not candidates else R.NO_TITLE_MATCH)
        return decide("needs_review",None,tuple(sorted(reasons,key=lambda r:r.value)),tuple(evaluations))
    best_rank=max(e.rank for e in possible)
    best=[e for e in possible if e.rank==best_rank]
    selected=None
    reasons=set()
    if not all(e.season_verified for e in best):
        reasons.add(R.INSUFFICIENT_METADATA)
    if any(e.title_method=="fuzzy" for e in best):
        reasons.add(R.LOW_CONFIDENCE_TITLE_MATCH)
    if len(best)>1:
        # Same URL means a single resource only when metadata agree, not a license
        # to merge conflicting release identities or audio declarations.
        entries=[by_id[e.candidate_id] for e in best]
        same_resource=len({canonical_url(c.url) for c in entries})==1 and len({(c.source.name,c.release_id,c.audio) for c in entries})==1
        same_release=all(c.release_id and c.trusted("release_id") and c.trusted("audio") for c in entries) and len({(c.source.name,c.release_id) for c in entries})==1
        if same_resource:
            selected=min(c.id for c in entries)
        elif same_release and target.audio_preference!=Audio.UNKNOWN:
            preferred=[c for c in entries if c.audio==target.audio_preference]
            if len(preferred)==1:
                selected=preferred[0].id
                for i,e in enumerate(evaluations):
                    if e.candidate_id==selected:
                        evaluations[i]=replace(e,evidence=e.evidence+(Evidence("audio_tiebreak","preferred",target.audio_preference.value,preferred[0].audio.value,preferred[0].source.name,"Equivalent verified release; preferred audio selected after all identity/title/metadata ranks"),))
        if selected is None:
            reasons.add(R.MULTIPLE_EQUIVALENT_CANDIDATES)
    else:
        selected=best[0].candidate_id
    if reasons:
        return decide("needs_review",None,tuple(sorted(reasons,key=lambda r:r.value)),tuple(evaluations))
    if target.audio_preference!=Audio.UNKNOWN and by_id[selected].audio!=target.audio_preference:
        reasons.add(R.LANGUAGE_MISMATCH)
        for i,e in enumerate(evaluations):
            if e.candidate_id==selected:
                evaluations[i]=replace(e,evidence=e.evidence+(Evidence("audio_preference","informational_mismatch",target.audio_preference.value,by_id[selected].audio.value,by_id[selected].source.name,"Better title retained; audio preference cannot override identity"),))
    return decide("matched",selected,tuple(sorted(reasons,key=lambda r:r.value)),tuple(evaluations))
