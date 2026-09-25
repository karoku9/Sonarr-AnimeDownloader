"""Standalone JSON WSGI API. No registration in the V3 app and no server startup.

Same-origin loopback-only transport, no CORS, no credentials/settings passthrough.
The factory exposes stable DTOs while storage/engine remain private dependencies.
"""
import json
import hmac
import ipaddress
import re
from urllib.parse import parse_qs,urlsplit
from .application_store import ApplicationStore,Conflict
from .application import ApplicationService
from .application_adapters import SnapshotAdapter
from .application_dto import CONTRACT_VERSION
from .production_sources import local_output
from .llm_judge import Judge,configured_provider
from .llm_verify import configured_verifier

class V4API:
    contract_version=CONTRACT_VERSION
    def __init__(self,service,*,execution_control=None,trusted_proxy_cidrs=(),allowed_hosts=(),mutation_token_loader=None):
        self.service=service;self.execution_control=execution_control
        self.trusted_proxy_cidrs=tuple(ipaddress.ip_network(value,strict=False) for value in trusted_proxy_cidrs)
        self.allowed_hosts={str(value).strip().casefold() for value in allowed_hosts if str(value).strip()}
        self.mutation_token_loader=mutation_token_loader

    def authorize_transport(self,environ,*,mutation=False):
        try:peer=ipaddress.ip_address(environ.get("REMOTE_ADDR",""))
        except ValueError:raise PermissionError("Untrusted network peer") from None
        loopback=peer.is_loopback
        trusted_proxy=any(peer in network for network in self.trusted_proxy_cidrs)
        if not loopback and not trusted_proxy:raise PermissionError("Loopback or configured proxy required")
        host=environ.get("HTTP_HOST","");parsed_host=urlsplit("//"+host)
        allowed={"localhost","127.0.0.1","::1",*self.allowed_hosts}
        if (parsed_host.hostname or "").casefold() not in allowed or parsed_host.username or parsed_host.password:
            raise PermissionError("Untrusted host")
        origin=environ.get("HTTP_ORIGIN")
        if origin:
            parsed=urlsplit(origin)
            if parsed.scheme not in {"http","https"} or parsed.netloc!=host or parsed.username or parsed.password:
                raise PermissionError("Same-origin access required")
        if mutation and not loopback:
            expected=self.mutation_token_loader() if self.mutation_token_loader else None
            supplied=environ.get("HTTP_AUTHORIZATION","")
            if not expected or not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:],expected):
                raise PermissionError("Authenticated proxy mutation required")
        return loopback,trusted_proxy

    @staticmethod
    def page(query):
        try:limit=int(query.get("limit",["50"])[0]);offset=int(query.get("offset",["0"])[0])
        except (ValueError,TypeError):raise ValueError("Invalid pagination")
        if limit<1 or limit>100 or offset<0:raise ValueError("limit must be1..100 and offset nonnegative")
        return offset,limit

    def dispatch(self,method,path,body,query,authorization=None):
        service=self.service;base="/api/v4"
        match=re.fullmatch(base+r"/operations/reconciliations/([0-9a-f]{64})",path)
        if match:
            if self.execution_control is None or method!="POST":raise KeyError("Unknown endpoint")
            return 200,self.execution_control.reconcile(match[1],body,authorization)
        match=re.fullmatch(base+r"/operations/executions/([0-9a-f]{64})/status",path)
        if match:
            if self.execution_control is None or method!="GET":raise KeyError("Unknown endpoint")
            return 200,self.execution_control.status(match[1],authorization)
        match=re.fullmatch(base+r"/operations/executions/([a-zA-Z0-9-]+)",path)
        if match:
            if self.execution_control is None or method!="POST":raise KeyError("Unknown endpoint")
            return 200,self.execution_control.execute(match[1],body,authorization)
        if method=="GET" and path==base+"/overview":return 200,service.overview()
        if method=="GET" and path==base+"/notifications":
            _,limit=self.page(query);after=int(query.get("after",["0"])[0])
            if after<0:raise ValueError("Invalid notification cursor")
            return 200,{"items":service.store.notification_events(after,limit),
                "backends":service.store.settings()["notification_backends"]}
        if method=="GET" and path==base+"/series":
            offset,limit=self.page(query)
            search=query.get("q",[None])[0];state=query.get("state",[None])[0]
            if search is not None and len(search)>200:raise ValueError("Series query is too long")
            return 200,service.series(offset=offset,limit=limit,query=search,state=state)
        match=re.fullmatch(base+r"/series/(\d+)",path)
        if method=="GET" and match:return 200,service.series_detail(int(match[1]))
        match=re.fullmatch(base+r"/series/(\d+)/seasons/(\d+)/override",path)
        if match:
            series_id,season=(int(match[1]),int(match[2]))
            if method=="GET":return 200,service.season_override(series_id,season)
            if method=="PATCH":return 200,service.update_season_override(series_id,season,body)
            if method=="DELETE":return 200,service.clear_season_override(series_id,season)
        if method=="GET" and path in {base+"/mappings",base+"/proposals",base+"/reviews"}:
            offset,limit=self.page(query);reviews=path.endswith("/reviews")
            state=query.get("state",["open" if reviews else "proposed" if path.endswith("/proposals") else None])[0]
            allowed={"open","resolved","rejected","dismissed","superseded"} if reviews else {"proposed","approved","rejected","needs_review","waiting","airing","unavailable","superseded"}
            if state is not None and state not in allowed:raise ValueError("Unknown state filter")
            return 200,service.items(reviews=reviews,state=state,offset=offset,limit=limit)
        match=re.fullmatch(base+r"/(?:mappings|reviews)/([a-zA-Z0-9-]+)(?:/(approve|choose|reject|dismiss|reopen|metadata))?",path)
        if match:
            id,action=match.groups()
            if method=="GET" and action is None:return 200,service.detail(id)
            if method=="GET" and action=="metadata":return 200,service.metadata(id)
            if method=="POST" and action and action!="metadata":return 200,service.action(id,action,body)
        match=re.fullmatch(base+r"/advanced/(?:mappings|reviews)/([a-zA-Z0-9-]+)/evidence",path)
        if method=="GET" and match:return 200,service.evidence(match[1])
        if method=="GET" and path==base+"/advanced/activity":
            _,limit=self.page(query);after=int(query.get("after",["0"])[0])
            if after<0:raise ValueError("Invalid activity cursor")
            return 200,{"items":service.store.activity(after,limit)}
        if method=="GET" and path==base+"/activity":
            _,limit=self.page(query);after=int(query.get("after",["0"])[0])
            if after<0:raise ValueError("Invalid activity cursor")
            return 200,service.activity(after,limit)
        if path==base+"/settings":
            if method=="GET":return 200,service.store.settings()
            if method=="PATCH":return 200,service.store.update_settings(body)
        if path==base+"/scans":
            if method=="GET":return 200,{"datasets":[{"id":id,"mode":"shadow"} for id in sorted(service.datasets)],"external_writes":False}
            if method=="POST":
                if set(body)!={"dataset"} or not isinstance(body["dataset"],str):raise ValueError("A configured dataset id is required")
                return 201,service.scan(body["dataset"])
        raise KeyError("Unknown endpoint")

    def __call__(self,environ,start_response):
        status=200;mutations_allowed=False
        try:
            method=environ.get("REQUEST_METHOD","GET");body={}
            loopback,_=self.authorize_transport(environ,mutation=method in {"POST","PATCH","PUT","DELETE"})
            mutations_allowed=loopback
            if method in {"POST","PATCH"}:
                if environ.get("CONTENT_TYPE","").split(";")[0].lower()!="application/json":raise ValueError("JSON object body required")
                length=int(environ.get("CONTENT_LENGTH") or "0")
                if not 0<length<=65536:raise ValueError("JSON body must be1..65536 bytes")
                body=json.loads(environ["wsgi.input"].read(length))
                if not isinstance(body,dict):raise ValueError("JSON object body required")
            status,data=self.dispatch(method,environ.get("PATH_INFO",""),body,parse_qs(environ.get("QUERY_STRING","")),environ.get("HTTP_AUTHORIZATION"))
            result={"data":data,"meta":{"contract_version":self.contract_version,"mutations_allowed":mutations_allowed}}
        except PermissionError as error:status=403;result={"error":{"code":"forbidden","message":str(error)}}
        except Conflict as error:status=409;result={"error":{"code":"conflict","message":str(error)}}
        except KeyError:status=404;result={"error":{"code":"not_found","message":"Resource or endpoint not found"}}
        except (ValueError,TypeError):status=400;result={"error":{"code":"invalid_request","message":"Invalid body, field, setting or pagination"}}
        except Exception:status=503;result={"error":{"code":"unavailable","message":"V4 operation unavailable; no external writes performed"}}
        result.setdefault("meta",{"contract_version":self.contract_version,"mutations_allowed":mutations_allowed})
        encoded=json.dumps(result,ensure_ascii=False).encode("utf-8")
        labels={200:"OK",201:"Created",202:"Accepted",400:"Bad Request",403:"Forbidden",404:"Not Found",409:"Conflict",503:"Service Unavailable"}
        start_response(f"{status} {labels[status]}",[("Content-Type","application/json; charset=utf-8"),("Content-Length",str(len(encoded))),("Cache-Control","no-store"),("X-Content-Type-Options","nosniff")])
        return [encoded]


def create_app(storage_path="work/application-v1.sqlite3",datasets=None,*,judge_provider=None,metadata_researcher=None,verifier=None,
    manual_source_validator=None):
    # Caller explicitly configures permitted sanitized local snapshots; nothing scans on startup.
    configured=datasets or {"production":"work/phase4/replay/production-snapshot.json"}
    adapters={id:(source if callable(getattr(source,"read",None)) else SnapshotAdapter(source)) for id,source in configured.items()}
    store=ApplicationStore(local_output(storage_path))
    provider=judge_provider if judge_provider is not None else configured_provider()
    verification=verifier if verifier is not None else configured_verifier()
    return V4API(ApplicationService(store,adapters,judge=Judge(provider,store),researcher=metadata_researcher,
        verifier=verification,manual_source_validator=manual_source_validator))
