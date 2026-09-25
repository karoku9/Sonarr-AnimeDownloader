"""One-shot read-only V3 capture. Credentials stay in container memory; stdout is sanitized."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from .production_sources import local_output

def capture_live_catalog(path):
    module_path=Path(__file__).resolve().parents[1]/"components"/"backend"/"animeworld_index.py"
    spec=importlib.util.spec_from_file_location("anidown_v4_animeworld_index",module_path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    index=module.AnimeWorldIndex(path,"https://www.animeworld.ac",max_pages=500)
    data=index.refresh()
    if data.get("catalog_complete") is not True:
        raise RuntimeError("AnimeWorld full catalog capture did not prove completeness")
    data["version"]=2
    data["source"]="animeworld_full_type_catalog"
    return data

CONTAINER_EXPORT = r"""
import os,json,pathlib,urllib.request,concurrent.futures,hashlib
root=pathlib.Path('/src/database')
def get(route):
    request=urllib.request.Request(os.environ['SONARR_URL'].rstrip('/')+'/api/v3/'+route,headers={'X-Api-Key':os.environ['API_KEY']},method='GET')
    with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
series=get('series')
tags=get('tag')
tag_names={int(t['id']):str(t.get('label') or '') for t in tags if isinstance(t,dict) and isinstance(t.get('id'),int)}
series_keys=['id','title','originalTitle','cleanTitle','sortTitle','year','firstAired','seriesType','status','ended','tvdbId','imdbId','tvMazeId']
clean=[]
for row in series:
    output={k:row[k] for k in series_keys if k in row}
    poster=next((str(image.get('remoteUrl')) for image in row.get('images',[])
        if isinstance(image,dict) and str(image.get('coverType') or '').casefold()=='poster'
        and str(image.get('remoteUrl') or '').startswith('https://')),None)
    if poster:output['poster_url']=poster
    output['tag_labels']=[tag_names[t] for t in row.get('tags',[]) if t in tag_names and tag_names[t]]
    output['alternateTitles']=[{k:a[k] for k in ['title','seasonNumber','sceneSeasonNumber','sceneOrigin'] if k in a} for a in row.get('alternateTitles',[])]
    output['seasons']=[{'seasonNumber':s['seasonNumber'],'statistics':{k:v for k,v in s.get('statistics',{}).items() if k in ['episodeCount','totalEpisodeCount','previousAiring','nextAiring']}} for s in row.get('seasons',[])]
    clean.append(output)
def episodes(row):
    try:
        raw=get('episode?seriesId='+str(row['id']))
        fields=['seasonNumber','episodeNumber','absoluteEpisodeNumber','sceneSeasonNumber','sceneEpisodeNumber','sceneAbsoluteEpisodeNumber','airDate','airDateUtc']
        return str(row['id']),[{k:e[k] for k in fields if k in e} for e in raw],None
    except Exception as error:return str(row['id']),[],type(error).__name__
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(episodes,series))
index=json.loads((root/'search_v3_index.json').read_text())
fields=['title','url','audio','aliases','year','source','table_titles','table_seasons','slug']
entries=[{k:e[k] for k in fields if k in e} for e in index.get('entries',[])]
rows=json.loads((root/'table.json').read_text())
mappings=[{k:r[k] for k in ['title','absolute','seasons'] if k in r} for r in rows]
# Preserve both explicit V3 language settings and the audio mode currently
# selected by persisted V3 season mappings. Explicit settings win.
prefs=json.loads((root/'mapping_preferences.json').read_text())
def audio_pref(value):
    if value in ('DUB_FIRST','DUB_ONLY'):return 'DUB'
    if value in ('SUB_FIRST','SUB_ONLY'):return 'SUB'
    return None
global_raw=prefs.get('global_language_preference','AUTO')
default_audio=audio_pref(global_raw) or 'SUB'
by_title={str(r.get('title') or '').strip().casefold():r for r in clean}
def base_url(value):
    value=str(value or '')
    if '/play/' not in value:return value
    prefix,tail=value.split('/play/',1)
    return prefix+'/play/'+tail.split('/',1)[0]
audio_by_url={base_url(e.get('url')):e.get('audio') for e in entries if e.get('audio') in ('SUB','DUB')}
audio_overrides={}
for mapping in rows:
    series_row=by_title.get(str(mapping.get('title') or '').strip().casefold())
    if not series_row:continue
    for season,urls in (mapping.get('seasons') or {}).items():
        if not str(season).isdigit() or int(season)<=0 or not isinstance(urls,list):continue
        modes={audio_by_url.get(base_url(url)) for url in urls}
        modes.discard(None)
        if len(modes)==1:audio_overrides[f"{series_row['id']}:{int(season)}"]=next(iter(modes))
for title,meta in prefs.get('series',{}).items():
    if not isinstance(meta,dict):continue
    raw=meta.get('language_preference','AUTO')
    explicit=audio_pref(raw)
    if explicit is None:continue
    series_row=by_title.get(str(title).strip().casefold())
    if not series_row:continue
    for season in series_row.get('seasons',[]):
        number=season.get('seasonNumber')
        if isinstance(number,int) and number>0:
            audio_overrides[f"{series_row['id']}:{number}"]=explicit
v3_language_policy={'default':default_audio,'overrides':audio_overrides,
    'source':'mapping_preferences.json','global_raw':global_raw}
a=json.loads((root/'search_v3_aliases.json').read_text())
print(json.dumps({'sonarr-series':clean,'sonarr-episodes':{id:rows for id,rows,error in results},'acquisition-errors':{id:error for id,rows,error in results if error},'catalog':{'version':index.get('version'),'generated_at':index.get('generated_at'),'entries':entries},'v3-mappings':mappings,'v3-language-policy':v3_language_policy,'manual-aliases':{title:[v for v in values if isinstance(v,str)] for title,values in a.items() if isinstance(values,list)},'runtime-source-hashes':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['search_v3_index.json','table.json','search_v3_aliases.json','mapping_preferences.json']}}))
"""

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",required=True)
    args=parser.parse_args(argv)
    folder=local_output(args.output_dir)
    if not folder.is_relative_to(local_output("work")):
        raise ValueError("Live capture must stay under ignored V4 work/")
    started=datetime.now(timezone.utc).isoformat()
    folder.mkdir(parents=True,exist_ok=True)
    # Sonarr/V3 metadata stays GET/read-only. AnimeWorld catalog is captured independently by V4.
    payload=json.loads(subprocess.check_output(["docker","exec","anidown-v3","python","-B","-c",CONTAINER_EXPORT],text=True,encoding="utf-8"))
    payload["catalog"]=capture_live_catalog(folder/"catalog.json")
    files={}
    for name,data in payload.items():
        encoded=json.dumps(data,ensure_ascii=False,indent=2)
        path=folder/(name+".json")
        path.write_text(encoded,encoding="utf-8")
        files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (folder/"capture-manifest.json").write_text(json.dumps({"schema_version":1,"started_at":started,"completed_at":datetime.now(timezone.utc).isoformat(),"files_sha256":files,"limitations":["Running V3/Sonarr are not transactionally frozen; GET capture is observational.","Only whitelisted metadata exported; credentials never leave container memory."]},indent=2),encoding="utf-8")
    print(json.dumps({"series":len(payload["sonarr-series"]),"catalog":len(payload["catalog"]["entries"]),"errors":len(payload["acquisition-errors"])}))

if __name__=="__main__":main()
