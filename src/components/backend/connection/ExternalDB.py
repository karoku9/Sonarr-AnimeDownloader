import httpx
import animeworld as aw
from typing import Optional

from ..core.Constant import LOGGER
from ..matching import generate_title_queries, score_result, summarize_search

class ExternalDB:
	"""
	Collegamento con le informazioni che si trovano su GitHub.
	https://github.com/Fribb/anime-lists
	"""

	def __init__(self):
		self.log = LOGGER
		self.client = httpx.Client()
		self._data = []
	
	def sync(self) -> list:
		"""
		Sincronizza i dati interni con il database esterno.

		Returns:
		  Tutti i dati aggiornati.
		"""

		res = self.client.get("https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json")
		res.raise_for_status()
		self._data = res.json()
		return self._data
	
	def getData(self)-> list:
		"""
		Restituisce tutti i dati.

		Returns:
		  I dati nel database.
		"""
		return list(self._data)
	
	def find(self, title:str, season:int, tvdb_id:int) -> Optional[dict[str, str]]:
		"""
		Cerca un url per il download.

		Args:
		  title: titolo dell'anime.
		  season: stagione dell'anime.
		  tvdb_id: ID di thetvdb.
		
		Returns:
		  Un dizionario con nome e url trovato.
		"""

		self.log.error("OLD_SEARCH_PATH_USED: ExternalDB.find")
		# Ottengo un elenco di ID di MyAnimeList che fanno match
		mal_ids = []
		for info in self._data:
			if "thetvdb_id" not in info: continue
			if "mal_id" not in info: continue
			if "type" not in info: continue
			if info["thetvdb_id"] != tvdb_id: continue
			if info["type"] != "TV": continue

			mal_ids.append(info["mal_id"])
		
		# Se non ho trovato nulla ritorno None
		if len(mal_ids) == 0: return None

		search = self.search_candidates({"title": title}, season, tvdb_id, mal_ids=mal_ids)
		res = search["raw_results"]

		# Se non ho trovato nulla ritorno None
		if len(res) == 0: return None

		# Converto le stagioni in numeri
		def convert(x):
			x["year"] = int(x["year"])
			if x["season"] == 'winter':
				x["season"] = 0
			elif x["season"] == 'spring':
				x["season"] = 1
			elif x["season"] == 'summer':
				x["season"] = 2
			else:
				x["season"] = 3
			return x
		res = list(map(convert, res))

		# Riordino per data
		res.sort(key=lambda x: (x["year"], x["season"]))

		# Controllo se esiste la stagione
		if len(res) < season: return None

		return {
			"name": res[season-1]["name"],
			"url": res[season-1]["link"]
		}

	def search_candidates(self, series:dict, season:int, tvdb_id:int, metadata=None, cache=None, refresh:bool=False, mal_ids:list[int]|None=None, animeworld_index=None, language_preference:str="AUTO") -> dict:
		"""
		Cerca possibili mapping AnimeWorld usando più varianti titolo e restituisce
		risultati con confidence, senza salvare nulla.
		"""
		self.log.error("OLD_SEARCH_PATH_USED: ExternalDB.search_candidates")
		if mal_ids is None:
			mal_ids = []
			for info in self._data:
				if "thetvdb_id" not in info: continue
				if "mal_id" not in info: continue
				if "type" not in info: continue
				if info["thetvdb_id"] != tvdb_id: continue
				if info["type"] != "TV": continue
				mal_ids.append(info["mal_id"])

		query_info = generate_title_queries(series, metadata)
		cache_key = f"{tvdb_id}:{season}:{'|'.join(query_info['queries'])}"
		if cache and not refresh:
			cached = cache.get(cache_key)
			if cached:
				cached["from_cache"] = True
				return cached

		if animeworld_index:
			animeworld_index.ensure_fresh()
			index_matches = animeworld_index.search(query_info["queries"], language_preference=language_preference)
			matches = [{
				"name": item["title"],
				"url": item["url"],
				"query": query_info["queries"][0] if query_info["queries"] else series.get("title", ""),
				"confidence": item["score"],
				"match_type": item["reason"],
				"status": "auto_matched" if item["confidence"] == "high" else "needs_review",
				"audio": item.get("audio", "UNKNOWN"),
				"reason": item["reason"],
			} for item in index_matches]
			summary = summarize_search(query_info["title"], query_info["queries"], query_info["aliases"], matches)
			summary["raw_results"] = [{"name": item["title"], "link": item["url"], "year": item.get("year") or 0, "season": "winter"} for item in index_matches]
			summary["index_size"] = len(animeworld_index.data.get("entries", []))
			summary["index_age"] = str(animeworld_index.age()) if animeworld_index.age() else None
			if cache:
				cache.set(cache_key, summary)
			self.log.info(f"🔎 Searching AnimeWorld for: {query_info['title']}")
			self.log.info(f"   Index loaded: yes, size: {summary['index_size']}, age: {summary['index_age']}")
			self.log.info(f"   Generated queries: {', '.join(query_info['queries'])}")
			for index, match in enumerate(matches[:5], start=1):
				self.log.info(f"   Candidate {index}: {match['name']} - score {match['confidence']} - {match.get('reason', match['match_type'])} - {match.get('audio', 'UNKNOWN')}")
			if summary["best"]:
				self.log.info(f"   Selected status: {summary['status']} ({summary['reason']})")
			else:
				self.log.info("   No AnimeWorld index candidates matched generated queries.")
			return summary

		all_results = []
		scored = []
		seen_links = set()
		for query in query_info["queries"]:
			try:
				found = aw.find(query)
			except Exception as e:
				self.log.warning(f"⚠️ AnimeWorld search failed for '{query}': {e}")
				found = []
			filtered = [x for x in found if x.get("malId") in mal_ids and x.get("language") == "jp"]
			for item in filtered:
				link = item.get("link")
				if link in seen_links:
					continue
				seen_links.add(link)
				all_results.append(dict(item))
				scored.append(score_result(query, {"name": item.get("name", ""), "url": item.get("link", "")}, query_info["aliases"]))

		scored.sort(key=lambda x: x["confidence"], reverse=True)
		summary = summarize_search(query_info["title"], query_info["queries"], query_info["aliases"], scored)
		summary["raw_results"] = all_results
		if cache:
			cache.set(cache_key, summary)
		self.log.info(f"🔎 Searching '{query_info['title']}'")
		self.log.info(f"   Candidates: {', '.join(query_info['queries'])}")
		if query_info["aliases"]:
			self.log.info(f"   Aliases: {', '.join(query_info['aliases'])}")
		self.log.info(f"   Results: {len(scored)}")
		if summary["best"]:
			self.log.info(f"   Best match: {summary['best']['name']}, confidence {summary['best']['confidence']}, status {summary['status']}")
		else:
			self.log.info(f"   No result: {summary['reason']}")
		return summary
