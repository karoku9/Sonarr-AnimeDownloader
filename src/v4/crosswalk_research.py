"""Read-only MAL/Fribb/AnimeWorld crosswalk research for unresolved V4 series.

All remote origins are fixed in code. Failures only reduce evidence; they never
create a mapping or make an external write.
"""
from copy import deepcopy
import html
import re

import httpx
from bs4 import BeautifulSoup

from .normalization import normalize_title

FRIBB_URL="https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
MAL_URL="https://myanimelist.net/anime/{mid}"
ANILIST_URL="https://graphql.anilist.co"
ANIMEWORLD_ORIGIN="https://www.animeworld.ac"


def _source_key(source):
    return (source.get("source"),str(source.get("record_id") or ""),source.get("ordering"))


class MALCrosswalkResearcher:
    def __init__(self,*,timeout_seconds=20.0,client=None):
        self.timeout_seconds=timeout_seconds
        self.client=client or httpx.Client(follow_redirects=True,timeout=timeout_seconds,
            headers={"User-Agent":"AniDown-V4-Metadata/1.0"})
        self._fribb_cache=None;self._mal_cache={};self._search_cache={}
        self._aw_ready=False

    def _json_get(self,url,max_bytes=30_000_000):
        response=self.client.get(url);response.raise_for_status()
        if len(response.content)>max_bytes:raise ValueError("metadata_response_limit")
        return response.json()
    def _fribb(self):
        if self._fribb_cache is None:
            value=self._json_get(FRIBB_URL)
            if not isinstance(value,list):raise ValueError("invalid_fribb_catalog")
            self._fribb_cache=value
        return self._fribb_cache

    def _anilist_exclusions(self,mid):
        query="query($id:Int){Media(idMal:$id,type:ANIME){description(asHtml:false)}}"
        try:
            response=self.client.post(ANILIST_URL,json={"query":query,"variables":{"id":mid}})
            response.raise_for_status()
            text=html.unescape((((response.json().get("data") or {}).get("Media") or {}).get("description") or ""))
        except Exception:return []
        plain=re.sub(r"<[^>]+>"," ",text)
        match=re.search(r"recap episodes.*?\bEp(?:isodes?)?\.?\s*((?:[0-9]+\s*,\s*)*[0-9]+)",plain,re.I|re.S)
        return [int(v) for v in re.findall(r"\d+",match.group(1))] if match else []

    def _mal_meta(self,mid):
        if mid in self._mal_cache:return self._mal_cache[mid]
        try:
            response=self.client.get(MAL_URL.format(mid=mid));response.raise_for_status()
            if len(response.content)>4_000_000:raise ValueError("mal_response_limit")
            page=response.text;soup=BeautifulSoup(page,"html.parser")
            og=soup.find("meta",attrs={"property":"og:title"})
            episodes=re.search(r"Episodes:</span>\s*([0-9]+|Unknown)",page)
            english=re.search(r"English:</span>\s*([^<]+)",page)
            titles=[og.get("content") if og else None,html.unescape(english.group(1).strip()) if english else None]
            year=None
            for label in ("Premiered:","Aired:"):
                node=next((x for x in soup.select("span.dark_text") if x.get_text(strip=True)==label),None)
                text=node.parent.get_text(" ",strip=True) if node and node.parent else ""
                found=re.search(r"\b(?:19|20)\d{2}\b",text)
                if found:year=int(found.group(0));break
            value=(list(dict.fromkeys(t for t in titles if t)),
                int(episodes.group(1)) if episodes and episodes.group(1).isdigit() else None,
                self._anilist_exclusions(mid),year)
        except Exception:value=([],None,[],None)
        self._mal_cache[mid]=value;return value
    def _ensure_aw(self):
        if self._aw_ready:return
        try:
            response=self.client.get(ANIMEWORLD_ORIGIN+"/");response.raise_for_status()
            token=re.search(r'<meta.*?id="csrf-token"\s*?content="(.*?)"',response.text,re.I)
            if token:self.client.headers["csrf-token"]=token.group(1)
        finally:self._aw_ready=True

    def _search_aw(self,query):
        if query in self._search_cache:return self._search_cache[query]
        try:
            self._ensure_aw()
            response=self.client.post(ANIMEWORLD_ORIGIN+"/api/search/v2?",params={"keyword":query})
            response.raise_for_status();value=response.json().get("animes",[])
            if not isinstance(value,list):value=[]
        except Exception:value=[]
        self._search_cache[query]=value;return value

    @staticmethod
    def _catalog_keys(snapshot):
        doc=snapshot.get("provider_catalog") or {}
        if doc.get("complete") is not True:return False,[]
        keys=[]
        for entry in doc.get("entries",[]):
            for value in [entry.get("title"),*entry.get("aliases",[])]:
                if isinstance(value,str) and value:keys.append(normalize_title(value))
        return True,keys

    def _source(self,row,season,catalog_complete,catalog_keys):
        mapped=[x for x in self._fribb() if x.get("type")=="TV"
            and x.get("tvdb_id")==row.get("tvdbId")
            and (x.get("season") or {}).get("tvdb")==season
            and isinstance(x.get("mal_id"),int)]
        if not mapped:return None
        aliases=[];provider_hits={};provider_conflicts={};expected=[];names_by_mid={}
        base_queries=[row.get("title")]+[a.get("title") for a in row.get("alternateTitles",[]) if isinstance(a,dict)]
        for item in mapped:
            mid=item["mal_id"];titles,count,excluded,release_year=self._mal_meta(mid)
            expected.append({"mal_id":mid,"episode_count":count,
                "episode_offset":(item.get("episode_offset") or {}).get("tvdb"),"release_year":release_year})
            slug=(item.get("anime-planet_id") or "").replace("-"," ").strip()
            names=list(dict.fromkeys([*titles,slug]));names_by_mid[mid]=[n for n in names if n]
            for name in names:
                if name:aliases.append({"season_number":season,"title":name,"episode_count":count,
                    "identity_verified":True,"mal_id":mid,
                    "episode_offset":(item.get("episode_offset") or {}).get("tvdb"),
                    "excluded_source_episodes":excluded})
            for query in list(dict.fromkeys([*names,*base_queries]))[:20]:
                if not query:continue
                for hit in self._search_aw(query):
                    if hit.get("malId")!=mid:continue
                    hit_year=int(hit["year"]) if str(hit.get("year") or "").isdigit() else None
                    if release_year and hit_year and hit_year!=release_year:
                        provider_conflicts[(mid,hit.get("id"))]=(hit,release_year);continue
                    provider_hits[(mid,hit.get("id"))]=hit
                    if hit.get("name"):
                        aliases.append({"season_number":season,"title":hit["name"],"episode_count":count,
                            "identity_verified":True,"mal_id":mid,
                            "episode_offset":(item.get("episode_offset") or {}).get("tvdb"),
                            "excluded_source_episodes":excluded})
        dedup={(a["season_number"],a["title"].casefold(),a.get("mal_id")):a for a in aliases}
        hit_mids={k[0] for k in provider_hits};conflict_mids={k[0] for k in provider_conflicts};missing=[]
        for item in expected:
            mid=item["mal_id"]
            if mid in hit_mids:continue
            if catalog_complete and mid in conflict_mids:missing.append(mid);continue
            wanted={normalize_title(v) for v in names_by_mid.get(mid,[]) if v}
            if catalog_complete and wanted and not any(k in wanted for k in catalog_keys):missing.append(mid)
        return {"source":"fribb_mal_crosswalk","record_id":f"{row['tvdbId']}:{season}",
            "independent_of_sonarr":True,"external_ids":{"tvdb":str(row["tvdbId"])},
            "season_aliases":list(dedup.values()),"expected_mal_entries":expected,
            "provider_mal_hits":[{"mal_id":k[0],"name":v.get("name"),"episodes":v.get("episodes"),
                "language":v.get("language"),"year":v.get("year")} for k,v in provider_hits.items()],
            "provider_mal_conflicts":[{"mal_id":k[0],"name":v[0].get("name"),"episodes":v[0].get("episodes"),
                "provider_year":v[0].get("year"),"expected_year":v[1],"reason":"release_year_conflict"}
                for k,v in provider_conflicts.items()],
            "provider_catalog_complete":catalog_complete,"missing_mal_ids_verified":missing}

    def enrich(self,snapshot,series_ids):
        wanted={int(v) for v in series_ids};result=deepcopy(snapshot);changed=False
        catalog_complete,catalog_keys=self._catalog_keys(result)
        rows={};seasons={}
        for case in result.get("cases",[]):
            raw=case.get("raw",{});row=raw.get("sonarr_series") or {};sid=row.get("id")
            if sid not in wanted:continue
            rows[sid]=row
            number=(case.get("derived",{}).get("target") or {}).get("season_number")
            if isinstance(number,int) and number>0:seasons.setdefault(sid,set()).add(number)
        researched={}
        for sid,row in rows.items():
            if not row.get("tvdbId"):continue
            for season in sorted(seasons.get(sid,set())):
                try:source=self._source(row,season,catalog_complete,catalog_keys)
                except Exception:source=None
                if source:researched.setdefault(sid,[]).append(source)
        for case in result.get("cases",[]):
            sid=(case.get("raw",{}).get("sonarr_series") or {}).get("id")
            additions=researched.get(sid)
            if not additions:continue
            current=case["raw"].get("external_metadata") or {"schema_version":1,"sources":[]}
            merged={_source_key(source):source for source in current.get("sources",[])}
            before=repr(sorted(merged))
            for source in additions:merged[_source_key(source)]=source
            if repr(sorted(merged))!=before:changed=True
            case["raw"]["external_metadata"]={"schema_version":1,"sources":list(merged.values())}
        return result,changed
class CompositeMetadataResearcher:
    """Apply public aliases/structure, MAL identity, then re-query the provider catalog."""
    def __init__(self,public_researcher,mal_researcher=None,provider_researcher=None):
        self.public=public_researcher
        self.mal=mal_researcher or MALCrosswalkResearcher()
        self.provider=provider_researcher or ProviderAliasExpansionResearcher()

    def enrich(self,snapshot,series_ids):
        result=snapshot;changed=False
        if self.public is not None:
            result,public_changed=self.public.enrich(result,series_ids)
            changed=changed or public_changed
        result,mal_changed=self.mal.enrich(result,series_ids)
        changed=changed or mal_changed
        result,provider_changed=self.provider.enrich(result,series_ids)
        return result,changed or provider_changed


class ProviderAliasExpansionResearcher:
    """Re-run provider retrieval after trusted external aliases are discovered."""

    def enrich(self,snapshot,series_ids):
        from .production_replay import retrieve
        from .production_sources import fetch_detail
        wanted={int(v) for v in series_ids};result=deepcopy(snapshot);changed=False
        catalog_doc=result.get("provider_catalog") or {}
        catalog=catalog_doc.get("entries") or []
        if not catalog:return result,False
        rows={}
        for case in result.get("cases",[]):
            row=(case.get("raw") or {}).get("sonarr_series") or {}
            if row.get("id") in wanted:rows[row["id"]]=row
        expansions={}
        for sid,row in rows.items():
            cases=[c for c in result.get("cases",[]) if (c.get("raw",{}).get("sonarr_series") or {}).get("id")==sid]
            if not cases:continue
            metadata=cases[0].get("raw",{}).get("external_metadata")
            try:entries=retrieve(row,catalog,[],metadata)
            except Exception:entries=[]
            if entries:expansions[sid]=entries
        detail_cache={}
        for sid,entries in expansions.items():
            cases=[c for c in result.get("cases",[]) if (c.get("raw",{}).get("sonarr_series") or {}).get("id")==sid]
            current_urls={e.get("url") for c in cases for e in c.get("raw",{}).get("catalog_entries",[]) if isinstance(e,dict)}
            additions=[e for e in entries if e.get("url") and e.get("url") not in current_urls]
            if not additions:continue
            for entry in additions:
                url=entry["url"]
                if url not in detail_cache:detail_cache[url]=fetch_detail(url)
            for case in cases:
                raw=case["raw"]
                merged={e.get("url"):e for e in raw.get("catalog_entries",[]) if isinstance(e,dict) and e.get("url")}
                for entry in additions:merged[entry["url"]]=entry
                raw["catalog_entries"]=list(merged.values())
                details=dict(raw.get("details") or {})
                for entry in additions:details[entry["url"]]=detail_cache[entry["url"]]
                raw["details"]=details
            changed=True
        return result,changed
