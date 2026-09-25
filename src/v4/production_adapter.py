"""Dormant production-shaped Sonarr/download adapter.

Nothing in the runtime imports or constructs these capabilities. Tests use an
isolated permit, loopback Sonarr double, fake downloader, and temporary paths.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.request
import urllib.parse
from urllib.parse import urlsplit

from .execution import DownstreamFailure,ProductionEffectAuthorization


_KEY=re.compile(r"^[0-9a-f]{64}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def _origin(value,*,loopback_only):
    parsed=urlsplit(value)
    if parsed.scheme not in ({"http"} if loopback_only else {"http","https"}) or not parsed.hostname or parsed.port is None:
        raise ValueError("An explicit Sonarr origin is required")
    if loopback_only and parsed.hostname not in {"127.0.0.1","::1"}:raise ValueError("Isolated tests require loopback Sonarr")
    if parsed.username or parsed.password or parsed.path not in {"","/"} or parsed.query or parsed.fragment:raise ValueError("Sonarr origin must not contain credentials or a path")
    return value.rstrip("/")


def _safe_root(value):
    root=Path(value).resolve()
    if root==Path(root.anchor):raise ValueError("A filesystem root is not an allowed effect boundary")
    return root


@dataclass(frozen=True)
class IsolatedTestPermit:
    staging_root: Path
    library_roots: tuple
    sonarr_origin: str
    production_effects: bool=False
    mode: str="isolated-test"

    def __post_init__(self):
        staging=_safe_root(self.staging_root);libraries=tuple(_safe_root(v) for v in self.library_roots)
        if not libraries or staging in libraries:raise ValueError("Separate staging and library roots are required")
        object.__setattr__(self,"staging_root",staging);object.__setattr__(self,"library_roots",libraries)
        object.__setattr__(self,"sonarr_origin",_origin(self.sonarr_origin,loopback_only=True))


@dataclass(frozen=True)
class ProductionEffectPermit:
    staging_root: Path
    library_roots: tuple
    sonarr_origin: str
    authorization: ProductionEffectAuthorization
    production_effects: bool=True
    mode: str="production"

    @classmethod
    def from_authorization(cls,authorization,*,staging_root,library_roots,sonarr_origin):
        if not isinstance(authorization,ProductionEffectAuthorization):raise ValueError("Production authorization object is required")
        return cls(_safe_root(staging_root),tuple(_safe_root(v) for v in library_roots),_origin(sonarr_origin,loopback_only=False),authorization)


@dataclass(frozen=True)
class ProductionAdapterConfig:
    """Explicit deployment input. There is deliberately no environment/default loader."""
    sonarr_origin: str
    sonarr_api_key_file: Path
    staging_root: Path
    library_roots: tuple
    journal_root: Path


def _read_api_key(path):
    path=Path(path).resolve()
    try:
        if not path.is_file() or not 1<=path.stat().st_size<=513:raise ValueError("Invalid Sonarr secret file")
        value=path.read_text(encoding="utf-8").strip()
    except OSError:raise ValueError("Sonarr secret file is unavailable") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,512}",value):raise ValueError("Invalid Sonarr secret value")
    return value


def build_production_adapter(config,authorization,*,downloader=None,opener=None):
    """Construct a dormant adapter only from explicit config, secret file and auth."""
    if not isinstance(config,ProductionAdapterConfig):raise ValueError("Explicit production adapter config is required")
    permit=ProductionEffectPermit.from_authorization(authorization,staging_root=config.staging_root,library_roots=config.library_roots,sonarr_origin=config.sonarr_origin)
    client=SonarrV3Client(permit.sonarr_origin,_read_api_key(config.sonarr_api_key_file),permit=permit,opener=opener)
    return ProductionDownstreamAdapter(permit,client,downloader or AnimeWorldEpisodeDownloader(),journal_root=config.journal_root)


class SonarrV3Client:
    """Bounded client for Sonarr v3 status, series lookup and rescan commands."""
    def __init__(self,base_url,api_key,*,permit,timeout=5,max_response_bytes=262144,opener=None):
        if base_url!=permit.sonarr_origin:raise ValueError("Sonarr origin differs from the effect permit")
        if not isinstance(api_key,str) or not api_key or len(api_key)>512:raise ValueError("Sonarr API key is required")
        if not 0<timeout<=15 or not 1024<=max_response_bytes<=1048576:raise ValueError("Invalid Sonarr limits")
        self.base=base_url;self._key=api_key;self.timeout=timeout;self.limit=max_response_bytes
        self.opener=opener or urllib.request.build_opener(_NoRedirect())

    def _request(self,method,path,payload=None):
        raw=json.dumps(payload,separators=(",",":")).encode() if payload is not None else None
        request=urllib.request.Request(self.base+"/api/v3"+path,data=raw,method=method,headers={"X-Api-Key":self._key,"Accept":"application/json","Content-Type":"application/json"})
        try:response=self.opener.open(request,timeout=self.timeout)
        except urllib.error.HTTPError:raise DownstreamFailure("sonarr_http_error") from None
        except Exception:raise DownstreamFailure("sonarr_unavailable") from None
        with response:
            if response.status<200 or response.status>=300 or response.headers.get_content_type()!="application/json":raise DownstreamFailure("invalid_sonarr_response")
            data=response.read(self.limit+1)
        if len(data)>self.limit:raise DownstreamFailure("sonarr_response_too_large")
        try:value=json.loads(data)
        except (UnicodeDecodeError,ValueError):raise DownstreamFailure("invalid_sonarr_response") from None
        if not isinstance(value,dict):raise DownstreamFailure("invalid_sonarr_response")
        return value

    def status(self):return self._request("GET","/system/status")
    def series(self,series_id):return self._request("GET",f"/series/{int(series_id)}")
    def rescan_series(self,series_id):return self._request("POST","/command",{"name":"RescanSeries","seriesId":int(series_id)})
    def wanted_missing(self):
        rows=[];page=1
        while page<=100:
            query=urllib.parse.urlencode({"includeSeries":"true","pageSize":100,"page":page})
            value=self._request("GET","/wanted/missing?"+query);batch=value.get("records") or []
            if not isinstance(batch,list):raise DownstreamFailure("invalid_sonarr_response")
            rows.extend(value for value in batch if isinstance(value,dict))
            if len(batch)<100:break
            page+=1
        return rows
    def queue_episode_ids(self):
        query=urllib.parse.urlencode({"includeUnknownSeriesItems":"false","includeSeries":"false",
            "includeEpisode":"true","pageSize":1000,"page":1})
        value=self._request("GET","/queue?"+query);rows=value.get("records") or []
        if not isinstance(rows,list):raise DownstreamFailure("invalid_sonarr_response")
        return {int(row["episodeId"]) for row in rows if isinstance(row,dict) and isinstance(row.get("episodeId"),int)}


class AnimeWorldEpisodeDownloader:
    """Minimal source port; dependency is imported lazily and can be replaced in tests."""
    def __init__(self,anime_factory=None):self._factory=anime_factory
    def fetch(self,source_url,source_episode,title,destination):
        if self._factory is None:
            import animeworld
            factory=animeworld.Anime
        else:factory=self._factory
        episodes=factory(source_url).getEpisodes()
        episode=next((value for value in episodes if str(getattr(value,"number",""))==str(source_episode)),None)
        if episode is None:raise DownstreamFailure("source_episode_unavailable")
        destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
        try:name=episode.download(title,destination)
        except Exception:raise DownstreamFailure("download_failed") from None
        path=(destination/str(name)).resolve()
        if path.parent!=destination or not path.is_file() or path.is_symlink():raise DownstreamFailure("invalid_download_artifact")
        return path


class ProductionDownstreamAdapter:
    def __init__(self,permit,sonarr,downloader,*,journal_root):
        if not isinstance(permit,(IsolatedTestPermit,ProductionEffectPermit)):raise ValueError("An explicit effect permit is required")
        self.permit=permit;self.sonarr=sonarr;self.downloader=downloader;self.journal_root=_safe_root(journal_root);self.last_manifest=None
        self.production_effects=permit.production_effects
        self.production_authorization=permit.authorization if isinstance(permit,ProductionEffectPermit) else None

    def probe(self):
        if not self.permit.staging_root.is_dir() or any(not root.is_dir() for root in self.permit.library_roots):
            raise DownstreamFailure("effect_path_unavailable")
        status=self.sonarr.status()
        if not isinstance(status.get("version"),str) or not status["version"]:raise DownstreamFailure("invalid_sonarr_response")
        return {"ready":True,"adapter":"sonarr-download-v1","mode":self.permit.mode,
            "production_effects":self.production_effects}

    def _journal(self,key):
        if not isinstance(key,str) or not _KEY.fullmatch(key):raise DownstreamFailure("invalid_execution_key")
        return self.journal_root/f"{key}.json"

    @staticmethod
    def _read(path):
        try:return json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError):return None

    def _write(self,path,value,*,exclusive=False):
        self.journal_root.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix(".tmp")
        try:
            if exclusive:
                with path.open("x",encoding="utf-8",newline="\n") as handle:
                    json.dump(value,handle,sort_keys=True,separators=(",",":"));handle.flush();os.fsync(handle.fileno())
                self._fsync_directory(self.journal_root)
                return
            with temporary.open("w",encoding="utf-8",newline="\n") as handle:
                json.dump(value,handle,sort_keys=True,separators=(",",":"));handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,path)
            self._fsync_directory(self.journal_root)
        except FileExistsError:raise DownstreamFailure("remote_state_unknown",outcome_unknown=True) from None
        except Exception:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
            raise DownstreamFailure("journal_write_failed") from None

    @staticmethod
    def _fsync_directory(path):
        try:descriptor=os.open(path,os.O_RDONLY)
        except (AttributeError,OSError,TypeError):return
        try:os.fsync(descriptor)
        except OSError:pass
        finally:os.close(descriptor)

    @staticmethod
    def _artifact_digest(path):
        value=hashlib.sha256();size=0
        with Path(path).open("rb") as handle:
            while True:
                chunk=handle.read(1024*1024)
                if not chunk:break
                size+=len(chunk);value.update(chunk)
        return size,value.hexdigest()

    def _valid_artifact(self,path,artifact,*,source):
        resolved=Path(path).resolve()
        root=self.permit.staging_root if source else self._inside_library(resolved)
        if root is None or not resolved.is_file() or resolved.is_symlink():return False
        if source and self.permit.staging_root not in resolved.parents:return False
        try:size,fingerprint=self._artifact_digest(resolved)
        except OSError:return False
        expected=artifact.get("sha256")
        return size==artifact.get("size") and isinstance(expected,str) and hmac.compare_digest(fingerprint,expected)

    def lookup(self,execution_key):
        path=self._journal(execution_key)
        if not path.exists():return {"status":"absent"}
        record=self._read(path)
        if not isinstance(record,dict) or record.get("execution_key")!=execution_key:return {"status":"unknown"}
        if record.get("state")=="completed" and isinstance(record.get("receipt"),dict):return {"status":"completed","receipt":record["receipt"]}
        if record.get("state")=="failed":return {"status":"failed","error_category":record.get("error_category")}
        return {"status":"unknown"}

    def reconcile(self,execution_key):
        """Resume only the same journaled effect identity after validating bytes."""
        path=self._journal(execution_key)
        if not path.exists():return {"status":"absent"}
        record=self._read(path)
        if not isinstance(record,dict) or record.get("execution_key")!=execution_key:return {"status":"unknown"}
        state=record.get("state")
        if state=="completed" and isinstance(record.get("receipt"),dict):
            return {"status":"completed","receipt":record["receipt"]}
        if state=="failed":return {"status":"failed","error_category":record.get("error_category")}
        if state=="preparing":
            staging=(self.permit.staging_root/execution_key).resolve()
            if self.permit.staging_root not in staging.parents:return {"status":"unknown"}
            shutil.rmtree(staging,ignore_errors=True)
            try:path.unlink();self._fsync_directory(self.journal_root)
            except OSError:return {"status":"unknown"}
            return {"status":"absent"}
        # Once rescan dispatch has begun, the remote command outcome cannot be
        # inferred from local files. Never issue it a second time.
        if state=="rescanning":return {"status":"unknown"}
        if state not in {"dispatching","moving"}:return {"status":"unknown"}
        artifacts=record.get("artifacts");series_ids=record.get("series_ids")
        if not isinstance(artifacts,list) or not artifacts or not isinstance(series_ids,list):return {"status":"unknown"}
        for artifact in artifacts:
            if not isinstance(artifact,dict) or set(artifact)!={"source","destination","series_id","size","sha256"}:
                return {"status":"unknown"}
            source=Path(artifact["source"]);destination=Path(artifact["destination"])
            destination_ok=self._valid_artifact(destination,artifact,source=False)
            source_ok=self._valid_artifact(source,artifact,source=True)
            if destination_ok:
                if source.exists():return {"status":"unknown"}
                continue
            if not source_ok or destination.exists():return {"status":"unknown"}
            try:shutil.move(source,destination)
            except Exception:return {"status":"unknown"}
            try:self._write(path,{**record,"state":"moving"})
            except DownstreamFailure:return {"status":"unknown"}
            record={**record,"state":"moving"}
        try:
            self._write(path,{**record,"state":"rescanning"})
            for series_id in sorted(set(series_ids)):self.sonarr.rescan_series(int(series_id))
        except Exception:return {"status":"unknown"}
        receipt={"adapter":"sonarr-download-v1","receipt_id":execution_key,
            "mode":self.permit.mode,"production_effects":self.permit.production_effects}
        completed={**record,"state":"completed","receipt":receipt,"artifact_count":len(artifacts)}
        try:self._write(path,completed)
        except DownstreamFailure:return {"status":"unknown"}
        self.last_manifest=completed
        return {"status":"completed","receipt":receipt}

    def _inside_library(self,path):
        resolved=Path(path).resolve()
        return next((root for root in self.permit.library_roots if resolved==root or root in resolved.parents),None)

    def execute(self,envelope):
        key=envelope.get("execution_key")
        if self.production_effects is True and (self.production_authorization is None or not self.production_authorization.permits_execution(key)):
            raise DownstreamFailure("production_effect_forbidden")
        state=self.lookup(key)
        if state["status"]=="completed":return state["receipt"]
        if state["status"]!="absent":raise DownstreamFailure("remote_state_unknown",outcome_unknown=True)
        path=self._journal(key);self._write(path,{"schema_version":1,"execution_key":key,"state":"preparing"},exclusive=True)
        artifacts=[];series_paths={}
        try:
            for segment_index,segment in enumerate(envelope.get("segments",[])):
                grouped={}
                for link in segment.get("episode_links",[]):
                    grouped.setdefault(link.get("source_episode"),[]).append(link)
                for source_index,(source_episode,links) in enumerate(grouped.items()):
                    if not isinstance(source_episode,int) or source_episode<=0:raise DownstreamFailure("invalid_execution_envelope")
                    series_ids={link.get("sonarr_series_id") for link in links}
                    if len(series_ids)!=1:raise DownstreamFailure("multi_series_source_episode_unsupported")
                    series_id=next(iter(series_ids))
                    if not isinstance(series_id,int) or any(link.get("target_id") not in envelope.get("target_ids",[]) for link in links):
                        raise DownstreamFailure("invalid_execution_envelope")
                    if series_id not in series_paths:
                        series=self.sonarr.series(series_id);destination=series.get("path")
                        root=self._inside_library(destination) if isinstance(destination,str) else None
                        if root is None or not Path(destination).resolve().is_dir():raise DownstreamFailure("sonarr_library_path_forbidden")
                        series_paths[series_id]=Path(destination).resolve()
                    destinations=sorted({(int(link["sonarr_season"]),int(link["sonarr_episode"])) for link in links})
                    seasons={season for season,_ in destinations}
                    if len(seasons)!=1:raise DownstreamFailure("cross_season_source_episode_unsupported")
                    season=next(iter(seasons));episode_numbers=[episode for _,episode in destinations]
                    if episode_numbers!=list(range(episode_numbers[0],episode_numbers[-1]+1)):
                        raise DownstreamFailure("noncontiguous_multi_episode_source")
                    folder=(self.permit.staging_root/key/f"{segment_index}-{source_index}").resolve()
                    if self.permit.staging_root not in folder.parents:raise DownstreamFailure("staging_path_forbidden")
                    folder.mkdir(parents=True,exist_ok=False)
                    suffix=f"E{episode_numbers[0]:02d}" if len(episode_numbers)==1 else f"E{episode_numbers[0]:02d}-E{episode_numbers[-1]:02d}"
                    title=f"{series_id} - S{season:02d}{suffix}"
                    source=Path(self.downloader.fetch(segment["source_url"],source_episode,title,folder)).resolve()
                    if folder not in source.parents or not source.is_file() or source.is_symlink():raise DownstreamFailure("invalid_download_artifact")
                    destination=(series_paths[series_id]/source.name).resolve()
                    if self._inside_library(destination) is None or destination.exists():raise DownstreamFailure("library_destination_conflict")
                    size,fingerprint=self._artifact_digest(source)
                    artifacts.append({"source":str(source),"destination":str(destination),"series_id":series_id,
                        "size":size,"sha256":fingerprint})
            if not artifacts:raise DownstreamFailure("empty_execution")
        except DownstreamFailure as error:
            self._write(path,{"schema_version":1,"execution_key":key,"state":"failed","error_category":error.category})
            raise
        except Exception:
            self._write(path,{"schema_version":1,"execution_key":key,"state":"failed","error_category":"adapter_error"})
            raise DownstreamFailure("adapter_error") from None
        dispatching={"schema_version":1,"execution_key":key,"state":"dispatching","artifacts":artifacts,"series_ids":sorted(series_paths)}
        self._write(path,dispatching);self.last_manifest=dispatching
        result=self.reconcile(key)
        if result.get("status")!="completed":raise DownstreamFailure("remote_state_unknown",outcome_unknown=True)
        return result["receipt"]
