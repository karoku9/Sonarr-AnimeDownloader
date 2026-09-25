"""Phase 12 recovery, ownership, operator and loopback-gateway contracts."""
from v4_test_support import repo_tempdir
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import io
import json
import unittest
from wsgiref.util import setup_testing_defaults

from src.v4.api import V4API, create_app
from src.v4.application import ApplicationService
from src.v4.application_adapters import SnapshotAdapter
from src.v4.application_store import ApplicationStore, Conflict
from src.v4.cutover import LoopbackTestGatewayAdapter, OperatorExecutionControl
from src.v4.execution import DownstreamFailure, ExecutionCoordinator, FakeDownstreamAdapter


FIXTURE = "tests/v4/fixtures/production_metadata_v1.json"
CONFIRMATION = "EXECUTE APPROVED PLAN"


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = repo_tempdir();self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"app.sqlite3"
        self.store = ApplicationStore(self.path)
        self.service = ApplicationService(self.store,{"fixture":SnapshotAdapter(FIXTURE)})
        self.service.scan("fixture")

    def item(self,target="182:1"):
        return next(item for item in self.store.list_items() if item["mapping_state"]!="superseded" and target in {t["target_id"] for t in item["original"]["targets"]})

    def approve(self):
        item=self.item();self.service.action(item["id"],"approve",{"expected_revision":item["revision"],"reason":"Phase12 fixture approval"})
        return self.store.get_item(item["id"])

    def request(self,app,method,path,body=None,token=None):
        env={};setup_testing_defaults(env);env.update(REQUEST_METHOD=method,PATH_INFO=path,REMOTE_ADDR="127.0.0.1",HTTP_HOST="localhost")
        if body is not None:
            raw=json.dumps(body).encode();env.update(CONTENT_TYPE="application/json",CONTENT_LENGTH=str(len(raw)),**{"wsgi.input":io.BytesIO(raw)})
        if token is not None:env["HTTP_AUTHORIZATION"]="Bearer "+token
        result=[];payload=b"".join(app(env,lambda status,headers:result.append(int(status.split()[0]))))
        return result[0],json.loads(payload)

    def test_interrupted_before_submit_reconciles_same_envelope_once(self):
        item=self.approve();adapter=FakeDownstreamAdapter();coordinator=ExecutionCoordinator(self.store,adapter,owner_token="v4-cutover")
        envelope=coordinator.reserve(item["id"],expected_revision=item["revision"])
        reopened=ExecutionCoordinator(ApplicationStore(self.path),adapter,owner_token="v4-cutover")
        result=reopened.reconcile(envelope["execution_key"])
        self.assertEqual(result["status"],"completed");self.assertEqual(len(adapter.calls),1)
        self.assertEqual(adapter.calls[0],envelope);self.assertEqual(self.store.execution_attempts()[0]["status"],"completed")

    def test_interrupted_after_submit_discovers_receipt_without_duplicate(self):
        item=self.approve();adapter=FakeDownstreamAdapter();coordinator=ExecutionCoordinator(self.store,adapter,owner_token="v4-cutover")
        envelope=coordinator.reserve(item["id"],expected_revision=item["revision"])
        adapter.execute(deepcopy(envelope))
        result=coordinator.reconcile(envelope["execution_key"])
        self.assertEqual(result["status"],"completed");self.assertEqual(len(adapter.calls),1)

    def test_unknown_reconciliation_stays_reserved_and_auditable(self):
        item=self.approve();adapter=FakeDownstreamAdapter(lookup_status="unknown");coordinator=ExecutionCoordinator(self.store,adapter,owner_token="v4-cutover")
        envelope=coordinator.reserve(item["id"],expected_revision=item["revision"])
        with self.assertRaises(DownstreamFailure):coordinator.reconcile(envelope["execution_key"])
        self.assertEqual(self.store.execution_attempts()[0]["status"],"reserved");self.assertEqual(adapter.calls,[])
        self.assertTrue(any(e["action"]=="execution_reconciliation_pending" for e in self.store.activity(limit=500)))

    def test_attested_atomic_transfer_and_reserved_attempt_blocks_rollback(self):
        self.store.claim_writer(("182:1",),"v3","old-v3")
        bad={"verified_by":"operator","reason":"cutover rehearsal","verification_method":"service_stopped","v3_stopped":False}
        with self.assertRaises(Conflict):self.store.transfer_writer(("182:1",),"v3","old-v3","v4","new-v4",bad)
        good={**bad,"v3_stopped":True}
        self.store.transfer_writer(("182:1",),"v3","old-v3","v4","new-v4",good)
        self.assertEqual(self.store.writer_claims()[0]["owner"],"v4")
        item=self.approve();ExecutionCoordinator(self.store,FakeDownstreamAdapter(),owner_token="new-v4",require_preclaimed=True).reserve(item["id"],expected_revision=item["revision"])
        rollback={"verified_by":"operator","reason":"rollback rehearsal","verification_method":"operator_rollback","rollback_authorized":True}
        with self.assertRaises(Conflict):self.store.transfer_writer(("182:1",),"v4","new-v4","v3","rollback-v3",rollback)
        self.assertNotIn("old-v3",json.dumps(self.store.activity(limit=500)))
        self.assertNotIn("new-v4",json.dumps(self.store.activity(limit=500)))

    def test_default_api_has_no_execution_route_and_injected_control_requires_secret_and_confirmation(self):
        item=self.approve();path=f"/api/v4/operations/executions/{item['id']}"
        default=create_app(Path(self.temp.name)/"default.sqlite3",{"fixture":FIXTURE})
        self.assertEqual(self.request(default,"POST",path,{"expected_revision":item["revision"],"confirmation":CONFIRMATION})[0],404)
        adapter=FakeDownstreamAdapter();coordinator=ExecutionCoordinator(self.store,adapter,owner_token="operator-v4")
        app=V4API(self.service,execution_control=OperatorExecutionControl(coordinator,"phase12-secret"))
        body={"expected_revision":item["revision"],"confirmation":CONFIRMATION}
        self.assertEqual(self.request(app,"POST",path,body)[0],403)
        self.assertEqual(self.request(app,"POST",path,{**body,"confirmation":"yes"},"phase12-secret")[0],400)
        status,result=self.request(app,"POST",path,body,"phase12-secret")
        self.assertEqual(status,200);self.assertEqual(result["data"]["status"],"completed");self.assertEqual(len(adapter.calls),1)
        self.assertNotIn("phase12-secret",json.dumps(self.store.activity(limit=500)))

    def test_loopback_test_gateway_is_idempotent_and_non_loopback_is_rejected(self):
        with self.assertRaises(ValueError):LoopbackTestGatewayAdapter("https://sonarr.example.test:8989")
        with self.assertRaises(ValueError):LoopbackTestGatewayAdapter("http://user:secret@127.0.0.1:8989")
        state={"submits":0,"receipts":{}}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,value):
                raw=json.dumps(value).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                key=self.path.rsplit("/",1)[-1];receipt=state["receipts"].get(key)
                self.reply(200,{"status":"completed","receipt":receipt}) if receipt else self.reply(404,{"status":"absent"})
            def do_POST(self):
                length=int(self.headers["Content-Length"]);envelope=json.loads(self.rfile.read(length));key=envelope["execution_key"]
                state["submits"]+=1;receipt={"adapter":"loopback-test","receipt_id":key,"mode":"test","production_effects":False};state["receipts"][key]=receipt
                self.reply(200,{"status":"completed","receipt":receipt})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        item=self.approve();adapter=LoopbackTestGatewayAdapter(f"http://127.0.0.1:{server.server_port}");coordinator=ExecutionCoordinator(self.store,adapter,owner_token="loopback-v4")
        result=coordinator.execute(item["id"],expected_revision=item["revision"])
        self.assertEqual(result["status"],"completed");self.assertEqual(state["submits"],1)
        self.assertEqual(adapter.lookup(result["execution_key"])["status"],"completed");self.assertEqual(state["submits"],1)


if __name__=="__main__":unittest.main()
