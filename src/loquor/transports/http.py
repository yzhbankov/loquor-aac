"""HTTP transport over Wi-Fi/IP.

Simple request/response: POST a signed envelope to ``/loquor/v1/message``,
receive a signed envelope back. ``GET /loquor/v1/capabilities`` lets peers
confirm identity and capabilities after discovery.

For the PoC this is plain HTTP on a trusted lab network with HMAC-signed
messages; production would add TLS with pinned credentials.
"""

from __future__ import annotations

import asyncio

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from loquor.discovery import PeerAddress
from loquor.messages import Envelope
from loquor.transports.base import MessageHandler, PeerUnreachable, Transport


class HttpTransport(Transport):
    def __init__(self, host: str = "0.0.0.0", port: int = 0, request_timeout: float = 10.0):
        self._host = host
        self._port = port
        self._timeout = ClientTimeout(total=request_timeout)
        self._runner: web.AppRunner | None = None
        self._session: ClientSession | None = None
        self._capabilities_doc: dict = {}

    def set_capabilities_doc(self, doc: dict) -> None:
        """Document served at /loquor/v1/capabilities (agent id + capabilities)."""
        self._capabilities_doc = doc

    async def start(self, handler: MessageHandler) -> int:
        app = web.Application()

        async def handle_message(request: web.Request) -> web.Response:
            try:
                envelope = Envelope.model_validate(await request.json())
            except Exception as exc:  # malformed/unvalidated input is rejected
                return web.json_response(
                    {"error": "invalid_envelope", "detail": str(exc)}, status=400
                )
            reply = await handler(envelope)
            return web.json_response(reply.model_dump(mode="json"))

        async def handle_capabilities(_: web.Request) -> web.Response:
            return web.json_response(self._capabilities_doc)

        app.router.add_post("/loquor/v1/message", handle_message)
        app.router.add_get("/loquor/v1/capabilities", handle_capabilities)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._session = ClientSession(timeout=self._timeout)

        # Resolve the actually bound port (port=0 means "pick a free one").
        assert self._runner.addresses, "server failed to bind"
        self._port = self._runner.addresses[0][1]
        return self._port

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def send(self, peer: PeerAddress, envelope: Envelope) -> Envelope:
        assert self._session is not None, "transport not started"
        url = f"{peer.base_url}/loquor/v1/message"
        try:
            async with self._session.post(
                url, json=envelope.model_dump(mode="json")
            ) as response:
                body = await response.json()
        except (ClientError, asyncio.TimeoutError, OSError) as exc:
            raise PeerUnreachable(f"{peer.agent_id} at {url}: {exc}") from exc
        return Envelope.model_validate(body)
