"""Bounded read-only public metadata research for unresolved V4 series."""
from copy import deepcopy
import html
import json
import re
import ssl
from urllib import request
from urllib.parse import urlsplit

_ALLOWED_HOSTS={"skyhook.sonarr.tv","services.sonarr.tv","api.tvmaze.com","thetvdb.com","www.thetvdb.com"}

class ResearchError(Exception):
    pass

def _source_key(source):
    return (source.get("source"),str(source.get("record_id") or ""),source.get("ordering"))

def _clean_title(value):
    value=re.sub(r"<[^>]+>"," ",value or "")
    return " ".join(html.unescape(value).split())

def _parse_tvdb_ordering(raw,*,title,tvdb_id,order):
    text=raw.decode("utf-8","replace") if isinstance(raw,(bytes,bytearray)) else str(raw)
    pattern=re.compile(r'<span[^>]*episode-label[^>]*>\s*S(\d+)E(\d+)\s*</span>\s*<a[^>]*>\s*(.*?)\s*</a>',re.I|re.S)
    special_pattern=re.compile(r'<(?:span|small)[^>]*episode-label[^>]*>\s*SPECIAL\s+0x(\d+)\s*</(?:span|small)>\s*<a[^>]*>\s*(.*?)\s*</a>',re.I|re.S)
    grouped={}
    for season,episode,name in pattern.findall(text):
        season=int(season);episode=int(episode)
        grouped.setdefault(season,[]).append({"episode_number":episode,"title":_clean_title(name)})
    for episode,name in special_pattern.findall(text):
        grouped.setdefault(0,[]).append({"episode_number":int(episode),"title":_clean_title(name)})
    seasons=[]
    for number,episodes in sorted(grouped.items()):
        episodes=sorted(episodes,key=lambda item:item["episode_number"])
        if tuple(e["episode_number"] for e in episodes)!=tuple(range(1,len(episodes)+1)):continue
        seasons.append({"season_number":number,"episode_count":len(episodes),"episodes":episodes})
    if not seasons:return None
    return {"source":f"tvdb_public_{order}_order","record_id":f"{tvdb_id}:{order}","ordering":order,
        "independent_of_sonarr":False,"title":title,"aliases":[],"external_ids":{"tvdb":str(tvdb_id)},"seasons":seasons}

class PublicMetadataResearcher:
    def __init__(self,*,opener=None,timeout_seconds=6.0,max_bytes=1_500_000):
        self.timeout_seconds=timeout_seconds;self.max_bytes=max_bytes
        if opener is not None:self._opener=opener
        else:
            try:
                import certifi
                context=ssl.create_default_context(cafile=certifi.where())
            except Exception:
                context=ssl.create_default_context()
            self._opener=lambda req,timeout:request.urlopen(req,timeout=timeout,context=context)

    def _read(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname not in _ALLOWED_HOSTS or parsed.username or parsed.password:raise ResearchError("untrusted_metadata_url")
        req=request.Request(url,headers={"User-Agent":"AniDown-V4-Metadata/1.0","Accept-Language":"en-US,en;q=0.9"})
        with self._opener(req,self.timeout_seconds) as response:
            final=urlsplit(response.geturl() if hasattr(response,"geturl") else url)
            if final.scheme!="https" or final.hostname not in _ALLOWED_HOSTS:raise ResearchError("metadata_redirect")
            raw=response.read(self.max_bytes+1)
        if len(raw)>self.max_bytes:raise ResearchError("metadata_response_limit")
        return raw

    def _json(self,url):
        try:value=json.loads(self._read(url))
        except (ValueError,TypeError,json.JSONDecodeError) as exc:raise ResearchError("invalid_metadata_json") from exc
        if not isinstance(value,dict):raise ResearchError("invalid_metadata_json")
        return value

    def _skyhook(self,row):
        tvdb=row.get("tvdbId")
        if not tvdb:return None
        value=self._json(f"https://skyhook.sonarr.tv/v1/tvdb/shows/en/{int(tvdb)}")
        if str(value.get("tvdbId"))!=str(tvdb):raise ResearchError("skyhook_identity_mismatch")
        episodes=value.get("episodes") or [];by_season={}
        for episode in episodes:
            number=episode.get("seasonNumber")
            if not isinstance(number,int) or number<=0:continue
            by_season.setdefault(number,[]).append(episode)
        seasons=[]
        for number,items in sorted(by_season.items()):
            dates=sorted(str(e.get("airDate"))[:10] for e in items if e.get("airDate"))
            seasons.append({"season_number":number,"episode_count":len(items),"premiere_date":dates[0] if dates else None})
        aliases=[]
        for item in value.get("alternativeTitles") or []:
            title=item.get("title") if isinstance(item,dict) else item
            if isinstance(title,str) and title and title not in aliases:aliases.append(title)
        ids={"tvdb":str(tvdb)}
        for field,name in (("tvMazeId","tvmaze"),("imdbId","imdb"),("tmdbId","tmdb")):
            if value.get(field):ids[name]=str(value[field])
        return {"source":"skyhook_tvdb","record_id":str(tvdb),"independent_of_sonarr":False,
            "title":value.get("title") or row.get("title"),"aliases":aliases,"external_ids":ids,
            "slug":value.get("slug"),"seasons":seasons}

    def _tvmaze(self,row,skyhook=None):
        tvmaze=row.get("tvMazeId") or ((skyhook or {}).get("external_ids") or {}).get("tvmaze")
        if not tvmaze:return None
        value=self._json(f"https://api.tvmaze.com/shows/{int(tvmaze)}?embed[]=seasons&embed[]=episodes")
        externals=value.get("externals") or {}
        expected_tvdb=row.get("tvdbId")
        if expected_tvdb and str(externals.get("thetvdb"))!=str(expected_tvdb):raise ResearchError("tvmaze_identity_mismatch")
        embedded=value.get("_embedded") or {};episode_rows=embedded.get("episodes") or []
        counts={}
        for episode in episode_rows:
            season=episode.get("season")
            if isinstance(season,int):counts[season]=counts.get(season,0)+1
        seasons=[]
        for season in embedded.get("seasons") or []:
            number=season.get("number")
            if not isinstance(number,int) or number<=0:continue
            seasons.append({"season_number":number,"name":season.get("name") or None,
                "episode_count":counts.get(number) or season.get("episodeOrder"),"premiere_date":season.get("premiereDate"),"end_date":season.get("endDate")})
        ids={"tvmaze":str(value.get("id"))}
        if externals.get("thetvdb"):ids["tvdb"]=str(externals["thetvdb"])
        if externals.get("imdb"):ids["imdb"]=str(externals["imdb"])
        return {"source":"tvmaze","record_id":str(value.get("id")),"independent_of_sonarr":True,
            "title":value.get("name") or row.get("title"),"aliases":[],"external_ids":ids,"seasons":seasons}

    def research(self,row):
        sources=[];skyhook=None
        try:skyhook=self._skyhook(row)
        except Exception:skyhook=None
        if skyhook:sources.append(skyhook)
        slug=(skyhook or {}).get("slug")
        if slug and re.fullmatch(r"[a-z0-9-]+",slug):
            for order in ("official","dvd","alternate","absolute"):
                try:
                    source=_parse_tvdb_ordering(self._read(f"https://thetvdb.com/series/{slug}/allseasons/{order}"),
                        title=skyhook.get("title") or row.get("title"),tvdb_id=row.get("tvdbId"),order=order)
                    if source:sources.append(source)
                except Exception:pass
        try:
            maze=self._tvmaze(row,skyhook)
            if maze:sources.append(maze)
        except Exception:pass
        return {"schema_version":1,"sources":sources} if sources else None

    def enrich(self,snapshot,series_ids):
        wanted={int(v) for v in series_ids};result=deepcopy(snapshot);changed=False
        rows={c["raw"]["sonarr_series"]["id"]:c["raw"]["sonarr_series"] for c in result.get("cases",[]) if c["raw"]["sonarr_series"]["id"] in wanted}
        researched={sid:self.research(row) for sid,row in rows.items()}
        for case in result.get("cases",[]):
            sid=case["raw"]["sonarr_series"]["id"];new=researched.get(sid)
            if not new:continue
            current=case["raw"].get("external_metadata") or {"schema_version":1,"sources":[]}
            merged={_source_key(source):source for source in current.get("sources",[])}
            before=json.dumps(list(merged.values()),sort_keys=True,ensure_ascii=False)
            for source in new.get("sources",[]):merged[_source_key(source)]=source
            after=json.dumps(list(merged.values()),sort_keys=True,ensure_ascii=False)
            if before!=after:changed=True
            case["raw"]["external_metadata"]={"schema_version":1,"sources":list(merged.values())}
        return result,changed
