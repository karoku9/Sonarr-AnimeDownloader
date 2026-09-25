"""Offline backup/readiness controls for a future, separately authorized cutover."""
import hashlib
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from .execution import ProductionEffectAuthorization


class SQLiteBackupVerifier:
    @staticmethod
    def _digest(path):
        value=hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b""):value.update(chunk)
        return value.hexdigest()

    @staticmethod
    def _logical_digest(path):
        value=hashlib.sha256()
        with closing(sqlite3.connect(Path(path).resolve())) as db:
            for statement in db.iterdump():
                value.update(statement.encode("utf-8"));value.update(b"\n")
        return value.hexdigest()

    @classmethod
    def create(cls,source,destination):
        source=Path(source).resolve();destination=Path(destination).resolve()
        if not source.is_file() or destination.exists() or source==destination:raise ValueError("Backup requires an existing source and new destination")
        destination.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(source)) as origin,closing(sqlite3.connect(destination)) as backup:
            origin.backup(backup);backup.commit()
        with closing(sqlite3.connect(destination)) as backup:
            result=backup.execute("PRAGMA integrity_check").fetchone()[0]
        if result!="ok":raise ValueError("Backup integrity check failed")
        return {"schema_version":1,"sha256":cls._digest(destination),"logical_sha256":cls._logical_digest(destination),"size_bytes":destination.stat().st_size,"integrity":"ok"}

    @classmethod
    def verify(cls,path,evidence):
        path=Path(path).resolve()
        if not path.is_file() or not isinstance(evidence,dict) or set(evidence)!={"schema_version","sha256","logical_sha256","size_bytes","integrity"}:return False
        try:
            with closing(sqlite3.connect(path)) as db:integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
            return evidence["schema_version"]==1 and evidence["integrity"]=="ok" and integrity=="ok" and evidence["size_bytes"]==path.stat().st_size and evidence["sha256"]==cls._digest(path) and evidence["logical_sha256"]==cls._logical_digest(path)
        except sqlite3.Error:return False

    @classmethod
    def restore_to_new_database(cls,backup_path,destination,evidence):
        backup_path=Path(backup_path).resolve();destination=Path(destination).resolve()
        if destination.exists() or not cls.verify(backup_path,evidence):raise ValueError("Verified backup and new restore destination are required")
        destination.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(backup_path)) as source,closing(sqlite3.connect(destination)) as target:
            source.backup(target);target.commit()
        restored_evidence={**evidence,"sha256":cls._digest(destination),"size_bytes":destination.stat().st_size}
        if not cls.verify(destination,restored_evidence):
            destination.unlink(missing_ok=True);raise ValueError("Restored database integrity check failed")
        return {"verified":True,"sha256":cls._digest(destination),"logical_sha256":cls._logical_digest(destination),"size_bytes":destination.stat().st_size}


class CutoverReadiness:
    @staticmethod
    def execution_health(store):
        attempts=store.execution_attempts()
        counts={state:sum(1 for value in attempts if value["status"]==state) for state in ("reserved","completed","failed")}
        return {"attempt_counts":counts,"writer_claim_count":len(store.writer_claims()),"unknown_outcomes":counts["reserved"]}

    @classmethod
    def evaluate(cls,store,*,backup_verified,adapter_ready,v3_quiescence):
        health=cls.execution_health(store);blockers=[]
        if not backup_verified:blockers.append("backup_not_verified")
        if not adapter_ready:blockers.append("adapter_not_ready")
        if health["unknown_outcomes"]:blockers.append("open_execution_reservations")
        if not isinstance(v3_quiescence,dict) or set(v3_quiescence)!={"v3_stopped","verification_method"} or v3_quiescence.get("v3_stopped") is not True or v3_quiescence.get("verification_method") not in {"service_stopped","shared_guard"}:
            blockers.append("v3_quiescence_not_verified")
        return {"ready":not blockers,"blockers":blockers,"health":health,"production_enabled":False}

    @staticmethod
    def validate_single_canary(readiness,envelope,authorization):
        if not isinstance(readiness,dict) or readiness.get("ready") is not True:raise ValueError("Cutover readiness is not satisfied")
        if not isinstance(envelope,dict) or envelope.get("schema_version")!=1 or not re.fullmatch(r"[0-9a-f]{64}",str(envelope.get("execution_key",""))) or not isinstance(envelope.get("target_ids"),(list,tuple)) or not envelope["target_ids"] or len(set(envelope["target_ids"]))!=len(envelope["target_ids"]) or any(not isinstance(value,str) or not value for value in envelope["target_ids"]) or not isinstance(envelope.get("segments"),list) or not envelope["segments"]:
            raise ValueError("One complete immutable execution envelope is required")
        if not isinstance(authorization,ProductionEffectAuthorization) or not authorization.permits_execution(envelope["execution_key"]):raise ValueError("Canary authorization must bind the execution key")
        return {"canary_ready":True,"execution_key":envelope["execution_key"],"batch_size":1,"production_enabled":False}
