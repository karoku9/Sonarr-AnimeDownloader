"""Live Sonarr + AnimeWorld dataset for the production V4 runtime."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

from .animeworld_index import AnimeWorldIndex,CatalogShortlistIndex
from .application_adapters import SnapshotAdapter
from .application_dto import MATCHER_VERSION,RESOLVER_VERSION
from .production_replay import retrieve,snapshot,tvdb_relocated_specials
from .provider_validation import ProviderDetailCache,is_due
from .sonarr_eligibility import eligible_live_series


_SERIES_FIELDS=("id","title","originalTitle","cleanTitle","sortTitle","year","firstAired",
    "seriesType","status","ended","tvdbId","imdbId","tvMazeId")
_EP_FIELDS=("seasonNumber","episodeNumber","absoluteEpisodeNumber","sceneSeasonNumber",
    "sceneEpisodeNumber","sceneAbsoluteEpisodeNumber","airDate","airDateUtc","hasFile","monitored")


class LiveSonarrDataset:
    """Rebuild the immutable resolver input from live Sonarr on every requested scan."""

    def __init__(self,sonarr_origin,api_key_file,seed_snapshot,cache_root,*,timeout=20,host_header=None,
        detail_fetcher=None,clock=None,validation_budget=30,detail_request_budget=256,
        candidate_limit=128):
        self.origin=str(sonarr_origin).rstrip("/")
        parsed=urllib.parse.urlsplit(self.origin)
        if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid Sonarr origin")
        self.key_file=Path(api_key_file).resolve()
        self.host_header=str(host_header).strip() if host_header else None
        if self.host_header and any(ch in self.host_header for ch in "\r\n"):
            raise ValueError("Invalid Sonarr Host header")
        self.seed=SnapshotAdapter(seed_snapshot)
        self.cache=Path(cache_root).resolve();self.timeout=timeout
        self.catalog_path=self.cache/"animeworld-catalog.json"
        self.details_root=self.cache/"details"
        self.detail_cache=ProviderDetailCache(self.details_root,**({"fetcher":detail_fetcher} if detail_fetcher else {}),
            **({"clock":clock} if clock else {}))
        self.validation_budget=max(1,min(100,int(validation_budget)))
        self.detail_request_budget=max(1,min(1000,int(detail_request_budget)))
        self.candidate_limit=max(8,min(512,int(candidate_limit)))

    def _key(self):
        value=self.key_file.read_text(encoding="utf-8").strip()
        if not 16<=len(value)<=512:raise ValueError("Invalid Sonarr secret")
        return value

    def _get(self,route):
        headers={"X-Api-Key":self._key(),"Accept":"application/json"}
        if self.host_header:headers["Host"]=self.host_header
        req=urllib.request.Request(self.origin+"/api/v3/"+route,headers=headers,method="GET")
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            if response.status!=200:raise RuntimeError("Sonarr request failed")
            raw=response.read(16*1024*1024+1)
        if len(raw)>16*1024*1024:raise RuntimeError("Sonarr response too large")
        value=json.loads(raw)
        return value

    @staticmethod
    def _clean_series(series,tags):
        tag_names={int(t["id"]):str(t.get("label") or "") for t in tags
            if isinstance(t,dict) and isinstance(t.get("id"),int)}
        result=[]
        for row in series:
            out={k:row[k] for k in _SERIES_FIELDS if k in row}
            poster=next((str(image.get("remoteUrl")) for image in row.get("images",[])
                if isinstance(image,dict) and str(image.get("coverType") or "").casefold()=="poster"
                and str(image.get("remoteUrl") or "").startswith("https://")),None)
            if poster:out["poster_url"]=poster
            out["tag_labels"]=[tag_names[t] for t in row.get("tags",[]) if t in tag_names and tag_names[t]]
            out["alternateTitles"]=[{k:a[k] for k in ("title","seasonNumber","sceneSeasonNumber","sceneOrigin") if k in a}
                for a in row.get("alternateTitles",[]) if isinstance(a,dict)]

            out["seasons"]=[{"seasonNumber":s["seasonNumber"],"statistics":{
                k:v for k,v in s.get("statistics",{}).items()
                if k in ("episodeFileCount","episodeCount","totalEpisodeCount","previousAiring","nextAiring")}}
                for s in row.get("seasons",[]) if isinstance(s,dict) and "seasonNumber" in s]
            result.append(out)
        return result

    @staticmethod
    def _clean_episodes(rows):
        return [{k:e[k] for k in _EP_FIELDS if k in e} for e in rows if isinstance(e,dict)]

    @staticmethod
    def _seed_context(seed):
        manual={};external={};availability={};details={};mappings=[];seen=set();audio_overrides={}
        for case in seed.get("cases",[]):
            raw=case.get("raw") or {};row=raw.get("sonarr_series") or {};sid=row.get("id")
            aliases=raw.get("manual_aliases")
            if isinstance(aliases,list) and row.get("title"):manual[row["title"]]=list(aliases)
            if sid is not None and raw.get("external_metadata") is not None:
                external[str(sid)]=deepcopy(raw["external_metadata"])
            target=(case.get("derived") or {}).get("target") or {}
            if raw.get("source_absence") and target.get("target_id"):
                availability[target["target_id"]]=deepcopy(raw["source_absence"])
            details.update(deepcopy(raw.get("details") or {}))
            decision=case.get("expected_v3_decision") or {}
            for mapping in decision.get("raw_mapping_rows",[]):
                marker=json.dumps(mapping,sort_keys=True,ensure_ascii=False)
                if marker not in seen:mappings.append(deepcopy(mapping));seen.add(marker)
            target_id=target.get("target_id")
            chosen_urls={str(url) for url in decision.get("urls",[]) if isinstance(url,str)}
            if target_id and chosen_urls:
                by_url={str(c.get("url")):str(c.get("audio")) for c in (case.get("derived") or {}).get("candidates",[])
                    if c.get("url") and c.get("audio") in {"SUB","DUB"}}
                modes={by_url[url] for url in chosen_urls if url in by_url}
                if len(modes)==1:audio_overrides[target_id]=next(iter(modes))
        return manual,external,availability,details,mappings,audio_overrides

    def _detail(self,url,seed_details):
        return self.detail_cache.get(url,seed_details)

    @staticmethod
    def _eligible_series(series):
        return [row for row in series if eligible_live_series(row)]

    def _due_provider_validations(self,preserve_groups,seed_details):
        candidates=[]
        for group in preserve_groups or []:
            for url,fingerprint in sorted((group.get("source_fingerprints") or {}).items()):
                cached=self.detail_cache.peek(url,seed_details)
                if fingerprint is None or cached is None or is_due(cached):
                    candidates.append((url,group["item_id"],fingerprint))
        return candidates[:self.validation_budget],{item_id for _,item_id,_ in candidates[self.validation_budget:]}

    @staticmethod
    def _inventory_fingerprint(row,season,*,specials=(),context=None):
        """Fingerprint only cheap Sonarr series/season inventory observations."""
        statistics=season.get("statistics") or {}
        payload={
            "series_id":row.get("id"),"series_type":row.get("seriesType"),
            "status":row.get("status"),"ended":row.get("ended"),
            "tag_labels":sorted(str(value).casefold() for value in row.get("tag_labels",[])),
            "season":season.get("seasonNumber"),
            "episode_count":statistics.get("episodeCount"),
            "total_episode_count":statistics.get("totalEpisodeCount"),
            "episode_file_count":statistics.get("episodeFileCount"),
            "previous_airing":statistics.get("previousAiring"),
            "next_airing":statistics.get("nextAiring"),
            "series":{key:row.get(key) for key in _SERIES_FIELDS},
            "alternate_titles":row.get("alternateTitles") or [],
            "relocated_specials":list(specials),
            "matcher_version":MATCHER_VERSION,
            "resolver_version":RESOLVER_VERSION,
            "seed_context":context,
        }
        encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _inventory(cls,eligible,external,contexts=None):
        regular={};special_series=set()
        for row in eligible:
            seasons=[season for season in row.get("seasons",[]) if isinstance(season.get("seasonNumber"),int)]
            relocated=[{"season":0,"statistics":season.get("statistics") or {}} for season in seasons
                if season["seasonNumber"]==0 and int((season.get("statistics") or {}).get("totalEpisodeCount") or 0)>0
                and tvdb_relocated_specials(row,external.get(str(row["id"])))]
            positive=[season for season in seasons if season["seasonNumber"]>0]
            for season in positive:
                number=season.get("seasonNumber");stats=season.get("statistics") or {}
                total=stats.get("totalEpisodeCount");aired=stats.get("episodeCount");files=stats.get("episodeFileCount")
                complete=(isinstance(total,int) and total>0 and aired==total and isinstance(files,int) and files>=total)
                regular[f'{row["id"]}:{number}']={"series_id":int(row["id"]),"episode_count":total,
                    "complete_on_sonarr":complete,"fingerprint":cls._inventory_fingerprint(row,season,
                        specials=relocated,context=(contexts or {}).get(str(row["id"])))}
            if relocated and not positive:special_series.add(int(row["id"]))
        return regular,special_series

    def _verify_due_providers(self,preserve_groups,seed_details):
        selected,pending=self._due_provider_validations(preserve_groups,seed_details)
        results={}
        for url,item_id,expected in selected:
            results[(item_id,url)]=(self.detail_cache.get(url,seed_details),expected)
        return selected,pending,results

    def _load_candidate_details(self,urls,seed_details,*,max_workers=4):
        """Load cached details and bound new provider requests for dirty mappings."""
        details={};due=[]
        for url in urls:
            cached=self.detail_cache.peek(url,seed_details)
            if cached is not None and not is_due(cached):
                details[url]=cached
            else:
                due.append(url)
        selected=due[:self.detail_request_budget]
        pending=set(due[self.detail_request_budget:])
        self._last_detail_request_count=len(selected)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            values=list(pool.map(lambda url:self._detail(url,seed_details),selected))
        details.update(zip(selected,values))
        return details,pending

    @classmethod
    def _diff_inventory(cls,preserve_groups,regular,series_audio_master,validation_results,
        inventory_fingerprints=None):
        prior={str(key):value for key,value in (inventory_fingerprints or {}).items()}
        preserved_item_ids=[];preserved_target_ids=set();invalidated=[];stale_retry=set()
        for group in preserve_groups or []:
            group_fingerprints=group.get("target_fingerprints") or {}
            for url in (group.get("source_fingerprints") or {}):
                result=validation_results.get((group["item_id"],url))
                if result and result[0].get("validation_status")=="stale_fetch_failed":
                    stale_retry.add(group["item_id"])
            valid=cls._preservation_valid(group,regular,series_audio_master,validation_results)
            if valid:
                for target_id in (group.get("target_counts") or {}):
                    expected=prior.get(str(target_id)) or group_fingerprints.get(str(target_id))
                    if expected and regular[str(target_id)]["fingerprint"]!=expected:
                        valid=False;break
            if valid:
                preserved_item_ids.append(group["item_id"])
                preserved_target_ids.update(map(str,group.get("target_counts") or {}))
            else:
                invalidated.append(group["item_id"])
        return preserved_item_ids,preserved_target_ids,invalidated,stale_retry

    @staticmethod
    def _preservation_valid(group,regular,series_audio_master,validation_results):
        target_counts=group.get("target_counts") or {}
        if not target_counts:return False
        for tid,count in target_counts.items():
            current=regular.get(str(tid));sid=str(tid).split(":",1)[0]
            if not current or current["episode_count"]!=count:return False
            wanted=series_audio_master.get(sid)
            if wanted and group.get("audio")!=wanted:return False
        for url,expected in (group.get("source_fingerprints") or {}).items():
            result=validation_results.get((group["item_id"],url))
            if result:
                current,_=result
                if current.get("validation_status")=="stale_fetch_failed":continue
                if current.get("status")!="ok" or current.get("source_fingerprint")!=expected:return False
        return True

    def read_incremental(self,*,preserve_groups=None,current_target_ids=None,series_audio_master=None,
        inventory_fingerprints=None,progress=None,scan_mode="normal",force_catalog_refresh=False):
        if scan_mode not in {"normal","deep_audit"}:raise ValueError("Invalid scan mode")
        if progress:progress("inventory_read",5)
        seed=self.seed.read()
        series_audio_master={str(k):v for k,v in (series_audio_master or {}).items() if v in {"SUB","DUB"}}
        manual,external,availability,seed_details,mappings,v3_audio=self._seed_context(seed)
        tags=self._get("tag");raw_series=self._get("series")
        series=self._clean_series(raw_series,tags)
        eligible=self._eligible_series(series)

        policy=seed.get("language_policy") or {}
        contexts={str(row["id"]):{
            "external":external.get(str(row["id"])),
            "manual_aliases":manual.get(row.get("title")),
            "availability":{key:value for key,value in availability.items()
                if str(key).startswith(f'{row["id"]}:')},
            "audio_policy":(policy.get("overrides") or {}).get(str(row["id"])),
        } for row in eligible}
        regular,special_series=self._inventory(eligible,external,contexts)
        if progress:progress("inventory_diff",15)
        selected_validations,pending_by_item,validation_results=self._verify_due_providers(
            preserve_groups,seed_details)
        if progress:progress("provider_verification",25)
        preserved_item_ids,preserved_target_ids,invalidated,stale_retry=self._diff_inventory(
            preserve_groups,regular,series_audio_master,validation_results,inventory_fingerprints)

        work_series_ids={meta["series_id"] for tid,meta in regular.items() if tid not in preserved_target_ids}
        work_series_ids.update(special_series)
        work_series=[row for row in eligible if int(row["id"]) in work_series_ids]
        if progress:progress("dirty_mapping",35)

        def episodes_for(row):
            return str(row["id"]),self._clean_episodes(self._get(
                "episode?"+urllib.parse.urlencode({"seriesId":row["id"]})))
        with ThreadPoolExecutor(max_workers=4) as pool:
            episodes=dict(pool.map(episodes_for,work_series))

        catalog=[]
        catalog_generation_available=True
        if work_series:
            if progress:progress("catalog_refresh" if force_catalog_refresh else "catalog_generation_load",45)
            self.cache.mkdir(parents=True,exist_ok=True)
            index=AnimeWorldIndex(self.catalog_path,"https://www.animeworld.ac",max_pages=500)
            catalog_doc=index.refresh() if force_catalog_refresh else index.data
            if catalog_doc.get("catalog_complete") is not True:raise RuntimeError("AnimeWorld catalog incomplete")
            catalog=catalog_doc.get("entries") or []
            catalog_generation_available=bool(catalog_doc.get("generated_at")) and bool(catalog)
        catalog_index=CatalogShortlistIndex(catalog,limit=getattr(self,"candidate_limit",128)) if work_series else None
        pools={};overflow_series=set();shortlist_examined=0
        if work_series and progress:progress("candidate_retrieval",55)
        for row in work_series:
            shortlist=catalog_index.retrieve(row)
            shortlist_examined+=shortlist.catalog_entries_examined
            if shortlist.overflow:overflow_series.add(int(row["id"]))
            pools[str(row["id"])]=retrieve(row,list(shortlist.entries),mappings,external.get(str(row["id"])))
        urls=sorted({entry["url"] for values in pools.values() for entry in values if entry.get("url")})
        if urls and progress:progress("provider_detail",70)
        details,pending_detail_urls=self._load_candidate_details(urls,seed_details,max_workers=4)
        url_series={entry.get("url"):int(series_id) for series_id,values in pools.items()
            for entry in values if entry.get("url")}
        pending_detail_series={url_series[url] for url in pending_detail_urls if url in url_series}

        live=snapshot(work_series,episodes,catalog,mappings,details,manual,pools=pools,
            availability=availability,external=external)
        live["cases"]=[case for case in live.get("cases",[])
            if str(((case.get("derived") or {}).get("target") or {}).get("target_id")) not in preserved_target_ids]
        current={str(v) for v in (current_target_ids or []) if str(v).rsplit(":",1)[-1]!="0"}
        sonarr_targets=set(regular)
        live["incremental"]={
            "sonarr_series_count":len(eligible),
            "sonarr_regular_target_count":len(sonarr_targets),
            "current_regular_target_count":len(current),
            "target_totals_match":len(sonarr_targets)==len(current),
            "target_sets_match":sonarr_targets==current,
            "missing_target_ids":sorted(sonarr_targets-current),
            "removed_target_ids":sorted(current-sonarr_targets),
            "preserved_item_ids":preserved_item_ids,
            "preserved_target_ids":sorted(preserved_target_ids),
            "work_series_count":len(work_series),
            "episode_fetches":len(work_series),
            "provider_rematches":len(work_series),
            "catalog_shortlists":len(work_series),
            "catalog_entries_examined":shortlist_examined,
            "provider_validations_performed":len(selected_validations),
            "provider_validation_budget":self.validation_budget,
            "provider_validation_pending_item_ids":sorted(pending_by_item),
            "provider_validation_stale_retry_item_ids":sorted(stale_retry),
            "provider_validation_invalidated_item_ids":sorted(invalidated),
            "candidate_overflow_series_ids":sorted(overflow_series),
            "provider_detail_requests":getattr(self,"_last_detail_request_count",0),
            "provider_detail_request_budget":getattr(self,"detail_request_budget",256),
            "provider_detail_pending_urls":len(pending_detail_urls),
            "provider_detail_pending_series_ids":sorted(pending_detail_series),
            "catalog_generation_available":catalog_generation_available,
            "catalog_refresh_performed":bool(force_catalog_refresh and work_series),
            "scan_mode":scan_mode,
        }
        live["target_inventory"]={target_id:value["fingerprint"] for target_id,value in sorted(regular.items())}
        live["candidate_overflow_target_ids"]=sorted(
            target_id for target_id,value in regular.items() if value["series_id"] in overflow_series)
        live["provider_request_pending_target_ids"]=sorted(
            target_id for target_id,value in regular.items() if value["series_id"] in pending_detail_series)
        live["catalog_generation_pending_target_ids"]=sorted(
            target_id for target_id,value in regular.items()
            if value["series_id"] in work_series_ids and not catalog_generation_available)
        live["provider_observations"]={url:{
            "status":value.get("status"),"fingerprint":value.get("source_fingerprint"),
            "validated_at":value.get("fetched_at"),"next_validation_at":value.get("next_validation_at"),
            "validation_status":value.get("validation_status"),
        } for url,value in details.items()}
        live["provider_catalog"]={"complete":True,"entries":[dict(e) for e in catalog]}
        policy=deepcopy(seed.get("language_policy") or {"default":"SUB","overrides":{}})
        overrides={**v3_audio,**(policy.get("overrides") or {})}
        strict_targets=[]
        for tid,meta in regular.items():
            wanted=series_audio_master.get(str(meta["series_id"]))
            if wanted:
                overrides[tid]=wanted;strict_targets.append(tid)
        policy["overrides"]=overrides
        policy["strict_targets"]=sorted(strict_targets)
        policy["series_audio_master"]=dict(sorted(series_audio_master.items()))
        policy["migrated_v3_audio_targets"]=len(v3_audio)
        live["language_policy"]=policy
        live["kind"]="production_metadata_replay"
        live["captured_at"]=seed.get("captured_at")
        live["live_sources"]={"sonarr":True,"animeworld_catalog":bool(work_series)}
        return live

    def read(self):
        return self.read_incremental()

    def read_deep_audit(self,*,preserve_groups=None,current_target_ids=None,series_audio_master=None,
        inventory_fingerprints=None,progress=None):
        """Explicit full-library audit; never called by the normal scan scheduler."""
        return self.read_incremental(preserve_groups=[],current_target_ids=current_target_ids or [],
            series_audio_master=series_audio_master or {},inventory_fingerprints={},progress=progress,
            scan_mode="deep_audit",force_catalog_refresh=True)
