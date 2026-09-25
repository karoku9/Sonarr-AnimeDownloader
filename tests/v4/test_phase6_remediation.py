import asyncio
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def yaml_block(document, key, indent):
    lines = document.splitlines()
    marker = " " * indent + key + ":"
    start = lines.index(marker)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            end = index
            break
    return "\n".join(lines[start:end])


class Phase6ComposeTests(unittest.TestCase):
    def setUp(self):
        self.compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.app = yaml_block(self.compose, "anidown-v4", 2)
        self.proxy = yaml_block(self.compose, "anidown-v4-loopback", 2)

    def test_only_proxy_publishes_loopback_and_app_stays_internal(self):
        self.assertNotIn("\n    ports:", self.app)
        self.assertIn("\n    expose:\n      - \"6004\"", self.app)
        self.assertIn("\n    networks:\n      - v4-preview-internal", self.app)
        self.assertNotIn("v4-preview-ingress", self.app)
        self.assertIn('\n    ports:\n      - "127.0.0.1:6004:6004"', self.proxy)
        self.assertIn("\n    networks:\n      v4-preview-internal: {}", self.proxy)
        self.assertIn("\n      v4-preview-ingress:\n        gw_priority: 1", self.proxy)
        self.assertIn("  v4-preview-internal:\n    driver: bridge\n    internal: true", self.compose)
        self.assertIn("  v4-preview-ingress:\n    driver: bridge", self.compose)
        self.assertNotIn("v4-preview-ingress:\n    internal: true", self.compose)

    def test_proxy_reuses_local_image_and_has_one_fixed_upstream(self):
        self.assertIn("\n    image: anidown-v4:preview-local", self.app)
        self.assertIn("\n    image: anidown-v4:preview-local", self.proxy)
        self.assertIn("\n    pull_policy: never", self.proxy)
        self.assertNotIn("\n    build:", self.proxy)
        self.assertNotIn("\n    environment:", self.proxy)
        self.assertIn(
            "\n    command:\n      - python\n      - -B\n      - -m\n      - src.v4.loopback_proxy",
            self.proxy,
        )
        self.assertIn(
            "\n    depends_on:\n      anidown-v4:\n        condition: service_healthy",
            self.proxy,
        )
        images = re.findall(r"^\s+image:\s*(\S+)$", self.compose, flags=re.MULTILINE)
        self.assertEqual(images, ["anidown-v4:preview-local", "anidown-v4:preview-local"])

    def test_both_containers_remain_unprivileged_and_effects_stay_off(self):
        for service in (self.app, self.proxy):
            self.assertIn("\n    read_only: true", service)
            self.assertIn("\n    cap_drop:\n      - ALL", service)
            self.assertIn("\n    security_opt:\n      - no-new-privileges:true", service)
            self.assertIn('\n    restart: "no"', service)
            self.assertIn("\n    pids_limit: 128", service)
        self.assertNotIn("\n    volumes:", self.proxy)
        self.assertIn('ANIDOWN_V4_LIVE_SONARR: "0"', self.app)
        self.assertIn('ANIDOWN_V4_DOWNLOADS_ENABLED: "0"', self.app)
        self.assertIn('ANIDOWN_V4_DOWNLOAD_AUTOSYNC: "0"', self.app)
        self.assertIn(
            "ANIDOWN_V4_TRUSTED_PROXY_CIDRS: 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
            self.app,
        )
        self.assertNotIn("0.0.0.0/0", self.app)

    def test_release_test_job_validates_the_compose_model(self):
        workflow = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
        self.assertIn(
            "docker compose --profile v4-preview -f docker-compose.yml config --quiet",
            workflow,
        )

    def test_proxy_module_is_part_of_the_embedded_source_manifest(self):
        from src.v4.source_manifest import build_manifest

        paths = {row["path"] for row in build_manifest(ROOT)["files"]}
        self.assertIn("src/v4/loopback_proxy.py", paths)


class Phase6ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_upstream_proxy_relays_both_directions(self):
        from src.v4.loopback_proxy import start_proxy

        payload = (b"phase6-request-" * 8192) + b"end"

        async def upstream(reader, writer):
            request = await reader.read()
            writer.write(b"reply:" + request)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        proxy_server = await start_proxy(
            listen_host="127.0.0.1",
            listen_port=0,
            upstream_host="127.0.0.1",
            upstream_port=upstream_port,
        )
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(payload)
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await reader.read(), b"reply:" + payload)
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            upstream_server.close()
            await proxy_server.wait_closed()
            await upstream_server.wait_closed()

    def test_container_entrypoint_has_no_destination_arguments(self):
        from src.v4 import loopback_proxy

        self.assertEqual(loopback_proxy.LISTEN_HOST, "0.0.0.0")
        self.assertEqual(loopback_proxy.LISTEN_PORT, 6004)
        self.assertEqual(loopback_proxy.UPSTREAM_HOST, "anidown-v4")
        self.assertEqual(loopback_proxy.UPSTREAM_PORT, 6004)
        self.assertLessEqual(loopback_proxy.MAX_CONNECTIONS, 64)
        self.assertLessEqual(loopback_proxy.CONNECT_TIMEOUT_SECONDS, 3)
        self.assertLessEqual(loopback_proxy.IDLE_TIMEOUT_SECONDS, 30)
        self.assertFalse(hasattr(loopback_proxy, "parse_args"))


if __name__ == "__main__":
    unittest.main()
