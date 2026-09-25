"""Regenerate stable frontend DTO fixtures from sanitized real source, offline."""
import hashlib,json,sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.v4.api import create_app
SOURCE=ROOT/"tests/v4/fixtures/production_metadata_v1.json"
TARGETS={"black":"70:1","crystal":"189:1","nadia":"182:1","ranma":"143:1"}
with TemporaryDirectory(dir=ROOT/"work") as temporary:
    app=create_app(Path(temporary)/"samples.sqlite3",{"real":SOURCE})
    with patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("network forbidden")):app.service.scan("real")
    samples={}
    for name,tid in TARGETS.items():
        item=next(i for i in app.service.store.list_items() if tid in {t["target_id"] for t in i["original"]["targets"]})
        dto=app.service.detail(item["id"]);dto["id"]="contract-"+name
        dto["created_at"]=dto["updated_at"]="2026-09-14T00:00:00Z"
        samples[name]=dto
HERE=Path(__file__).parent
(HERE/"contract-samples.json").write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding="utf-8")
(HERE/"sample-manifest.json").write_text(json.dumps({"schema_version":1,"source":"tests/v4/fixtures/production_metadata_v1.json","sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),"targets":TARGETS,"derived":"application DTO only","sanitization":"Normalize generated opaque item ID and clock timestamps only; native semantic metadata unchanged; no raw URLs or credentials."},indent=2),encoding="utf-8")
