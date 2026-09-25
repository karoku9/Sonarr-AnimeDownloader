"""Runtime 4.1 contracts: loopback static UI, durable asynchronous jobs, audit."""
import io,json,threading,time,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from wsgiref.util import setup_testing_defaults
from src.v4.runtime import create_runtime

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir="work");self.addCleanup(self.temp.cleanup)
        self.app=create_runtime(Path(self.temp.name)/"runtime.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"})
        self.addCleanup(self.app.close)
    def request(self,method,path,body=None,**headers):
        env={};setup_testing_defaults(env)
        route,_,query=path.partition("?");env.update(REQUEST_METHOD=method,PATH_INFO=route,QUERY_STRING=query,REMOTE_ADDR="127.0.0.1",HTTP_HOST="127.0.0.1:6004")
        if body is not None:
            raw=json.dumps(body).encode();env.update(CONTENT_TYPE="application/json",CONTENT_LENGTH=str(len(raw)),**{"wsgi.input":io.BytesIO(raw)})
        env.update(headers);out=[]
        raw=b"".join(self.app(env,lambda status,h:out.append((int(status.split()[0]),dict(h)))))
        return out[0][0],out[0][1],json.loads(raw) if out[0][1].get("Content-Type","").startswith("application/json") else raw.decode()
    def complete(self,id):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            data=self.request("GET","/api/v4/scans/"+id)[2]["data"]
            if data["status"] in {"completed","failed"}:return data
            time.sleep(.01)
        self.fail("Job did not finish")
    def test_static_shell_same_origin_and_semantic_navigation(self):
        status,headers,html=self.request("GET","/")
        self.assertEqual(status,200);self.assertIn("text/html",headers["Content-Type"])
        self.assertIn('aria-label="Navigazione principale"',html)
        for page in ("series","downloads","reviews","activity","settings"):self.assertIn('href="#/'+page+'"',html)
        self.assertNotIn('href="#/dashboard"',html)
        self.assertNotIn("frontend_OLD",html);self.assertIn("script-src 'self'",headers["Content-Security-Policy"])
        self.assertIn('class="skip-link" href="#main"',html)
        self.assertIn('id="notice" class="notice" role="status" aria-live="polite"',html)
    def test_assets_are_allowlisted(self):
        for path in ("/assets/app.js","/assets/styles.css","/assets/presentation.js","/assets/dialog-focus.js"):
            self.assertEqual(self.request("GET",path)[0],200)
        for path in ("/assets/../.env","/.env","/work/application.sqlite3","/assets/missing.js"):
            self.assertEqual(self.request("GET",path)[0],404)
    def test_static_host_and_origin_guard(self):
        self.assertEqual(self.request("GET","/",HTTP_HOST="evil.example")[0],403)
        self.assertEqual(self.request("GET","/",REMOTE_ADDR="192.168.1.1")[0],403)
        self.assertEqual(self.request("GET","/",HTTP_ORIGIN="https://evil.example")[0],403)
    def test_docker_bridge_transport_requires_explicit_proxy_and_authenticated_mutations(self):
        self.assertEqual(self.request("GET","/api/v4/overview",REMOTE_ADDR="172.18.0.1")[0],403)
        token=Path(self.temp.name)/"mutation-token";token.write_text("x"*48,encoding="utf-8")
        self.app=create_runtime(Path(self.temp.name)/"docker.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"},
            docker_transport=True,trusted_proxy_cidrs=["172.18.0.1/32"],auth_token_file=token)
        self.addCleanup(self.app.close)
        self.assertEqual(self.request("GET","/",REMOTE_ADDR="172.18.0.1")[0],200)
        local=self.request("GET","/api/v4/overview")[2]
        self.assertIs(local["meta"]["mutations_allowed"],True)
        proxied=self.request("GET","/api/v4/overview",REMOTE_ADDR="172.18.0.1")
        self.assertEqual(proxied[0],200);self.assertIs(proxied[2]["meta"]["mutations_allowed"],False)
        self.assertEqual(self.request("PATCH","/api/v4/settings",{"display_language":"en"},REMOTE_ADDR="172.18.0.1")[0],403)
        authenticated=self.request("PATCH","/api/v4/settings",{"display_language":"en"},REMOTE_ADDR="172.18.0.1",
            HTTP_AUTHORIZATION="Bearer "+"x"*48)
        self.assertEqual(authenticated[0],200);self.assertIs(authenticated[2]["meta"]["mutations_allowed"],False)
        self.assertEqual(self.request("GET","/",REMOTE_ADDR="8.8.8.8")[0],403)
        self.assertEqual(self.request("GET","/api/v4/overview",REMOTE_ADDR="8.8.8.8")[0],403)
        self.assertEqual(self.request("GET","/",REMOTE_ADDR="172.18.0.1",HTTP_HOST="evil.example")[0],403)
        self.assertEqual(self.request("GET","/api/v4/overview",REMOTE_ADDR="172.18.0.1",HTTP_ORIGIN="https://evil.example")[0],403)
    def test_startup_does_not_scan_or_import(self):
        self.assertEqual(self.app.service.store.list_items(),[])
        self.assertEqual(self.request("GET","/api/v4/scans")[2]["data"]["jobs"],[])
    def test_scan_returns_202_before_service_finishes_and_ui_remains_usable(self):
        entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def scan(dataset,progress=None):
            entered.set();release.wait(3)
            if progress:progress("candidate_enrichment",25)
            return {"status":"completed","target_count":12,"item_count":11,"external_writes":False}
        self.app.service.scan=scan
        status,_,body=self.request("POST","/api/v4/scans",{"dataset":"real"})
        self.assertEqual(status,202);self.assertEqual(body["meta"]["contract_version"],"4.1")
        id=body["data"]["job_id"];self.assertTrue(entered.wait(1))
        self.assertEqual(self.request("GET","/api/v4/overview")[0],200)
        self.assertEqual(self.request("GET","/")[0],200)
        duplicate=self.request("POST","/api/v4/scans",{"dataset":"real"})[2]["data"]
        self.assertEqual(id,duplicate["job_id"])
        release.set();job=self.complete(id)
        self.assertEqual(job["status"],"completed");self.assertEqual(job["progress"],100)
        self.assertFalse(job["external_writes"])
    def test_failed_scan_public_error_does_not_leak_paths(self):
        def scan(*args,**kwargs):raise ValueError("secret C:/V3/.env")
        self.app.service.scan=scan
        id=self.request("POST","/api/v4/scans",{"dataset":"real"})[2]["data"]["job_id"]
        job=self.complete(id);self.assertEqual(job["status"],"failed")
        self.assertNotIn("secret",json.dumps(job));self.assertNotIn(".env",json.dumps(job))
        self.assertTrue(any(e["action"]=="scan_failed" for e in self.app.service.store.activity()))
    def test_unknown_dataset_and_fields_rejected(self):
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"missing"})[0],400)
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"real","url":"http://V3"})[0],400)
        self.assertEqual(self.request("POST","/api/v4/scans",{"dataset":"real","mode":"unsafe"})[0],400)
        self.assertEqual(self.request("GET","/api/v4/scans/missing")[0],404)

    def test_manual_deep_audit_is_explicit_and_durable(self):
        captured=[]
        def scan(dataset,progress=None,scan_mode="normal"):
            captured.append((dataset,scan_mode))
            if progress:progress("catalog_refresh",45)
            return {"status":"completed","scan_mode":scan_mode,"target_count":0}
        self.app.service.scan=scan
        status,_,body=self.request("POST","/api/v4/scans",{"dataset":"real","mode":"deep_audit"})
        self.assertEqual(status,202)
        job=self.complete(body["data"]["job_id"])
        self.assertEqual((job["scan_mode"],job["result"]["scan_mode"]),("deep_audit","deep_audit"))
        self.assertEqual(captured,[("real","deep_audit")])
    def test_job_survives_runtime_restart(self):
        self.app.service.scan=lambda dataset,progress=None:{"status":"completed","target_count":0}
        id=self.request("POST","/api/v4/scans",{"dataset":"real"})[2]["data"]["job_id"]
        self.complete(id);self.app.close()
        self.app=create_runtime(Path(self.temp.name)/"runtime.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"})
        self.assertEqual(self.request("GET","/api/v4/scans/"+id)[2]["data"]["status"],"completed")
    def test_interrupted_job_is_failed_on_restart(self):
        with self.app.service.store._connect() as db:
            db.execute("""INSERT INTO v4_scan_jobs(job_id,dataset,status,progress,stage,created_at,
                started_at,completed_at,result,error) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("interrupted","real","running",50,"release_resolver","2026-01-01T00:00:00Z",None,None,None,None))
        self.app.close();self.app=create_runtime(Path(self.temp.name)/"runtime.sqlite3",{"real":"tests/v4/fixtures/production_metadata_v1.json"})
        job=self.request("GET","/api/v4/scans/interrupted")[2]["data"]
        self.assertEqual(job["status"],"failed");self.assertIn("interrupted",job["error"]["code"])
    def test_frontend_is_series_first_and_automatic_matches_need_no_approval(self):
        _,_,source=self.request("GET","/assets/app.js")
        self.assertIn("renderSeriesDetail",source)
        self.assertIn("seasonCard",source)
        self.assertIn("data-open-series",source)
        self.assertIn("data-override",source)
        self.assertIn("Mapping deterministico completo. Nessuna approvazione manuale richiesta.",source)
        self.assertIn("source_urls",source)
        self.assertIn("manual_approved",source)
        self.assertIn("ignore_errors",source)
        self.assertIn("auto_scan_enabled",source)
        self.assertIn("auto_scan_interval_minutes",source)
        self.assertIn("state.filter",source)
        self.assertNotIn("data-action=\\\"approve\\\"",source)
        self.assertIn("loadSeriesPages", source)
        self.assertIn("offset=", source)
        self.assertIn("mutations_allowed",source)
        self.assertIn("readOnlyBanner",source)
        self.assertNotIn('onclick="location.reload()"',source)
    def test_notification_outbox_endpoint_is_transport_neutral(self):
        with self.app.service.store._connect() as db:
            self.app.service.store._event(db,None,"scan_failed",{"code":"fixture"},"test")
        status,_,body=self.request("GET","/api/v4/notifications?limit=10")
        self.assertEqual(status,200)
        self.assertEqual(body["data"]["items"][0]["topic"],"error")
        self.assertEqual({b["id"] for b in body["data"]["backends"]},{"outbox","telegram"})

    def test_audit_label_is_local_user(self):
        self.app.service.store.update_settings({"display_language":"it"})
        event=self.app.service.store.activity()[0]
        self.assertEqual(event["actor"],"local-user")

    def test_activity_runtime_is_latest_first_and_cursor_does_not_repeat(self):
        for locale in ("it","en","it"):self.app.service.store.update_settings({"display_language":locale})
        page=self.request("GET","/api/v4/activity?limit=2")[2]["data"]
        self.assertEqual([i["sequence"] for i in page["items"]],[3,2])
        older=self.request("GET","/api/v4/activity?limit=2&before=2")[2]["data"]
        self.assertEqual([i["sequence"] for i in older["items"]],[1])
        self.assertEqual(self.request("GET","/api/v4/activity?before=-1")[0],400)
    def test_activity_simple_timeline_excludes_automatic_item_flood_but_advanced_keeps_it(self):
        self.app.service.scan("real")
        self.app.service.store.update_settings({"display_language":"en"})
        page=self.request("GET","/api/v4/activity?limit=50")[2]["data"]
        self.assertEqual([e["action"] for e in page["items"]],["settings_updated","scan_completed"])
        raw=self.request("GET","/api/v4/advanced/activity?limit=50&after=0")[2]["data"]
        self.assertTrue(any(e["action"] in {"proposal_created","review_created"} for e in raw["items"]))
        self.assertEqual(self.request("GET","/api/v4/activity?limit=1&before="+str(page["next_cursor"]))[2]["data"]["items"],[])
        nadia=next(i for i in self.app.service.store.list_items() if "182:1" in {t["target_id"] for t in i["original"]["targets"]})
        self.assertEqual(self.request("POST","/api/v4/reviews/"+nadia["id"]+"/approve",{"expected_revision":0,"reason":"Verified episode coverage"})[0],200)
        decision=self.request("GET","/api/v4/activity?limit=1")[2]["data"]["items"][0]
        self.assertEqual((decision["action"],decision["actor"],decision["entity_title"],decision["reason"]),("approve","local-user","Nadia: The Secret of Blue Water","Verified episode coverage"))

    def test_real_scan_job_outputs_public_coverage_and_variant_summary(self):
        from unittest.mock import patch
        with patch("urllib.request.OpenerDirector.open",side_effect=AssertionError("network forbidden")):
            id=self.request("POST","/api/v4/scans",{"dataset":"real"})[2]["data"]["job_id"]
            job=self.complete(id)
        self.assertEqual(job["status"],"completed");self.assertEqual(job["result"]["target_count"],12)
        items=self.request("GET","/api/v4/mappings?limit=100")[2]["data"]["items"]
        black=next(i for i in items if i["title"]=="Black Lagoon")
        self.assertEqual(black["coverage_status"],"complete");self.assertEqual(black["episode_count"],24)
        self.assertTrue(all(v["audio"]=="DUB" for v in black["selected_variants"]))
        self.assertNotIn("https://",json.dumps(items))
    def test_real_approve_reject_are_local_user_audited_and_keep_original(self):
        self.app.service.scan("real")
        item=next(i for i in self.app.service.store.list_items() if '182:1' in {t['target_id'] for t in i['original']['targets']})
        original=json.dumps(item['original'],sort_keys=True)
        status,_,body=self.request('POST','/api/v4/reviews/'+item['id']+'/approve',{'expected_revision':0,'reason':'UI contract verification'})
        self.assertEqual(status,200);self.assertEqual(body['data']['human_decision']['actor'],'local-user')
        self.assertRegex(body['data']['plan']['segments'][0]['premiere_date'],r'^\d{4}-\d{2}-\d{2}$')
        self.assertTrue(any(v['selected'] for v in body['data']['plan']['segments'][0]['variants']))
        self.assertEqual(self.request('POST','/api/v4/reviews/'+item['id']+'/reopen',{'expected_revision':1,'reason':'Reopen test'})[0],200)
        self.assertEqual(self.request('POST','/api/v4/reviews/'+item['id']+'/reject',{'expected_revision':2,'reason':'Reject test'})[0],200)
        self.assertEqual(json.dumps(self.app.service.store.get_item(item['id'])['original'],sort_keys=True),original)
        self.assertEqual([e['action'] for e in self.app.service.store.activity() if e['item_id']==item['id']][-3:],['approve','reopen','reject'])

    def test_malformed_static_host_is_rejected_without_runtime_exception(self):
        self.assertEqual(self.request('GET','/',HTTP_HOST='[bad')[0],403)
