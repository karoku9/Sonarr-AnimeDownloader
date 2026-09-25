from v4_test_support import repo_tempdir
import shutil
import shlex
import re
import unittest
from pathlib import Path

from src.v4.source_manifest import INCLUDED, build_manifest, compare_manifests


ROOT = Path(__file__).resolve().parents[2]


class Phase5RemediationTests(unittest.TestCase):
    def test_release_manifest_matches_the_runtime_image_file_set(self):
        expected = build_manifest(ROOT, revision="phase5-fixture", tree_state="clean")
        paths = {row["path"] for row in expected["files"]}

        self.assertIn("src/v4/runtime.py", paths)
        self.assertIn("frontend-v4/index.html", paths)
        self.assertIn("tests/v4/fixtures/production_metadata_v1.json", paths)
        self.assertNotIn("Dockerfile.v4", paths)
        self.assertNotIn("frontend-v4/package.json", paths)
        self.assertNotIn("frontend-v4/tests/presentation.test.mjs", paths)
        self.assertNotIn("tests/v4/fixtures/phase16_regressions_v1.json", paths)
        self.assertFalse(any("graphify-out" in path for path in paths))

        with repo_tempdir() as temporary:
            image_root = Path(temporary)
            for row in expected["files"]:
                source = ROOT / row["path"]
                destination = image_root / row["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            observed = build_manifest(
                image_root,
                revision="phase5-fixture",
                tree_state="clean",
            )

        self.assertEqual(compare_manifests(expected, observed)["status"], "identical")

    def test_manifest_allowlist_matches_dockerfile_copy_sources(self):
        dockerfile = (ROOT / "Dockerfile.v4").read_text(encoding="utf-8")
        copied_sources = []
        for line in dockerfile.splitlines():
            if not line.startswith("COPY "):
                continue
            fields = shlex.split(line)
            copied_sources.extend(source.rstrip("/") for source in fields[1:-1])

        self.assertEqual(set(INCLUDED), set(copied_sources))

    def test_release_python_sources_are_plain_utf8_without_bom(self):
        manifest = build_manifest(ROOT)
        python_paths = [ROOT / row["path"] for row in manifest["files"] if row["path"].endswith(".py")]
        self.assertTrue(python_paths)
        self.assertFalse(
            [path.relative_to(ROOT).as_posix() for path in python_paths if path.read_bytes().startswith(b"\xef\xbb\xbf")]
        )

    def test_release_pipeline_compares_source_and_embedded_manifest_bytes(self):
        workflow = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
        self.assertIn("SOURCE-MANIFEST.expected.json", workflow)
        self.assertIn("SOURCE-MANIFEST.observed.json", workflow)
        self.assertIn("cmp --silent", workflow)

    def test_release_pipeline_pins_every_external_action_by_commit(self):
        workflow = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
        references = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE)
        self.assertTrue(references)
        for reference in references:
            if reference.startswith("./"):
                continue
            _action, separator, revision = reference.rpartition("@")
            self.assertEqual(separator, "@", reference)
            self.assertRegex(revision, r"^[0-9a-f]{40}$", reference)

    def test_release_pipeline_does_not_interpolate_external_values_into_shell(self):
        workflow = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
        self.assertNotIn("version='${{ github.event.inputs.version }}'", workflow)
        self.assertNotIn("version='${{ github.event.release.tag_name }}'", workflow)
        self.assertNotIn("docker_user='${{ secrets.DOCKER_HUB_USERNAME }}'", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn('[[ "$version" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]', workflow)
        self.assertNotIn("id-token: write", workflow)

    def test_v4_image_base_is_digest_pinned(self):
        dockerfile = (ROOT / "Dockerfile.v4").read_text(encoding="utf-8")
        first_line = dockerfile.splitlines()[0]
        self.assertRegex(first_line, r"^FROM python:3\.13\.2-slim@sha256:[0-9a-f]{64}$")

    def test_docker_context_contains_the_pinned_requirements(self):
        for name in (".dockerignore", "Dockerfile.v4.dockerignore"):
            context = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("!requirements-v4.txt", context.splitlines())

    def test_compose_preview_is_offline_and_has_no_host_specific_inputs(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("env_file:", compose)
        self.assertNotIn("C:/Users/", compose)
        self.assertNotIn("C:/anidown/", compose)
        self.assertNotIn("host.docker.internal", compose)
        self.assertNotIn("production-snapshot.json", compose)
        self.assertNotIn("ANIDOWN_V4_SONARR_KEY_FILE", compose)
        self.assertNotIn("ANIDOWN_V4_AUTH_TOKEN_FILE", compose)
        self.assertIn('ANIDOWN_V4_LIVE_SONARR: "0"', compose)
        self.assertIn("tests/v4/fixtures/production_metadata_v1.json", compose)
        self.assertIn("image: anidown-v4:preview-local", compose)
        self.assertIn('restart: "no"', compose)
        self.assertIn("internal: true", compose)


if __name__ == "__main__":
    unittest.main()
