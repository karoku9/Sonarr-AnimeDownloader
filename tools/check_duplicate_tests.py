"""Fail CI when a Python test method is silently overwritten."""
import argparse
import ast
from pathlib import Path


def duplicates(paths):
    found = []
    for root in paths:
        for path in sorted(Path(root).rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module_seen = {}
            for item in tree.body:
                is_test = (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"))
                is_test = is_test or (isinstance(item, ast.ClassDef) and item.name.startswith("Test"))
                if not is_test:
                    continue
                if item.name in module_seen:
                    found.append((path, "<module>", item.name, module_seen[item.name], item.lineno))
                else:
                    module_seen[item.name] = item.lineno
            for node in (item for item in ast.walk(tree) if isinstance(item, ast.ClassDef)):
                seen = {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                        if item.name in seen:
                            found.append((path, node.name, item.name, seen[item.name], item.lineno))
                        else:
                            seen[item.name] = item.lineno
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=["tests"])
    args = parser.parse_args()
    found = duplicates(args.paths)
    for path, scope, name, first, second in found:
        print(f"{path}:{second}: duplicate {scope}.{name}; first defined at line {first}")
    raise SystemExit(1 if found else 0)


if __name__ == "__main__":
    main()
