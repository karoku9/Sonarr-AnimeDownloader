# Frontend regressions

Offline DTO samples are generated from the stable sanitized real production fixture (source hash in sample-manifest.json). Only generated item IDs and timestamps are normalized. No invented anime metadata or hardcoded matching facts. Rebuild from V4 root: `python -B frontend-v4/tests/build-samples.py`; network is forbidden and temporary SQLite remains inside V4/work. Run `node --test frontend-v4/tests/presentation.test.mjs` (bundled Node or available Node). No install/build needed. Browser interaction evidence and screenshots remain ignored under work/phase7.

Owner: V4 frontend maintainers. Producer: build-samples.py; non-LLM consumer: presentation.test.mjs; fixture manifest schema1. Source-hash regression fails on drift; regenerate and review DTO differences when API/source changes. Retire these sample artifacts only when the corresponding frontend contract tests are replaced. They are test evidence, not alternate API or UI authority.
