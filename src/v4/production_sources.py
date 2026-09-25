"""V4-only production capture: allowlisted GETs and whitelist metadata, no runtime imports."""
from datetime import datetime,timezone
import hashlib
import json
import urllib.request
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .shadow import ROOT

HOSTS={"www.animeworld.ac","animeworld.ac","www.animeworld.tv","animeworld.tv"}

def local_output(path):
    result=(ROOT/path).resolve()
    if not result.is_relative_to(ROOT):
        raise ValueError("Artifact must stay inside V4")
    return result

def public_url(url):
    p=urlsplit(url)
    if p.scheme!="https" or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443) or p.query or p.fragment:
        raise ValueError("Unexpected catalog URL; refuse instead of silently changing identifiers")
    return url

class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def extract_detail(html,url):
    soup=BeautifulSoup(html,"html.parser")
    heading=soup.select_one("h1.title")
    pairs=[]
    allowed={"Tipo:","Stato:","Anno:","Categoria:","Audio:","Data di Uscita:","Stagione:","Episodi:"}
    for dt in soup.select("dt"):
        dd=dt.find_next_sibling("dd")
        label=dt.get_text(" ",strip=True)
        if dd and label in allowed:
            pairs.append({"label":label,"value":dd.get_text(" ",strip=True)})
    structured=[]
    def whitelist_ld(value):
        if isinstance(value,list):
            for v in value:whitelist_ld(v)
        elif isinstance(value,dict):
            if "@graph" in value:whitelist_ld(value["@graph"])
            if value.get("@type") in ("TVEpisode","TVSeries","Movie","Anime"):
                clean={k:value[k] for k in ("@type","name","alternateName","episodeNumber","datePublished","startDate","numberOfEpisodes") if isinstance(value.get(k),(str,int,list))}
                parent=value.get("partOfSeries")
                if isinstance(parent,dict):clean["partOfSeries"]={k:parent[k] for k in ("@type","name","alternateName") if isinstance(parent.get(k),(str,list))}
                structured.append(clean)
    for node in soup.select('script[type="application/ld+json"]'):
        try:whitelist_ld(json.loads(node.string or node.get_text()))
        except (ValueError,TypeError):pass
    numbers=sorted({int(n.get("data-episode-num")) for n in soup.select('[data-episode-num]') if str(n.get("data-episode-num","")).isdigit()})
    return {"title":heading.get_text(" ",strip=True) if heading else None,"url":url,"fields":pairs,"structured":structured,"available_episode_numbers":numbers}

def fetch_detail(url):
    public_url(url)
    now=datetime.now(timezone.utc).isoformat()
    try:
        request=urllib.request.Request(url,headers={"User-Agent":"AniDown-v4 read-only metadata replay"},method="GET")
        with urllib.request.build_opener(RestrictedRedirect()).open(request,timeout=15) as response:
            final=public_url(response.url)
            html=response.read(4_000_000)
        raw=extract_detail(html,final)
        ok=bool(raw["title"] and raw["fields"])
        return {"schema_version":1,"source":"animeworld_detail","fetched_at":now,"content_sha256":hashlib.sha256(html).hexdigest(),"status":"ok" if ok else "metadata_not_found","raw":raw}
    except Exception as error:
        return {"schema_version":1,"source":"animeworld_detail","fetched_at":now,"status":"fetch_failed","error_type":type(error).__name__,"http_status":getattr(error,"code",None),"raw":{"url":url}}
