from .Constant import LOGGER
from ..connection import Sonarr, ExternalDB
from ..database import *
from ..sonarr_eligibility import is_anime_series
from ..search_v3.variants import alternate_title_matches_season

from typing import Iterable, List
from functools import reduce
from itertools import count

class Processor:
	"""Processa i dati che provengono da Sonarr"""

	def __init__(self, sonarr:Sonarr, *, settings:Settings, tags:Tags, table:Table, external:ExternalDB, search_v3=None, mapping_automation=None, mapping_resolver=None):
		self.sonarr = sonarr
		self.settings = settings
		self.tags = tags
		self.table = table
		self.external = external
		self.search_v3 = search_v3
		self.mapping_automation = mapping_automation
		self.mapping_resolver = mapping_resolver or (mapping_automation.resolver if mapping_automation else None)
		self.log = LOGGER
	
	def getData(self) -> list:
		"""Restituisce i dati elaborati."""

		# Raccolgo tutti gli episodi
		missing:Iterable = self.getAllMissing()

		# Rimuovo le serie, stagioni non validi
		missing = filter(self.__filter, missing)

		# Collego gli url per il download e rimuovo le stagioni che non fanno match
		missing = filter(self.__bindUrl, missing)
		
		# Riodino le stagioni e gli episodi di ciascuna serie
		missing = list(map(self.__sortSerie, missing))

		return missing

	def getAllMissing(self) -> list:
		"""
		Ottiene tutta la lista di episodi formattati.
		
		Returns:
		  La lista di episodi mancanti.
		"""

		missing = []

		for page in count(1):
			res = self.sonarr.wantedMissing(page=page)
			res.raise_for_status()
			data = res.json()

			if len(data["records"]) == 0: break

			missing.extend(data['records'])

		# Riduco le informazioni a solo quelle indispensabili
		missing = reduce(self.__reduce, missing, [])

		return missing

	### FUNCTIONS ###

	def __sortSerie(self, elem:dict) -> dict:
		"""
		Riordina le stagione e gli episodi di una serie.

		Args:
		  elem: serie da ordinare.
		
		Returns:
		  La serie ordinata
		"""

		# Ordino le stagioni
		elem["seasons"].sort(key=lambda x: x["number"])

		for season in elem["seasons"]:
			# ordino gli episodi
			season["episodes"].sort(key=lambda x: (x['seasonNumber'], x['episodeNumber']))

		return elem


	def __filter(self, elem:dict) -> bool:
		"""
		Filtra le serie e stagioni non valide.

		Args:
		  elem: serie da filtrare.
		
		Returns:
		  True da prendere / False da scartare
		"""

		# Controllo che sia effettivamente un anime
		eligible, tag_override = is_anime_series(elem, self.settings, self.tags)
		if not eligible:
			self.log.debug(f"❌ Serie '{elem['title']}' scartata perchè non è di tipo anime.")
			return False
		if tag_override:
			self.log.warning(f"SONARR_SERIES_TYPE_TAG_OVERRIDE\n  title={elem['title']}\n  series_type={elem['type']}\n  reason=active whitelist tag")

		# Controllo i tag
		active_tags:List[int] = [x['id'] for x in self.tags if self.tags.isActive(x['id'])]
		serie_tags:List[int] = [x for x in elem["tags"] if x in active_tags]
		if any(serie_tags) and self.settings["TagsMode"] == "BLACKLIST":
			self.log.debug(f"❌ Serie '{elem['title']}' scartata perchè ha uno dei tag [{', '.join([self.tags[x]['name'] for x in serie_tags])}].")
			return False
		if not any(serie_tags) and self.settings["TagsMode"] == "WHITELIST":
			self.log.debug(f"❌ Serie '{elem['title']}' scartata perchè non ha nessuno dei tag [{', '.join([self.tags[x]['name'] for x in active_tags])}].")
			return False

		def filterSeason(season:dict) -> bool:
			"""Filtra le stagioni."""
			# Controllo che non siano episodi speciali
			if season["number"] == 0:
				self.log.debug(f"❌ Stagione {season['number']} della serie '{elem['title']}' scartata perchè contiene episodi speciali.")
				return False

			return True

		# Filtro le stagioni non valide
		elem["seasons"] = list(filter(filterSeason,elem["seasons"]))

		# Controllo che la serie contenga delle stagioni
		if len(elem["seasons"]) == 0: return False

		return True

	def __reduce(self, base:list, elem:dict):
		"""
		Riduce le informazioni della lista di episodi in informazioni essenziali.

		Args:
		  base: lista contenente il risultato della riduzione
		  elem: elemento da aggiungere alla base
		"""
		
		# Controllo se è presentè già la serie
		serie = self.__extractSerie(elem)
		for s in base:
			if s["id"] == serie["id"]:
				# Se esiste la salvo
				serie = s
				break
		else:
			# altrimenti l'aggiungo
			base.append(serie)
			serie["seasons"] = []
		
		# Controllo se è già presente la stagione
		season = self.__extractSeason(elem)
		for s in serie["seasons"]:
			if s["number"] == season["number"]:
				# Se esiste la salvo
				season = s
				break
		else:
			# altrimenti l'aggiungo
			serie["seasons"].append(season)
			season["urls"] = []
			season["episodes"] = []

		# Aggiungo l'episodio
		episode = self.__extractEpisode(elem)
		season["episodes"].append(episode)

		return base
	
	def __bindUrl(self, elem:dict) -> bool:
		"""
		Collega l'url di download a tutte le stagioni contenute nella serie in elem, se non trova nulla rimuove la stagione.

		Args:
		  elem: serie che contiene le stagioni a cui verranno aggiunti gli url di download

		Returns:
		  True da prendere / False da scartare
		"""

		table_entry = None
		title = elem["title"]
		if self.mapping_resolver:
			table_entry = self.mapping_resolver.find_entry(elem)
		elif title in self.table:
			table_entry = self.table[title]
		if table_entry is None:
			if not self.settings["AutoBind"]:
				# Se non è attiva la ricerca automatica provo a trovare dei url
				self.log.debug(f"❌ Serie '{title}' scartata perchè non è presente nella TABELLA DI CONVERSIONE.")
				return False
		else:
			if table_entry["absolute"]:
				# Se la serie è in formato 'absolute'
				elem = self.__convertToAbsolute(elem)

		series_metadata_cache = {}

		def getAlternativeTitles(seriesId:int, season_number:int):
			"""Ottiene i titoli alternativi di una serie Sonarr"""
			aliases = elem.get("alternateTitles")
			if not isinstance(aliases, list) or not aliases:
				if seriesId not in series_metadata_cache:
					res = self.sonarr.serie(seriesId)
					res.raise_for_status()
					series_metadata_cache[seriesId] = res.json()
				aliases = series_metadata_cache[seriesId].get("alternateTitles") or []
			return [alias for alias in aliases if alternate_title_matches_season(alias, season_number)]

		def filterSeason(season:dict) -> bool:
			"""Filtra le stagioni."""
			season_number = str(season["number"])
			series_info = None
			if self.mapping_resolver:
				resolution = self.mapping_resolver.resolve_existing_mapping(elem, season_number, log_skip=True)
				if resolution["should_search"]:
					series_info = self.__sonarrSeriesInfo(elem, getAlternativeTitles(elem['id'], season_number))
					resolution = self.mapping_resolver.resolve_existing_mapping(series_info, season_number, log_skip=True)
				if not resolution["should_search"]:
					season["urls"].extend(list(resolution["urls"]))
					return True
				if not self.settings["AutoBind"]:
					self.log.debug(f"Stagione {season['number']} della serie '{title}' senza mapping valido; ricerca automatica disattivata.")
					return False
			elif table_entry and season_number in table_entry["seasons"]:
				# Se la stagione è presente nella tabella
				if len(table_entry["seasons"][season_number]) == 0:
					# Una stagione senza URL e una mappatura incompleta che AutoBind puo completare.
					self.log.debug(f"❌ Stagione {season['number']} della serie '{title}' non contiene url nella TABELLA DI CONVERSIONE.")
					if not self.settings["AutoBind"]:
						return False
				else:
					season["urls"].extend(list(table_entry["seasons"][season_number]))
					return True
			else:
				# Se la stagione NON è presente in tabella
				self.log.debug(f"❌ Stagione {season['number']} della serie '{title}' non è presente nella TABELLA DI CONVERSIONE.")
				if not self.settings["AutoBind"]:
					return False

			# Se è attiva la ricerca automatica provo a trovare dei url mancanti o incompleti.
			if season['number'] == 'absolute':
				# Se la stagione è di tipo absolute
				self.log.debug(f"⛔ La ricerca automatica degli url di download è incompatibile con le serie ad ordinamento assoluto.")
				return False
			series_info = series_info or self.__sonarrSeriesInfo(elem, getAlternativeTitles(elem['id'], season_number))
			if not self.search_v3:
				self.log.error("OLD_SEARCH_PATH_USED: Search V3 unavailable in Processor.")
				return False
			language = self.mapping_automation.preferences.language_for(title) if self.mapping_automation else "AUTO"
			if self.mapping_automation:
				search = self.mapping_automation.search_mapping(series_info, season["number"], language_preference=language)
			else:
				self.log.error("OLD_SEARCH_PATH_USED: Processor mapping automation unavailable.")
				search = self.search_v3.search(series_info, language_preference=language, season=season["number"])
			if search["status"] != "matched" or not search["selected_candidate"]:
				if self.mapping_automation:
					self.mapping_automation.handle_search_result(title, season_number, search)
				self.log.debug(f"Ricerca automatica Search V3 per '{elem['title']}' stagione {season['number']}: {search['status']}; {search['errors']}")
				return False
			res = {"name": search["selected_candidate"]["title"], "url": search["selected_candidate"]["url"]}

			self.log.warning(f"Ricerca Search V3 selezionata per la stagione {season['number']} della serie '{elem['title']}': {res['url']} (score {search['selected_candidate']['score']})")
			if self.mapping_automation:
				self.mapping_automation.handle_search_result(title, season_number, search)
			# aggiungo ciò che ho trovato
			season["urls"].append(res["url"])

			return True

		# Aggiungo gli url alle stagioni
		elem["seasons"] = list(filter(filterSeason, elem["seasons"]))

		# Se ha almeno una stagione la prendo altrimenti la lascio
		return len(elem["seasons"]) > 0
	
	def __convertToAbsolute(self, elem:dict) -> dict:
		"""
		Converte una serie normale in una con formato 'absolute', cioè con una sola stagione di nome absolute.
		Se ci sono degli episodi che non hanno la numerazione assoluta li scarta.

		Args:
		  elem: la serie da convertire.
		
		Returns:
		  La serie converita.
		"""
		absolute_season = elem["seasons"][0]
		absolute_season["number"] = 'absolute'

		def checkEpisode(episode:dict) -> bool:
			"""Controlla se un episodio ha la numerazione assoluta."""
			if not episode["absoluteEpisodeNumber"]:
				self.log.debug(f"❌ Episodio E{episode['episodeNumber']}S{episode['seasonNumber']} della serie '{elem['title']}' scartato per mancanza di numerazione assoluta.")
				return False
			return True

		for season in elem["seasons"][1:]:
			absolute_season["episodes"].extend(filter(checkEpisode, season["episodes"]))
		
		del elem["seasons"][1:]

		return elem

	### EXTRACTOR ###

	def __extractSerie(self, elem:dict) -> dict:
		"""
		Estrare da elem solo le informazioni importanti che riguardano la serie.

		Args:
		  elem: elemento da cui estrarre le informazioni.

		Returns:
		  Le informazioni estratte.
		"""

		return {
			"title": elem["series"]["title"],
			"cleanTitle": elem["series"].get("cleanTitle"),
			"sortTitle": elem["series"].get("sortTitle"),
			"originalTitle": elem["series"].get("originalTitle"),
			"path": elem["series"]["path"],
			"tvdbId": elem["series"]["tvdbId"] if "tvdbId" in elem["series"] else None,
			"tvRageId": elem["series"]["tvRageId"] if "tvRageId" in elem["series"] else None,
			"tvMazeId": elem["series"]["tvMazeId"] if "tvMazeId" in elem["series"] else None,
			"imdbId": elem["series"]["imdbId"] if "imdbId" in elem["series"] else None,
			"id": elem["series"]["id"],
			"type": elem["series"]["seriesType"],
			"tags": elem["series"]["tags"],
			"alternateTitles": elem["series"].get("alternateTitles") or [],
		}

	def __sonarrSeriesInfo(self, elem:dict, alternate_titles:list) -> dict:
		return {
			"title": elem.get("title", ""),
			"cleanTitle": elem.get("cleanTitle", ""),
			"sortTitle": elem.get("sortTitle", ""),
			"originalTitle": elem.get("originalTitle", ""),
			"alternateTitles": [title if isinstance(title, dict) else {"title": title} for title in alternate_titles],
		}

	def __extractSeason(self, elem:dict) -> dict:
		"""
		Estrare da elem solo le informazioni importanti che riguardano la stagione.

		Args:
		  elem: elemento da cui estrarre le informazioni.

		Returns:
		  Le informazioni estratte.
		"""

		return {
			"number": elem["seasonNumber"]
		}
	
	def __extractEpisode(self, elem:dict) -> dict:
		"""
		Estrare da elem solo le informazioni importanti che riguardano l'episodio.

		Args:
		  elem: elemento da cui estrarre le informazioni.

		Returns:
		  Le informazioni estratte.
		"""

		return {
			"episodeNumber": elem["episodeNumber"],
			"seasonNumber": elem["seasonNumber"],
			"absoluteEpisodeNumber": elem["absoluteEpisodeNumber"] if "absoluteEpisodeNumber" in elem else None,
			"title": elem["title"],
			"id": elem["id"]
		}
