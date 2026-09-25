"""Read-only per-target replay. JSON stdout and Markdown are derived reports only."""
import argparse
from collections import Counter
import hashlib
import json
from .shadow import ROOT, read_local_json, compare_v3
from .models import Target, Candidate, Review
from .matching import match

def compare_dataset(dataset):
    records=[]
    for index,case in enumerate(dataset["cases"]):
        target=Target.from_dict(case["target"])
        candidates=tuple(Candidate.from_dict(c) for c in case["candidates"])
        decision=match(target,candidates)
        status,url=compare_v3(target,candidates)
        selected=next((c for c in candidates if c.id==decision.selected_candidate_id),None)
        changed=url!=(selected.url if selected else None) or (status=="matched")!=(decision.outcome=="matched")
        review=Review.from_decision(f"shadow:{index}",target,candidates,decision) if decision.outcome!="matched" else None
        records.append({"cohort":case["cohort"],"target":target.canonical_title,"season":target.season_number,"v3":{"basis":"pure replay, not historical decision","status":status,"url":url},"v4":decision.to_dict(),"selected":selected.to_dict() if selected else None,"excluded":[{"candidate":c.to_dict(),"evaluation":next(e for e in decision.to_dict()["evaluations"] if e["candidate_id"]==c.id)} for c in candidates if not selected or c.id!=selected.id],"different":changed,"review_required":review is not None,"review_snapshot":review.to_dict() if review else None,"ground_truth":case.get("ground_truth"),"expected_url":next((c.url for c in candidates if c.id==case.get("ground_truth")),None)})
    candidates={(c["id"],c["url"]) for case in dataset["cases"] for c in case["candidates"]}
    series={case["target"]["canonical_title"] for case in dataset["cases"]}
    truth=[r for r in records if r["ground_truth"] is not None]
    incorrect=[r for r in truth if r["selected"] and r["selected"]["id"]!=r["ground_truth"]]
    abstained=[r for r in truth if not r["selected"]]
    return {"mode":"read_only_shadow","source_sha256":dataset["source_sha256"],"limitations":dataset["limitations"],"counts":{"series":len(series),"targets":len(records),"distinct_candidates":len(candidates),"distinct_candidate_ids":len({id for id,url in candidates}),"distinct_urls":len({url for id,url in candidates}),"cohorts":dict(Counter(r["cohort"] for r in records)),"v3_matched":sum(r["v3"]["status"]=="matched" for r in records),"v4_matched":sum(r["v4"]["outcome"]=="matched" for r in records),"different":sum(r["different"] for r in records),"reviews":sum(r["review_required"] for r in records),"synthetic_ground_truth_cases":len(truth),"synthetic_wrong_selections":len(incorrect),"synthetic_abstentions":len(abstained),"v3_synthetic_wrong_selections":sum(r["v3"]["url"] is not None and r["v3"]["url"]!=r["expected_url"] for r in truth),"v3_synthetic_abstentions":sum(r["v3"]["url"] is None for r in truth)},"targets":records}

def markdown(report):
    def clean(value):
        return str(value).replace("|",r"\|").replace("\n"," ")
    lines=["# V4 read-only shadow comparison","",json.dumps(report["counts"],ensure_ascii=False),""]
    lines.extend("- "+s for s in report["limitations"])
    for r in report["targets"]:
        lines.extend(["",f"## {clean(r['target'])} / S{r['season']} ({r['cohort']})","",f"V3 replay: {r['v3']['status']} / {r['v3']['url'] or 'null'}",f"V4: {r['v4']['outcome']} / {r['selected']['canonical_title'] if r['selected'] else 'null'}",f"Difference: {r['different']}; review required: {r['review_required']}","Reasons: "+", ".join(r["v4"]["reason_codes"]),""])
        for e in r["v4"]["evaluations"]:
            lines.append(f"- Candidate {e['candidate_id']}: hard reject={e['hard_rejected']}; reasons={','.join(e['reason_codes'])}")
            for evidence in e["evidence"]:
                lines.append("  - "+clean(f"{evidence['signal']}: {evidence['outcome']}; expected={evidence['expected']}; observed={evidence['observed']}; source={evidence['source']}; {evidence['detail']}"))
    return "\n".join(lines)+"\n"

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",required=True)
    parser.add_argument("--report",help="Optional Markdown report inside V4 work/")
    args=parser.parse_args(argv)
    input_path=(ROOT/args.dataset).resolve()
    before=hashlib.sha256(input_path.read_bytes()).hexdigest()
    report=compare_dataset(read_local_json(args.dataset))
    if args.report:
        path=(ROOT/args.report).resolve()
        if not path.is_relative_to(ROOT/"work") or path==input_path:
            raise ValueError("Report must be inside V4 work/ and separate from input")
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(markdown(report),encoding="utf-8")
    if before!=hashlib.sha256(input_path.read_bytes()).hexdigest():
        raise RuntimeError("Shadow input changed")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
