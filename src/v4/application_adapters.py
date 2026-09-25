"""Configured offline metadata adapter/enricher boundary for application scans.

No client-provided paths, credentials, remote URLs or implicit network acquisition.
Future live read-only adapters must implement this interface separately.
"""
import json
from .production_sources import local_output,public_url
from .production_replay import snapshot as derive_snapshot,catalog_entry_from_detail

class SnapshotAdapter:
    def __init__(self,path):self.path=local_output(path)

    def read(self):
        value=json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version")!=1 or value.get("kind") not in {"production_metadata_replay","stable_sanitized_production_subset"}:
            raise ValueError("Expected sanitized production metadata snapshot v1")
        if not isinstance(value.get("cases"),list) or not value["cases"]:raise ValueError("Snapshot has no cases")
        for case in value["cases"]:
            for entry in case["raw"]["catalog_entries"]:public_url(entry["url"])
            for url in case["expected_v3_decision"]["urls"]:public_url(url)
        for entry in (value.get("provider_catalog") or {}).get("entries",[]):
            if isinstance(entry,dict) and entry.get("url"):public_url(entry["url"])
        return value

class SnapshotEnricher:
    def enrich(self,snapshot):
        # Rebuild from native catalog/Sonarr observations rather than trusting cached derived candidates.
        # The production adapter already quarantines false V3 table labels and preserves alias scope.
        series={};episodes={};catalog={};details={};mappings=[];manual={};pools={};availability={};external={};seen_mappings=set()
        for case in snapshot["cases"]:
            raw=case["raw"];row=raw["sonarr_series"];sid=row["id"]
            if raw.get("source_absence"):
                availability[case["derived"]["target"]["target_id"]]=raw["source_absence"]
            if raw.get("external_metadata") is not None:
                marker=json.dumps(raw["external_metadata"],sort_keys=True)
                previous=external.get(str(sid))
                if previous is not None and json.dumps(previous,sort_keys=True)!=marker:raise ValueError("Conflicting external metadata observations")
                external[str(sid)]=raw["external_metadata"]
            if sid in series and series[sid]!=row:raise ValueError("Conflicting Sonarr series observations")
            series[sid]=row
            episodes.setdefault(str(sid),{})
            for e in [*raw["sonarr_episodes"],*raw.get("sonarr_special_episodes",[])]:episodes[str(sid)][(e["seasonNumber"],e["episodeNumber"])]=e
            pools.setdefault(str(sid),{})
            for e in raw["catalog_entries"]:
                if e.get("source")=="table":continue
                marker=(e.get("url"),e.get("title"));catalog[marker]=e;pools[str(sid)][marker]=e
            details.update(raw["details"])
            for detail in raw["details"].values():
                native=catalog_entry_from_detail(detail)
                if native:
                    marker=(native["url"],native["title"]);catalog[marker]=native;pools[str(sid)][marker]=native
            for m in case["expected_v3_decision"].get("raw_mapping_rows",[]):
                marker=json.dumps(m,sort_keys=True)
                if marker not in seen_mappings:mappings.append(m);seen_mappings.add(marker)
            aliases=raw.get("manual_aliases",{})
            if isinstance(aliases,list):manual[row["title"]]=aliases
        rebuilt=derive_snapshot(list(series.values()),{sid:list(es.values()) for sid,es in episodes.items()},
            list(catalog.values()),mappings,details,manual,pools={sid:list(es.values()) for sid,es in pools.items()},availability=availability,external=external)
        for key in ("provider_catalog","language_policy"):
            if key in snapshot:rebuilt[key]=json.loads(json.dumps(snapshot[key]))
        wanted={f"{case['raw']['sonarr_series']['id']}:{case['raw']['sonarr_season']['seasonNumber']}" for case in snapshot["cases"]}
        wanted_series={case["raw"]["sonarr_series"]["id"] for case in snapshot["cases"]}
        rebuilt["cases"]=[c for c in rebuilt["cases"] if c["derived"]["target"]["target_id"] in wanted or
            (c["raw"]["sonarr_series"]["id"] in wanted_series and c["derived"]["target"]["season_number"]==0)]
        return rebuilt
