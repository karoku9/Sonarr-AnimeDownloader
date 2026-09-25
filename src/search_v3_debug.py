import argparse
import json
import logging
import os
import pathlib

from components.backend.database.Table import Table
from components.backend.search_v3 import SearchV3Service


def build_service() -> SearchV3Service:
	database = pathlib.Path(os.getenv("DATABASE_FOLDER", "/src/database"))
	table = Table(database.joinpath("table.json"))
	base_url = os.getenv("ANIMEWORLD_URL", "https://www.animeworld.ac")
	return SearchV3Service(database, base_url, table, logging.getLogger("search_v3_debug"))


def main():
	parser = argparse.ArgumentParser(description="Inspect AniDown Search V3 matching diagnostics.")
	parser.add_argument("query")
	parser.add_argument("--refresh", action="store_true", help="Refresh the public AnimeWorld catalog index first.")
	parser.add_argument("--language", default="AUTO")
	args = parser.parse_args()
	result = build_service().search(
		args.query,
		language_preference=args.language,
		debug=True,
		refresh_index=args.refresh,
		auto_refresh=False,
	)
	print(f"Search engine: {result['search_engine']}")
	print(f"Index exists: {result['index']['exists']}")
	print(f"Index size: {result['index']['size']}")
	print(f"Index generated at: {result['index']['generated_at']}")
	print(f"Status: {result['status']}")
	print(f"Variants: {json.dumps(result['title_variants'], ensure_ascii=False)}")
	print(f"Manual aliases: {json.dumps(result['manual_aliases'], ensure_ascii=False)}")
	print("Top candidates:")
	for candidate in result["top_candidates"][:10]:
		print(
			f"  {candidate['score']:.3f} {candidate['confidence']} {candidate['title']} "
			f"[{candidate['audio']}] {candidate['reason']} -> {candidate['url']}"
		)
	print(f"Selected candidate: {json.dumps(result['selected_candidate'], ensure_ascii=False)}")
	if result["errors"]:
		print(f"Errors: {json.dumps(result['errors'], ensure_ascii=False)}")


if __name__ == "__main__":
	main()
