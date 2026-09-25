"""Read-only comparison of V4 against V3 pure ranking on existing V4-local JSON.
Never constructs SearchV3Service/Core/Table or contacts external services.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
from urllib.parse import urlsplit
from .matching import match
from .models import Alias, Audio, Candidate, SourceMetadata, Target

ROOT = Path(__file__).resolve().parents[2]

def read_local_json(path: str):
    resolved = (ROOT / path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("Shadow input must be inside the V4 worktree")
    return json.loads(resolved.read_text(encoding="utf-8"))

def targets_from_sonarr(payload):
    series = payload if isinstance(payload,list) else [payload]
    targets=[]
    for item in series:
        source=SourceMetadata("sonarr",str(item.get("id","snapshot")))
        aliases=tuple(Alias(str(a["title"]),language=a.get("language"),kind="sonarr_alternate",
                            season_number=a.get("seasonNumber"),scene_season_number=a.get("sceneSeasonNumber"),
                            source=source) if isinstance(a,dict) else Alias(a,source=source)
                      for a in item.get("alternateTitles",[]) if isinstance(a,str) or a.get("title"))
        for record in item.get("seasons",[]):
            season=record.get("seasonNumber")
            if season is None or season==0:
                continue
            stats=record.get("statistics",{})
            targets.append(Target(str(item["title"]),season_number=int(season),alternate_titles=aliases,
                                  release_year=item.get("year") if season==1 else None,
                                  episode_count=stats.get("totalEpisodeCount"),episodes_complete=False,
                                  source=source,target_id=str(item.get("id","snapshot"))+":"+str(season)))
    return tuple(targets)

def candidates_from_catalog(payload, format):
    if format=="v4":
        entries=payload.get("entries",[]) if isinstance(payload,dict) else payload
        return tuple(Candidate.from_dict(c) for c in entries)
    entries=[]
    if format=="legacy-table":
        for row in payload:
            for season,urls in row.get("seasons",{}).items():
                for url in urls:
                    # Existing assignments are observations, not verified release metadata.
                    entries.append({"title":row["title"],"url":url,"table_seasons":[season],"source":"legacy_table"})
    else:
        entries=payload.get("entries",[]) if isinstance(payload,dict) else payload
    candidates=[]
    for index,item in enumerate(entries):
        if not item.get("url") or not item.get("title"):
            continue
        seasons=tuple(int(s) for s in item.get("table_seasons",[]) if str(s).isdigit())
        source=SourceMetadata(str(item.get("source","legacy_index")),str(index),reliable=False)
        audio=item.get("audio","UNKNOWN")
        candidates.append(Candidate(str(index),item["title"],item["url"],season_number=item.get("season_number"),
                                    season_namespace="catalog",target_seasons=(),mapped_seasons=seasons,
                                    release_year=item.get("year"),audio=Audio(audio) if audio in Audio._value2member_map_ else Audio.UNKNOWN,
                                    alternate_titles=tuple(Alias(a,source=source) for a in item.get("aliases",[]) if isinstance(a,str)),source=source))
    return tuple(candidates)

def compare_v3(target, candidates):
    # Load only pure V3 modules under an isolated package to bypass components/__init__.
    name="anidown_v4_shadow_legacy"
    if name not in sys.modules:
        package=types.ModuleType(name)
        package.__path__=[str(ROOT/"src/components/backend/search_v3")]
        sys.modules[name]=package
    variants=importlib.import_module(name+".variants")
    scorer=importlib.import_module(name+".scorer")
    language=importlib.import_module(name+".language")
    service=importlib.import_module(name+".service")
    series={"title":target.canonical_title,"alternateTitles":[{"title":a.text,"seasonNumber":a.season_number,"sceneSeasonNumber":a.scene_season_number} for a in target.alternate_titles]}
    built=variants.TitleVariantBuilder().build(series,season=target.season_number)
    entries=[]
    for c in candidates:
        slug=urlsplit(c.url).path.rsplit("/",1)[-1]
        entries.append({"title":c.canonical_title,"url":c.url,"normalized_title":variants.normalize_title(c.canonical_title),
                        "normalized_slug":variants.normalize_title(slug),"slug":slug,"audio":c.audio.value,
                        "aliases":[a.text for a in c.alternate_titles],"table_seasons":[str(s) for s in c.mapped_seasons],
                        "source":"table" if c.source.name in ("legacy_table","documentation_mapping") else c.source.name})
    results=scorer.CandidateScorer().score_all(entries,built["normalized_variants"],[],[],sonarr_aliases=built["sonarr_aliases"],season_aliases=built["season_aliases"])
    results,_=service.SearchV3Service.filter_candidates_for_season(None,results,target.season_number)
    preference={Audio.SUB:"SUB_FIRST",Audio.DUB:"DUB_FIRST",Audio.UNKNOWN:"AUTO"}[target.audio_preference]
    ranked=language.LanguagePreferenceResolver().apply(results,preference,"AUTO")
    selected,status=service.SearchV3Service.select_candidate(None,ranked[:12],preference)
    return status,selected.get("url") if selected else None

def compare(targets,candidates):
    counters={"targets":len(targets),"candidates":len(candidates),"v3_matched":0,"v4_matched":0,"v4_needs_review":0,"different_selection":0}
    reasons={}
    for target in targets:
        decision=match(target,candidates)
        status,v3url=compare_v3(target,candidates)
        counters["v3_matched"]+=int(status=="matched")
        counters["v4_matched"]+=int(decision.outcome=="matched")
        counters["v4_needs_review"]+=int(decision.outcome=="needs_review")
        v4url=next((c.url for c in candidates if c.id==decision.selected_candidate_id),None)
        counters["different_selection"]+=int(v3url!=v4url)
        for code in decision.reason_codes:
            reasons[code.value]=reasons.get(code.value,0)+1
    return {"mode":"read_only_shadow","comparison_basis":"replay V3 pure ranking on supplied candidate snapshot; not historical persisted decisions",
            "counts":counters,"review_reasons":reasons}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sonarr-series",required=True,help="V4-local Sonarr series snapshot JSON")
    parser.add_argument("--catalog",required=True,help="V4-local catalog JSON")
    parser.add_argument("--catalog-format",choices=["v4","legacy-table","legacy-index"],default="legacy-index")
    args=parser.parse_args(argv)
    payload=read_local_json(args.sonarr_series);catalog=read_local_json(args.catalog)
    result=compare(targets_from_sonarr(payload),candidates_from_catalog(catalog,args.catalog_format))
    result["input_sha256"]={"series":hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),"catalog":hashlib.sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()}
    print(json.dumps(result,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
