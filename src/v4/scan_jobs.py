"""One bounded offline scan worker; durable V4-only pollable job state."""
from concurrent.futures import ThreadPoolExecutor
import json,threading,uuid
from datetime import datetime,timezone
from .application_store import encode

class ScanJobs:
    def __init__(self,service):
        self.service=service;self.store=service.store;self.lock=threading.Lock()
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix="anidown-v4-shadow")
        with self.store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS v4_scan_jobs (job_id TEXT PRIMARY KEY,dataset TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL,stage TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,result TEXT,error TEXT)")
            columns={row["name"] for row in db.execute("PRAGMA table_info(v4_scan_jobs)")}
            if "scan_mode" not in columns:
                db.execute("ALTER TABLE v4_scan_jobs ADD COLUMN scan_mode TEXT NOT NULL DEFAULT 'normal'")
            interrupted=db.execute("SELECT job_id FROM v4_scan_jobs WHERE status IN ('queued','running')").fetchall()
            for row in interrupted:
                error={"code":"scan_interrupted","message":"Scan interrupted by runtime restart. Run a new scan."}
                db.execute("UPDATE v4_scan_jobs SET status='failed',completed_at=?,error=? WHERE job_id=?",(self.store._now(),encode(error),row[0]))
                self.store._event(db,None,"scan_failed",{"job_id":row[0],"code":"scan_interrupted"},"shadow_scan")
    def _dto(self,row):
        if row is None:raise KeyError("Unknown scan job")
        value=dict(row)
        for key in ("result","error"):value[key]=json.loads(value[key]) if value[key] else None
        return {**value,"mode":"shadow","external_writes":False}
    def get(self,id):
        with self.store._connect() as db:return self._dto(db.execute("SELECT * FROM v4_scan_jobs WHERE job_id=?",(id,)).fetchone())
    def list(self):
        with self.store._connect() as db:return [self._dto(row) for row in db.execute("SELECT * FROM v4_scan_jobs ORDER BY created_at DESC,rowid DESC LIMIT 20")]
    def start(self,dataset,actor="shadow_scan",scan_mode="normal"):
        if dataset not in self.service.datasets:raise ValueError("Unknown configured dataset")
        if scan_mode not in {"normal","deep_audit"}:raise ValueError("Invalid scan mode")
        with self.lock:
            with self.store._connect() as db:
                row=db.execute("SELECT * FROM v4_scan_jobs WHERE status IN ('queued','running') ORDER BY created_at LIMIT 1").fetchone()
                if row:return self._dto(row) # at most one active job, repeat clicks/refresh coalesce
                id=str(uuid.uuid4());now=self.store._now()
                db.execute("""INSERT INTO v4_scan_jobs(job_id,dataset,status,progress,stage,created_at,
                    started_at,completed_at,result,error,scan_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (id,dataset,"queued",0,"queued",now,None,None,None,None,scan_mode))
                self.store._event(db,None,"scan_started",{"job_id":id,"dataset":dataset,"scan_mode":scan_mode},actor)
            self.worker.submit(self._run,id,dataset,actor,scan_mode)
            return self.get(id)
    def _progress(self,id,stage,percent):
        with self.store._connect() as db:db.execute("UPDATE v4_scan_jobs SET stage=?,progress=? WHERE job_id=? AND status='running'",(stage,percent,id))
    def _run(self,id,dataset,actor="shadow_scan",scan_mode="normal"):
        with self.store._connect() as db:db.execute("UPDATE v4_scan_jobs SET status='running',stage='inventory_read',started_at=? WHERE job_id=?",(self.store._now(),id))
        try:
            arguments={"progress":lambda stage,percent:self._progress(id,stage,percent)}
            if scan_mode!="normal":arguments["scan_mode"]=scan_mode
            result=self.service.scan(dataset,**arguments)
            with self.store._connect() as db:db.execute("UPDATE v4_scan_jobs SET status='completed',progress=100,stage='completed',completed_at=?,result=? WHERE job_id=?",(self.store._now(),encode(result),id))
        except Exception:
            error={"code":"scan_failed","message":"Scan could not complete. Existing decisions are retained; retry the scan."}
            with self.store._connect() as db:
                db.execute("UPDATE v4_scan_jobs SET status='failed',stage='failed',completed_at=?,error=? WHERE job_id=?",(self.store._now(),encode(error),id))
                self.store._event(db,None,"scan_failed",{"job_id":id,"code":"scan_failed"},actor)
    def close(self):self.worker.shutdown(wait=True)

class AutoScanScheduler:
    def __init__(self,service,jobs,dataset="production"):
        self.service=service;self.store=service.store;self.jobs=jobs;self.dataset=dataset
        self.stop_event=threading.Event();self.thread=None

    @staticmethod
    def _parse(value):
        if not value:return None
        text=str(value)
        if text.endswith("Z"):text=text[:-1]+"+00:00"
        parsed=datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def start(self):
        self.thread=threading.Thread(target=self._loop,name="anidown-v4-auto-scan",daemon=True)
        self.thread.start()

    def _due(self,settings):
        if not settings.get("auto_scan_enabled"):return False
        with self.store._connect() as db:
            row=db.execute("""SELECT COALESCE(completed_at,started_at,created_at)
                FROM v4_scan_jobs ORDER BY created_at DESC,rowid DESC LIMIT 1""").fetchone()
        if row is None:return True
        last=self._parse(row[0]);now=datetime.now(timezone.utc)
        return (now-last).total_seconds()>=int(settings["auto_scan_interval_minutes"])*60

    def status(self):
        settings=self.store.settings()
        return {"enabled":bool(settings.get("auto_scan_enabled")),
            "interval_minutes":int(settings.get("auto_scan_interval_minutes",60)),
            "dataset":self.dataset}

    def _loop(self):
        while not self.stop_event.wait(5):
            try:
                settings=self.store.settings()
                if self._due(settings):
                    self.jobs.start(self.dataset,actor="auto_scan")
            except Exception:
                if self.stop_event.is_set():break
                try:
                    with self.store._connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        self.store._event(db,None,"scan_failed",
                            {"code":"auto_scan_scheduler_failed"},"auto_scan")
                except Exception:
                    pass

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
