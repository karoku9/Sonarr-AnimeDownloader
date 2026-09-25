import pathlib
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup
try:
	from .atomic_json import read_json_durable, write_json_durable
except ImportError:  # Legacy isolated-module test harness.
	import importlib.util
	_atomic_spec = importlib.util.spec_from_file_location("anidown_legacy_atomic_json", pathlib.Path(__file__).with_name("atomic_json.py"))
	_atomic_json = importlib.util.module_from_spec(_atomic_spec)
	_atomic_spec.loader.exec_module(_atomic_json)
	read_json_durable = _atomic_json.read_json_durable
	write_json_durable = _atomic_json.write_json_durable


def normalize_aw_title(value: str) -> str:
	value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
	value = value.replace("’", "'").replace("‘", "'").replace("`", "'")
	value = re.sub(r"\((ita|sub ita|dub)\)", " ", value, flags=re.I)
	value = re.sub(r"\(\d{4}\)\s*$", " ", value)
	value = re.sub(r"[^\w\s]", " ", value.lower())
	return re.sub(r"\s+", " ", value).strip()


def title_variants(value: str) -> list[str]:
	values = [value]
	values.append(re.split(r"\s*[:;|]\s*", value, maxsplit=1)[0])
	values.append(re.sub(r"\s*\(\d{4}\)\s*$", "", value))
	values.append(re.sub(r"\s+(\d+(st|nd|rd|th)?\s+season|season\s+\d+|part\s+\d+)\s*$", "", value, flags=re.I))
	values.append(re.sub(r"^(the|a|an)\s+", "", value, flags=re.I))
	return unique([normalize_aw_title(v) for v in values])


def unique(values: list[str]) -> list[str]:
	seen = set()
	result = []
	for value in values:
		if value and value not in seen:
			seen.add(value)
			result.append(value)
	return result


def detect_audio(title: str, text: str = "") -> str:
	combined = f"{title} {text}"
	if re.search(r"\(ita\)|\bdub\b|doppiato|italiano", combined, re.I):
		return "DUB"
	if re.search(r"sub\s*ita|jap|japanese|subbed", combined, re.I):
		return "SUB"
	return "UNKNOWN"


def parse_year(text: str) -> int | None:
	match = re.search(r"(19|20)\d{2}", text or "")
	return int(match.group(0)) if match else None


class AnimeWorldIndex:
	CATALOG_TYPES = ((0, "Anime"), (4, "Movie"), (1, "OVA"), (2, "ONA"), (3, "Special"), (5, "Music"))
	def __init__(self, path: pathlib.Path, base_url: str, table=None, max_pages: int = 500) -> None:
		self.path = path
		self.base_url = base_url.rstrip("/")
		self.table = table
		self.max_pages = max_pages
		self._page_html_cache = {}
		self._catalog_stats = {}
		self.client = httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "AniDown-v4 full catalog indexer"})
		self.path.parent.mkdir(parents=True, exist_ok=True)
		if not self.path.exists() or self.path.stat().st_size == 0:
			self.write({"generated_at": None, "source_base_url": self.base_url, "entries": []})
		self.data = self.read()

	def read(self) -> dict[str, Any]:
		return read_json_durable(self.path)

	def write(self, data: dict[str, Any]) -> None:
		write_json_durable(self.path, data)

	def sync(self) -> None:
		self.write(self.data)

	def age(self) -> timedelta | None:
		generated_at = self.data.get("generated_at")
		if not generated_at:
			return None
		try:
			return datetime.now() - datetime.fromisoformat(generated_at)
		except ValueError:
			return None

	def is_stale(self, max_age_hours: int = 24) -> bool:
		age = self.age()
		return age is None or age > timedelta(hours=max_age_hours)

	def ensure_fresh(self) -> None:
		if self.is_stale():
			self.refresh()

	def refresh(self) -> dict[str, Any]:
		entries: dict[str, dict[str, Any]] = {}
		pages = self.discover_index_pages()
		for page_url in pages:
			for entry in self.parse_listing_page(page_url):
				entries[entry["url"]] = entry
		self.data = {
			"generated_at": datetime.now().isoformat(timespec="seconds"),
			"source_base_url": self.base_url,
			"catalog_pages": len(pages),
			"catalog_types": self._catalog_stats,
			"catalog_complete": True,
			"entries": sorted(entries.values(), key=lambda item: item["title"].lower()),
		}
		self.sync()
		return self.data

	def entries_from_existing_table(self) -> list[dict[str, Any]]:
		if not self.table:
			return []
		entries = []
		for serie in self.table:
			for urls in serie.get("seasons", {}).values():
				for url in urls:
					if url:
						entries.append(self.entry_from_url(serie.get("title", ""), url, "table.json"))
		return entries

	def _get_html(self, url: str, attempts: int = 3) -> str:
		last_error = None
		for _ in range(attempts):
			try:
				response = self.client.get(url)
				response.raise_for_status()
				return response.text
			except Exception as exc:
				last_error = exc
		raise RuntimeError(f"AnimeWorld catalog request failed after {attempts} attempts: {url}") from last_error

	def _listing_urls_from_html(self, html: str, page_url: str) -> list[str]:
		soup = BeautifulSoup(html, "html.parser")
		container = soup.select_one("div.film-list")
		if container is None:
			raise RuntimeError(f"AnimeWorld catalog layout changed: missing div.film-list on {page_url}")
		urls = []
		seen = set()
		for link in container.find_all("a", href=True):
			href = urljoin(self.base_url, link["href"]).split("#", 1)[0]
			if "/play/" not in urlparse(href).path or href in seen:
				continue
			seen.add(href)
			urls.append(href)
		return urls

	def discover_index_pages(self) -> list[str]:
		pages = []
		self._page_html_cache = {}
		self._catalog_stats = {}
		for type_id, category in self.CATALOG_TYPES:
			first_fingerprint = None
			branch_pages = []
			branch_urls = set()
			completed = False
			for page in range(1, self.max_pages + 1):
				url = f"{self.base_url}/filter?type={type_id}&page={page}"
				html = self._get_html(url)
				urls = self._listing_urls_from_html(html, url)
				if not urls:
					if page == 1:
						raise RuntimeError(f"AnimeWorld {category} catalog page 1 is empty")
					completed = True
					break
				fingerprint = tuple(urls)
				if first_fingerprint is None:
					first_fingerprint = fingerprint
				elif fingerprint == first_fingerprint:
					completed = True
					break
				branch_pages.append(url)
				branch_urls.update(urls)
				self._page_html_cache[url] = html
			if not completed:
				raise RuntimeError(f"AnimeWorld {category} catalog exceeded safety limit of {self.max_pages} pages; refusing partial index")
			self._catalog_stats[category] = {"type_id": type_id, "pages": len(branch_pages), "entries": len(branch_urls)}
			pages.extend(branch_pages)
		return pages

	def is_listing_url(self, url: str) -> bool:
		parsed = urlparse(url)
		if parsed.netloc and parsed.netloc != urlparse(self.base_url).netloc:
			return False
		if parsed.path.rstrip("/") == "/animes":
			return True
		if parsed.path.rstrip("/") == "/filter":
			return True
		query = parse_qs(parsed.query)
		return "page" in query and ("/animes" in parsed.path or "/filter" in parsed.path)

	def parse_listing_page(self, url: str) -> list[dict[str, Any]]:
		html = self._page_html_cache.get(url) or self._get_html(url)
		soup = BeautifulSoup(html, "html.parser")
		container = soup.select_one("div.film-list")
		if container is None:
			raise RuntimeError(f"AnimeWorld catalog layout changed: missing div.film-list on {url}")
		query = parse_qs(urlparse(url).query)
		type_value = query.get("type", [None])[0]
		category = next((name for ident, name in self.CATALOG_TYPES if str(ident) == str(type_value)), "")
		entries = {}
		for link in container.find_all("a", href=True):
			href = urljoin(self.base_url, link["href"]).split("#", 1)[0]
			if "/play/" not in urlparse(href).path or href in entries:
				continue
			title = self.title_from_link(link)
			if not title:
				continue
			item = link.find_parent("div", class_="item")
			context = item.get_text(" ", strip=True) if item else link.get_text(" ", strip=True)
			entry = self.entry_from_url(title, href, "animeworld_catalog", context)
			entry["category"] = category
			entry["listing_page"] = url
			entries[href] = entry
		return list(entries.values())

	def title_from_link(self, link) -> str:
		title = link.get("title") or link.get_text(" ", strip=True)
		img = link.find("img")
		if img:
			title = img.get("alt") or img.get("title") or title
		title = re.sub(r"^Ep\s+\d+\s+", "", title or "", flags=re.I).strip()
		return title

	def entry_from_url(self, title: str, url: str, source: str, context: str = "") -> dict[str, Any]:
		audio = detect_audio(title, context)
		return {
			"title": title,
			"normalized_title": normalize_aw_title(title),
			"url": url,
			"category": "",
			"year": parse_year(f"{title} {context}"),
			"audio": audio,
			"contains_ita": bool(re.search(r"\(ita\)", title, re.I)),
			"aliases": [],
			"source": source,
		}

	def search(self, queries: list[str], language_preference: str = "AUTO", limit: int = 12) -> list[dict[str, Any]]:
		normalized_queries = unique([variant for query in queries for variant in title_variants(query)])
		results = []
		for entry in self.data.get("entries", []):
			score, reason = self.score_entry(normalized_queries, entry)
			if score <= 0:
				continue
			adjusted = self.apply_language_preference(score, entry, language_preference)
			confidence = "high" if adjusted >= 0.9 else "medium" if adjusted >= 0.68 else "low"
			result = dict(entry)
			result.update({"score": round(adjusted, 3), "confidence": confidence, "reason": reason})
			results.append(result)
		return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]

	def score_entry(self, normalized_queries: list[str], entry: dict[str, Any]) -> tuple[float, str]:
		names = [entry.get("normalized_title", "")]
		names.extend(normalize_aw_title(alias) for alias in entry.get("aliases", []))
		best = (0.0, "")
		for query in normalized_queries:
			for name in names:
				if not query or not name:
					continue
				if query == name:
					return 1.0, "exact normalized match"
				if query in name or name in query:
					best = max(best, (0.78, "contains normalized title"), key=lambda item: item[0])
				ratio = self.ratio(query, name)
				if ratio > best[0]:
					best = (ratio, "fuzzy normalized match")
		if best[0] < 0.58:
			return 0.0, ""
		return best

	def ratio(self, left: str, right: str) -> float:
		from difflib import SequenceMatcher
		return SequenceMatcher(None, left, right).ratio()

	def apply_language_preference(self, score: float, entry: dict[str, Any], preference: str) -> float:
		audio = entry.get("audio", "UNKNOWN")
		if preference == "DUB_ONLY" and audio != "DUB":
			return 0
		if preference == "SUB_ONLY" and audio == "DUB":
			return 0
		if preference == "DUB_FIRST" and audio == "DUB":
			return min(score + 0.05, 1.0)
		if preference == "SUB_FIRST" and audio == "SUB":
			return min(score + 0.05, 1.0)
		return score
