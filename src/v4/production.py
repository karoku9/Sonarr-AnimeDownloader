"""Offline replay command; enrichment is explicit GET-only and never runs implicitly."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from .production_sources import local_output,fetch_detail
from .production_replay import snapshot,retrieve
from .production_metrics import report,markdown

def verify_capture(folder):
    manifest=json.loads((folder/"capture-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version")!=1:
        raise ValueError("Unsupported capture schema")
    for filename,expected in manifest["files_sha256"].items():
        path=(folder/filename).resolve()
        if path.parent!=folder.resolve() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError("Capture manifest drift or escaping source path")
    return manifest

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir",required=True)
    parser.add_argument("--details-dir",required=True)
    parser.add_argument("--output-dir",required=True)
    parser.add_argument("--enrich",action="store_true",help="Explicit public metadata GETs; no implicit refresh")
    parser.add_argument("--pools",help="Previously frozen V4-local candidate pools")
    args=parser.parse_args(argv)
    capture=local_output(args.capture_dir);details_folder=local_output(args.details_dir);output=local_output(args.output_dir)
    if not output.is_relative_to(local_output("work")) or not details_folder.is_relative_to(local_output("work")):
        raise ValueError("Generated capture/replay artifacts stay under ignored V4 work/")
    capture_manifest=verify_capture(capture)
    def load(name):return json.loads((capture/(name+".json")).read_text(encoding="utf-8"))
    series=load("sonarr-series");episodes=load("sonarr-episodes");catalog_doc=load("catalog");catalog=catalog_doc["entries"];mappings=load("v3-mappings");manual=load("manual-aliases");language_policy=load("v3-language-policy")
    pools=json.loads(local_output(args.pools).read_text(encoding="utf-8")) if args.pools else {str(r["id"]):retrieve(r,catalog,mappings) for r in series if r.get("seriesType")=="anime"}
    current={e["url"]:e for e in catalog}
    for pool in pools.values():
        if any(current.get(e["url"])!=e for e in pool):raise ValueError("Frozen pool drift against captured catalog")
    urls=sorted({e["url"] for pool in pools.values() for e in pool})
    details_folder.mkdir(parents=True,exist_ok=True)
    def enrich(url):
        path=details_folder/(hashlib.sha256(url.encode()).hexdigest()+".json")
        if path.exists():return
        detail=fetch_detail(url)
        path.write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding="utf-8")
    if args.enrich:
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(enrich,urls))
    details={}
    for url in urls:
        path=details_folder/(hashlib.sha256(url.encode()).hexdigest()+".json")
        details[url]=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status":"not_captured","raw":{"url":url}}
    native=snapshot(series,episodes,catalog,mappings,details,manual,pools=pools)
    # Preserve the complete public provider catalog so metadata discovered later can
    # trigger a second read-only candidate retrieval pass instead of being trapped
    # by the initial Sonarr-title-only pool.
    native["provider_catalog"]={"complete":catalog_doc.get("catalog_complete") is True,
        "entries":[dict(e) for e in catalog]}
    if language_policy.get("default")!="SUB" or not isinstance(language_policy.get("overrides"),dict):
        raise ValueError("Captured V3 language policy is invalid")
    native["language_policy"]=language_policy
    native["capture_manifest"]=capture_manifest
    native["captured_at"]=native["capture_manifest"]["completed_at"]
    native["sources_sha256"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in capture.glob("*.json")}
    native["enrichment_counts"]={status:sum(d.get("status")==status for d in details.values()) for status in sorted({d.get("status") for d in details.values()})}
    replay=report(native)
    output.mkdir(parents=True,exist_ok=True)
    for name,data in (("production-snapshot.json",native),("production-report.json",replay)):
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    (output/"production-report.md").write_text(markdown(replay),encoding="utf-8")
    print(json.dumps({"counts":replay["counts"],"rates":replay["rates"],"enrichment":native["enrichment_counts"]},indent=2))

if __name__=="__main__":main()
