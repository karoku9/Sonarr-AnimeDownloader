"""Against the explicit V4 loopback runtime only; never calls V3 or Sonarr."""
import argparse
import json
import time
import urllib.request


def call(base,method,path,body=None):
    raw=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request(base+path,data=raw,method=method,headers={"Content-Type":"application/json"} if raw else {})
    with urllib.request.urlopen(request,timeout=10) as response:
        result=response.read()
        return response.status,json.loads(result) if path.startswith("/api/") else result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base",default="http://127.0.0.1:6004")
    parser.add_argument("--scan",action="store_true")
    parser.add_argument("--verify-decisions",action="store_true")
    args=parser.parse_args()
    assert args.base=="http://127.0.0.1:6004", "Smoke must target the V4 loopback container"
    assert call(args.base,"GET","/")[0]==200
    overview=call(args.base,"GET","/api/v4/overview")[1]
    assert overview["meta"]["contract_version"]=="4.1"
    assert overview["data"]["mode"]=="shadow" and overview["data"]["external_writes"] is False
    settings=call(args.base,"GET","/api/v4/settings")[1]["data"]
    assert settings["downloads_enabled"] is False and settings["external_writes_enabled"] is False and settings["llm_enabled"] is False
    for path in ("/api/v4/mappings","/api/v4/reviews","/api/v4/activity","/api/v4/scans"):
        assert call(args.base,"GET",path)[0]==200
    if args.scan:
        status,envelope=call(args.base,"POST","/api/v4/scans",{"dataset":"production"})
        assert status==202
        job=envelope["data"]
        states=[job["status"]]
        deadline=time.monotonic()+30
        while job["status"] not in {"completed","failed"} and time.monotonic()<deadline:
            time.sleep(.1)
            job=call(args.base,"GET","/api/v4/scans/"+job["job_id"])[1]["data"]
            states.append(job["status"])
        assert job["status"]=="completed",job
        assert job["external_writes"] is False
        print("scan",status,"->",'/'.join(states[:12]),"->",job["status"],job["result"]["target_count"],"targets")
    overview=call(args.base,"GET","/api/v4/overview")[1]["data"]
    print("overview",overview["service_status"],overview["current_target_count"],"targets",overview["mapping_counts"],overview["open_reviews"],"open reviews")
    if args.verify_decisions:
        items=call(args.base,"GET","/api/v4/mappings?limit=100")[1]["data"]["items"]
        crystal=next(item for item in items if item["title"]=="Sailor Moon Crystal" and len(item["targets"])==2)
        attack=next(item for item in items if item["title"]=="Attack on Titan")
        assert crystal["mapping_state"]=="approved" and crystal["review_state"]=="resolved"
        assert attack["mapping_state"]=="rejected" and attack["review_state"]=="rejected"
        timeline=call(args.base,"GET","/api/v4/activity?limit=100")[1]["data"]["items"]
        assert any(e["action"]=="approve" and e["actor"]=="local-user" and e["entity_id"]==crystal["id"] for e in timeline)
        assert any(e["action"]=="reject" and e["actor"]=="local-user" and e["entity_id"]==attack["id"] for e in timeline)
        for item in (crystal,attack):
            detail=call(args.base,"GET","/api/v4/reviews/"+item["id"])[1]["data"]
            raw=call(args.base,"GET","/api/v4/advanced/reviews/"+item["id"]+"/evidence")[1]["data"]
            assert raw["original_snapshot"]["targets"] and raw["original_snapshot"]["candidates"]
            assert raw["snapshot_digest"] and detail["human_decision"]
            print("decision",item["title"],item["id"],item["mapping_state"],item["review_state"],raw["snapshot_digest"],raw["created_at"])


if __name__=="__main__":main()
