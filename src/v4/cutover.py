"""Disabled-by-default Phase 12 cutover controls and loopback test gateway."""
import hmac
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .execution import DownstreamFailure


CONFIRMATION="EXECUTE APPROVED PLAN"
RECONCILE_CONFIRMATION="RECONCILE EXECUTION"


class OperatorExecutionControl:
    def __init__(self,coordinator,bearer_token):
        if not isinstance(bearer_token,str) or not bearer_token or len(bearer_token)>512:raise ValueError("Operator bearer token is required")
        self.coordinator=coordinator;self._token=bearer_token

    def _authorize(self,authorization):
        expected="Bearer "+self._token
        if not isinstance(authorization,str) or not hmac.compare_digest(authorization,expected):raise PermissionError("Operator authorization required")

    def execute(self,item_id,body,authorization):
        self._authorize(authorization)
        if set(body)!={"expected_revision","confirmation"} or body.get("confirmation")!=CONFIRMATION:raise ValueError("Exact execution confirmation is required")
        return self.coordinator.execute(item_id,expected_revision=body["expected_revision"])

    def reconcile(self,execution_key,body,authorization):
        self._authorize(authorization)
        if set(body)!={"confirmation"} or body.get("confirmation")!=RECONCILE_CONFIRMATION:raise ValueError("Exact reconciliation confirmation is required")
        return self.coordinator.reconcile(execution_key)

    def status(self,execution_key,authorization):
        self._authorize(authorization)
        attempt=next((value for value in self.coordinator.store.execution_attempts() if value["execution_key"]==execution_key),None)
        if attempt is None:raise KeyError("Unknown execution")
        allowed=("execution_key","status","created_at","finished_at","receipt","error_category")
        return {key:attempt[key] for key in allowed}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


class LoopbackTestGatewayAdapter:
    def __init__(self,base_url,*,timeout=3,max_response_bytes=65536,opener=None):
        parsed=urlsplit(base_url)
        if parsed.scheme!="http" or parsed.hostname not in {"127.0.0.1","::1"} or parsed.port is None or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"","/"}:
            raise ValueError("Phase12 gateway must be an explicit loopback HTTP origin")
        if not 0<timeout<=10 or not 1024<=max_response_bytes<=262144:raise ValueError("Invalid test gateway limits")
        self.base=base_url.rstrip("/");self.timeout=timeout;self.limit=max_response_bytes;self.opener=opener or urllib.request.build_opener(_NoRedirect())

    def _request(self,method,path,payload=None):
        raw=json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode() if payload is not None else None
        request=urllib.request.Request(self.base+path,data=raw,method=method,headers={"Content-Type":"application/json","Accept":"application/json"})
        try:response=self.opener.open(request,timeout=self.timeout)
        except urllib.error.HTTPError as error:
            if method=="GET" and error.code==404:return {"status":"absent"}
            raise DownstreamFailure("test_gateway_http_error") from None
        except Exception:raise DownstreamFailure("test_gateway_unavailable") from None
        with response:
            if response.status!=200 or response.headers.get_content_type()!="application/json":raise DownstreamFailure("invalid_gateway_response")
            data=response.read(self.limit+1)
        if len(data)>self.limit:raise DownstreamFailure("gateway_response_too_large")
        try:value=json.loads(data)
        except (UnicodeDecodeError,ValueError):raise DownstreamFailure("invalid_gateway_response") from None
        if not isinstance(value,dict) or value.get("status")!="completed" or set(value)!={"status","receipt"}:raise DownstreamFailure("invalid_gateway_response")
        return value

    def lookup(self,execution_key):
        if not isinstance(execution_key,str) or len(execution_key)!=64:return {"status":"unknown"}
        try:return self._request("GET","/v1/executions/"+execution_key)
        except DownstreamFailure:return {"status":"unknown"}

    def execute(self,envelope):
        state=self.lookup(envelope["execution_key"])
        if state["status"]=="completed":return state["receipt"]
        if state["status"]!="absent":raise DownstreamFailure("remote_state_unknown")
        return self._request("POST","/v1/executions",envelope)["receipt"]
