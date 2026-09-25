"""AniDown V4 loopback runtime: same-origin assets and asynchronous API 4.1.

Run: python -B -m src.v4.runtime
No import at startup. Automatic live scans run only when the persisted scheduler setting is enabled.
"""
import argparse,os,re,tempfile
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer,make_server
from .api import V4API,create_app
from .scan_jobs import ScanJobs,AutoScanScheduler
from .composite_replay import choose_plan_variants
from .models import Candidate,Audio
from .metadata_research import PublicMetadataResearcher
from .crosswalk_research import CompositeMetadataResearcher
from .notifications import NotificationWorker
from .live_dataset import LiveSonarrDataset
from .download_manager import CoordinatorDownloadQueue
from .execution import ExecutionCoordinator,approved_execution_scopes
from .provider_validation import ProviderSourceValidator
from .application_store import approved_execution_key,manual_approved_execution_key
from .series_view import override_active

ROOT=Path(__file__).resolve().parents[2]
ASSETS={"/":"index.html","/assets/app.js":"app.js","/assets/presentation.js":"presentation.js","/assets/styles.css":"styles.css","/assets/icon.svg":"icon.svg","/assets/dialog-focus.js":"dialog-focus.js"}
TYPES={".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".css":"text/css; charset=utf-8",".svg":"image/svg+xml"}
PUBLIC_ACTIVITY_ACTIONS=("scan_started","scan_completed","scan_failed","approve","choose","reject","dismiss","reopen","settings_updated")
CSP="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"

class AsyncAPI(V4API):
    contract_version="4.1"
    def __init__(self,service,jobs,downloads=None,**transport):
        super().__init__(service,**transport);self.jobs=jobs;self.downloads=downloads
    @staticmethod
    def present(detail,original):
        by_id={c["id"]:Candidate.from_dict(c) for c in original["candidates"]}
        preference=Audio(original.get("audio_preference","SUB")) if original.get("audio_preference") in {"SUB","DUB"} else Audio.SUB
        for plan in [detail["plan"]]+detail["alternate_plans"]:
            groups=[[by_id[v["candidate_id"]] for v in segment["variants"]] for segment in plan["segments"]]
            _,defaults=choose_plan_variants(groups,preference)
            defaults_by_id={c.id for c in defaults}
            for segment in plan["segments"]:
                variants=segment["variants"]
                selected=next((v for v in variants if v["selected"]),None)
                if selected is None and defaults_by_id:
                    selected=next((v for v in variants if v["candidate_id"] in defaults_by_id),None)
                    if selected:selected["selected"]=True
                candidate=by_id[selected["candidate_id"]] if selected else None
                segment["release_year"]=candidate.release_year if candidate else None
                segment["premiere_date"]=candidate.premiere_date.isoformat() if candidate and candidate.premiere_date else None
        return detail

    def dispatch(self,method,path,body,query,authorization=None):
        if path=="/api/v4/downloads":
            if self.downloads is None:return 200,{"enabled":False,"active":[],"history":[],"counts":{},"max_concurrent":0}
            if method=="GET":return 200,self.downloads.status()
            if method=="POST":
                if set(body)-{"limit","episode_id"}:raise ValueError("Unexpected download sync field")
                return 202,self.downloads.sync_missing(body.get("limit"),body.get("episode_id"))
        if path=="/api/v4/scans":
            if method=="GET":return 200,{"datasets":[{"id":id,"mode":"shadow",
                "scan_modes":["normal","deep_audit"]} for id in sorted(self.service.datasets)],
                "jobs":self.jobs.list(),"external_writes":False}
            if method=="POST":
                if set(body)-{"dataset","mode"} or "dataset" not in body or not isinstance(body["dataset"],str):
                    raise ValueError("A configured dataset id is required")
                mode=body.get("mode","normal")
                if mode not in {"normal","deep_audit"}:raise ValueError("Invalid scan mode")
                return 202,self.jobs.start(body["dataset"],scan_mode=mode)
        if method=="GET" and path=="/api/v4/activity":
            _,limit=self.page(query);before=int(query.get("before",["0"])[0])
            if before<0:raise ValueError("Invalid activity cursor")
            rows=self.service.store.public_activity(PUBLIC_ACTIVITY_ACTIONS,before=before,limit=limit)
            items=[]
            for row in rows:
                payload=row["payload"]
                items.append({"sequence":row["sequence"],"entity_id":row["item_id"],"action":row["action"],"actor":row["actor"],"timestamp":row["timestamp"],
                    "message":row["action"].replace("_"," "),"reason":payload.get("decision",{}).get("reason"),"entity_title":row["entity_title"]})
            return 200,{"items":items,"next_cursor":rows[-1]["sequence"] if rows else before}
        match=re.fullmatch(r"/api/v4/scans/([a-zA-Z0-9-]+)",path)
        if method=="GET" and match:return 200,self.jobs.get(match[1])
        status,data=super().dispatch(method,path,body,query,authorization)
        if isinstance(data,dict) and "plan" in data and "id" in data:
            self.present(data,self.service.store.get_item(data["id"])["original"])

        if path=="/api/v4/overview" and method=="GET":
            jobs=self.jobs.list()
            downloads=self.downloads.status(history_limit=10) if self.downloads is not None else {"enabled":False,"active":[],"history":[],"counts":{},"max_concurrent":0}
            data={**data,"service_status":"running","last_scan":jobs[0] if jobs else None,
                "active_scan":next((j for j in jobs if j["status"] in {"queued","running"}),None),
                "downloads":downloads}
        return status,data

class Runtime:
    def __init__(self,service,*,docker_transport=False,downloads=None,transport=None):
        self.service=service;self.downloads=downloads
        self.notifications=NotificationWorker(service.store);self.notifications.start()
        self.jobs=ScanJobs(service)
        self.auto_scan=AutoScanScheduler(service,self.jobs);self.auto_scan.start()
        self.api=AsyncAPI(service,self.jobs,downloads,**(transport or {}))
        if self.downloads is not None:self.downloads.start()
        self.docker_transport=docker_transport
    def close(self):
        self.auto_scan.close()
        self.jobs.close()
        if self.downloads is not None:self.downloads.close()
        self.notifications.close()
    def __call__(self,environ,start_response):
        path=environ.get("PATH_INFO","")
        if path.startswith("/api/"):return self.api(environ,start_response)
        allowed=False
        try:
            self.api.authorize_transport(environ,mutation=False);allowed=True
        except (ValueError,PermissionError):allowed=False
        status=200 if allowed and path in ASSETS and environ.get("REQUEST_METHOD")=="GET" else 403 if not allowed else 404
        if status==200:
            file=ROOT/"frontend-v4"/ASSETS[path];raw=file.read_bytes();content=TYPES[file.suffix]
        else:raw=b"Forbidden" if status==403 else b"Not found";content="text/plain; charset=utf-8"
        headers=[("Content-Type",content),("Content-Length",str(len(raw))),("Cache-Control","no-store"),("X-Content-Type-Options","nosniff"),("Content-Security-Policy",CSP),("Referrer-Policy","no-referrer")]
        start_response(f"{status} "+{200:"OK",403:"Forbidden",404:"Not Found"}[status],headers)
        return [raw]

def _flag(name,default=False):
    value=os.getenv(name)
    if value is None:return default
    return value.strip().casefold() in {"1","true","yes","on"}

def _secret_loader(path):
    secret_path=Path(path).resolve() if path else None
    def load():
        if secret_path is None:return None
        value=secret_path.read_text(encoding="utf-8").strip()
        if not 32<=len(value)<=512:raise PermissionError("Invalid mutation token")
        return value
    return load

def _writable_probe(root):
    root=Path(root).resolve()
    if not root.is_dir():raise ValueError("effect root is unavailable")
    temporary=None
    try:
        descriptor,name=tempfile.mkstemp(prefix=".anidown-preflight-",dir=root)
        temporary=Path(name)
        with os.fdopen(descriptor,"wb") as handle:
            handle.write(b"preflight");handle.flush();os.fsync(handle.fileno())
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    return root

def _prepare_downloads(service,requested,adapter,authorization,owner_token,sonarr,workers,poll_seconds,auto_sync):
    if not requested:
        capability={"ready":False,"status":"disabled","diagnostics":[]}
        service.store.set_runtime_capability(capability);return None
    missing=[]
    if adapter is None:missing.append("execution_adapter")
    if authorization is None:missing.append("production_authorization")
    if not isinstance(owner_token,str) or not owner_token:missing.append("owner_token")
    if sonarr is None and adapter is not None:sonarr=getattr(adapter,"sonarr",None)
    if sonarr is None:missing.append("sonarr_scheduler")
    if os.getenv("ANIDOWN_V4_V3_QUIESCENCE")!="service_stopped":missing.append("v3_quiescence")
    if missing:
        capability={"ready":False,"status":"configuration_error","diagnostics":sorted(missing)}
        service.store.set_runtime_capability(capability);return None
    diagnostics=[]
    try:
        if getattr(adapter,"production_effects",False) is not True:raise ValueError("production adapter required")
        required=getattr(adapter,"production_authorization",None)
        if required is None or not authorization.same_capability(required):raise ValueError("adapter authorization mismatch")
        authorized_key=authorization.manifest["execution_key"]
        authorized_item=None;authorized_target_id=None
        validation_states=service.store.provider_validation_states()
        for item in service.store.list_items():
            if item["mapping_state"]=="superseded":continue
            overrides=[service.store.season_override(target["target_id"])
                for target in item["original"]["targets"]]
            automatic_allowed=(item["mapping_state"]=="proposed" and item["review_state"]=="not_required"
                and item["human_decision"] is None and not any(override_active(value) for value in overrides)
                and validation_states.get(item["id"],{}).get("state","current")=="current")
            approved_allowed=(item["mapping_state"]=="approved" and item["review_state"]=="resolved"
                and validation_states.get(item["id"],{}).get("state","current")=="current")
            automatic_audio=None
            if automatic_allowed:
                audio_values={override.get("audio") for override in overrides}
                audio_values.discard(None)
                if len(audio_values)>1:automatic_allowed=False
                elif audio_values:automatic_audio=next(iter(audio_values))
            if automatic_allowed or approved_allowed:
                for scope_target,scope_episode in approved_execution_scopes(item,automatic=automatic_allowed):
                    common=approved_execution_key(item,audio_preference=automatic_audio,
                        target_id=scope_target,sonarr_episode=scope_episode)
                    if common==authorized_key:
                        authorized_item=item;authorized_target_id=scope_target;break
            if authorized_item:break
            for target,override in zip(item["original"]["targets"],overrides):
                approval=override.get("approval")
                for row in override.get("episode_map") or ():
                    episode=row.get("sonarr_episode") if isinstance(row,dict) else None
                    if approval and manual_approved_execution_key(item,target["target_id"],approval,
                            sonarr_episode=episode)==authorized_key:
                        authorized_item=item;authorized_target_id=target["target_id"];break
                if authorized_item:break
            if authorized_item:break
        if authorized_item is None:raise ValueError("authorization is not bound to a current mapping revision")
        target_ids=[authorized_target_id]
        with service.store._connect() as db:
            for target_id in target_ids:
                claim=db.execute("SELECT owner,owner_token FROM v4_writer_claims WHERE target_id=?",(target_id,)).fetchone()
                if not claim or (claim["owner"],claim["owner_token"])!=("v4",owner_token):
                    raise ValueError("current authorization lacks exact V4 writer ownership")
        permit=getattr(adapter,"permit",None)
        staging=_writable_probe(permit.staging_root)
        libraries=tuple(_writable_probe(root) for root in permit.library_roots)
        journal=_writable_probe(adapter.journal_root)
        devices={staging.stat().st_dev,*(root.stat().st_dev for root in libraries)}
        if len(devices)!=1:raise ValueError("staging and libraries must share one filesystem")
        if journal==staging or journal in staging.parents or staging in journal.parents:
            raise ValueError("journal and staging roots must be distinct")
        probe=adapter.probe()
        if not isinstance(probe,dict) or probe.get("ready") is not True:raise ValueError("adapter probe failed")
        coordinator=ExecutionCoordinator(service.store,adapter,owner_token=owner_token,require_preclaimed=True,
            production_authorization=authorization,manual_source_validator=service.manual_source_validator)
        recovery=coordinator.reconcile_startup()
        if not recovery["ready"]:
            diagnostics.append("reconciliation_unknown")
            capability={"ready":False,"status":"recovery_blocked","diagnostics":diagnostics}
            service.store.set_runtime_capability(capability);return None
    except Exception as error:
        diagnostics.append(type(error).__name__)
        capability={"ready":False,"status":"preflight_failed","diagnostics":diagnostics}
        service.store.set_runtime_capability(capability);return None
    capability={"ready":True,"status":"ready","diagnostics":[]}
    service.store.set_runtime_capability(capability)
    return CoordinatorDownloadQueue(service,sonarr,coordinator,max_workers=workers,
        poll_seconds=poll_seconds,enabled=True,auto_sync=auto_sync)

def create_runtime(storage_path="work/phase7/application.sqlite3",datasets=None,*,docker_transport=False,public_metadata=False,
    trusted_proxy_cidrs=None,allowed_hosts=None,auth_token_file=None,manual_source_validator=None,
    download_adapter=None,production_authorization=None,execution_owner_token=None,download_sonarr=None):
    researcher=CompositeMetadataResearcher(PublicMetadataResearcher()) if public_metadata else None
    configured=dict(datasets or {})
    origin=os.getenv("ANIDOWN_V4_SONARR_ORIGIN","").strip()
    key_file=os.getenv("ANIDOWN_V4_SONARR_KEY_FILE","").strip()
    host_header=os.getenv("ANIDOWN_V4_SONARR_HOST_HEADER","").strip() or None
    if _flag("ANIDOWN_V4_LIVE_SONARR"):
        seed=configured.get("production")
        if not seed or not origin or not key_file:raise ValueError("Live Sonarr requires seed, origin and secret file")
        configured["production"]=LiveSonarrDataset(origin,key_file,seed,
            os.getenv("ANIDOWN_V4_LIVE_CACHE_ROOT","/app/work/live"),host_header=host_header)
    validator=manual_source_validator or ProviderSourceValidator()
    service=create_app(storage_path,configured or None,metadata_researcher=researcher,
        manual_source_validator=validator).service
    workers=max(1,min(3,int(os.getenv("ANIDOWN_V4_DOWNLOAD_WORKERS","3"))))
    downloads=_prepare_downloads(service,_flag("ANIDOWN_V4_DOWNLOADS_ENABLED"),download_adapter,
        production_authorization,execution_owner_token,download_sonarr,workers,
        max(15,int(os.getenv("ANIDOWN_V4_DOWNLOAD_POLL_SECONDS","60"))),
        _flag("ANIDOWN_V4_DOWNLOAD_AUTOSYNC"))
    cidrs=trusted_proxy_cidrs
    if cidrs is None:cidrs=[value.strip() for value in os.getenv("ANIDOWN_V4_TRUSTED_PROXY_CIDRS","").split(",") if value.strip()]
    hosts=allowed_hosts
    if hosts is None:hosts=[value.strip() for value in os.getenv("ANIDOWN_V4_ALLOWED_HOSTS","").split(",") if value.strip()]
    token_file=auth_token_file if auth_token_file is not None else os.getenv("ANIDOWN_V4_AUTH_TOKEN_FILE","").strip()
    transport={"trusted_proxy_cidrs":cidrs or (),"allowed_hosts":hosts or (),
        "mutation_token_loader":_secret_loader(token_file) if token_file else None}
    return Runtime(service,docker_transport=docker_transport,downloads=downloads,transport=transport)

class ThreadedServer(ThreadingMixIn,WSGIServer):
    daemon_threads=True

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="AniDown V4 same-origin shadow runtime")
    parser.add_argument("--port",type=int,default=6004)
    parser.add_argument("--storage",default="work/phase7/application.sqlite3")
    parser.add_argument("--dataset-source",default=None,help="V4-contained offline snapshot")
    parser.add_argument("--docker-transport",action="store_true",help="Bind the container interface; proxy trust still requires explicit configuration")
    parser.add_argument("--public-metadata",action="store_true",help="Research unresolved series using bounded read-only public metadata")
    args=parser.parse_args();app=create_runtime(args.storage,{"production":args.dataset_source} if args.dataset_source else None,docker_transport=args.docker_transport,public_metadata=args.public_metadata)
    try:
        bind="0.0.0.0" if args.docker_transport else "127.0.0.1"
        with make_server(bind,args.port,app,server_class=ThreadedServer) as server:
            print(f"AniDown V4: {bind}:{args.port} (shadow)",flush=True);server.serve_forever()
    finally:app.close()
