"""Deterministic source manifest embedded in every V4 release image."""
import argparse
import hashlib
import json
import os
from pathlib import Path


INCLUDED = (
    "anidown_eligibility.py",
    "requirements-v4.txt",
    "src/__init__.py",
    "src/v4",
    "frontend-v4/index.html",
    "frontend-v4/app.js",
    "frontend-v4/presentation.js",
    "frontend-v4/dialog-focus.js",
    "frontend-v4/styles.css",
    "frontend-v4/icon.svg",
    "tests/v4/fixtures/production_metadata_v1.json",
)
EXCLUDED_SUFFIXES = {".pyc", ".sqlite", ".sqlite3"}
EXCLUDED_DIRECTORIES = {"__pycache__", "graphify-out"}


def release_files(root):
    root = Path(root).resolve()
    result = []
    for name in INCLUDED:
        path = root / name
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            result.extend(candidate for candidate in path.rglob("*") if candidate.is_file())
    return sorted(
        {
            path
            for path in result
            if path.suffix not in EXCLUDED_SUFFIXES
            and not EXCLUDED_DIRECTORIES.intersection(path.parts)
        },
        key=lambda path: path.relative_to(root).as_posix(),
    )


def build_manifest(root, revision=None, tree_state="unverified"):
    root = Path(root).resolve()
    if tree_state not in {"clean", "dirty", "unverified"}:
        raise ValueError("tree_state must be clean, dirty or unverified")
    files = [{
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    } for path in release_files(root)]
    payload = {"schema_version": 2, "artifact": "anidown-v4", "vcs_revision": revision or None,
        "tree_state": tree_state, "files": files}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def compare_manifests(expected, observed):
    """Compare captured bytes without claiming unavailable repository history."""
    def rows(value):
        if not isinstance(value, dict) or value.get("artifact") != "anidown-v4" or not isinstance(value.get("files"), list):
            raise ValueError("Invalid AniDown V4 source manifest")
        result = {}
        for row in value["files"]:
            if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                    or not isinstance(row.get("sha256"), str) or not isinstance(row.get("size"), int)
                    or row["path"] in result):
                raise ValueError("Invalid source manifest file row")
            result[row["path"]] = (row["sha256"], row["size"])
        return result

    left = rows(expected)
    right = rows(observed)
    added = sorted(set(right) - set(left))
    removed = sorted(set(left) - set(right))
    changed = sorted(path for path in set(left) & set(right) if left[path] != right[path])
    identical = not added and not removed and not changed
    return {"status": "identical" if identical else "different", "added": added, "removed": removed,
        "changed": changed, "expected_revision": expected.get("vcs_revision"),
        "observed_revision": observed.get("vcs_revision"),
        "expected_tree_state": expected.get("tree_state", "unverified"),
        "observed_tree_state": observed.get("tree_state", "unverified")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--revision", default=os.getenv("ANIDOWN_V4_VCS_REVISION") or None)
    parser.add_argument("--tree-state", choices=("clean", "dirty", "unverified"),
        default=os.getenv("ANIDOWN_V4_TREE_STATE") or "unverified")
    args = parser.parse_args()
    output = Path(args.output)
    output.write_text(json.dumps(build_manifest(args.root, args.revision, args.tree_state), sort_keys=True,
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
