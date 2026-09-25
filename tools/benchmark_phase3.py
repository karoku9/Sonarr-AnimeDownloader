"""Benchmark Phase 3 read projections on a disposable SQLite copy.

The source file is opened only by shutil.copy2.  ApplicationStore migrations and all
benchmark reads target the new working copy, never the supplied source database.
"""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.v4.application import ApplicationService
from src.v4.application_store import ApplicationStore
from src.v4.application_dto import item_list_dto
from src.v4.animeworld_index import CatalogShortlistIndex
from src.v4.live_dataset import LiveSonarrDataset
from src.v4.series_view import series_list,series_detail


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def measure(call, runs, store):
    values = []
    decode_counts = []
    decode_bytes = []
    payload = None
    original_decode = store._decode
    counters = {"count": 0, "bytes": 0}
    def counted_decode(row):
        counters["count"] += 1
        counters["bytes"] += len(row["original"].encode("utf-8")) if row is not None else 0
        return original_decode(row)
    store._decode = counted_decode
    try:
        for _ in range(runs):
            counters.update(count=0, bytes=0)
            started = time.perf_counter()
            payload = call()
            values.append((time.perf_counter() - started) * 1000)
            decode_counts.append(counters["count"])
            decode_bytes.append(counters["bytes"])
    finally:
        store._decode = original_decode
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "median_ms": round(statistics.median(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "payload_bytes": len(encoded),
        "immutable_json_decodes": int(statistics.median(decode_counts)),
        "immutable_json_bytes_decoded": int(statistics.median(decode_bytes)),
    }, payload


def legacy_read_model(store):
    """Reproduce the pre-projection read semantics for an apples-to-apples baseline."""
    def overview():
        items=store.list_items()
        states={state:sum(item["mapping_state"]==state for item in items) for state in
            ("proposed","approved","rejected","needs_review","waiting","airing","unavailable","superseded")}
        return {"mapping_counts":states,
            "open_reviews":sum(item["review_state"]=="open" for item in items),
            "current_target_count":store.overview_counts()[2],"mode":"shadow","external_writes":False}
    def mappings():
        items=store.list_items();selected=items[:50]
        return {"items":[item_list_dto(item) for item in selected],"total":len(items),"offset":0,"limit":50}
    def series():
        rows=series_list(store.list_items(),store.season_overrides(),store.provider_validation_states())
        return {"items":rows,"total":len(rows),"offset":0,"limit":200}
    def detail(series_id):
        return series_detail(store.list_items(),store.season_overrides(),series_id,
            store.provider_validation_states())
    return overview,mappings,series,detail


def scan_benchmark(parent):
    class Seed:
        def read(self):
            return {"cases":[],"language_policy":{"default":"SUB","overrides":{}}}
    class Cache:
        def peek(self,_url,_seed):return None
        def get(self,_url,_seed):raise AssertionError("empty catalog must not request details")

    rows=[{"id":series_id,"title":f"Series {series_id}","seriesType":"anime","tags":[1],
        "seasons":[{"seasonNumber":1,"statistics":{"totalEpisodeCount":12,
            "episodeCount":12,"episodeFileCount":0}}]} for series_id in range(1,101)]
    routes=[]
    with TemporaryDirectory(dir=parent) as temporary:
        dataset=LiveSonarrDataset.__new__(LiveSonarrDataset)
        dataset.seed=Seed();dataset.detail_cache=Cache();dataset.validation_budget=30
        dataset.detail_request_budget=256;dataset.candidate_limit=128
        dataset.cache=Path(temporary);dataset.catalog_path=dataset.cache/"catalog.json"
        dataset._get=lambda route:routes.append(route) or ([{"id":1,"label":"animeworld"}]
            if route=="tag" else rows if route=="series" else [])
        initial=dataset.read_incremental(preserve_groups=[],current_target_ids=[],series_audio_master={})
        fingerprints=initial["target_inventory"]
        groups=[{"item_id":f"item-{series_id}","target_counts":{f"{series_id}:1":12},
            "audio":"SUB","source_fingerprints":{}} for series_id in range(1,101)]
        routes.clear();started=time.perf_counter()
        unchanged=dataset.read_incremental(preserve_groups=groups,
            current_target_ids=list(fingerprints),series_audio_master={},
            inventory_fingerprints=fingerprints)["incremental"]
        unchanged_ms=(time.perf_counter()-started)*1000
        rows[36]["seasons"][0]["statistics"]["totalEpisodeCount"]=13
        routes.clear();started=time.perf_counter()
        dirty=dataset.read_incremental(preserve_groups=groups,
            current_target_ids=list(fingerprints),series_audio_master={},
            inventory_fingerprints=fingerprints)["incremental"]
        dirty_ms=(time.perf_counter()-started)*1000
        dirty_routes=list(routes)

    catalog=[{"title":f"SyntheticTitle{index:05d}","aliases":[],
        "url":f"https://www.animeworld.ac/play/synthetic-{index}"} for index in range(10000)]
    started=time.perf_counter();index=CatalogShortlistIndex(catalog,limit=50)
    build_ms=(time.perf_counter()-started)*1000
    started=time.perf_counter()
    shortlists=[index.retrieve({"title":f"SyntheticTitle{value:05d}","alternateTitles":[]})
        for value in range(17)]
    retrieval_ms=(time.perf_counter()-started)*1000
    return {
        "fixture":{"series":100,"targets":100,"changed_targets":1,"catalog_entries":10000,
            "indexed_queries":17},
        "no_change":{"elapsed_ms":round(unchanged_ms,3),"sonarr_routes":["tag","series"],
            **{key:unchanged[key] for key in ("work_series_count","episode_fetches",
                "provider_rematches","catalog_shortlists","provider_validations_performed")}},
        "one_dirty":{"elapsed_ms":round(dirty_ms,3),"sonarr_routes":dirty_routes,
            **{key:dirty[key] for key in ("work_series_count","episode_fetches",
                "provider_rematches","catalog_shortlists","catalog_entries_examined")}},
        "provider_index":{"build_ms":round(build_ms,3),"retrieval_17_ms":round(retrieval_ms,3),
            "catalog_entries_examined":sum(row.catalog_entries_examined for row in shortlists),
            "overflow_count":sum(row.overflow for row in shortlists)},
    }


def main():
    parser = ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("working_copy", type=Path)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    working_copy = args.working_copy.resolve()
    if args.runs < 3:
        raise SystemExit("--runs must be at least 3")
    if working_copy.exists():
        raise SystemExit("working_copy already exists; choose a new disposable path")
    working_copy.parent.mkdir(parents=True, exist_ok=True)
    source_hash = digest(source)
    shutil.copy2(source, working_copy)
    store = ApplicationStore(working_copy)
    service = ApplicationService(store, {})
    before = {};after = {}
    old_overview,old_mappings,old_series,old_detail=legacy_read_model(store)
    before["overview"], _ = measure(old_overview, args.runs, store)
    before["mapping_list_50"], _ = measure(old_mappings, args.runs, store)
    before["series"], legacy_series = measure(old_series, args.runs, store)
    after["overview"], _ = measure(service.overview, args.runs, store)
    after["mapping_list_50"], _ = measure(lambda: service.items(offset=0, limit=50), args.runs, store)
    after["series_summary"], series = measure(lambda: service.series(offset=0, limit=200), args.runs, store)
    first_series = series["items"][0]["series_id"] if series["items"] else None
    if first_series is not None:
        before["series_detail"], old_detail = measure(lambda: old_detail(first_series), args.runs, store)
        after["series_detail"], new_detail = measure(lambda: service.series_detail(first_series), args.runs, store)
    else:
        old_detail=new_detail=None
    result = {
        "method": "median of repeated in-process calls on one fresh disposable source copy; before explicitly reproduces the pre-projection full-history read algorithms",
        "runs": args.runs,
        "source": str(source),
        "source_sha256": source_hash,
        "working_copy": str(working_copy),
        "current_targets": service.overview()["current_target_count"],
        "series": series["total"],
        "first_series_id": first_series,
        "database_rows": {
            "immutable_items": len(store.list_items()),
            "current_targets": service.overview()["current_target_count"],
            "projected_series": series["total"],
        },
        "before": before,
        "after": after,
        "semantic_equivalence": {
            "series_count_equal": legacy_series["total"] == series["total"],
            "first_series_detail_equal": old_detail == new_detail,
        },
        "scan_architecture": scan_benchmark(working_copy.parent),
        "query_plans": store.projection_query_plans(),
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
