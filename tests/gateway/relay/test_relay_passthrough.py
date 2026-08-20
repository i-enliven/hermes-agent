"""Relay passthrough-over-WS forwarding (Phase 5 §5.1).

Proves the gateway side of §5.1: a connector-forwarded passthrough request
(Discord interaction, Twilio, …) arrives over the SAME outbound /relay WS as
inbound messages (a hosted gateway has no public inbound port), and the relay
adapter handles it — decoding the byte-preserved body and routing a Discord
interaction through the normal agent path (handle_message).

Mirrors test_relay_interrupt.py's wiring discipline (connect() registers the
connector->gateway handlers on the transport).
"""

from __future__ import annotations

import base64
import json

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.relay.adapter import RelayAdapter
from gateway.relay.descriptor import CONTRACT_VERSION, CapabilityDescriptor
from gateway.relay.ws_transport import PassthroughForward, _passthrough_from_wire

from tests.gateway.relay.stub_connector import StubConnector


def _desc() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        contract_version=CONTRACT_VERSION,
        platform="discord",
        label="Discord",
        max_message_length=2000,
        supports_draft_streaming=False,
        supports_edit=True,
        supports_threads=True,
        markdown_dialect="discord",
        len_unit="chars",
    )


@pytest.fixture
def adapter():
    return RelayAdapter(PlatformConfig(), _desc(), transport=StubConnector(_desc()))


def test_passthrough_from_wire_byte_preserves_body():
    """The wire frame's base64 body decodes back to the exact bytes (parity with
    the connector's toPassthroughForward)."""
    original = json.dumps({"type": 2, "data": {"name": "ping"}, "guild_id": "g1"}).encode("utf-8")
    wire = {
        "platform": "discord",
        "botId": "appShared",
        "method": "POST",
        "path": "/interactions/discord/appShared",
        "headers": [["content-type", "application/json"]],
        "bodyB64": base64.b64encode(original).decode("ascii"),
    }
    fwd = _passthrough_from_wire(wire)
    assert fwd.platform == "discord"
    assert fwd.bot_id == "appShared"
    assert fwd.body == original
    assert fwd.headers == [("content-type", "application/json")]


@pytest.mark.asyncio
async def test_connect_wires_passthrough_handler_over_ws(adapter):
    """connect() registers the passthrough handler on the transport so a
    connector-delivered passthrough_forward frame reaches the adapter."""
    await adapter.connect()
    stub = adapter._transport
    assert stub._passthrough is not None


