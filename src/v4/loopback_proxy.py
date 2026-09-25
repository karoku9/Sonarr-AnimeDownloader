"""Bounded TCP relay from Docker's host ingress to the isolated V4 runtime.

The container entry point deliberately has no arguments or environment-based
destination. It can connect only to the fixed AniDown service on the internal
Compose network, so it is not a general-purpose forward proxy.
"""

import asyncio
from contextlib import suppress


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 6004
UPSTREAM_HOST = "anidown-v4"
UPSTREAM_PORT = 6004
BUFFER_SIZE = 64 * 1024
CONNECT_TIMEOUT_SECONDS = 3
IDLE_TIMEOUT_SECONDS = 30
MAX_CONNECTIONS = 64


async def _close(writer):
    if writer is None:
        return
    writer.close()
    with suppress(ConnectionError, OSError):
        await writer.wait_closed()


async def _pump(reader, writer):
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(BUFFER_SIZE), IDLE_TIMEOUT_SECONDS)
            if not chunk:
                return
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), IDLE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        return


class FixedUpstreamProxy:
    def __init__(self, upstream_host, upstream_port):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.active_connections = 0

    async def handle(self, client_reader, client_writer):
        if self.active_connections >= MAX_CONNECTIONS:
            await _close(client_writer)
            return
        self.active_connections += 1
        upstream_writer = None
        client_to_upstream = None
        upstream_to_client = None
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(self.upstream_host, self.upstream_port),
                CONNECT_TIMEOUT_SECONDS,
            )
            client_to_upstream = asyncio.create_task(_pump(client_reader, upstream_writer))
            upstream_to_client = asyncio.create_task(_pump(upstream_reader, client_writer))
            done, _pending = await asyncio.wait(
                (client_to_upstream, upstream_to_client),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if upstream_to_client in done:
                client_to_upstream.cancel()
            else:
                with suppress(AttributeError, ConnectionError, OSError):
                    upstream_writer.write_eof()
                await upstream_to_client
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            for task in (client_to_upstream, upstream_to_client):
                if task is not None and not task.done():
                    task.cancel()
            tasks = [task for task in (client_to_upstream, upstream_to_client) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await _close(upstream_writer)
            await _close(client_writer)
            self.active_connections -= 1


async def start_proxy(
    *,
    listen_host=LISTEN_HOST,
    listen_port=LISTEN_PORT,
    upstream_host=UPSTREAM_HOST,
    upstream_port=UPSTREAM_PORT,
):
    proxy = FixedUpstreamProxy(upstream_host, upstream_port)
    return await asyncio.start_server(
        proxy.handle,
        listen_host,
        listen_port,
        backlog=MAX_CONNECTIONS,
    )


async def serve():
    server = await start_proxy()
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or ())
    print(
        f"AniDown V4 loopback ingress: {addresses} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
