from . import Constant as ctx
from ..utility import ColoredString as cs
from ..database import *
from ..connection import *
from ..search_v3 import SearchV3Service
from ..mapping_automation import MappingAutomation
from ..runtime import RuntimeSettings
from ..event_log import StructuredEventStore
from .Downloader import Downloader
from .Processor import Processor

import logging, logging.handlers
import sys, threading
import time
from typing import Optional
import httpx
import animeworld as aw

class Core(threading.Thread):

	def __init__(self, *, 
		settings:Optional[Settings]=None, 
		tags:Optional[Tags]=None, 
		table:Optional[Table]=None, 
		sonarr:Optional[Sonarr]=None,
		github:Optional[GitHub]=None,
		connections_db:Optional[ConnectionsDB]=None,
		external:Optional[ExternalDB]=None
	):
		"""
		Inizializzazione funzionalità di base.

		Args:
		  settings: Override Settings
		  tags: Override Tags
		  table: Override Table
		  sonarr: Override Sonarr
		  github: Override GitHub
		  connections_db: Override ConnectionsDB
		  external: Override ExternalDB
		"""

		### Setup Thread ###
		super().__init__(name=self.__class__.__name__, daemon=True)

		self.semaphore = threading.Condition()
		self.scan_lock = threading.Lock()
		self.version = ctx.VERSION

		### Setup logger ###
		self.__setupLog()

		### Setup database ###
		self.settings = settings if settings else Settings(ctx.DATABASE_FOLDER.joinpath('settings.json'))
		self.tags = tags if tags else Tags(ctx.DATABASE_FOLDER.joinpath('tags.json'))
		self.table = table if table else Table(ctx.DATABASE_FOLDER.joinpath('table.json'))
		connections_path = ctx.DATABASE_FOLDER.joinpath('connections.json')
		if not connections_path.exists() and ctx.SCRIPT_FOLDER.joinpath('connections.json').exists():
			connections_path = ctx.SCRIPT_FOLDER.joinpath('connections.json')
		self.connections_db = connections_db if connections_db else ConnectionsDB(connections_path, ctx.SCRIPT_FOLDER)
		self.external = external if external else ExternalDB()
		self.events = StructuredEventStore(ctx.DATABASE_FOLDER.joinpath("ui_log_events.json"))
		self.search_v3 = SearchV3Service(ctx.DATABASE_FOLDER, ctx.ANIMEWORLD_URL, self.table, self.log, self.events)
		self.runtime_settings = RuntimeSettings(ctx.DATABASE_FOLDER.joinpath("runtime_settings.json"), self.log, self.events)

		### Fix log level ###
		self.log.setLevel(self.settings["LogLevel"])

		### Setup Connection ###
		self.sonarr = sonarr if sonarr else Sonarr(ctx.SONARR_URL, ctx.API_KEY)
		self.github = github if github else GitHub()
		self.connections = ConnectionsManager(self.connections_db)
		self.mapping_automation = MappingAutomation(
			ctx.DATABASE_FOLDER,
			self.table,
			self.search_v3,
			self.runtime_settings,
			self.sonarr,
			self.settings,
			self.tags,
			self.log,
			self.events,
		)
		self.mapping_resolver = self.mapping_automation.resolver

		### Setup Logic ###
		aw.SES.base_url = ctx.ANIMEWORLD_URL
		self.processor = Processor(sonarr=self.sonarr, settings=self.settings, table=self.table, tags=self.tags, external=self.external, search_v3=self.search_v3, mapping_automation=self.mapping_automation, mapping_resolver=self.mapping_resolver)
		self.downloader = Downloader(settings=self.settings, sonarr=self.sonarr, connections=self.connections, folder=ctx.DOWNLOAD_FOLDER, runtime_settings=self.runtime_settings)

		self.error = None

		### Welcome Message ###
		self.log.info(cs.blue(f"┌───────────────────────────────────[{time.strftime('%d %b %Y %H:%M:%S')}]───────────────────────────────────┐"))
		self.log.info(cs.blue(r"│                 _                _____                      _                 _            │"))
		self.log.info(cs.blue(r"│     /\         (_)              |  __ \                    | |               | |           │"))
		self.log.info(cs.blue(r"│    /  \   _ __  _ _ __ ___   ___| |  | | _____      ___ __ | | ___   __ _  __| | ___ _ __  │"))
		self.log.info(cs.blue(r"│   / /\ \ | '_ \| | '_ ` _ \ / _ \ |  | |/ _ \ \ /\ / / '_ \| |/ _ \ / _` |/ _` |/ _ \ '__| │"))
		self.log.info(cs.blue(r"│  / ____ \| | | | | | | | | |  __/ |__| | (_) \ V  V /| | | | | (_) | (_| | (_| |  __/ |    │"))
		self.log.info(cs.blue(r"│ /_/    \_\_| |_|_|_| |_| |_|\___|_____/ \___/ \_/\_/ |_| |_|_|\___/ \__,_|\__,_|\___|_|    │"))
		self.log.info(cs.blue(r"│                                                                                            │"))
		self.log.info(cs.blue(f"└────────────────────────────────────{ctx.VERSION:─^20}────────────────────────────────────┘"))
		self.log.info("")
		self.log.info("Globals")
		self.log.info(f"  ├── {ctx.SONARR_URL = :}")
		self.log.info("  ├── API_KEY = [configured]")
		self.log.debug(f"  ├── {ctx.ANIMEWORLD_URL = :}")
		self.log.debug(f"  ├── {ctx.DOWNLOAD_FOLDER = :}")
		self.log.debug(f"  ├── {ctx.DATABASE_FOLDER = :}")
		self.log.debug(f"  ├── {ctx.SCRIPT_FOLDER = :}")
		self.log.info(f"  └── {ctx.VERSION = :}")
		self.log.info("")
		self.log.info("Settings")
		for index, setting in reversed(list(enumerate(self.settings))):
			if index > 0:
				self.log.info(f"  ├── {setting} = {self.settings[setting]}")
			else:
				self.log.info(f"  └── {setting} = {self.settings[setting]}")
		self.log.info("")
		self.log.debug("Tags")
		for index, tag in reversed(list(enumerate(self.tags))):
			if index > 0:
				self.log.debug(f"  ├── {tag['id']} - {tag['name']} ({'🟢' if tag['active'] else '🔴'})")
			else:
				self.log.debug(f"  └── {tag['id']} - {tag['name']} ({'🟢' if tag['active'] else '🔴'})")
		self.log.debug("")
		self.log.debug("Connections")
		for index, connection in reversed(list(enumerate(self.connections_db))):
			if index > 0:
				self.log.debug(f"  ├── {connection['name']} - {connection['script']} ({'🟢' if connection['active'] else '🔴'})")
			else:
				self.log.debug(f"  └── {connection['name']} - {connection['script']} ({'🟢' if connection['active'] else '🔴'})")
		self.log.debug("")


	def __setupLog(self):
		"""Configura la parte riguardante il logger."""

		logger = ctx.LOGGER

		stream_handler = logging.StreamHandler(sys.stdout)
		stream_handler.terminator = '\n'
		stream_handler.setFormatter(logging.Formatter('%(levelname)-8s %(message)s'))
		logger.addHandler(stream_handler)

		file_handler = logging.FileHandler(filename='log.log', encoding='utf-8', mode='w')
		file_handler.terminator = '\n'
		file_handler.setFormatter(logging.Formatter('%(levelname)-8s %(message)s'))
		logger.addHandler(file_handler)

		logger.propagate = True

		self.log = logger

	def run(self):
		"""Avvio del processo di ricerca episodi."""
		self.log.info("")
		self.log.info("]────────────────────────────────────────────────────────────────────────────────────────────[")
		self.log.info("")

		# Acquire lock
		self.semaphore.acquire()

		try:
			while True:
				start = time.time()
				self.log.info(f"╭───────────────────────────────────「{time.strftime('%d %b %Y %H:%M:%S')}」───────────────────────────────────╮")

				self.job()
				
				next_run = self.settings['ScanDelay']*60 + start
				wait = next_run - time.time()
				self.log.info(f"╰───────────────────────────────────「{time.strftime('%d %b %Y %H:%M:%S', time.localtime(next_run))}」───────────────────────────────────╯")
				self.log.info("")

				# release lock and wait for next execution
				self.semaphore.wait(timeout=wait)
		except Exception as e:
			# Errore interno non recuperabile
			self.log.critical("]─────────────────────────────────────────[CRITICAL]─────────────────────────────────────────[")
			self.log.exception(e)
			self.error = e

	def job(self, trigger: str = "scheduled"):
		"""
		Processo principale di ricerca e download.
		"""
		if not self.scan_lock.acquire(blocking=False):
			self.log.info("MANUAL_SCAN_ALREADY_RUNNING" if trigger == "manual" else "SCHEDULED_SCAN_SKIPPED_ALREADY_RUNNING")
			return {"started": False, "reason": "already_running"}
		try:
			return self.__job_locked(trigger)
		finally:
			self.scan_lock.release()

	def __job_locked(self, trigger: str = "scheduled"):
		try:
			self.log.info("")
			if trigger == "manual":
				self.log.info("MANUAL_SCAN_STARTED")
				if self.events:
					self.events.record({
						"type": "RUNTIME",
						"event": "MANUAL_SCAN_STARTED",
						"status": "info",
						"compact": "Manual scan started",
						"details": {"trigger": trigger},
					})

			missing = self.processor.getData()
			episode_count = sum(len(season.get("episodes", [])) for serie in missing for season in serie.get("seasons", []))
			if trigger == "manual":
				self.log.info(f"SONARR_WANTED_MISSING_REFRESH\n  episodes_found={episode_count}\n  series_found={len(missing)}")
				if self.events:
					self.events.record({
						"type": "SONARR",
						"event": "SONARR_WANTED_MISSING_REFRESH",
						"status": "info",
						"compact": f"Sonarr wanted/missing refresh | {episode_count} episodes found",
						"details": {"episodes_found": episode_count, "series_found": len(missing)},
					})

			self.log.info("")
			self.log.info("──────────────────────────────────────────────────────────────────────────────────────────────")
			self.log.info("")
			
			for serie in missing:
				self.log.info("─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ")
				self.log.info("")

				self.downloader.download(serie)			

				self.log.info("")
				self.log.info("─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ")
				self.log.info("")
			if trigger == "manual":
				self.log.info(f"MANUAL_SCAN_COMPLETED\n  episodes_found={episode_count}\n  series_processed={len(missing)}")
				if self.events:
					self.events.record({
						"type": "RUNTIME",
						"event": "MANUAL_SCAN_COMPLETED",
						"status": "success",
						"compact": f"Manual scan completed | processed {episode_count} missing episodes",
						"details": {"episodes_found": episode_count, "series_processed": len(missing), "downloaded": None, "skipped": None, "errors": 0},
					})
			return {"started": True, "episodes_found": episode_count, "series_processed": len(missing)}
		except aw.DeprecatedLibrary as e:
			self.log.error(cs.red(f"🅴🆁🆁🅾🆁: {e}"))
			if trigger == "manual" and self.events:
				self.events.record({
					"type": "RUNTIME",
					"event": "MANUAL_SCAN_FAILED",
					"status": "error",
					"compact": f"Manual scan failed | {e}",
					"details": {"error": str(e)},
				})
			return {"started": True, "error": str(e)}

	def runScanNow(self) -> dict:
		"""Run one scan immediately in a background worker."""
		if not self.scan_lock.acquire(blocking=False):
			if self.events:
				self.events.record({
					"type": "RUNTIME",
					"event": "MANUAL_SCAN_ALREADY_RUNNING",
					"status": "warning",
					"compact": "Manual scan ignored | a scan is already running",
					"details": {},
				})
			return {"ok": False, "started": False, "message": "A scan is already running"}
		def run_manual():
			try:
				self.__job_locked("manual")
			finally:
				self.scan_lock.release()
		worker = threading.Thread(target=run_manual, name="ManualScan", daemon=True)
		worker.start()
		return {"ok": True, "started": True, "message": "Manual scan started"}
				
	def wakeUp(self) -> bool:
		"""
		Fa partire immediatamente il processo di ricerca e download.
		"""
		try:
			# acquire lock
			self.semaphore.acquire()
			# resume thread
			self.semaphore.notify()
			# release lock
			self.semaphore.release()
		except RuntimeError as e:
			return False
		else:
			return True



	# def join(self) -> None:
	# 	super().join()
	# 	# Se è stata sollevata un eccezione la propaga
	# 	if self.error: raise self.error
