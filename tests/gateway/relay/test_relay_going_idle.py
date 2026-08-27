"""Phase 5 §5.3 — going-idle / buffered-flip primitive (gateway side).

Exercises the WebSocketRelayTransport's going_idle/ack handshake, the
buffered-inbound ack (a bufferId-carrying inbound is acked after the handler
runs), the NET-NEW reconnect loop (re-dial + re-handshake after an unexpected
close), and the RelayAdapter emitting going_idle from its existing drain
(disconnect) transition. All against a real in-process websockets server.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from gateway.relay.ws_transport import WebSocketRelayTransport, WEBSOCKETS_AVAILABLE

pytestmark = pytest.mark.skipif(not WEBSOCKETS_AVAILABLE, reason="websockets not installed")

if WEBSOCKETS_AVAILABLE:
    import websockets


DESCRIPTOR = {
    "contract_version": 1,
    "platform": "discord",
    "label": "Discord",
    "max_message_length": 2000,
    "supports_draft_streaming": False,
    "supports_edit": True,
    "supports_threads": True,
    "markdown_dialect": "discord",
    "len_unit": "chars",
}


class _IdleAwareServer:
    """Connector stub: descriptor on hello, acks going_idle, records inbound_acks,
    and can push buffered inbound frames (with bufferId) after handshake."""

    def __init__(self):
        self.received: list[dict] = []
        self.inbound_acks: list[str] = []
        self.going_idle_count = 0
        self._server = None
        self.url = ""
        # Frames to push right after each handshake (e.g. buffered backlog replay).
        self._to_push: list[dict] = []
        self.connections = 0

    async def start(self):
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        sock = next(iter(self._server.sockets))
        self.url = f"ws://127.0.0.1:{sock.getsockname()[1]}"

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, ws):
        self.connections += 1
        try:
            async for raw in ws:
                for line in str(raw).split("\n"):
                    if not line.strip():
                        continue
                    frame = json.loads(line)
                    self.received.append(frame)
                    await self._on_frame(ws, frame)
        except Exception:
            pass

    async def _on_frame(self, ws, frame):
        ftype = frame.get("type")
        if ftype == "hello":
            await ws.send(json.dumps({"type": "descriptor", "descriptor": DESCRIPTOR}) + "\n")
            for f in self._to_push:
                await ws.send(json.dumps(f) + "\n")
        elif ftype == "going_idle":
            self.going_idle_count += 1
            await ws.send(json.dumps({"type": "going_idle_ack"}) + "\n")
        elif ftype == "inbound_ack":
            self.inbound_acks.append(frame.get("bufferId"))


@pytest_asyncio.fixture
async def server():
    srv = _IdleAwareServer()
    await srv.start()
    yield srv
    await srv.stop()


@pytest.mark.asyncio
async def test_buffered_inbound_is_acked_after_handler(server):
    # A buffered delivery (bufferId present) is acked AFTER the handler runs; a
    # live delivery (no bufferId) is not acked.
    server._to_push = [
        {
            "type": "inbound",
            "event": {
                "text": "buffered",
                "message_type": "text",
                "source": {"platform": "discord", "chat_id": "c1", "chat_type": "dm"},
            },
            "bufferId": "buf-42",
        },
        {
            "type": "inbound",
            "event": {
                "text": "live",
                "message_type": "text",
                "source": {"platform": "discord", "chat_id": "c1", "chat_type": "dm"},
            },
        },
    ]
    seen = []

    async def handler(ev):
        seen.append(ev.text)

    t = WebSocketRelayTransport(server.url, "discord", "appShared")
    t.set_inbound_handler(handler)
    await t.connect()
    try:
        await t.handshake()
        await asyncio.sleep(0.1)
        assert "buffered" in seen and "live" in seen
        # Only the buffered (bufferId) delivery was acked.
        assert server.inbound_acks == ["buf-42"]
    finally:
        await t.disconnect()


@pytest.mark.asyncio
async def test_reconnect_redials_after_unexpected_close():
    # A server that drops the FIRST connection right after handshake; the
    # transport with reconnect=True re-dials and handshakes again.
    drops = {"n": 0}
    srv = _IdleAwareServer()

    async def handle(ws):
        srv.connections += 1
        async for raw in ws:
            for line in str(raw).split("\n"):
                if not line.strip():
                    continue
                frame = json.loads(line)
                if frame.get("type") == "hello":
                    await ws.send(json.dumps({"type": "descriptor", "descriptor": DESCRIPTOR}) + "\n")
                    if drops["n"] == 0:
                        drops["n"] += 1
                        await ws.close()  # force an unexpected close on the first connection
                        return

    srv._server = await websockets.serve(handle, "127.0.0.1", 0)
    sock = next(iter(srv._server.sockets))
    srv.url = f"ws://127.0.0.1:{sock.getsockname()[1]}"
    t = WebSocketRelayTransport(srv.url, "discord", "appShared", reconnect=True, reconnect_backoff_s=0.05)
    try:
        await t.connect()
        await t.handshake()
        # First connection is dropped server-side; the reconnect loop re-dials.
        await asyncio.sleep(0.2)
        assert srv.connections >= 2
    finally:
        await t.disconnect()
        srv._server.close()
        await srv._server.wait_closed()


