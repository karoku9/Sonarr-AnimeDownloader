"""Dormant, marker-gated validation against an isolated loopback Sonarr."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .execution import DownstreamFailure
from .production_adapter import (
    IsolatedTestPermit,
    ProductionDownstreamAdapter,
    SonarrV3Client,
    _read_api_key,
)


CONFIRMATION="VALIDATE DISPOSABLE SONARR"
MARKER="ANIDOWN V4 DISPOSABLE SONARR VALIDATION\n"
EXPECTED_INSTANCE_NAME="Sonarr AniDown V4 Disposable Validation"
_FIELDS={
    "schema_version","purpose","sonarr_origin","expected_version","api_key_file",
    "validation_root","staging_root","library_root","journal_root","series_id",
}
_VERSION=re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")


def _contained(root,path,*,kind):
    original=Path(path)
    resolved=original.resolve()
    if resolved==root or root not in resolved.parents:raise ValueError(f"{kind} must be inside the validation root")
    if original.is_symlink():raise ValueError(f"{kind} must not be a symlink")
    return resolved


@dataclass(frozen=True)
class DisposableValidationConfig:
    sonarr_origin: str
    expected_version: str
    api_key_file: Path
    validation_root: Path
    staging_root: Path
    library_root: Path
    journal_root: Path
    series_id: int

    @classmethod
    def from_manifest(cls,manifest,confirmation):
        if confirmation!=CONFIRMATION or not isinstance(manifest,dict) or set(manifest)!=_FIELDS:
            raise ValueError("Exact disposable validation manifest and confirmation are required")
        if manifest.get("schema_version")!=1 or manifest.get("purpose")!="disposable-sonarr-validation":
            raise ValueError("Unsupported disposable validation manifest")
        root_input=Path(manifest.get("validation_root",""))
        root=root_input.resolve()
        if not root_input.is_dir() or root_input.is_symlink():raise ValueError("Validation root must be an existing real directory")
        marker=root/".anidown-v4-disposable"
        try:marked=marker.is_file() and not marker.is_symlink() and marker.read_text(encoding="utf-8")==MARKER
        except OSError:marked=False
        if not marked:raise ValueError("Exact disposable validation marker is required")
        staging=_contained(root,manifest.get("staging_root",""),kind="staging_root")
        library=_contained(root,manifest.get("library_root",""),kind="library_root")
        journal=_contained(root,manifest.get("journal_root",""),kind="journal_root")
        secret=_contained(root,manifest.get("api_key_file",""),kind="api_key_file")
        if len({staging,library,journal,secret})!=4:raise ValueError("Disposable validation paths must be distinct")
        if not staging.is_dir() or not library.is_dir():raise ValueError("Staging and library roots must already exist")
        if journal.exists() and not journal.is_dir():raise ValueError("Journal root must be absent or a directory")
        if not secret.is_file() or secret.is_symlink():raise ValueError("API key file must be a regular contained file")
        version=manifest.get("expected_version")
        if not isinstance(version,str) or not _VERSION.fullmatch(version):raise ValueError("A bounded exact Sonarr version is required")
        series_id=manifest.get("series_id")
        if not isinstance(series_id,int) or isinstance(series_id,bool) or not 0<series_id<=2147483647:
            raise ValueError("A positive disposable Sonarr series ID is required")
        permit=IsolatedTestPermit(staging_root=staging,library_roots=(library,),sonarr_origin=manifest.get("sonarr_origin"))
        return cls(permit.sonarr_origin,version,secret,root,staging,library,journal,series_id)


class _FixtureArtifactDownloader:
    def fetch(self,source_url,source_episode,title,destination):
        if source_url!="fixture://disposable-sonarr-validation" or source_episode!=1:
            raise DownstreamFailure("invalid_validation_fixture")
        destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
        path=destination/"anidown-v4-validation.mkv";path.write_bytes(b"anidown-v4-disposable-validation\n")
        return path


class DisposableSonarrValidation:
    def __init__(self,config):
        if not isinstance(config,DisposableValidationConfig):raise ValueError("Validated disposable configuration is required")
        self.config=config

    @staticmethod
    def _empty(path):
        return not path.exists() or (path.is_dir() and not any(path.iterdir()))

    def run(self):
        config=self.config
        if not all(self._empty(path) for path in (config.staging_root,config.library_root,config.journal_root)):
            raise ValueError("Disposable staging, library and journal roots must be empty")
        permit=IsolatedTestPermit(config.staging_root,(config.library_root,),config.sonarr_origin)
        key_value=_read_api_key(config.api_key_file)
        client=SonarrV3Client(permit.sonarr_origin,key_value,permit=permit)
        status=client.status();version=status.get("version")
        if status.get("instanceName")!=EXPECTED_INSTANCE_NAME:
            raise ValueError("Loopback Sonarr does not have the disposable validation identity")
        if version!=config.expected_version:raise ValueError("Disposable Sonarr version does not match the manifest")
        identity={"purpose":"disposable-sonarr-validation","origin":permit.sonarr_origin,"version":version,"series_id":config.series_id}
        execution_key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        target_id=f"disposable:{config.series_id}"
        envelope={
            "schema_version":1,"execution_key":execution_key,"item_id":"disposable-validation",
            "snapshot_digest":"0"*64,"approved_revision":0,"plan_id":"disposable-validation",
            "target_ids":[target_id],
            "segments":[{"release_id":"disposable-validation","selected_candidate_id":"fixture",
                "source_url":"fixture://disposable-sonarr-validation","episode_links":[{
                    "target_id":target_id,"sonarr_series_id":config.series_id,"source_episode":1,
                    "sonarr_season":1,"sonarr_episode":1,"absolute_episode":1,
                }]}],
        }
        adapter=ProductionDownstreamAdapter(permit,client,_FixtureArtifactDownloader(),journal_root=config.journal_root)
        receipt=adapter.execute(envelope)
        restarted_client=SonarrV3Client(permit.sonarr_origin,key_value,permit=permit)
        restarted=ProductionDownstreamAdapter(permit,restarted_client,_FixtureArtifactDownloader(),journal_root=config.journal_root)
        lookup=restarted.lookup(execution_key)
        if lookup.get("status")!="completed" or lookup.get("receipt")!=receipt:
            raise DownstreamFailure("restart_lookup_failed",outcome_unknown=True)
        return {
            "schema_version":1,"status":"completed","adapter":receipt["adapter"],
            "mode":receipt["mode"],"production_effects":False,"sonarr_version":version,
            "series_id":config.series_id,"execution_key":execution_key,
            "artifact_count":adapter.last_manifest["artifact_count"],
            "fresh_adapter_lookup":"completed","receipt_id_matches":receipt["receipt_id"]==execution_key,
            "isolated_paths":True,"instance_identity_verified":True,
        }


def load_manifest(path):
    manifest_path=Path(path)
    try:
        if not manifest_path.is_file() or manifest_path.is_symlink() or manifest_path.stat().st_size>16384:
            raise ValueError
        value=json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,ValueError,json.JSONDecodeError):
        raise ValueError("A bounded JSON validation manifest is required") from None
    if not isinstance(value,dict):raise ValueError("Validation manifest must be an object")
    return value
