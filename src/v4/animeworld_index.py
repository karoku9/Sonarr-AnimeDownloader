import json
import os
import pathlib
import re
import tempfile
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup


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


def _family_key(value: str) -> str:
	"""Return a conservative title-family key used only for candidate retrieval."""
	tokens = [token for token in normalize_aw_title(value).split()
		if token not in {"season", "part", "cour", "the", "a", "an"} and not token.isdigit()]
	return " ".join(tokens[:2])


@dataclass(frozen=True)
class CatalogShortlist:
	entries: tuple[dict[str, Any], ...]
	overflow: bool
	total_matches: int
	catalog_entries_examined: int


class CatalogShortlistIndex:
	"""Generation-local provider/title/family index with fail-closed overflow.

	The index never claims a truncated shortlist is complete.  If more candidates
	match the retrieval keys than the configured bound, callers must persist an
	explicit ``candidate_overflow`` review and cannot auto-select from the prefix.
	"""
	def __init__(self, entries, limit=128):
		self.entries = tuple(dict(entry) for entry in entries)
		self.limit = max(8, min(512, int(limit)))
		self.key_limit = 64
		self.by_url = {}
		self.by_title = defaultdict(set)
		self.by_family = defaultdict(set)
		self.by_token = defaultdict(set)
		for position, entry in enumerate(self.entries):
			url = str(entry.get("url") or "")
			if url:
				self.by_url[url] = position
			titles = [entry.get("title") or "", *(entry.get("aliases") or [])]
			for title in titles:
				for key in title_variants(str(title)):
					self.by_title[key].add(position)
					family = _family_key(key)
					if family:
						self.by_family[family].add(position)
					for token in key.split():
						if len(token) >= 4:
							self.by_token[token].add(position)
		def position_key(position):
			return (str(self.entries[position].get("title") or "").casefold(),
				str(self.entries[position].get("url") or ""))
		self.all_positions = tuple(sorted(range(len(self.entries)), key=position_key))
		for lookup in (self.by_title, self.by_family, self.by_token):
			for key, positions in tuple(lookup.items()):
				lookup[key] = tuple(sorted(positions, key=position_key))

	def _bounded_union(self, buckets):
		positions = set()
		examined = 0
		overflow = False
		largest = 0
		for bucket in buckets:
			largest = max(largest, len(bucket))
			overflow = overflow or len(bucket) > self.limit
			prefix = bucket[:self.limit + 1]
			examined += len(prefix)
			positions.update(prefix)
			if len(positions) > self.limit:
				overflow = True
		return positions, overflow, max(largest, len(positions)), examined

	def retrieve(self, row, external_titles=()):
		observed = [row.get("title") or ""]
		observed.extend(value.get("title") if isinstance(value, dict) else str(value)
			for value in row.get("alternateTitles", []) or [])
		observed.extend(str(value) for value in external_titles if value)
		all_keys = unique([variant for title in observed for variant in title_variants(title)])
		key_overflow = len(all_keys) > self.key_limit
		keys = all_keys[:self.key_limit]
		strong_buckets = []
		token_buckets = []
		seen_strong = set()
		seen_tokens = set()
		for key in keys:
			for kind, value in (("title", key), ("family", _family_key(key))):
				identity = (kind, value)
				lookup = self.by_title if kind == "title" else self.by_family
				if value and identity not in seen_strong and lookup.get(value):
					seen_strong.add(identity)
					strong_buckets.append(lookup[value])
			for token in key.split():
				if len(token) >= 4 and token not in seen_tokens and self.by_token.get(token):
					seen_tokens.add(token)
					token_buckets.append(self.by_token[token])
		buckets = strong_buckets or token_buckets
		positions, bucket_overflow, total_matches, examined = self._bounded_union(buckets)
		# No indexed overlap is itself uncertainty.  Small catalogs can be examined
		# completely; large catalogs become explicit overflow instead of an unsafe
		# full-library fuzzy scan or a silent empty shortlist.
		if not positions:
			if len(self.entries) > self.limit:
				return CatalogShortlist(entries=(), overflow=True,
					total_matches=len(self.entries), catalog_entries_examined=0)
			positions = set(self.all_positions)
			total_matches = len(positions)
			examined = len(positions)
		ordered = sorted(positions, key=lambda position:(
			str(self.entries[position].get("title") or "").casefold(),
			str(self.entries[position].get("url") or ""),
		))
		# Token-only retrieval cannot prove that a fuzzy candidate outside the bucket
		# is irrelevant, so it is review-only unless the complete catalog is bounded.
		uncertain_omission = not strong_buckets and len(positions) < len(self.entries)
		overflow = key_overflow or bucket_overflow or len(ordered) > self.limit or uncertain_omission
		selected = ordered[:self.limit]
		return CatalogShortlist(
			entries=tuple(self.entries[position] for position in selected),
			overflow=overflow,
			total_matches=len(self.entries) if uncertain_omission else total_matches,
			catalog_entries_examined=examined,
		)


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
			backup=self._backup_path()
			if backup.exists():self.write(self._read_validated(backup))
			else:self.write({"generated_at": None, "source_base_url": self.base_url,
				"catalog_complete": True, "entries": []})
		try:
			self.data = self.read()
		except (OSError, ValueError, TypeError):
			backup = self._backup_path()
			if not backup.exists():
				raise
			self.data = self._read_validated(backup)
			self.write(self.data)

	def _backup_path(self) -> pathlib.Path:
		return self.path.with_name(self.path.name + ".lkg")

	def _validate(self, data: Any) -> dict[str, Any]:
		if (not isinstance(data, dict) or data.get("catalog_complete") is not True
			or data.get("source_base_url") != self.base_url or "generated_at" not in data
			or not isinstance(data.get("entries"), list)):
			raise ValueError("Invalid AnimeWorld catalog document")
		for entry in data["entries"]:
			if not isinstance(entry, dict) or not isinstance(entry.get("title"), str) or not isinstance(entry.get("url"), str):
				raise ValueError("Invalid AnimeWorld catalog entry")
		return data

	def _read_validated(self, path: pathlib.Path) -> dict[str, Any]:
		with path.open("r", encoding="utf-8") as file:
			return self._validate(json.load(file))

	def read(self) -> dict[str, Any]:
		return self._read_validated(self.path)

	def write(self, data: dict[str, Any]) -> None:
		data = self._validate(data)
		temporary = None
		try:
			with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=self.path.parent,
				prefix=self.path.name + ".", suffix=".tmp", delete=False) as file:
				temporary = pathlib.Path(file.name)
				json.dump(data, file, indent=2, ensure_ascii=False)
				file.write("\n");file.flush();os.fsync(file.fileno())
			self._read_validated(temporary)
			self._atomic_replace(temporary, self.path);temporary = None
			self._fsync_directory()
			self._publish_backup(data)
		finally:
			if temporary is not None:
				try:temporary.unlink(missing_ok=True)
				except OSError:pass

	def _publish_backup(self, data: dict[str, Any]) -> None:
		backup = self._backup_path();temporary = None
		try:
			with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=self.path.parent,
				prefix=backup.name + ".", suffix=".tmp", delete=False) as file:
				temporary = pathlib.Path(file.name)
				json.dump(data, file, indent=2, ensure_ascii=False)
				file.write("\n");file.flush();os.fsync(file.fileno())
			self._read_validated(temporary)
			self._atomic_replace(temporary, backup);temporary = None
			self._fsync_directory()
		finally:
			if temporary is not None:
				try:temporary.unlink(missing_ok=True)
				except OSError:pass

	def _fsync_directory(self) -> None:
		try:
			descriptor = os.open(self.path.parent, os.O_RDONLY)
		except (AttributeError, OSError, TypeError):
			return
		try:os.fsync(descriptor)
		except OSError:pass
		finally:os.close(descriptor)

	@staticmethod
	def _atomic_replace(source: pathlib.Path, destination: pathlib.Path, attempts: int = 8) -> None:
		for attempt in range(attempts):
			try:
				os.replace(source, destination)
				return
			except PermissionError:
				if attempt + 1 >= attempts:raise
				time.sleep(min(0.05, 0.005 * (2 ** attempt)))

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
