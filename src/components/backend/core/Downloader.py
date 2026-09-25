from ..database import Settings
from ..connection import ConnectionsManager, Sonarr
from .Constant import LOGGER
from ..utility import ColoredString as cs
from ..legacy_episode_ranges import expand_episode_number

import re, pathlib, time
import shutil, tenacity
import animeworld as aw
from copy import deepcopy
from functools import reduce
from typing import Callable, Any, List


class Downloader:
	"""Gestisce il corretto download degli episodi."""

	def __init__(self, settings:Settings, sonarr:Sonarr, connections:ConnectionsManager, folder:pathlib.Path, runtime_settings=None):
		"""
		Args:
		  settings: Impostazioni
		  sonarr: collegamento con Sonarr
		  connections: collegamento con le Connections
		  folder: la cartella di download
		"""

		self.settings = settings
		self.sonarr = sonarr
		self.connections = connections
		self.folder = folder
		self.runtime_settings = runtime_settings
		self.log = LOGGER
		self.hook = lambda x:None

	def connectHook(self, hook:Callable[[dict[str,Any]], None]):
		"""
		Collega la funzione di hook che verrà richiamata svariate volte durante il download per monitorarne il progresso.

		Args:
		  hook: funzione da richiamare durante il download
		"""
		self.hook = hook

	def download(self, serie:dict):
		"""
		Scarica ogni episodio contenuto nella serie.

		Args:
		  serie: dizionario con le informazioni
		"""

		for season in serie["seasons"]:
			try:
				reason = self.__downloadBlockReason()
				if reason:
					for episode in season["episodes"]:
						self.__logSkippedDownload(serie, season, episode, reason)
					continue
				self.log.info(f"🔎 Ricerca serie '{serie['title']}' stagione {season['number']}.")

				tmp = [aw.Anime(link=x) for x in season["urls"]]

				episodes_str = ", ".join([str(x["episodeNumber"]) for x in season["episodes"]])
				self.log.info(f"🔎 Ricerca episodio {episodes_str}.")

				episode_groups = [x.getEpisodes() for x in tmp]
				if len(season["urls"]) > 1:
					self.__logMultiUrlRoute(serie, season, episode_groups)
				episodi:List[aw.Episodio] = reduce(self.flattenEpisodes, episode_groups, [])

				for episode in season["episodes"]:
					reason = self.__downloadBlockReason()
					if reason:
						self.__logSkippedDownload(serie, season, episode, reason)
						continue
					self.log.info("")
					self.log.info(f"⚙️ Verifica se l'episodio S{episode['seasonNumber']}E{episode['episodeNumber']} è disponibile.")
					route = self.__routeForEpisode(season, episode, episode_groups)
					if route:
						self.log.info(f"ROUTE_BY_URL_ORDER\n  title={serie['title']}\n  season={season['number']}\n  episode={episode['episodeNumber']}\n  url_index={route['url_index']}\n  source_episode={route['source_episode']}\n  url={route['url']}")
						if self.runtime_settings and getattr(self.runtime_settings, "events", None):
							self.runtime_settings.events.record({
								"type": "DOWNLOAD",
								"event": "DOWNLOAD_ROUTE_SELECTED",
								"status": "success",
								"title": serie["title"],
								"season": season["number"],
								"compact": f"{serie['title']} S{season['number']}E{episode['episodeNumber']} | routed by URL order | URL #{route['url_index']} | source episode {route['source_episode']}",
								"details": route,
							})

					# Controllo se è in download su Sonarr
					if self.__isInQueue(episode['id']):
						self.log.info("🔒 L'episodio è già in download su Sonarr.")
						continue

					episodio = None

					if season["number"] == 'absolute':
						# La serie è in formato assoluto
						res = filter(lambda x: x.number == str(episode['absoluteEpisodeNumber']), episodi)
						episodio = next(res, None)
					else:
						# La serie è normale
						res = filter(lambda x: x.number == str(episode['episodeNumber']), episodi)
						episodio = next(res, None)
					
					if not episodio:
						self.log.info("✖️ L'episodio NON è ancora uscito.")
						continue
					
					self.log.info("✔️ L'episodio è disponibile.")
					reason = self.__downloadBlockReason()
					if reason:
						self.__logSkippedDownload(serie, season, episode, reason)
						continue
					self.log.warning(f"⏳ Download episodio S{episode['seasonNumber']}E{episode['episodeNumber']}.")

					title = f'{serie["title"]} - S{episode["seasonNumber"]}E{episode["episodeNumber"]}'
					file = episodio.download(title, self.folder, hook=self.hook)

					if not file:
						self.log.warning("⚠️ Errore in fase di download.")
						continue

					file = self.folder.joinpath(file)
					
					self.log.info("✔️ Dowload Completato.")

					if self.__skipPostDownloadActions(serie, season, episode):
						continue

					if self.settings["MoveEp"]:
						# Se l'episodio deve essere spostato
					
						destination = pathlib.Path(serie["path"])
						self.log.warning(f"⏳ Spostamento episodio episodio S{episode['seasonNumber']}E{episode['episodeNumber']} in {destination}.")
						if not self.__moveFile(file, destination):
							self.log.error("✖️ Fallito spostamento episodio.")
							continue

						self.log.info("✔️ Episodio spostato.")
						if self.__skipPostDownloadActions(serie, season, episode):
							continue
						# Dopo aver spostato il file faccio scansionare a Sonarr la serie per trovarlo
						self.log.info(f"⏳ Aggiornamento serie '{serie['title']}'.")
						self.sonarr.commandRescanSeries(serie['id'])

						if self.settings["RenameEp"]:
							# Se l'episodio deve essere rinominato
							self.log.info("⏳ Rinominando l'episodio.")

							# Aspetto 2s che Sonarr abbia finito di ricaricare la serie
							time.sleep(2)

							# Chiedo a Sonarr di rinominare l'episodio scaricato
							if self.__skipPostDownloadActions(serie, season, episode):
								continue
							self.__renameFile(episode['id'], serie['id'])

							self.log.info("✔️ Episodio rinominato.")
					
					if self.__skipPostDownloadActions(serie, season, episode):
						continue
					# Invio una notifica tramite Connections
					self.log.info('✉️ Inviando il messaggio tramite Connections.')
					self.connections.send(f"*Episode Downloaded*\n{serie['title']} - {episode['seasonNumber']}x{episode['episodeNumber']} - {episode['title']}")

			except aw.AnimeNotAvailable as e:
				self.log.info(f'⚠️ {e}')
			except (aw.ServerNotSupported, aw.Error404) as e:
				self.log.warning(cs.yellow(f"🆆🅰🆁🅽🅸🅽🅶: {e}"))

	def __downloadBlockReason(self) -> str | None:
		return self.runtime_settings.download_block_reason() if self.runtime_settings else None

	def __logSkippedDownload(self, serie: dict, season: dict, episode: dict, reason: str) -> None:
		marker = "SEARCH_ONLY_MODE_SKIP_DOWNLOAD" if reason == "search_only" else "DOWNLOAD_SKIPPED_RUNTIME_PAUSED"
		self.log.warning(
			f"{marker}\n"
			f"  title={serie['title']}\n"
			f"  season={season['number']}\n"
			f"  episode={episode['episodeNumber']}\n"
			"  action=skipped_download_due_to_pause"
		)
		if self.runtime_settings and getattr(self.runtime_settings, "events", None):
			self.runtime_settings.events.record({
				"level": "WARNING",
				"type": "DOWNLOAD",
				"event": marker,
				"status": "warning",
				"title": serie["title"],
				"season": season["number"],
				"compact": f"{serie['title']} S{season['number']}E{episode['episodeNumber']} | download skipped | {reason} mode",
				"details": {"reason": reason, "episode": episode["episodeNumber"], "action": "skipped_download_due_to_pause"},
			})

	def __skipPostDownloadActions(self, serie: dict, season: dict, episode: dict) -> bool:
		if self.__downloadBlockReason() != "search_only":
			return False
		self.log.warning(
			"SEARCH_ONLY_MODE_SKIP_DOWNLOAD\n"
			f"  title={serie['title']}\n"
			f"  season={season['number']}\n"
			f"  episode={episode['episodeNumber']}\n"
			"  action=skipped_post_download_actions"
		)
		if self.runtime_settings and getattr(self.runtime_settings, "events", None):
			self.runtime_settings.events.record({
				"level": "WARNING",
				"type": "DOWNLOAD",
				"event": "SEARCH_ONLY_MODE_SKIP_DOWNLOAD",
				"status": "warning",
				"title": serie["title"],
				"season": season["number"],
				"compact": f"{serie['title']} S{season['number']}E{episode['episodeNumber']} | post-download actions skipped | search-only mode",
				"details": {"episode": episode["episodeNumber"], "action": "skipped_post_download_actions"},
			})
		return True

	def __routeForEpisode(self, season: dict, episode: dict, episode_groups: list[list[Any]]) -> dict | None:
		if season["number"] == "absolute" or len(season.get("urls", [])) < 2:
			return None
		target = int(episode.get("episodeNumber", 0) or 0)
		start = 1
		for index, group in enumerate(episode_groups, start=1):
			count = len([ep for ep in group if re.search(r"^\d+$", str(getattr(ep, "number", ""))) is not None])
			end = start + count - 1
			if count > 0 and start <= target <= end:
				return {
					"url_index": index,
					"url": season["urls"][index - 1],
					"source_episode": target - start + 1,
					"flattened_start": start,
					"flattened_end": end,
					"season_urls": list(season.get("urls", [])),
					"episode_counts": [len(group) for group in episode_groups],
				}
			start = end + 1
		return None

	def __logMultiUrlRoute(self, serie: dict, season: dict, episode_groups: list[list[Any]]) -> None:
		ranges = []
		start = 1
		for index, group in enumerate(episode_groups, start=1):
			count = len([ep for ep in group if re.search(r"^\d+$", str(getattr(ep, "number", ""))) is not None])
			end = start + count - 1 if count else None
			ranges.append({"url_index": index, "url": season["urls"][index - 1], "source_episode_count": count, "flattened_start": start if count else None, "flattened_end": end})
			if count:
				start = end + 1
		self.log.info(
			"MULTI_URL_ROUTE_PREVIEW\n"
			f"  title={serie['title']}\n"
			f"  season={season['number']}\n"
			f"  urls_count={len(season.get('urls', []))}\n"
			f"  ranges={ranges}"
		)
		if self.runtime_settings and getattr(self.runtime_settings, "events", None):
			self.runtime_settings.events.record({
				"type": "DOWNLOAD",
				"event": "MULTI_URL_ROUTE_PREVIEW",
				"status": "info",
				"title": serie["title"],
				"season": season["number"],
				"compact": f"{serie['title']} S{season['number']} | URL order route prepared | {len(ranges)} URLs",
				"details": {"ranges": ranges, "season_urls": list(season.get("urls", []))},
			})

	def flattenEpisodes(self, base:list[aw.Episodio], elem:list[aw.Episodio]) -> list[aw.Episodio]:
		"""
		Linearizza la lista di episodi che appartengono a più pagine Animeworld e corregge eventuali problemi.

		Args:
			base: lista contenente il risultato della riduzione
			elem: lista di episodi da aggiungere alla base
		"""

		# numero da aggiungere per rendere consecutivi gli episodi di varie stagioni
		limit = 0 if len(base) == 0 else int(base[-1].number)

		for ep in elem:
			# Parse every endpoint before mutating the source object.  The old code
			# changed ``ep.number`` to the first endpoint and then split that changed
			# value again, crashing deterministically for values such as ``1-2``.
			numbers = expand_episode_number(ep.number, offset=limit)
			for index, number in enumerate(numbers):
				current = ep if index == 0 else deepcopy(ep)
				current.number = str(number)
				base.append(current)

		return base
	
	def __isInQueue(self, episode_id:int) -> bool:
		"""
		Controllo se un episodio è in download su Sonarr.

		Args:
		  episode_id: L'ID dell'episodio.

		Returns:
		  True se è in download su Sonarr, altrimenti False.
		"""

		# Controllo che non sia già in download su sonarr
		res = self.sonarr.queue()
		res.raise_for_status()
		records = res.json()["records"]

		for record in records:
			if episode_id == record["episodeId"]: return True
		return False
	
	def __moveFile(self, src:pathlib.Path, dst:pathlib.Path) -> pathlib.Path:
		"""
		Sposta il file da src a dst.

		Args:
		  src: file da spostare
		  dst: cartella di destinazione
		
		Returns:
		  La path che punta al file spostato.
		"""

		if not src.is_file():
			raise FileNotFoundError(src)

		# Controllo se la cartella di destinazione non sia una cartella windows
		if re.match(r"\w:", str(dst)):
			dst = pathlib.PureWindowsPath(dst).as_posix()
			dst = pathlib.PosixPath(re.sub(r"\w:","",dst))
		
		if not dst.is_dir():
			# Se la cartella non esiste viene creata
			dst.mkdir(parents=True)
			self.log.warning(f'⚠️ La cartella {dst} è stata creata.')
		
		dst = dst.joinpath(src.name)
		return shutil.move(src,dst)
	
	@tenacity.retry(reraise=True, stop=tenacity.stop_after_attempt(3), wait=tenacity.wait_fixed(2))
	def __renameFile(self, episode_id:int, serie_id:int) -> None:
		"""
		Rinomina il file seguendo la formattazione definita su Sonarr.

		Args:
		  episode_id: id_episodio su Sonarr
		  serie_id: id della serie su Sonarr
		"""

		res = self.sonarr.episode(episode_id)
		res.raise_for_status()
		res = res.json()

		if "episodeFile" not in res: raise Exception("Episodio Non trovato")

		file_id = res["episodeFile"]["id"]
		self.sonarr.commandRenameFiles(serie_id,[file_id])
