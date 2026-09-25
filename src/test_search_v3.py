import argparse
import os
import pathlib
import sys

from components.backend.database.Table import Table
from components.backend.search_v3 import SearchV3Service


QUERIES = [
	"The Ramparts of Ice",
	"Go For It, Nakamura-kun!!",
	"Gals Can't Be Kind to Otaku!?",
	"I Want to End This Love Game",
	"Witch Hat Atelier",
	"NEEDY GIRL OVERDOSE",
	"Heavenly Delusion",
	"Kill la Kill",
	"One-Punch Man",
	"High School DxD",
]
TABLE_REQUIRED = {"Heavenly Delusion", "Kill la Kill", "One-Punch Man", "High School DxD"}


def main() -> int:
	parser = argparse.ArgumentParser(description="AniDown Search V3 acceptance checks.")
	parser.add_argument("--no-refresh", action="store_true")
	args = parser.parse_args()
	database = pathlib.Path(os.getenv("DATABASE_FOLDER", "/src/database"))
	service = SearchV3Service(
		database,
		os.getenv("ANIMEWORLD_URL", "https://www.animeworld.ac"),
		Table(database.joinpath("table.json")),
	)
	if not args.no_refresh:
		refresh = service.refresh_index()
		print(f"Index refresh: size={refresh['index']['size']} errors={refresh['errors'][:3]}")
	if service.index_status()["size"] == 0:
		print("index_empty")
		return 1
	failed = False
	for query in QUERIES:
		result = service.search(query, debug=True, auto_refresh=False)
		candidates = result["top_candidates"]
		matches = [candidate for candidate in candidates if candidate["score"] >= 0.50]
		print(f"{query}: status={result['status']} candidates={len(matches)} diagnostic_candidates={len(candidates)}")
		for candidate in candidates[:3]:
			print(f"  {candidate['score']:.3f} {candidate['title']} {candidate['reason']} {candidate['url']}")
		if query in TABLE_REQUIRED and not matches:
			print(f"  FAIL: table-backed title returned no candidates: {query}")
			failed = True
		if not matches:
			print(f"  Diagnostics: variants={result['normalized_variants']} errors={result['errors']}")
	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(main())
