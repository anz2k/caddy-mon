import asyncio
import json
import logging
from typing import Set, Dict, Any

try:
    from fastapi import Request
except ImportError:
    Request = Any  # type: ignore

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Manages active SSE subscriber queues and dispatches events."""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        """Register a new client connection queue."""
        q = asyncio.Queue(maxsize=50)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        """Unregister a client connection queue."""
        async with self._lock:
            self._subscribers.discard(q)

    async def broadcast(self, event_type: str, data: Dict[str, Any]):
        """Send an SSE event message to all connected clients."""
        if not self._subscribers:
            return

        payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        async with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    # Drop slow clients if queue is saturated
                    pass


# Global broadcaster instance
broadcaster = EventBroadcaster()


async def sse_event_stream(request: Request):
    """Async generator yielding SSE formatted strings for StreamingResponse."""
    queue = await broadcaster.subscribe()
    try:
        # Send initial connected event
        yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for next event or send a keep-alive ping every 15s
                msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield msg
            except asyncio.TimeoutError:
                # Keep-alive comment
                yield ": ping\n\n"
    finally:
        await broadcaster.unsubscribe(queue)
