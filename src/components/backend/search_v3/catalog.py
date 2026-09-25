import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .index_store import SearchIndexStore
from .variants import normalize_title, unique_text


class AnimeWorldCatalogBuilder:
	def __init__(
		self,
		store: SearchIndexStore,
		base_url: str,
		table,
		logger: logging.Logger | None = None,
		max_pages: int = 400,
		request_delay: float = 0.05,
	) -> None:
		self.store = store
		self.base_url = base_url.rstrip("/")
		self.table = table
		self.log = logger or logging.getLogger(__name__)
		self.max_pages = max_pages
		self.request_delay = request_delay
		self.client = httpx.Client(
			timeout=12,
			follow_redirects=True,
			headers={"User-Agent": "AniDown-v3 Search-V3 public catalog indexer"},
		)

	def build(self) -> dict[str, Any]:
		old_data = self.store.load()
		table_entries = self.table_entries()
		remote_entries = []
		errors = []
		success_count = 0
		source_urls = [f"{self.base_url}/", f"{self.base_url}/ongoing", f"{self.base_url}/az-list"]
		az_pages = [f"{self.base_url}/az-list"]
		for url in source_urls:
			entries, error, html = self.fetch_listing(url)
			if error:
				errors.append(error)
				continue
			success_count += 1
			remote_entries.extend(entries)
			if url.endswith("/az-list"):
				az_pages = self.discover_archive_pages(html)
		for url in az_pages[1:self.max_pages]:
			time.sleep(self.request_delay)
			entries, error, _ = self.fetch_listing(url)
			if error:
				errors.append(error)
			else:
				success_count += 1
				remote_entries.extend(entries)

		if success_count == 0 and old_data.get("entries"):
			entries = self.merge_entries(old_data["entries"], table_entries)
			data = dict(old_data)
			data["entries"] = entries
			return {
				"data": data,
				"errors": errors + ["All public AnimeWorld catalog requests failed; retained existing index."],
				"refreshed": False,
			}

		entries = self.merge_entries(remote_entries, table_entries)
		data = {
			"version": 3,
			"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
			"source_base_url": self.base_url,
			"entries": entries,
		}
		self.store.save_atomic(data)
		return {"data": data, "errors": errors, "refreshed": True}

	def seed_table_entries(self) -> dict[str, Any]:
		current = self.store.load()
		entries = self.merge_entries(current.get("entries", []), self.table_entries())
		if entries != current.get("entries", []):
			current["entries"] = entries
			self.store.save_atomic(current)
		return current

	def table_entries(self) -> list[dict[str, Any]]:
		data = self.table.getData() if hasattr(self.table, "getData") else list(self.table or [])
		entries = []
		for series in data:
			title = str(series.get("title", "") or "").strip()
			for season, urls in (series.get("seasons", {}) or {}).items():
				for url in urls or []:
					if title and url:
						entry = self.entry(title, url, source="table")
						entry["table_titles"] = [title]
						entry["table_seasons"] = [str(season)]
						entries.append(entry)
		return entries

	def discover_archive_pages(self, html: str) -> list[str]:
		soup = BeautifulSoup(html, "html.parser")
		page_numbers = [1]
		for link in soup.find_all("a", href=True):
			match = re.search(r"/az-list\?page=(\d+)", link["href"])
			if match:
				page_numbers.append(int(match.group(1)))
		text = soup.get_text(" ", strip=True)
		total_match = re.search(r"pagina\s+.*?\s+di\s+(\d+)", text, flags=re.I)
		if total_match:
			page_numbers.append(int(total_match.group(1)))
		last_page = min(max(page_numbers), self.max_pages)
		return [f"{self.base_url}/az-list?page={page}" for page in range(1, last_page + 1)]

	def fetch_listing(self, url: str) -> tuple[list[dict[str, Any]], str | None, str]:
		try:
			response = self.client.get(url)
			response.raise_for_status()
			html = response.text
			return self.parse_listing(html, url), None, html
		except Exception as error:
			return [], f"{url}: {error}", ""

	def parse_listing(self, html: str, source_url: str) -> list[dict[str, Any]]:
		soup = BeautifulSoup(html, "html.parser")
		entries = []
		for anchor in soup.find_all("a", href=True):
			url = urljoin(self.base_url + "/", anchor["href"])
			if "/play/" not in urlparse(url).path:
				continue
			title = self.anchor_title(anchor)
			if not title:
				continue
			entries.append(self.entry(title, url, source="animeworld_catalog", context=anchor.get_text(" ", strip=True)))
		return self.merge_entries(entries)

	def anchor_title(self, anchor) -> str:
		title = anchor.get("title") or ""
		image = anchor.find("img")
		if image:
			title = image.get("alt") or image.get("title") or title
		title = title or anchor.get_text(" ", strip=True)
		title = re.sub(r"^(?:DUB\s+)?(?:ONA\s+)?(?:Ep\.?\s*\d+\s*)+", "", title, flags=re.I).strip()
		return title

	def entry(self, title: str, url: str, source: str, context: str = "") -> dict[str, Any]:
		slug = self.slug_from_url(url)
		audio = self.audio_for(title, slug, context)
		if audio == "UNKNOWN" and source == "animeworld_catalog":
			audio = "SUB"
		return {
			"title": title,
			"normalized_title": normalize_title(title),
			"url": url,
			"slug": slug,
			"normalized_slug": normalize_title(slug.replace("-", " ")),
			"audio": audio,
			"year": self.year_for(f"{title} {context}"),
			"source": source,
			"aliases": [],
		}

	def slug_from_url(self, url: str) -> str:
		path = urlparse(url).path
		play_part = path.split("/play/", 1)[-1].split("/", 1)[0]
		return play_part.rsplit(".", 1)[0]

	def audio_for(self, title: str, slug: str, context: str) -> str:
		text = f"{title} {slug} {context}"
		if re.search(r"(?:\(|-|_|\s)ita(?:\)|-|_|\s|$)|\bdub\b|doppiat", text, flags=re.I):
			return "DUB"
		if re.search(r"\bsub\b|\bgiapponese\b|\bjap\b", text, flags=re.I):
			return "SUB"
		return "UNKNOWN"

	def year_for(self, text: str) -> int | None:
		match = re.search(r"\b(19|20)\d{2}\b", text)
		return int(match.group(0)) if match else None

	def merge_entries(self, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
		merged = {}
		catalog_urls = {}
		for group in groups:
			for entry in group:
				url = entry.get("url")
				if not url:
					continue
				if entry.get("source") == "animeworld_catalog":
					catalog_key = (entry.get("normalized_slug", ""), entry.get("audio", "UNKNOWN"))
					existing_url = catalog_urls.get(catalog_key)
					if existing_url and existing_url in merged:
						current = merged[existing_url]
						current["aliases"] = unique_text(current.get("aliases", []) + [entry.get("title", "")] + entry.get("aliases", []))
						if self.preferred_catalog_url(url, current["url"]):
							merged.pop(existing_url)
							current["url"] = url
							merged[url] = current
							catalog_urls[catalog_key] = url
						continue
					catalog_urls[catalog_key] = url
				if url not in merged:
					merged[url] = dict(entry)
					merged[url]["aliases"] = list(entry.get("aliases", []))
					merged[url]["table_titles"] = list(entry.get("table_titles", []))
					merged[url]["table_seasons"] = list(entry.get("table_seasons", []))
					continue
				current = merged[url]
				current["table_titles"] = unique_text(current.get("table_titles", []) + entry.get("table_titles", []))
				current["table_seasons"] = sorted(set(current.get("table_seasons", []) + entry.get("table_seasons", [])))
				if current.get("source") == "table" and entry.get("source") == "animeworld_catalog":
					entry_copy = dict(entry)
					entry_copy["aliases"] = unique_text(entry.get("aliases", []) + current.get("aliases", []) + [current.get("title", "")])
					entry_copy["table_titles"] = list(current.get("table_titles", []))
					entry_copy["table_seasons"] = list(current.get("table_seasons", []))
					merged[url] = entry_copy
				elif current.get("source") == "animeworld_catalog" and entry.get("source") == "table":
					current["aliases"] = unique_text(current.get("aliases", []) + [entry.get("title", "")] + entry.get("aliases", []))
				else:
					current["aliases"] = unique_text(current.get("aliases", []) + [entry.get("title", "")] + entry.get("aliases", []))
		return sorted(merged.values(), key=lambda item: (item.get("normalized_title", ""), item.get("url", "")))

	def preferred_catalog_url(self, candidate: str, existing: str) -> bool:
		candidate_path = urlparse(candidate).path
		existing_path = urlparse(existing).path
		candidate_rank = (candidate_path.rstrip("/").count("/"), len(candidate_path))
		existing_rank = (existing_path.rstrip("/").count("/"), len(existing_path))
		return candidate_rank < existing_rank
