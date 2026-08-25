"""Unit tests for caddy_mon.sse EventBroadcaster."""

import json
import pytest
from caddy_mon.sse import EventBroadcaster


@pytest.mark.asyncio
async def test_broadcaster_subscribe_and_broadcast():
    broadcaster = EventBroadcaster()
    queue = await broadcaster.subscribe()

    test_data = {"status": "ok", "count": 42}
    await broadcaster.broadcast("state_update", test_data)

    msg = queue.get_nowait()
    assert msg.startswith("event: state_update\n")
    assert f"data: {json.dumps(test_data)}\n\n" in msg

    await broadcaster.unsubscribe(queue)
    assert len(broadcaster._subscribers) == 0


@pytest.mark.asyncio
async def test_broadcaster_multiple_subscribers():
    broadcaster = EventBroadcaster()
    q1 = await broadcaster.subscribe()
    q2 = await broadcaster.subscribe()

    await broadcaster.broadcast("ping", {"msg": "hello"})

    m1 = q1.get_nowait()
    m2 = q2.get_nowait()

    assert "event: ping\n" in m1
    assert "event: ping\n" in m2

    await broadcaster.unsubscribe(q1)
    await broadcaster.unsubscribe(q2)
