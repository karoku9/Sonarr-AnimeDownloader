import pathlib
from copy import deepcopy
from ..atomic_json import read_json_durable, write_json_durable

class Database:
	"""Gestione file JSON"""

	def __init__(self, db:pathlib.Path) -> None:
		"""
		Collega il database.

		Args:
		  db: il file del database
		"""
		if not db.is_file(): raise FileNotFoundError()
		self.db = db
		self.fix()
		self._data = self.read()
	
	def read(self):
		"""Legge le informazioni contenute nel database."""
		return read_json_durable(self.db)
	
	def write(self, data) -> None:
		"""Scrive le informazioni nel database."""
		write_json_durable(self.db, data)
	
	def sync(self) -> None:
		"""Sincronizza il contenuto del db con quello in memoria."""
		self.write(self._data)

	def fix(self) -> None:
		"""Controlla l'integrità del database e nel caso lo corregge."""
		raise NotImplementedError()
	
	def getData(self):
		"""Restituisce una copia di tutto il contenuto del database."""
		return deepcopy(self._data)
	
	def setData(self, data) -> bool:
		"""Sovrascrive il contenuto del database."""

		self._data = deepcopy(data)
		self.sync()
		return True
