"""Whitelist-only local dataset builder; observations are never ground truth."""
import argparse
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit
from .shadow import ROOT, read_local_json, targets_from_sonarr
from .models import Target, Candidate, SourceMetadata

def public_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!="https" or parsed.hostname not in {"www.animeworld.tv","animeworld.tv","catalog.example"} or parsed.username or parsed.password:
        raise ValueError("Unexpected or credential-bearing candidate URL")
    return urlunsplit(("https",parsed.hostname,parsed.path,"",""))

def build_dataset():
    cases=[]; inputs={}
    def load(path):
        resolved=ROOT/path
        inputs[path]=hashlib.sha256(resolved.read_bytes()).hexdigest()
        return read_local_json(path)
    rows=load("docs/static/examples/json/table.json")
    for index,row in enumerate(rows):
        source=SourceMetadata("documentation_mapping",str(index),reliable=False)
        candidates=[]
        for season,urls in row.get("seasons",{}).items():
            for pos,url in enumerate(urls):
                candidates.append(Candidate(f"doc:{index}:{season}:{pos}",row["title"],public_url(url),season_namespace="catalog",mapped_seasons=(int(season),) if str(season).isdigit() else (),source=source))
        for season in row.get("seasons",{}):
            target=Target(row["title"],season_number=int(season) if str(season).isdigit() else None,source=source,target_id=f"doc:{index}:{season}")
            cases.append({"cohort":"documentation_observation","target":target.to_dict(),"candidates":[c.to_dict() for c in candidates],"ground_truth":None})
    for target in targets_from_sonarr(load("tests/dump/serie.json")):
        cases.append({"cohort":"sonarr_snapshot","target":target.to_dict(),"candidates":[],"ground_truth":None})
    for row in load("tests/dump/getAllMissing.json"):
        for season in row.get("seasons",[]):
            # Missing episodes are NOT total episode count. Paths, IDs and tags omitted.
            target=Target(row["title"],season_number=season["number"],source=SourceMetadata("sonarr_reduced_snapshot"),target_id=f"sonarr-reduced:{len(cases)}")
            cases.append({"cohort":"sonarr_snapshot","target":target.to_dict(),"candidates":[],"ground_truth":None})
    expected={"black_lagoon":"bl1","nadia":"nadia","sailor_moon":"sm2","ranma":"original"}
    for name in expected:
        fixture=load(f"tests/v4/fixtures/{name}.json")
        fixture["target"]["source"]={"name":"synthetic_fixture","record_id":name,"reliable":True}
        for c in fixture["candidates"]:
            c["source"]={"name":"synthetic_fixture","record_id":name,"reliable":True}
        target=Target.from_dict(fixture["target"])
        candidates=[Candidate.from_dict(c) for c in fixture["candidates"]]
        cases.append({"cohort":"synthetic_regression","target":target.to_dict(),"candidates":[c.to_dict() for c in candidates],"ground_truth":expected[name]})
    import copy
    black=copy.deepcopy(next(c for c in cases if c["target"]["canonical_title"]=="Black Lagoon"))
    black["target"]["season_number"]=2
    black["target"]["field_provenance"]=[]
    barrage=next(c for c in black["candidates"] if c["id"]=="bl2")
    barrage.update(season_number=1,season_namespace="catalog",release_id="synthetic-barrage",field_provenance=[])
    barrage["crosswalks"]=[{"id":"verified-provider-release","sonarr_season":2,"candidate_namespace":"catalog","candidate_season":1,"candidate_release_id":"synthetic-barrage","season_title":"Black Lagoon: The Second Barrage","verified":True,"source":{"name":"synthetic_manual_alias","reliable":True},"reason":"Fixture asserts independently verified provider release corresponds to Sonarr S2"}]
    black["ground_truth"]="bl2"
    cases.append(black)
    crystal=copy.deepcopy(next(c for c in cases if c["target"]["canonical_title"]=="Sailor Moon Crystal"))
    crystal["target"]["season_number"]=1
    crystal["target"]["field_provenance"]=[]
    crystal["ground_truth"]="sm1"
    cases.append(crystal)
    shared=copy.deepcopy(crystal)
    shared["target"]["season_number"]=2
    shared["ground_truth"]=None
    shared["expected_review"]=True
    for candidate in shared["candidates"]:
        candidate["url"]="https://catalog.example/crystal-shared"
    cases.append(shared)
    return {"schema_version":1,"source_sha256":inputs,"limitations":["Documentation titles label legacy mappings; they are not independent catalog release metadata.","Mandatory four titles have no real local snapshot and use explicitly synthetic fixtures.","V3 is pure replay, not recovered historical decisions."],"cases":cases}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    path=(ROOT/args.output).resolve()
    if not path.is_relative_to(ROOT/"work"):
        raise ValueError("Generated dataset must be inside V4 work/")
    dataset=build_dataset()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"cases":len(dataset["cases"]),"output":str(path)}))

if __name__=="__main__":
    main()
