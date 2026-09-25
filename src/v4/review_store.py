"""Explicit V4-local review persistence. Never stores mappings or runs shadow."""
from datetime import datetime, timezone
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

ROOT=Path(__file__).resolve().parents[2]

class ReviewRepository:
    def __init__(self,path):
        self.path=Path(path).resolve()
        if not self.path.is_relative_to(ROOT/"work"):
            raise ValueError("Review storage must be inside V4 work/ (ignored local storage)")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self._sqlite_lock=threading.Lock()
        self._sqlite_metrics={"lock_retries":0,"lock_failures":0,"last_locked_operation":None}
        # WAL is a database-level invariant, not a per-request optimization.  Set it
        # before schema work so every later connection observes the same policy.
        with closing(sqlite3.connect(self.path,timeout=1.0)) as db:
            with db:
                db.execute("PRAGMA busy_timeout=1000")
                mode=str(db.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
                if mode!="wal":raise RuntimeError("SQLite WAL initialization failed")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA foreign_keys=ON")
        with self._connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0,1):
                raise ValueError("Unsupported review storage schema")
            db.execute("CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, original TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL, notes TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolution TEXT)")
            db.execute("CREATE TRIGGER IF NOT EXISTS immutable_snapshot BEFORE UPDATE OF original,digest,created_at ON reviews BEGIN SELECT RAISE(ABORT,'Original review is immutable'); END")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=1.0)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=1000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        except sqlite3.OperationalError as error:
            if "locked" in str(error).casefold() or "busy" in str(error).casefold():
                with self._sqlite_lock:self._sqlite_metrics["lock_failures"]+=1
            raise
        finally:
            db.close()

    def _idempotent_write(self,label,operation,*,attempts=4):
        """Retry only caller-declared idempotent transactions after lock errors."""
        if not isinstance(label,str) or not label or not 1<=attempts<=8:
            raise ValueError("Invalid SQLite retry policy")
        for attempt in range(attempts):
            try:
                with self._connect() as db:return operation(db)
            except sqlite3.OperationalError as error:
                locked="locked" in str(error).casefold() or "busy" in str(error).casefold()
                if not locked or attempt+1>=attempts:raise
                with self._sqlite_lock:
                    self._sqlite_metrics["lock_retries"]+=1
                    self._sqlite_metrics["last_locked_operation"]=label
                # Bounded deterministic jitter prevents identical workers from
                # repeatedly colliding without making tests timing-random.
                jitter=(int(hashlib.sha256(f"{label}:{attempt}".encode()).hexdigest()[:4],16)%8)/1000
                time.sleep(min(0.2,0.02*(2**attempt)+jitter))

    def sqlite_diagnostics(self):
        with self._connect() as db:
            mode=str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            timeout=int(db.execute("PRAGMA busy_timeout").fetchone()[0])
        with self._sqlite_lock:metrics=dict(self._sqlite_metrics)
        return {"journal_mode":mode,"busy_timeout_ms":timeout,**metrics}

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def create(self,review):
        if review.state!="open" or review.decision_snapshot.outcome=="matched":
            raise ValueError("Only original open reviews may be created")
        payload=json.dumps(review.to_dict(),sort_keys=True,ensure_ascii=False)
        now=self._now()
        with self._connect() as db:
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?)",(review.id,payload,hashlib.sha256(payload.encode()).hexdigest(),"open","",0,now,now,None))
        return self.get(review.id)

    def get(self,id):
        with self._connect() as db:
            row=db.execute("SELECT * FROM reviews WHERE id=?",(id,)).fetchone()
        if row is None:
            raise KeyError(id)
        result=dict(row)
        if hashlib.sha256(result["original"].encode()).hexdigest()!=result["digest"]:
            raise ValueError("Review snapshot integrity failure")
        result["original"]=json.loads(result["original"])
        result["resolution"]=json.loads(result["resolution"]) if result["resolution"] else None
        return result

    def list_open(self):
        with self._connect() as db:
            ids=[r[0] for r in db.execute("SELECT id FROM reviews WHERE state='open' ORDER BY created_at,id")]
        return [self.get(id) for id in ids]

    def _change(self,id,expected_revision,state,notes=None,resolution=None):
        with self._connect() as db:
            result=db.execute("UPDATE reviews SET state=?, notes=COALESCE(?,notes), resolution=?, revision=revision+1, updated_at=? WHERE id=? AND revision=? AND state='open'",(state,notes,json.dumps(resolution) if resolution else None,self._now(),id,expected_revision))
            if result.rowcount!=1:
                raise ValueError("Stale revision, closed review or unknown review")
        return self.get(id)

    def update(self,id,notes,*,expected_revision):
        return self._change(id,expected_revision,"open",notes=notes)

    def resolve(self,id,candidate_id,reason,*,expected_revision):
        if not reason.strip():
            raise ValueError("Resolution requires a reason")
        original=self.get(id)["original"]
        if candidate_id is not None:
            known={c["id"] for c in original["candidate_snapshots"]}
            rejected={e["candidate_id"] for e in original["decision_snapshot"]["evaluations"] if e["hard_rejected"]}
            if candidate_id not in known or candidate_id in rejected:
                raise ValueError("Candidate missing or structurally rejected; new metadata requires a new review")
        return self._change(id,expected_revision,"resolved",resolution={"candidate_id":candidate_id,"reason":reason,"timestamp":self._now()})

    def dismiss(self,id,reason,*,expected_revision):
        if not reason.strip():
            raise ValueError("Dismissal requires a reason")
        return self._change(id,expected_revision,"dismissed",resolution={"candidate_id":None,"reason":reason,"timestamp":self._now()})
