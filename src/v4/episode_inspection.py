"""Explicit GET-only inspection of catalog episode metadata, never media resources."""
import argparse
from datetime import datetime, timezone
import json
from urllib.parse import urljoin, urlsplit
import urllib.request
from bs4 import BeautifulSoup
from .production_sources import public_url, RestrictedRedirect, extract_detail, local_output


def catalog_page(url):
    public_url(url)
    if not urlsplit(url).path.startswith("/play/"):
        raise ValueError("Only catalog release/episode HTML pages may be inspected")
    return url


def inspect(url):
    catalog_page(url)
    request=urllib.request.Request(url,headers={"User-Agent":"AniDown-v4 metadata inspection"},method="GET")
    with urllib.request.build_opener(RestrictedRedirect()).open(request,timeout=15) as response:
        final=catalog_page(response.url)
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("Refuse non-HTML resources")
        html=response.read(4_000_000)
    soup=BeautifulSoup(html,"html.parser")
    episodes=[]
    for node in soup.select("[data-episode-num]"):
        number=node.get("data-episode-num")
        if not str(number).isdigit():continue
        href=node.get("href")
        if not href:continue
        try:href=catalog_page(urljoin(final,href))
        except ValueError:continue
        episodes.append({"number":int(number),"url":href,"provenance":"observed:catalog_episode_list"})
    episodes=sorted({(e["number"],e["url"]):e for e in episodes}.values(),key=lambda e:(e["number"],e["url"]))
    return {"schema_version":1,"requested_url":url,"fetched_at":datetime.now(timezone.utc).isoformat(),
        "raw":extract_detail(html,final),"episode_entries":episodes,
        "limitations":["JSON-LD datePublished is retained as publication metadata, never assumed episode air date.",
                        "Player URLs and media resources are not extracted or requested."]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--url",required=True);parser.add_argument("--output",required=True)
    args=parser.parse_args();output=local_output(args.output)
    result=inspect(args.url);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":main()
