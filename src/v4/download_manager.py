"""Wanted/missing scheduling for the single authoritative execution coordinator.

This module performs no download, move, rescan, rename, or durable effect-state
transition. The immutable execution ledger and downstream adapter own all effects.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

from .application_store import Conflict
from .execution import DownstreamFailure, ExecutionCoordinator


class CoordinatorDownloadQueue:
    """Poll Sonarr and submit each current mapping through its coordinator once."""
    def __init__(self, service, sonarr, coordinator, *, max_workers=3, poll_seconds=60,
        enabled=False, auto_sync=True):
        if not isinstance(coordinator, ExecutionCoordinator):
            raise ValueError("Download scheduling requires the authoritative ExecutionCoordinator")
        self.service=service;self.store=service.store;self.sonarr=sonarr;self.coordinator=coordinator
        self.max_workers=max_workers;self.poll_seconds=poll_seconds;self.enabled=bool(enabled);self.auto_sync=auto_sync
        self.stop_event=threading.Event();self.thread=None
        self.pool=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix="anidown-v4-execution")
        self._submitted=set();self._lock=threading.Lock()

    def start(self):
        if not self.enabled:return False
        if not self.auto_sync:return True
        self.thread=threading.Thread(target=self._loop,name="anidown-v4-execution-scheduler",daemon=True)
        self.thread.start();return True

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
        self.pool.shutdown(wait=False,cancel_futures=False)

    @staticmethod
    def _future(row):
        value=row.get("airDateUtc") or row.get("airDate")
        if not value:return False
        try:
            if str(value).endswith("Z"):value=str(value)[:-1]+"+00:00"
            aired=datetime.fromisoformat(str(value))
            if aired.tzinfo is None:aired=aired.replace(tzinfo=timezone.utc)
            return aired>datetime.now(timezone.utc)
        except ValueError:return False

    def _mapped_missing(self):
        library={row["series_id"]:row for row in self.service.series()["items"]}
        queued=self.sonarr.queue_episode_ids();result=[];manual_valid={};details={}
        for row in self.sonarr.wanted_missing():
            if not isinstance(row,dict) or row.get("monitored") is False or self._future(row):continue
            episode_id=row.get("id");series_id=row.get("seriesId")
            season_no=row.get("seasonNumber");episode_no=row.get("episodeNumber")
            if not all(isinstance(value,int) for value in (episode_id,series_id,season_no,episode_no)):continue
            if episode_id in queued or season_no<=0:continue
            if series_id not in library:continue
            if series_id not in details:
                try:details[series_id]=self.service.series_detail(series_id)
                except KeyError:continue
            series=details[series_id]
            season=next((value for value in series["seasons"] if value["season"]==season_no),None)
            if not season or not season.get("can_execute"):continue
            mode="automatic"
            if season["automation"]=="manual":
                key=(series_id,season_no)
                if key not in manual_valid:manual_valid[key]=self.service.manual_override_executable(*key)
                if not manual_valid[key]:continue
                mode="manual"
            elif season["automation"]!="auto":continue
            link=next((value for value in season.get("effective",{}).get("episodes",[])
                if value.get("episode")==episode_no and value.get("source_url")
                and isinstance(value.get("source_episode"),int)),None)
            if not link:continue
            item=self.store.get_item(season["mapping_id"])
            if item["mapping_state"] in {"rejected","dismissed","superseded"}:continue
            result.append({"episode_id":episode_id,"series_id":series_id,
                "target_id":season["target_id"],"series_title":series["title"],
                "season":season_no,"episode":episode_no,"source_episode":link["source_episode"],
                "audio":season.get("effective",{}).get("audio"),"mapping_id":item["id"],
                "mapping_revision":item["revision"],"execution_mode":mode})
        return result

    @staticmethod
    def _operation_key(row):
        return (row["mapping_id"],row["target_id"],row["episode"])

    def sync_missing(self,limit=None,episode_id=None):
        if not self.enabled:return {"enabled":False,"queued":0,"candidates":0}
        candidates=self._mapped_missing()
        if episode_id is not None:
            if not isinstance(episode_id,int) or isinstance(episode_id,bool) or episode_id<1:
                raise ValueError("Invalid Sonarr episode id")
            candidates=[row for row in candidates if row["episode_id"]==episode_id]
        if limit is not None:
            if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:
                raise ValueError("Invalid download sync limit")
            candidates=candidates[:limit]
        operations={self._operation_key(row):row for row in candidates}
        submitted=0
        with self._lock:
            for key,row in operations.items():
                if key in self._submitted:continue
                self._submitted.add(key);submitted+=1
                future=self.pool.submit(self._run_operation,key,row)
                future.add_done_callback(lambda _future,value=key:self._done(value))
        return {"enabled":True,"queued":submitted,"candidates":len(candidates)}

    def _done(self,key):
        with self._lock:self._submitted.discard(key)

    def _run_operation(self,key,row):
        try:
            if row["execution_mode"]=="manual":
                self.coordinator.execute_manual(row["mapping_id"],row["target_id"],
                    expected_revision=row["mapping_revision"],sonarr_episode=row["episode"])
            else:
                self.coordinator.execute_automatic(row["mapping_id"],
                    expected_revision=row["mapping_revision"],target_id=row["target_id"],
                    sonarr_episode=row["episode"])
        except (Conflict,DownstreamFailure):
            return

    def _loop(self):
        while not self.stop_event.is_set():
            try:self.sync_missing()
            except Exception as error:
                with self.store._connect() as db:
                    self.store._event(db,None,"runtime_error",{
                        "code":"download_sync_failed","stage":"wanted_missing",
                        "detail":type(error).__name__},"v4-downloader")
            if self.stop_event.wait(self.poll_seconds):return

    def status(self,history_limit=50):
        attempts=self.store.execution_attempts();active=[];history=[]
        for attempt in attempts:
            item=self.store.get_item(attempt["item_id"]);target=item["original"]["targets"][0]
            row={"job_id":attempt["execution_key"],"execution_key":attempt["execution_key"],
                "series_title":target.get("canonical_title"),"season":target.get("season_number"),
                "episode":0,"audio":item["original"].get("audio_preference"),
                "status":"reconciliation_required" if attempt["status"]=="reserved" else attempt["status"],
                "progress":100.0 if attempt["status"]=="completed" else 0.0,
                "created_at":attempt["created_at"],"completed_at":attempt["finished_at"]}
            (active if attempt["status"]=="reserved" else history).append(row)
        history=sorted(history,key=lambda value:value.get("completed_at") or "",reverse=True)[:history_limit]
        counts={status:sum(attempt["status"]==status for attempt in attempts)
            for status in ("reserved","completed","failed")}
        return {"enabled":self.enabled,"auto_sync":self.auto_sync,"max_concurrent":self.max_workers,
            "active":active,"history":history,"counts":counts}


# Compatibility name for local callers; this is a coordinator scheduler, not an
# independent effect executor.
DownloadQueue=CoordinatorDownloadQueue
